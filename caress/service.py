"""Application lifecycle, live Emby projections, and explicit crop sessions."""

import asyncio
import time
import uuid
from contextlib import asynccontextmanager
from dataclasses import dataclass
from weakref import WeakValueDictionary

import httpx

from .emby import EmbyClient, needs_enrichment
from .http import FetchError
from .images import crop_poster, crop_source_image
from .library import identify, project
from .runtime import Runtime
from .scraper import Scraper
from .tasks import BatchRunner


@dataclass
class CropSession:
    server: str
    item: str
    identity: str
    data: bytes
    expires: float


class Service:
    def __init__(self, settings):
        self.settings = settings
        self.runtime = Runtime(settings.database)
        self.locks = WeakValueDictionary()
        self.writer_lock = asyncio.Lock()
        self.batches = BatchRunner(self)
        self.crops = {}
        self.crop_pending = 0
        self.emby_lock = asyncio.Lock()
        self.clients = {}

    def item_lock(self, server, item):
        key = (server, item)
        lock = self.locks.get(key)
        if lock is None:
            lock = asyncio.Lock()
            self.locks[key] = lock
        return lock

    async def close(self):
        await self.batches.cancel()
        self.crops.clear()
        for client, _ in self.clients.values():
            await client.close()
        self.clients.clear()
        self.runtime.close()

    @asynccontextmanager
    async def emby(self):
        if not self.settings.emby.url or not self.settings.emby.api_key:
            raise ValueError("请先配置 Emby 地址和 API Key")
        snapshot = self.settings.emby.model_copy(deep=True)
        key = (snapshot.url, snapshot.api_key, tuple(snapshot.library_ids))
        async with self.emby_lock:
            if key not in self.clients:
                client = EmbyClient(snapshot)
                try:
                    await client.test()
                except BaseException:
                    await client.close()
                    raise
                self.clients[key] = [client, 0]
            entry = self.clients[key]
            entry[1] += 1
        try:
            yield entry[0]
        finally:
            async with self.emby_lock:
                entry[1] -= 1
                await self.retire_clients()

    async def retire_clients(self):
        # Active leases retain their snapshot until completion; new requests use new settings.
        current = self.settings.emby
        key = (current.url, current.api_key, tuple(current.library_ids))
        for old, (client, users) in list(self.clients.items()):
            if old != key and not users:
                del self.clients[old]
                await client.close()

    async def configure(self, settings):
        self.settings = settings
        self.crops.clear()
        async with self.emby_lock:
            await self.retire_clients()

    async def works(self):
        async with self.emby() as client:
            return [
                project(item, self.runtime, client.server)
                async for item in client.items()
            ]

    async def scoped_item(self, client, ident):
        if not await client.contains(ident):
            raise ValueError("项目不属于已选择的媒体库，请刷新列表")
        return await client.item(ident)

    async def detail(self, ident):
        async with self.emby() as client:
            item = await self.scoped_item(client, ident)
            result = project(item, self.runtime, client.server)
            related, seen = [], {str(item["Id"])}
            candidates = await client.parts(item)
            ids = [str(p["Id"]) for p in candidates]
            ids += [
                str(m["ItemId"])
                for m in item.get("MediaSources", [])
                if m.get("ItemId")
            ]
            for other in ids:
                if other in seen:
                    continue
                seen.add(other)
                if await client.contains(other):
                    related.append(
                        project(await client.item(other), self.runtime, client.server)
                    )
                else:
                    result.setdefault("related_errors", []).append(
                        "关联项目不属于已选择的媒体库或无法确认归属"
                    )
            result["related"] = related
            result["needs_enrichment"] = result[
                "status"
            ] != "unsupported" and needs_enrichment(
                item, await client.image_info(ident), identify(item)[2]
            )
            return result

    def clean_crops(self):
        for token, session in list(self.crops.items()):
            if session.expires < time.monotonic():
                del self.crops[token]

    def crop_session(self, ident, token):
        self.clean_crops()
        session = self.crops.get(token)
        if not session or session.item != ident:
            raise ValueError("裁剪会话已过期，请重新获取原始封面")
        return session

    async def crop_source(self, ident, source=None, *, fetch_source=False):
        self.clean_crops()
        if len(self.crops) + self.crop_pending >= 4:
            raise ValueError("裁剪会话过多，请关闭其他裁剪器或十分钟后重试")
        self.crop_pending += 1
        try:
            return await self._crop_source(ident, source, fetch_source=fetch_source)
        finally:
            self.crop_pending -= 1

    async def _crop_source(self, ident, source, *, fetch_source):
        async with self.emby() as client:
            item = await self.scoped_item(client, ident)
            code, identity, _ = identify(item)
            data = None
            if not fetch_source:
                for row in await client.image_info(ident):
                    if row["ImageType"] == "Backdrop" and row.get("Width", 0) > row.get(
                        "Height", 0
                    ):
                        try:
                            image = await client.image(
                                ident, "Backdrop", row.get("ImageIndex", 0)
                            )
                            data = await asyncio.to_thread(
                                crop_source_image, image.content
                            )
                            break
                        except httpx.HTTPError, ValueError, OSError:
                            continue
            if data is None:
                record = self.runtime.record(client.server, ident, identity, code)
                selected = source or (record["source"] if record else None)
                try:
                    async with (
                        asyncio.timeout(45),
                        Scraper(
                            self.settings.model_copy(deep=True), runtime=self.runtime
                        ) as scraper,
                    ):
                        data = await scraper.cover(code, selected)
                except (
                    ValueError,
                    OSError,
                    FetchError,
                    httpx.HTTPError,
                    TimeoutError,
                ) as exc:
                    raise ValueError(
                        "无法获取完整原图，请稍后重试或在 Emby 检查背景图"
                    ) from exc
                data = await asyncio.to_thread(crop_source_image, data)
            token = uuid.uuid4().hex
            self.crops[token] = CropSession(
                client.server, ident, identity, data, time.monotonic() + 600
            )
            asyncio.get_running_loop().call_later(600, self.crops.pop, token, None)
            try:
                preview = await self.crop_preview(ident, token, center=True)
            except BaseException:
                self.crops.pop(token, None)
                raise
            return {"token": token, **preview}

    async def crop_preview(self, ident, token, box=None, center=False):
        import base64

        session = self.crop_session(ident, token)
        crop = await asyncio.to_thread(crop_poster, session.data, box, center)
        return {k: crop[k] for k in ("box", "width", "height")} | {
            "preview": "data:image/png;base64,"
            + base64.b64encode(crop["preview"]).decode()
        }

    async def crop_apply(self, ident, token, box):
        session = self.crop_session(ident, token)
        async with self.item_lock(session.server, ident):
            async with self.emby() as client:
                item = await self.scoped_item(client, ident)
                _, identity, _ = identify(item)
                if client.server != session.server or identity != session.identity:
                    raise ValueError("Emby 项目已变化，请重新获取原图")
                data = (await asyncio.to_thread(crop_poster, session.data, box))[
                    "content"
                ]
                await client.upload(ident, "Primary", data)
                self.crops.pop(token, None)
        return await self.detail(ident)
