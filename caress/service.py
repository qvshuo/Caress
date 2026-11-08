"""Service lifetime, work projections and shared resources."""
import asyncio
import fcntl
import logging
import sqlite3
from pathlib import Path
from urllib.parse import quote

from .actors import ActorAliases
from .catalog import CatalogRules
from .http import WebClient
from .models import Category, Work
from .runtime import RuntimeStore
from .scanner import scan, validate_roots
from .scraper import Scraper
from .settings import Settings
from .storage import image_stems, target_folder
from .translation import Translator

logger = logging.getLogger(__name__)


class Caress:
    def __init__(self, settings: Settings, *, scraper_factory=Scraper):
        if not settings.media_directories:
            raise ValueError("media_directories are required")
        self.settings = settings
        self.output = settings.database.parent / "images"
        validate_roots(settings.media_directories, self.output, require_existing=False)
        internal = settings.database.parent
        if internal.is_symlink():
            raise ValueError("runtime directory must not be a symlink")
        internal.mkdir(parents=True, exist_ok=True)
        lock = internal / "service.lock"
        if lock.is_symlink():
            raise ValueError("service lock must not be a symlink")
        self.process_lock = lock.open("a")
        try:
            fcntl.flock(self.process_lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            self.runtime = RuntimeStore(settings.database)
            if not self.runtime.connection.execute("SELECT 1 FROM sqlite_master WHERE name='options'").fetchone():
                with self.runtime.transaction():
                    settings.save(connection=self.runtime.connection)
            rules = CatalogRules.load(settings.database)
            ActorAliases.load(settings.database)
        except (OSError, ValueError, sqlite3.Error) as exc:
            if hasattr(self, "runtime"):
                self.runtime.close()
            self.process_lock.close()
            if isinstance(exc, ValueError):
                raise
            raise ValueError(
                "runtime records are unreadable or another caress writer is active"
            ) from None
        self.operation_lock = asyncio.Lock()
        self._work_locks = {}
        self.scraper_factory = scraper_factory
        from .operations import WorkOperations
        from .tasks import TaskQueue
        self.tasks = TaskQueue(self)
        self.operations = WorkOperations(self)
        self.translator = Translator(settings.translation, database=settings.database)
        self._artwork_client = None
        self._artwork_lock = asyncio.Lock()
        from .emby import EmbySync
        self.emby = EmbySync(self)
        self.closed = False
        metadata_snapshot = self.runtime.metadata_snapshot()
        for row in self.runtime.works_data:
            if row["status"] != "invalid_key":
                work = self.runtime.work(row["code"])
                self._apply_result(work, self.runtime.result(work.code), rules=rules, metadata_snapshot=metadata_snapshot)
                row.update(work.model_dump(mode="json", exclude={"media"}))


    async def artwork_client(self):
        async with self._artwork_lock:
            if self._artwork_client is None:
                client = WebClient(self.settings, runtime=self.runtime)
                await client.__aenter__()
                self._artwork_client = client
            return self._artwork_client

    async def resolve_actors(self, actors, names, category, ids):
        async with Scraper(self.settings, client=await self.artwork_client()) as scraper:
            return await scraper.resolve_actors(actors, names, category, ids)

    def work_lock(self, code):
        return self._work_locks.setdefault(code, asyncio.Lock())

    def work_running(self, code):
        return code in self.tasks.running_codes

    async def activate_settings(self, *, rescan=True):
        updated = Settings.load(self.settings.database)
        if updated.model_dump() == self.settings.model_dump():
            return
        if rescan and updated.media_directories != self.settings.media_directories:
            rows = await self.scan_async(settings=updated, persist=False)
            self.runtime.save_scan([Work.model_validate(row) for row in rows])
        old_translator = self.translator
        old_client, self._artwork_client = self._artwork_client, None
        self.settings = updated
        self.translator = Translator(updated.translation, database=updated.database)
        await old_translator.close()
        if old_client is not None:
            await old_client.__aexit__(None, None, None)

    def _apply_result(self, work, result, *, rules=None, metadata_snapshot=None):
        work.category = (rules or CatalogRules.load(self.settings.database)).classify(work.code).category
        work.title, work.actors = None, []
        work.poster = None
        work.status = "failed" if result.get("status") not in (None, "found", "unchanged") else "pending"
        metadata = self.runtime.metadata(work.code) if metadata_snapshot is None else metadata_snapshot.get(work.code)
        if metadata:
            work.title, work.actors = metadata.title, [actor.name for actor in metadata.actors]
            work.status = "partial" if result.get("partial") else "scraped"
        folder = target_folder(self.output, work.code)
        stems = {stem + "-poster" for stem in image_stems(work)}
        try:
            posters = sorted(folder.iterdir())
        except OSError:
            posters = []
        for poster in posters:
            if poster.stem not in stems or poster.suffix.lower() not in {".png", ".jpg", ".jpeg", ".webp", ".gif"}:
                continue
            try:
                if poster.is_symlink() or not poster.is_file():
                    continue
                stat = poster.stat()
            except OSError:
                # ArtworkCandidate can be replaced while a scan reads its thumbnail projection.
                continue
            work.poster = f"/api/works/{quote(work.code, safe='')}/artwork?file={quote(poster.name, safe='')}&v={stat.st_mtime_ns}-{stat.st_size}"
            break

    def _queue_status(self, work):
        if work.status != "invalid_key":
            if self.work_running(work.code):
                work.status = "running"
            elif self.tasks.pending_counts[work.code]:
                work.status = "queued"

    def _scan_rows(self, options, results, previous, persist, metadata_snapshot):
        works = scan(options.media_directories)
        rules = CatalogRules.load(options.database)
        for work in works:
            if work.status == "pending":
                result = results.get(work.code, {})
                self._apply_result(work, result, rules=rules, metadata_snapshot=metadata_snapshot)
                self._queue_status(work)
            elif persist and (work.code, work.status) not in previous:
                logger.warning("跳过 %s：%s", work.code, "; ".join(work.warnings))
        return works

    def refresh_work(self, code):
        try:
            work = self.runtime.work(code)
        except ValueError:
            return
        if not self.work_running(code):
            self._apply_result(work, self.runtime.result(code))
        self._queue_status(work)
        self.runtime.update_work(work)

    async def scan_async(self, *, settings=None, persist=True):
        options, results = settings or self.settings, self.runtime.results
        before = {row["code"]: {key: value for key, value in row.items() if key != "media"} for row in self.runtime.works()}
        previous = {(m["code"], m["status"]) for m in before.values()}
        works = await asyncio.to_thread(self._scan_rows, options, results, previous, persist, self.runtime.metadata_snapshot())
        if persist:
            if self.closed or options is not self.settings:
                return self.runtime.works()
            # Running works retain their identity; completed writes beat the scan's older snapshot.
            running = set(self.tasks.running_codes)
            for code in running:
                current = self.runtime.works(codes=[code])
                if len(current) == 1 and current[0]["status"] != "invalid_key":
                    retained = self.runtime.work(code)
                    media = {item.id: item for item in retained.media}
                    for scanned in works:
                        if scanned.code == code:
                            media.update({item.id: item for item in scanned.media})
                    retained.media = list(media.values())
                    works = [work for work in works if work.code != code]
                    works.append(retained)
            for work in works:
                current = self.runtime.find_work(work.code)
                if current and work.status != "invalid_key" and (
                    current != before.get(work.code)
                    or results.get(work.code) != self.runtime.results.get(work.code)
                ):
                    for field in ("title", "actors", "poster", "status", "category", "warnings"):
                        setattr(work, field, Category(current[field]) if field == "category" else current[field])
                self._queue_status(work)
            works.sort(key=lambda work: work.code)
            self.runtime.save_scan(works)
        return [work.model_dump(mode="json") for work in works]

    def health(self, code):
        from .models import WorkHealth
        result = self.runtime.result(code)
        roles = self.runtime.selection(code).get("artwork", {}).get("roles", {})
        folder = target_folder(self.output, code)
        problems = []
        for role, state in roles.items():
            name = state.get("file")
            if name and (Path(name).name != name or not (folder / name).is_file() or (folder / name).is_symlink()):
                label = "封面" if role == "poster" else "封面原图" if role == "fanart" else "剧照 " + role.removeprefix("fanart")
                problems.append(label + "文件缺失，请重新选择图片来源后保存")
        return WorkHealth(metadata=self.runtime.metadata(code) is not None,
            incomplete_stages=result.get("incomplete_stages", []), artwork_problems=problems)

    async def close(self):
        self.closed = True
        await self.tasks.close()
        if self._artwork_client is not None:
            await self._artwork_client.__aexit__(None, None, None)
        await self.translator.close()
        self.runtime.close()
        self.process_lock.close()


class Application:
    """Own the configured service and change its resources at safe boundaries."""

    def __init__(self, settings, service_factory, directories):
        self.settings = settings
        self.database = settings.database
        self.service_factory = service_factory
        self.directories = directories
        self.service = None
        self.configuring = False

    async def start(self):
        from .images import validate_resources
        validate_resources()
        if self.directories:
            self.directories.validate(self.settings)
        if self.settings.media_directories:
            self.service = self.service_factory(self.settings)
            try:
                if not self.service.runtime.has_scan():
                    await self.service.scan_async()
            except BaseException:
                await self.close()
                raise

    async def close(self):
        if self.service:
            await self.service.close()
            self.service = None

    def current_settings(self):
        return Settings.load(self.database) if self.service else self.settings

    async def configure(self, value):
        if self.configuring:
            raise ValueError("configuration change in progress")
        service = self.service
        if service and service.operation_lock.locked():
            raise ValueError("cannot change configuration while metadata is being edited")

        def merge(old, new):
            if isinstance(old, dict) and isinstance(new, dict):
                return {
                    **old,
                    **{
                        k: old.get(k)
                        if k in {"api_key", "cookie"} and v == "***"
                        else merge(old.get(k), v)
                        for k, v in new.items()
                    },
                }
            return new

        previous = self.current_settings()
        updated = Settings.model_validate(merge(previous.model_dump(mode="json"), value))
        updated.database = previous.database
        if self.directories:
            self.directories.validate(updated)
        ready = bool(updated.media_directories)
        path = self.database
        if service and service.tasks.active():
            if not ready:
                raise ValueError("任务结束后才能清空媒体目录")
            from .scanner import validate_roots
            await asyncio.to_thread(validate_roots, updated.media_directories, service.output)
            with service.runtime.transaction():
                updated.save(connection=service.runtime.connection)
            return
        self.configuring = True
        candidate = None
        try:
            if service and ready:
                preview = await service.scan_async(settings=updated, persist=False)
                with service.runtime.transaction():
                    service.runtime.save_scan([Work.model_validate(row) for row in preview])
                    updated.save(connection=service.runtime.connection)
                await service.activate_settings(rescan=False)
            elif ready:
                candidate = self.service_factory(updated)
                await candidate.scan_async()
                with candidate.runtime.transaction():
                    updated.save(connection=candidate.runtime.connection)
                self.service = candidate
                candidate = None
            else:
                updated.save(path)
                if service:
                    await service.close()
                    self.service = None
            self.settings = updated
        finally:
            if candidate:
                await candidate.close()
            self.configuring = False
        return

