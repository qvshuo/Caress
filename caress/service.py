"""Application lifecycle, live Emby projections, and explicit crop sessions."""

import asyncio
import time
import uuid
from contextlib import asynccontextmanager
from dataclasses import dataclass
from io import BytesIO
from weakref import WeakValueDictionary

import httpx
from PIL import Image, ImageOps

from .emby import EmbyClient
from .http import FetchError
from .images import crop_poster
from .library import filter_works, identify, neighbors, project
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
        self.runtime.close()

    @asynccontextmanager
    async def emby(self):
        if not self.settings.emby.url or not self.settings.emby.api_key:
            raise ValueError("请先配置 Emby 地址和 API Key")
        client = EmbyClient(self.settings.emby.model_copy(deep=True))
        try:
            await client.test()
            yield client
        finally:
            await client.close()

    async def works(self):
        async with self.emby() as client:
            return [
                project(item, self.runtime, client.server)
                async for item in client.items()
            ]

    async def scoped_item(self, client, ident):
        # Validate scope using Emby's authoritative recursive library membership.
        found = False
        async for item in client.items():
            if str(item["Id"]) == ident:
                found = True
                break
        if not found:
            raise ValueError("项目不属于已选择的媒体库，请刷新列表")
        return await client.item(ident)

    async def detail(self, ident, query="", status=""):
        async with self.emby() as client:
            rows = [
                project(item, self.runtime, client.server)
                async for item in client.items()
            ]
            if not any(row["id"] == ident for row in rows):
                raise ValueError("项目不属于已选择的媒体库，请刷新列表")
            item = await client.item(ident)
            result = project(item, self.runtime, client.server)
            related = await client.parts(item)
            ids = {str(item["Id"]), *(str(p["Id"]) for p in related)}
            for source in item.get("MediaSources", []):
                other = source.get("ItemId")
                if other and str(other) not in ids:
                    related.append(await client.item(other))
                    ids.add(str(other))
            result["navigation"] = neighbors(filter_works(rows, query, status), ident)
            result["related"] = [
                project(p, self.runtime, client.server) for p in related
            ]
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
            origin = "Emby 背景图"
            if not fetch_source:
                for row in await client.image_info(ident):
                    if row["ImageType"] == "Backdrop" and row.get("Width", 0) > row.get(
                        "Height", 0
                    ):
                        data = await client.image(
                            ident, "Backdrop", row.get("ImageIndex", 0)
                        )
                        break
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
                origin = "来源原始封面"

            # Normalize EXIF orientation once so the browser and pixel coordinates agree.
            def normalized(content):
                with Image.open(BytesIO(content)) as image:
                    output = BytesIO()
                    ImageOps.exif_transpose(image).save(output, format="PNG")
                    return output.getvalue()

            data = await asyncio.to_thread(normalized, data)
            if len(data) > 32 * 1024 * 1024:
                raise ValueError("原始封面超过裁剪资源限制")
            token = uuid.uuid4().hex
            self.crops[token] = CropSession(
                client.server, ident, identity, data, time.monotonic() + 600
            )
            asyncio.get_running_loop().call_later(600, self.crops.pop, token, None)
            return {
                "token": token,
                "origin": origin,
                **await self.crop_preview(ident, token, center=True),
            }

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
