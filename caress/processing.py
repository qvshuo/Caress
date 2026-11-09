"""One transient scrape per code in a batch; each Emby Item is written separately."""

import asyncio
import hashlib
import shutil
from dataclasses import dataclass, field
from pathlib import Path

from .actors import ActorAliases, actor_type, map_actor_names
from .emby import pixel_digest
from .http import FetchError
from .images import crop_poster, fetch_artwork, image_plan
from .models import ScrapeRequest, Status
from .portraits import Gfriends, actor_image
from .scraper import Scraper
from .translation import Translator, actor_name_rules


@dataclass
class Prepared:
    metadata: object
    source: str
    images: list = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    mappings: list = field(default_factory=list)
    kind: int | None = None
    digests: dict = field(default_factory=dict)


class WorkProcessor:
    def __init__(self, settings, runtime, temporary):
        self.settings = settings
        self.temporary = Path(temporary)
        self.scraper = Scraper(settings, runtime=runtime)
        self.translator = Translator(settings.translation)
        self.names = ActorAliases.load(settings.database)
        self.portraits = Gfriends(self.names, Path(temporary))
        self.prepared = {}
        self.people_lock = asyncio.Lock()
        self.people_done = set()
        self.people_attempts = {}

    async def __aenter__(self):
        await self.scraper.__aenter__()
        return self

    async def __aexit__(self, *args):
        # All connections are closed even if one close fails.
        try:
            await self.scraper.__aexit__(*args)
        finally:
            try:
                await self.translator.close()
            finally:
                await self.portraits.__aexit__(*args)

    async def prepare(self, code, source=None):
        key = (code, source)
        if key not in self.prepared:
            self.prepared[key] = asyncio.create_task(self._prepare(code, source))
        return await asyncio.shield(self.prepared[key])

    async def release(self, code, source):
        task = self.prepared.pop((code, source), None)
        if task is None:
            return
        if not task.done():
            task.cancel()
        await asyncio.gather(task, return_exceptions=True)
        if not task.cancelled() and task.exception() is None:
            prepared = task.result()
            folder = (
                self.temporary
                / hashlib.sha256((code + prepared.source).encode()).hexdigest()
            )
            await asyncio.to_thread(shutil.rmtree, folder)

    async def finish(self):
        for task in self.prepared.values():
            if not task.done():
                task.cancel()
        # Drain every group even when one directory fails to clean up.
        results = await asyncio.gather(
            *(self.release(*key) for key in list(self.prepared)), return_exceptions=True
        )
        for result in results:
            if isinstance(result, BaseException):
                raise result

    async def _prepare(self, code, source):
        result = await self.scraper.scrape(ScrapeRequest(code=code, source=source))
        if result.status != Status.FOUND or result.metadata is None:
            errors = "; ".join(f"{r.source}: {r.status}" for r in result.results)
            raise ValueError("刮削未成功：" + errors)
        metadata = result.metadata.model_copy(deep=True)
        selected = next(r for r in result.results if r.source == result.selected_source)
        warnings = await self.scraper.resolve_actors(
            metadata.actors,
            self.names,
            result.classification.category,
            selected.actor_ids,
        )
        source_actors = metadata.actors
        metadata.actors, mappings, issues = map_actor_names(
            metadata.actors,
            self.names,
            actor_type(result.classification.category),
            selected.actor_ids,
        )
        original = metadata.original_title or metadata.title
        metadata.original_title = original
        metadata.title = original
        report = await self.translator.metadata(
            metadata,
            names=actor_name_rules(self.names, [*source_actors, *metadata.actors]),
        )
        prepared = Prepared(
            metadata,
            result.selected_source.value,
            warnings=[
                *warnings,
                *[i["error"] for i in report.errors],
                *["演员别名存在歧义，保留原名" for _ in issues],
            ],
            mappings=mappings,
            kind=actor_type(result.classification.category),
        )
        folder = (
            self.temporary
            / hashlib.sha256((code + prepared.source).encode()).hexdigest()
        )
        folder.mkdir()
        try:
            downloaded = {}
            for candidate, role in image_plan(metadata.artwork):
                try:
                    if candidate.url not in downloaded:
                        image = await fetch_artwork(self.scraper.client, candidate)
                        path = folder / (
                            hashlib.sha256(candidate.url.encode()).hexdigest()
                            + ".original"
                        )
                        await asyncio.to_thread(path.write_bytes, image)
                        downloaded[candidate.url] = path
                    path = downloaded[candidate.url]
                    data = await asyncio.to_thread(path.read_bytes)
                    if role == "poster":
                        data = (await asyncio.to_thread(crop_poster, data))["content"]
                        path = folder / "poster.png"
                        await asyncio.to_thread(path.write_bytes, data)
                    if role != "poster":
                        prepared.digests[path] = await asyncio.to_thread(
                            pixel_digest, data
                        )
                    prepared.images.append(
                        ("Primary" if role == "poster" else "Backdrop", path)
                    )
                except (FetchError, ValueError, OSError) as exc:
                    prepared.errors.append(f"{role}: {exc}")
            return prepared

        except BaseException:
            await asyncio.to_thread(shutil.rmtree, folder)
            raise

    async def write(self, client, ident, prepared, media):
        current = await client.write_metadata(ident, prepared.metadata, media)
        signatures = (
            await client.backdrop_signatures(ident)
            if any(kind == "Backdrop" for kind, _ in prepared.images)
            else set()
        )
        for kind, path in prepared.images:
            data = await asyncio.to_thread(path.read_bytes)
            if kind == "Primary":
                await client.upload(ident, kind, data)
            else:
                await client.append_backdrop(
                    ident, data, signatures, prepared.digests.get(path)
                )
        # Portraits are optional and require a verified mapping, an Emby ID and no existing image.
        warnings = list(prepared.warnings)
        async with self.people_lock:
            for mapping in prepared.mappings:
                if not mapping["actor_id"]:
                    continue
                person = next(
                    (
                        p
                        for p in current.get("People", [])
                        if p.get("Type") == "Actor" and p["Name"] == mapping["name"]
                    ),
                    None,
                )
                if (
                    not person
                    or not person.get("Id")
                    or person["Id"] in self.people_done
                    or self.people_attempts.get(person["Id"], 0) >= 2
                ):
                    continue
                self.people_attempts[person["Id"]] = (
                    self.people_attempts.get(person["Id"], 0) + 1
                )
                try:
                    if any(
                        i["ImageType"] == "Primary"
                        for i in await client.image_info(person["Id"])
                    ):
                        self.people_done.add(person["Id"])
                        continue
                    from .models import Actor

                    records = await self.portraits.save(
                        [Actor(name=person["Name"])], kind=prepared.kind
                    )
                    if records and records[0]["status"] in ("saved", "existing"):
                        image = actor_image(self.portraits.output, person["Name"])
                        await client.upload(person["Id"], "Primary", image.read_bytes())
                        self.people_done.add(person["Id"])
                    elif records and records[0]["status"] == "not_found":
                        self.people_done.add(person["Id"])
                except (ValueError, OSError, FetchError) as exc:
                    warnings.append(f"演员头像：{exc}")
                except Exception as exc:
                    from .emby import sync_error

                    warnings.append(sync_error(exc))
        if prepared.errors:
            raise ValueError(
                "元数据可能已写入，图片未全部成功：" + "; ".join(prepared.errors)
            )
        return warnings
