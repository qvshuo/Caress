"""Emby is the library. Every mutation starts with a full read and is verified."""

import asyncio
import base64
import hashlib
import io
from copy import deepcopy
from urllib.parse import quote

import httpx
from PIL import Image

from .images import validate_image

FIELDS = "Path,DateCreated,MediaSources,Overview,People,Studios,Genres,Tags,ProviderIds,OriginalTitle"


def sync_error(exc):
    if isinstance(exc, httpx.HTTPStatusError):
        return f"Emby 请求失败（HTTP {exc.response.status_code}）"
    if isinstance(exc, httpx.TimeoutException):
        return "Emby 请求超时"
    if isinstance(exc, httpx.HTTPError):
        return "无法连接 Emby，请检查地址、网络与 API Key"
    return str(exc)


def image_pixels_match(expected_bytes, actual_bytes):
    return pixel_digest(expected_bytes) == pixel_digest(actual_bytes)


def pixel_digest(data):
    if len(data) > 32 * 1024 * 1024:
        raise ValueError("artwork exceeds 32 MiB")
    with Image.open(io.BytesIO(data)) as image:
        if image.width * image.height > 40_000_000:
            raise ValueError("image exceeds 40 megapixels")
        digest = hashlib.sha256(str(image.size).encode())
        for y in range(0, image.height, 128):
            with image.crop((0, y, image.width, min(y + 128, image.height))) as strip:
                digest.update(strip.convert("RGBA").tobytes())
        return digest.digest()


def field_value(key, value):
    if key in ("People", "Studios", "GenreItems", "TagItems"):
        values = [
            (item["Name"], item.get("Type"), item.get("Role")) for item in value or []
        ]
        return values if key == "People" else sorted(values)
    if key == "PremiereDate":
        return value[:10] if value else None
    if key in ("OriginalTitle", "Overview"):
        return value or ""
    return value


def fields_match(actual, expected):
    return all(
        field_value(key, actual.get(key)) == field_value(key, value)
        for key, value in expected.items()
    )


def metadata_update(original, metadata, media):
    """Missing source values never clear current values; unrelated fields survive."""
    changes = {
        "ProviderIds": {**original.get("ProviderIds", {}), "caress": metadata.code}
    }
    for key, value in [
        ("Name", metadata.title),
        ("OriginalTitle", metadata.original_title),
        ("Overview", metadata.plot),
        ("ProductionYear", metadata.year),
    ]:
        if value:
            changes[key] = value
    if metadata.release_date:
        changes.update(
            PremiereDate=metadata.release_date.isoformat() + "T00:00:00Z",
            ProductionYear=metadata.year or metadata.release_date.year,
        )
    if metadata.studio:
        changes["Studios"] = [{"Name": metadata.studio}]
    if metadata.genres:
        changes["GenreItems"] = [
            {"Name": name} for name in dict.fromkeys(metadata.genres)
        ]
    people = deepcopy(original.get("People", []))
    for kind, names in [
        ("Actor", [a.name for a in metadata.actors]),
        ("Director", metadata.directors),
    ]:
        if names:
            old = {p["Name"]: p for p in people if p.get("Type") == kind}
            people = [p for p in people if p.get("Type") != kind]
            people += [
                old.get(name, {"Name": name, "Type": kind})
                for name in dict.fromkeys(names)
            ]
    if metadata.actors or metadata.directors:
        changes["People"] = people
    labels = (["中文字幕"] if any(m.subtitles for m in media) else []) + (
        ["无码破解"] if any(m.uncensored for m in media) else []
    )
    if metadata.tags or labels:
        tags = deepcopy(original.get("TagItems", []))
        existing = {t["Name"] for t in tags}
        tags += [
            {"Name": t}
            for t in dict.fromkeys([*metadata.tags, *labels])
            if t not in existing
        ]
        changes["TagItems"] = tags
    return changes


