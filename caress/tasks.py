import asyncio
import fcntl
import logging
import sqlite3
import uuid
from contextlib import AsyncExitStack
from collections import Counter
from typing import Literal
from pathlib import Path
from urllib.parse import quote

from pydantic import BaseModel, ConfigDict, Field

from .actors import ActorAliases, actor_type, library_actors
from .catalog import CatalogRules
from .scraper import Scraper
from .portraits import Gfriends, actor_image_index
from .http import WebClient
from .logging import progress, work_code
from .logging import task_id as log_task_id
from .models import Actor, Category
from .storage import image_stems, target_folder
from .processing import complete_report, process_work, remap_work, retry_work, retry_task_report
from .runtime import RuntimeStore, now
from .scanner import scan, validate_roots
from .settings import Settings
from .translation import Translator

logger = logging.getLogger(__name__)


class TaskRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    type: Literal[
        "fill_missing",
        "refresh",
        "update_portraits",
        "remap_actors",
    ]
    codes: list[str] = Field(default_factory=list)
    actor_names: list[str] = Field(default_factory=list)
    actor_ids: list[str] = Field(default_factory=list)
    retry_only: bool = False


class TaskQueue:
    def __init__(self, settings: Settings, *, scraper_factory=Scraper):
        if not settings.media_directories:
            raise ValueError("media_directories are required")
        self.settings = settings
        self.output = Path("/data/images")
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
            CatalogRules.load(settings.database)
            ActorAliases.load(settings.database)
        except OSError, ValueError, sqlite3.Error:
            if hasattr(self, "runtime"):
                self.runtime.close()
            self.process_lock.close()
            raise ValueError(
                "runtime records are unreadable or another caress writer is active"
            ) from None
        self.running = {}
        self.task_lock = asyncio.Lock()
        self._run_lock = asyncio.Lock()
        self._work_locks = {}
        self._running_codes = set()
        self._pending_counts = Counter()
        self.pending_settings = None
        self.on_idle = None
        self.scraper_factory = scraper_factory
        self.translator = Translator(settings.translation, database=settings.database)
        self._artwork_client = None
        self._artwork_lock = asyncio.Lock()
        from .emby import EmbySync
        self.emby = EmbySync(self)
        self.closed = False
        for row in self.runtime.works_data:
            if row["status"] != "invalid_key":
                work = self.runtime.work(row["code"])
                self._apply_result(work, self.runtime.results.get(work.code, {}))
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
        return code in self._running_codes

    async def apply_pending_settings(self):
        updated = self.pending_settings
        if updated is None or not updated.media_directories:
            return
        # Called only between batches; no worker observes a partially changed configuration.
        old = self.translator
        if updated.media_directories != self.settings.media_directories:
            rows = await asyncio.to_thread(self._scan_rows, updated, self.runtime.results, set(), False, self.runtime.metadata_snapshot())
            self.runtime.save_scan(rows)
        self.settings = updated
        self.translator = Translator(updated.translation, database=updated.database)
        if self._artwork_client is not None:
            self._artwork_client.settings = updated
        self.pending_settings = None
        await old.close()

    def active(self):
        return [t for t in self.runtime.tasks() if t["status"] in ("pending", "running")]

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
                # Artwork can be replaced while a scan reads its thumbnail projection.
                continue
            work.poster = f"/api/works/{quote(work.code, safe='')}/artwork?file={quote(poster.name, safe='')}&v={stat.st_mtime_ns}-{stat.st_size}"
            break

    def _queue_status(self, work):
        if work.status != "invalid_key":
            if self.work_running(work.code):
                work.status = "running"
            elif self._pending_counts[work.code]:
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
            self._apply_result(work, self.runtime.results.get(code, {}))
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
            running = set(self._running_codes)
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

    def create(self, request: TaskRequest):
        if self.task_lock.locked() and not self.active():
            raise ValueError("cannot create tasks while metadata is being edited")
        validate_roots(self.settings.media_directories, self.output)
        if request.actor_ids and request.type != "remap_actors":
            raise ValueError("actor_ids is only supported for actor remapping")
        eligible = {
            m["code"]
            for m in self.runtime.works()
            if m["status"] != "invalid_key"
        }
        if request.type in ("update_portraits", "remap_actors"):
            names = ActorAliases.load(self.settings.database)
            actors = library_actors([self.runtime.work(code) for code in eligible], self.output,
                                   names)
            eligible_actors = {actor["name"]: actor for actor in actors
                               if request.type == "remap_actors" or actor["portrait_eligible"]}
            selected_names = set(request.actor_names)
            if request.type == "remap_actors":
                # Pin identities across predecessor renames and partial retries.
                selected_ids = set(request.actor_ids)
                selected_ids.update(ident for name in selected_names if (ident := names.resolve_id(name)))
                for code in eligible:
                    for row in self.runtime.selection(code).get("actor_mappings", []):
                        if row["name"] in selected_names or row.get("actor_id") in selected_ids:
                            selected_names.add(row["name"])
                            if row.get("actor_id"):
                                selected_ids.add(row["actor_id"])
                if request.retry_only:
                    selected_names.intersection_update(eligible_actors)
            if not selected_names or selected_names - eligible_actors.keys():
                raise ValueError("no eligible actors or requested actor is unavailable")
            eligible = {code for name in selected_names for code in eligible_actors[name]["codes"]}
        elif request.actor_names:
            raise ValueError("actor_names is only supported for actor tasks")
        keys = sorted(set(request.codes) if request.codes else eligible)
        if not keys or any(k not in eligible for k in keys):
            raise ValueError("no eligible works or requested filename is invalid")
        params = request.model_dump(mode="json")
        params["codes"] = keys
        if request.type in ("update_portraits", "remap_actors"):
            params["actor_names"] = sorted(selected_names)
        if request.type == "remap_actors":
            params["actor_ids"] = sorted(selected_ids)
        ident = uuid.uuid4().hex
        task = {
            "id": ident,
            "type": request.type,
            "status": "pending",
            "params": params,
            "total": len(keys),
            "progress": 0,
            "success": 0,
            "failed": 0,
            "created_at": now(),
            "finished_at": None,
            "error": None,
            "items": [
                {"code": key, "status": "pending", "attempts": 0, "result": None} for key in keys
            ],
        }
        queued = {"stage": "wait", "status": "pending", "source": None, "message": "任务已排队", "time": now()}
        for item in task["items"]:
            item.update(stage=dict(queued), events=[dict(queued)])
        self.runtime.save_task(task)
        for item in task["items"]:
            self._pending_counts[item["code"]] += 1
            if not self.work_running(item["code"]):
                self.runtime.work_row(item["code"])["status"] = "queued"
        worker = asyncio.create_task(self.run(ident))
        self.running[ident] = worker
        worker.add_done_callback(lambda _: self.running.pop(ident, None))
        return self.runtime.task(ident)

    def delete(self, ident):
        task = self.runtime.task_data[ident]
        if task["status"] in ("pending", "running"):
            raise ValueError("cancel the task before deleting it")
        self.runtime.delete_task(ident)
        return {"deleted": True}

    async def cancel(self, ident):
        task = self.runtime.task_data[ident]
        if task["status"] not in ("pending", "running"):
            raise ValueError("task is not active")
        worker = self.running.get(ident)
        if worker:
            worker.cancel()
            await asyncio.gather(worker, return_exceptions=True)
        for item in task["items"]:
            if item["status"] in ("pending", "running"):
                if item["status"] == "pending":
                    self._pending_counts[item["code"]] -= 1
                item.update(status="cancelled", result=None)
                event = {
                    "stage": "finished",
                    "status": "cancelled",
                    "source": None,
                    "message": "任务已取消",
                    "time": now(),
                }
                item["events"] = [*item.get("events", []), event][-60:]
                item["stage"] = event
        task.update(status="cancelled", finished_at=now(), current=None)
        self.runtime.save_task(task)
        for code in task["params"]["codes"]:
            self.refresh_work(code)
        result = self.runtime.task(ident)
        if not self.closed and not self.active() and self.on_idle:
            await self.on_idle()
        return result

    def retry(self, ident):
        task = self.runtime.task(ident)
        if task["status"] in ("running", "pending"):
            raise ValueError("cannot retry active task")
        keys = [i["code"] for i in task["items"] if i["status"] in ("failed", "cancelled", "partial")]
        if not keys:
            raise ValueError("task contains no failed items")
        return self.create(TaskRequest.model_validate({**{key: value for key, value in task["params"].items() if key in TaskRequest.model_fields}, "codes": keys, "retry_only": True}))

    async def run(self, ident):
        async with self._run_lock:
            await self._run(ident)
        if not self.closed and not self.active() and self.on_idle:
            await self.on_idle()

    async def _run(self, ident):
        log_task_id.set(ident)
        task = self.runtime.task_data[ident]
        request = TaskRequest.model_validate(task["params"])
        task["status"] = "running"
        self.runtime.save_task(task)
        try:
            async with self.task_lock:
                await self.apply_pending_settings()
            if request.type in ("fill_missing", "refresh"):
                await self.translator.verify(force=True)
            names = ActorAliases.load(self.settings.database)
            self.runtime.save_issues(names.pending(self.runtime.issues))
            async with AsyncExitStack() as stack:
                scraper = portraits = None
                if request.type != "remap_actors":
                    scraper = await stack.enter_async_context(self.scraper_factory(self.settings))
                    portraits = await stack.enter_async_context(Gfriends(self.settings, names, self.output))
                    if isinstance(scraper.client, WebClient):
                        scraper.client.runtime = self.runtime
                else:
                    portraits = actor_image_index(self.output)
                async def process_item(item):
                    async with self.work_lock(item["code"]):
                        self._running_codes.add(item["code"])
                        try:
                            await process_locked_item(item)
                        finally:
                            self._running_codes.discard(item["code"])
                            self.refresh_work(item["code"])

                async def process_locked_item(item):
                    work_code.set(item["code"])
                    self._pending_counts[item["code"]] -= 1
                    item.update(status="running", attempts=item["attempts"] + 1)
                    self.runtime.save_task(task, item)

                    def update_progress(event):
                        events = item.get("events", [])
                        if event["stage"] == "wait" and event.get("source"):
                            events = [old for old in events if not (old["stage"] == "wait" and old.get("source") == event["source"])]
                        item["events"] = [*events, event][-60:]
                        item["stage"] = event
                        task["current"] = {"code": item["code"], **event}
                        self.runtime.save_task(task, item)

                    previous = self.runtime.results.get(item["code"], {})
                    repairing = request.type in ("fill_missing", "refresh") and request.retry_only and previous.get("partial")
                    repair_stages = previous.get("incomplete_stages", [])
                    token = progress.set(update_progress)
                    try:
                        work = self.runtime.work(item["code"])
                        self.runtime.work_row(item["code"])["status"] = "running"
                        report = await self.process(scraper, work, names, request, portraits)
                        if report["status"] in ("found", "unchanged") or report.get("partial"):
                            await self.emby.sync(work.code)
                    except Exception as exc:
                        logger.exception("处理 %s 失败", item["code"])
                        report = {"code": item["code"], "status": "error", "error": str(exc)}
                    finally:
                        progress.reset(token)
                    task_report = retry_task_report(report, repair_stages) if repairing else report
                    outcome = "skipped" if task_report["status"] == "skipped" else "partial" if task_report.get("partial") else "success" if task_report["status"] in ("found", "unchanged") else "failed"
                    item.update(status=outcome, result=task_report)
                    # A failed attempt does not replace the health of unchanged output files.
                    if request.type in ("fill_missing", "refresh") and (report["status"] in ("found", "unchanged") or not self.runtime.results.get(item["code"])):
                        self.runtime.record_result(item["code"], report)
                    update_progress({"stage": "finished", "status": outcome, "source": None,
                                     "message": None, "time": now()})
                    logger.info("番号 %s：%s", item["code"], report["status"])

                pending = iter(task["items"])
                async def worker():
                    for item in pending:
                        await process_item(item)
                async with asyncio.TaskGroup() as group:
                    for _ in range(min(10, len(task["items"]))):
                        group.create_task(worker())
            task.update(status="finished", current=None, finished_at=now())
        except Exception as exc:
            logger.exception("任务 %s 失败", ident)
            for item in task["items"]:
                if item["status"] in ("pending", "running"):
                    if item["status"] == "pending":
                        self._pending_counts[item["code"]] -= 1
                    event = {"stage": "finished", "status": "failed", "source": None,
                             "message": str(exc), "time": now()}
                    item.update(status="failed", result={"code": item["code"], "status": "error", "error": str(exc)}, stage=event)
                    item["events"] = [*item.get("events", []), event][-60:]
                    rows = self.runtime.works(codes=[item["code"]])
                    if len(rows) == 1 and rows[0]["status"] != "invalid_key":
                        self.refresh_work(item["code"])
            task.update(status="finished", current=None, error=str(exc), finished_at=now())
        self.runtime.save_task(task)

    async def process(self, scraper, work, names, request, portraits):
        if request.type == "remap_actors":
            return await remap_work(work, self.output, names, self.runtime, request.actor_names, request.actor_ids, portraits)
        if request.type == "update_portraits":
            metadata = self.runtime.metadata(work.code)
            actors = dict.fromkeys(actor.name for actor in metadata.actors if actor.name in request.actor_names) if metadata else {}
            warnings = await scraper.resolve_actors([Actor(name=name) for name in actors], names, work.category, {})
            records = await portraits.save([Actor(name=name) for name in actors], replace=True, kind=actor_type(work.category))
            failed = any(record["status"] == "error" for record in records)
            report = complete_report({"code": work.code, "status": "error" if failed else "found", "portraits": records, "warnings": warnings,
                                      "stages": {"portraits": "failed" if failed else "success"}})
            report["partial"] = failed and any(record["status"] in ("saved", "existing") for record in records)
            if failed:
                report["error"] = "部分头像更新失败" if report["partial"] else "头像更新失败"
            return report
        if request.retry_only and self.runtime.results.get(work.code, {}).get("partial"):
            stages = self.runtime.results.get(work.code, {}).get("incomplete_stages", [])
            if not stages:
                raise ValueError("没有待重试阶段")
            return await retry_work(scraper, work, self.output, self.translator, self.runtime, portraits, stages)
        return await process_work(
            scraper,
            work,
            self.output,
            names,
            self.translator,
            missing_only=request.type == "fill_missing",
            portraits=portraits,
            runtime=self.runtime,
        )

    async def close(self):
        self.closed = True
        for task in self.active():
            await self.cancel(task["id"])
        if self._artwork_client is not None:
            await self._artwork_client.__aexit__(None, None, None)
        await self.translator.close()
        self.runtime.close()
        self.process_lock.close()
