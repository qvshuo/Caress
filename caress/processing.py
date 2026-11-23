import asyncio
import hashlib
import shutil
from dataclasses import dataclass, field
from io import BytesIO
from pathlib import Path

import httpx
from PIL import Image

from .emby import ImageWriteError, image_pixels_match, pixel_digest, sync_error
from .http import FetchError
from .images import crop_poster, fetch_artwork, image_plan
from .models import Actor, ScrapeRequest, Status
from .portraits import Gfriends, actor_image
from .scraper import Scraper
from .translation import Translator, actor_name_rules


@dataclass
class Prepared:
    metadata: object
    source: str
    images: list = field(default_factory=list)
    errors: list[tuple[str, str]] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    mappings: list = field(default_factory=list)
    digests: dict = field(default_factory=dict)
    poster_lock: asyncio.Lock = field(default_factory=asyncio.Lock)


class WorkProcessor:
    def __init__(self, settings, runtime, temporary):
        self.settings = settings
        self.temporary = Path(temporary)
        self.scraper = Scraper(settings, runtime=runtime)
        self.translator = Translator(settings.translation)
        self.portraits = Gfriends(Path(temporary))
        self.prepared = {}
        self.people_lock = asyncio.Lock()
        self.people_done = set()
        self.people_attempts = {}

    async def __aenter__(self):
        await self.scraper.__aenter__()
        return self

    async def __aexit__(self, *args):
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
        results = await asyncio.gather(
            *(self.release(*key) for key in list(self.prepared)), return_exceptions=True
        )
        for result in results:
            if isinstance(result, BaseException):
                raise result

    async def _prepare(self, code, source):
        result = await self.scraper.scrape(ScrapeRequest(code=code, source=source))
        if result.status != Status.FOUND or result.metadata is None:
            errors = "；".join(
                f"{r.source}：{r.message or ('未找到作品' if r.status == Status.NOT_FOUND else '来源不支持此作品' if r.status == Status.UNSUPPORTED else '来源请求失败')}"
                for r in result.results
            )
            raise ValueError("未获取到作品元数据：" + errors)
        if not result.metadata.title.strip():
            raise ValueError("来源作品标题为空，未写入元数据")
        metadata = result.metadata.model_copy(deep=True)
        source_actors = metadata.actors
        mappings = {}
        for actor in source_actors:
            match = await self.portraits.resolve(actor.name)
            name = match.name if match else actor.name
            mappings[name] = mappings.get(name) or match
        metadata.actors = [Actor(name=name) for name in mappings]
        warnings = (
            ["Gfriends 演员索引不可用，已保留来源原名：" + self.portraits.index_error]
            if self.portraits.index_error
            else []
        )
        original = metadata.original_title or metadata.title
        metadata.original_title = original
        metadata.title = original
        if self.settings.translation.enabled:
            await self.translator.verify()
        report = await self.translator.metadata(
            metadata,
            names=actor_name_rules(a.name for a in [*source_actors, *metadata.actors]),
        )
        prepared = Prepared(
            metadata,
            result.selected_source.value,
            errors=[
                ("Backdrop", f"{source_result.source}：{error}")
                for source_result in result.results
                if source_result.source == result.selected_source
                for error in source_result.backdrop_errors
            ],
            warnings=[
                *result.warnings,
                *warnings,
                *[i["error"] for i in report.errors],
            ],
            mappings=[
                {"name": name, "portrait": match} for name, match in mappings.items()
            ],
        )
        folder = (
            self.temporary
            / hashlib.sha256((code + prepared.source).encode()).hexdigest()
        )
        folder.mkdir()
        try:
            planned = {}
            for candidate, role in image_plan(metadata.artwork):
                entry = planned.setdefault(candidate.url, (candidate, []))
                kind = "Primary" if role == "poster" else "Backdrop"
                if kind not in entry[1]:
                    entry[1].append(kind)
            for url, (candidate, kinds) in planned.items():
                try:
                    data = await fetch_artwork(self.scraper.client, candidate)
                    if "Backdrop" in kinds:
                        with Image.open(BytesIO(data)) as image:
                            width, height = image.size
                            if image.getexif().get(274) in (5, 6, 7, 8):
                                width, height = height, width
                        if width <= height:
                            kinds = [kind for kind in kinds if kind != "Backdrop"]
                    if not kinds:
                        continue
                    path = folder / (
                        hashlib.sha256(url.encode()).hexdigest() + ".original"
                    )
                    await asyncio.to_thread(path.write_bytes, data)
                    if "Backdrop" in kinds:
                        prepared.digests[path] = await asyncio.to_thread(
                            pixel_digest, data
                        )
                    prepared.images.extend((kind, path) for kind in kinds)
                except (FetchError, ValueError, OSError) as exc:
                    prepared.errors.extend(
                        (
                            kind,
                            f"{'封面' if kind == 'Primary' else '背景图'}（{candidate.source}）：{exc}",
                        )
                        for kind in kinds
                    )
            return prepared

        except BaseException:
            await asyncio.to_thread(shutil.rmtree, folder)
            raise

    async def prepare_write(self, client, item, prepared, images, *, replace):
        warnings = []
        has_primary = any(row["ImageType"] == "Primary" for row in images)
        if replace or not has_primary:
            errors = [message for kind, message in prepared.errors if kind == "Primary"]
            if errors:
                raise ValueError("封面准备失败：" + "; ".join(errors))
            path = next((p for kind, p in prepared.images if kind == "Primary"), None)
            if path is not None:
                async with prepared.poster_lock:
                    poster = path.parent / "poster.png"
                    if not poster.exists():
                        try:
                            result = await asyncio.to_thread(
                                crop_poster, await asyncio.to_thread(path.read_bytes)
                            )
                            await asyncio.to_thread(
                                poster.write_bytes, result["content"]
                            )
                        except (ValueError, OSError) as exc:
                            raise ValueError(f"封面准备失败：{exc}") from exc
            elif replace:
                warnings.append(
                    "未获取到新封面，已保留原封面" if has_primary else "未获取到封面"
                )
        async with self.people_lock:
            actors = [p for p in item.get("People", []) if p.get("Type") == "Actor"]
            for mapping in prepared.mappings:
                match = mapping["portrait"]
                if match is None:
                    if replace:
                        warnings.append("演员头像未匹配：" + mapping["name"])
                    continue
                try:
                    if not replace and actors:
                        person = next(
                            (p for p in actors if p["Name"] == mapping["name"]), None
                        )
                        if not person:
                            continue
                        if person.get("Id") and any(
                            row["ImageType"] == "Primary"
                            for row in await client.image_info(person["Id"])
                        ):
                            continue
                    if actor_image(self.portraits.output, mapping["name"]):
                        continue
                    key = ("download", mapping["name"])
                    if self.people_attempts.get(key, 0) >= 2:
                        warnings.append("演员头像获取失败：本批次重试次数已用尽")
                        continue
                    self.people_attempts[key] = self.people_attempts.get(key, 0) + 1
                    result = await self.portraits.save(match)
                    if result["status"] == "error":
                        warnings.append("演员头像获取失败：" + result["error"])
                except (ValueError, OSError, httpx.HTTPError) as exc:
                    warnings.append("演员头像准备失败：" + sync_error(exc))
        return warnings

    async def write(self, client, ident, prepared, media, *, replace):
        current, changed = await client.write_metadata(
            ident, prepared.metadata, media, replace=replace
        )
        existing = await client.image_info(ident)
        has_primary = any(row["ImageType"] == "Primary" for row in existing)
        warnings = [*prepared.warnings, *(message for _, message in prepared.errors)]
        retry_backdrops = any(kind == "Backdrop" for kind, _ in prepared.errors)
        if replace and not retry_backdrops:
            try:
                backdrops = [
                    (path, prepared.digests[path])
                    for kind, path in prepared.images
                    if kind == "Backdrop"
                ]
                changed = await client.replace_backdrops(ident, backdrops) or changed
            except (ValueError, OSError, httpx.HTTPError) as exc:
                retry_backdrops = True
                if isinstance(exc, ImageWriteError):
                    changed = True
                    warnings.append(f"背景图重建未完成，部分图片可能已写入：{exc}")
                else:
                    warnings.append("背景图重建未完成：" + sync_error(exc))
        signatures = set()
        if not replace and any(kind == "Backdrop" for kind, _ in prepared.images):
            try:
                signatures = await client.backdrop_signatures(ident)
            except (ValueError, OSError, httpx.HTTPError) as exc:
                retry_backdrops = True
                warnings.append(f"背景图读取失败，未追加图片：{exc}")
                signatures = None
        for kind, path in prepared.images:
            if kind == "Primary" and has_primary and not replace:
                continue
            if kind == "Primary":
                data = await asyncio.to_thread((path.parent / "poster.png").read_bytes)
                if has_primary:
                    try:
                        current_image = await client.image(ident, "Primary")
                        if await asyncio.to_thread(
                            image_pixels_match, data, current_image.content
                        ):
                            continue
                    except ValueError:
                        pass  # A corrupt existing poster may be explicitly replaced.
                    except httpx.HTTPStatusError as exc:
                        if exc.response.status_code != 404:
                            raise
                await client.upload(ident, kind, data)
                has_primary = True
                changed = True
            elif not replace and signatures is not None:
                try:
                    data = await asyncio.to_thread(path.read_bytes)
                    changed = (
                        await client.append_backdrop(
                            ident, data, signatures, prepared.digests.get(path)
                        )
                        or changed
                    )
                except (ValueError, OSError, httpx.HTTPError) as exc:
                    retry_backdrops = True
                    changed = isinstance(exc, ImageWriteError) or changed
                    warnings.append("背景图补齐失败：" + sync_error(exc))
        async with self.people_lock:
            for mapping in prepared.mappings:
                match = mapping["portrait"]
                if match is None:
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
                try:
                    if not replace and any(
                        i["ImageType"] == "Primary"
                        for i in await client.image_info(person["Id"])
                    ):
                        self.people_done.add(person["Id"])
                        continue
                    image = actor_image(self.portraits.output, person["Name"])
                    if image:
                        data = image.read_bytes()
                        self.people_attempts[person["Id"]] = (
                            self.people_attempts.get(person["Id"], 0) + 1
                        )
                        await client.upload(person["Id"], "Primary", data)
                        self.people_done.add(person["Id"])
                        changed = True
                except (ValueError, OSError, httpx.HTTPError) as exc:
                    changed = isinstance(exc, ImageWriteError) or changed
                    warnings.append("演员头像写入失败：" + sync_error(exc))
        return warnings, changed, retry_backdrops