class EmbyClient:
    def __init__(self, settings, *, transport=None):
        self.settings = settings
        self.user_lock = asyncio.Lock()
        self.user = None
        self.server = None
        self.client = httpx.AsyncClient(
            base_url=settings.url.rstrip("/") + "/emby/",
            headers={"X-Emby-Token": settings.api_key},
            timeout=30,
            trust_env=False,
            follow_redirects=False,
            transport=transport,
        )

    async def request(self, method, path, **kwargs):
        response = await self.client.request(method, path, **kwargs)
        response.raise_for_status()
        return response.json() if response.content else None

    async def close(self):
        await self.client.aclose()

    async def test(self):
        info = await self.request("GET", "System/Info")
        if not info.get("Id"):
            raise ValueError("Emby 没有返回服务器身份")
        self.server = info["Id"]
        return {
            "valid": True,
            "server": info["ServerName"],
            "version": info["Version"],
            "id": self.server,
        }

    async def libraries(self):
        return await self.request("GET", "Library/VirtualFolders")

    async def items(self):
        seen = set()
        for library in self.settings.library_ids:
            start = 0
            while True:
                data = await self.request(
                    "GET",
                    "Items",
                    params={
                        "ParentId": library,
                        "Recursive": "true",
                        "IncludeItemTypes": "Movie",
                        "Fields": FIELDS,
                        "StartIndex": start,
                        "Limit": 200,
                        "SortBy": "SortName",
                        "SortOrder": "Ascending",
                    },
                )
                for item in data["Items"]:
                    if str(item["Id"]) not in seen:
                        seen.add(str(item["Id"]))
                        yield item
                start += len(data["Items"])
                if not data["Items"] or start >= data["TotalRecordCount"]:
                    break

    async def contains(self, ident):
        for library in self.settings.library_ids:
            data = await self.request(
                "GET",
                "Items",
                params={
                    "ParentId": library,
                    "Recursive": "true",
                    "Ids": str(ident),
                    "IncludeItemTypes": "Movie",
                    "Limit": 1,
                    "EnableImages": "false",
                    "EnableUserData": "false",
                },
            )
            if any(str(row["Id"]) == str(ident) for row in data["Items"]):
                return True
        return False

    async def contains_person(self, ident):
        for library in self.settings.library_ids:
            data = await self.request(
                "GET",
                "Items",
                params={
                    "ParentId": library,
                    "Recursive": "true",
                    "PersonIds": str(ident),
                    "IncludeItemTypes": "Movie",
                    "Fields": "People",
                    "Limit": 1,
                    "EnableImages": "false",
                    "EnableUserData": "false",
                },
            )
            if any(
                str(person.get("Id")) == str(ident)
                for item in data["Items"]
                for person in item.get("People", [])
            ):
                return True
        return False

    async def item(self, ident):
        async with self.user_lock:
            if self.user is None:
                users = await self.request("GET", "Users")
                admin = next(
                    (u for u in users if u.get("Policy", {}).get("IsAdministrator")),
                    None,
                )
                if admin is None:
                    raise ValueError("Emby API Key 无管理员用户")
                self.user = admin["Id"]
        item = await self.request(
            "GET", f"Users/{self.user}/Items/{quote(str(ident), safe='')}"
        )
        if self.server and item.get("ServerId") != self.server:
            raise ValueError("Emby 服务器身份变化，请重新连接")
        return item

    async def parts(self, item):
        if item.get("PartCount", 1) <= 1:
            return []
        data = await self.request("GET", f"Videos/{item['Id']}/AdditionalParts")
        return data["Items"]

    async def image_info(self, ident):
        return await self.request("GET", f"Items/{ident}/Images")

    async def image(self, ident, kind, index=0, *, tag=None):
        params = {"Format": "original", "EnableImageEnhancers": "false"}
        if tag:
            params["Tag"] = tag
        # No redirects: never forward the API key to an external image URL.
        async with self.client.stream(
            "GET",
            f"Items/{ident}/Images/{kind}/{index}",
            params=params,
            headers={"Cache-Control": "no-cache"},
        ) as response:
            response.raise_for_status()
            data = bytearray()
            async for chunk in response.aiter_bytes():
                data.extend(chunk)
                if len(data) > 32 * 1024 * 1024:
                    raise ValueError("Emby 图片超过 32 MiB")
        return await asyncio.to_thread(validate_image, bytes(data))

    async def upload(self, ident, kind, data, index=None):
        info = await asyncio.to_thread(validate_image, data)
        mime = info.mime
        path = f"Items/{ident}/Images/{kind}" + (
            f"/{index}" if index is not None else ""
        )
        await self.request(
            "POST", path, content=base64.b64encode(data), headers={"Content-Type": mime}
        )
        saved = await self.image_info(ident)
        if index is None and kind == "Backdrop":
            index = max(
                (row.get("ImageIndex", 0) for row in saved if row["ImageType"] == kind),
                default=-1,
            )
        index = index or 0
        if not any(
            row["ImageType"] == kind and row.get("ImageIndex", 0) == index
            for row in saved
        ):
            raise ValueError("Emby 图片回读验证失败")
        if not await asyncio.to_thread(
            image_pixels_match, data, (await self.image(ident, kind, index)).content
        ):
            raise ValueError("Emby 图片内容回读不一致")

    async def write_metadata(self, ident, metadata, media):
        original = await self.item(ident)
        changes = metadata_update(original, metadata, media)
        # Full DTO preserves system/user fields and the existing lock state.
        if not fields_match(original, changes):
            await self.request("POST", f"Items/{ident}", json={**original, **changes})
        current = await self.item(ident)
        if not fields_match(current, changes) or any(
            current.get(k) != original.get(k) for k in ("LockData", "LockedFields")
        ):
            raise ValueError("Emby 元数据或锁定状态回读不一致；可能已部分写入，请重试")
        return current

    async def backdrop_signatures(self, ident):
        signatures, decoded = set(), {}
        for row in await self.image_info(ident):
            if row["ImageType"] == "Backdrop":
                data = (
                    await self.image(ident, "Backdrop", row.get("ImageIndex", 0))
                ).content
                raw = hashlib.sha256(data).digest()
                if raw not in decoded:
                    decoded[raw] = await asyncio.to_thread(pixel_digest, data)
                signatures.add(decoded[raw])
        return signatures

    async def append_backdrop(self, ident, data, signatures, digest=None):
        digest = digest or await asyncio.to_thread(pixel_digest, data)
        if digest not in signatures:
            await self.upload(ident, "Backdrop", data)
            signatures.add(digest)
