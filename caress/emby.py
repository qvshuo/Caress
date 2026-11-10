"""Emby is the library. Every mutation starts with a full read and is verified."""

import asyncio
import base64
import hashlib
import io
import logging
from copy import deepcopy
from datetime import datetime
from urllib.parse import quote

import httpx
from PIL import Image

from .images import validate_image

FIELDS = "Path,DateCreated,MediaSources,Overview,People,Studios,Genres,Tags,ProviderIds,OriginalTitle"


def sync_error(exc):
    if isinstance(exc, httpx.HTTPStatusError):
        operation = f"{exc.request.method} {exc.request.url.path}"
        if exc.response.is_stream_consumed:
            body = exc.response.text
            key = exc.request.headers.get("X-Emby-Token")
            if key:
                body = body.replace(key, "[redacted]")
            logging.getLogger("caress.emby").error(
                "Emby %s HTTP %s，响应：%s", operation, exc.response.status_code, body
            )
        return f"Emby 请求失败（{operation}，HTTP {exc.response.status_code}）"
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


def has_content(key, value):
    if key == "ProductionYear":
        return (
            isinstance(value, int) and not isinstance(value, bool) and 1 < value <= 9999
        )
    if key == "PremiereDate":
        try:
            return isinstance(value, str) and datetime.fromisoformat(value).year > 1
        except ValueError:
            return False
    if key in ("Studios", "GenreItems", "TagItems"):
        return any(has_content("Name", row.get("Name")) for row in value or [])
    return isinstance(value, str) and bool(value.strip())


def filename_tags(media):
    return (["中文字幕"] if any(m.subtitles for m in media) else []) + (
        ["无码破解"] if any(m.uncensored for m in media) else []
    )


def needs_enrichment(item, images, media):
    for key in (
        "Name",
        "OriginalTitle",
        "Overview",
        "PremiereDate",
        "ProductionYear",
        "Studios",
        "GenreItems",
        "TagItems",
    ):
        if not has_content(key, item.get(key)):
            return True
    people = item.get("People") or []
    if any(
        not any(
            p.get("Type") == kind and has_content("Name", p.get("Name")) for p in people
        )
        for kind in ("Actor", "Director")
    ):
        return True
    if any(
        p.get("Type") == "Actor" and p.get("Id") and not p.get("PrimaryImageTag")
        for p in people
    ):
        return True
    if not has_content("Name", (item.get("ProviderIds") or {}).get("caress")):
        return True
    if not set(filename_tags(media)).issubset(
        {t["Name"] for t in item.get("TagItems") or []}
    ):
        return True
    return not {"Primary", "Backdrop"}.issubset({row["ImageType"] for row in images})


def metadata_update(original, metadata, media, *, replace):
    """Fill missing fields or replace supported fields; absent source values never clear data."""
    changes = {}

    def put(key, value):
        if has_content(key, value) and (
            replace or not has_content(key, original.get(key))
        ):
            changes[key] = value

    for key, value in [
        ("Name", metadata.title),
        ("OriginalTitle", metadata.original_title),
        ("Overview", metadata.plot),
    ]:
        put(key, value)
    put(
        "ProductionYear",
        metadata.year
        or (metadata.release_date.year if metadata.release_date else None),
    )
    if metadata.release_date and metadata.release_date.year > 1:
        put("PremiereDate", metadata.release_date.isoformat() + "T00:00:00Z")
    put("Studios", [{"Name": metadata.studio}] if metadata.studio else [])
    put(
        "GenreItems",
        [
            {"Name": name}
            for name in dict.fromkeys(metadata.genres)
            if has_content("Name", name)
        ],
    )
    people = deepcopy(original.get("People") or [])
    for kind, names in [
        ("Actor", [a.name for a in metadata.actors]),
        ("Director", metadata.directors),
    ]:
        names = [name for name in dict.fromkeys(names) if has_content("Name", name)]
        old = {
            p["Name"]: p
            for p in people
            if p.get("Type") == kind and has_content("Name", p.get("Name"))
        }
        current_names = [
            p["Name"]
            for p in people
            if p.get("Type") == kind and has_content("Name", p.get("Name"))
        ]
        if names and (replace or not old) and names != current_names:
            people = [p for p in people if p.get("Type") != kind]
            people += [old.get(name, {"Name": name, "Type": kind}) for name in names]
    if people != (original.get("People") or []):
        changes["People"] = people
    tags = deepcopy(original.get("TagItems") or [])
    existing = {t["Name"] for t in tags}
    names = metadata.tags if replace or not has_content("TagItems", tags) else []
    tags += [
        {"Name": name}
        for name in dict.fromkeys([*names, *filename_tags(media)])
        if has_content("Name", name) and name not in existing
    ]
    if tags != (original.get("TagItems") or []):
        changes["TagItems"] = tags
    provider_ids = dict(original.get("ProviderIds") or {})
    if replace or not has_content("Name", provider_ids.get("caress")):
        provider_ids["caress"] = metadata.code
        changes["ProviderIds"] = provider_ids
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
                    "IncludeItemTypes": "Movie,Video",
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

    async def write_metadata(self, ident, metadata, media, *, replace):
        original = await self.item(ident)
        changes = metadata_update(original, metadata, media, replace=replace)
        # Full DTO preserves system/user fields and the existing lock state.
        changed = not fields_match(original, changes)
        if changed:
            await self.request("POST", f"Items/{ident}", json={**original, **changes})
        current = await self.item(ident)
        if not fields_match(current, changes) or (
            changed
            and any(
                current.get(k) != original.get(k) for k in ("LockData", "LockedFields")
            )
        ):
            raise ValueError("Emby 元数据或锁定状态回读不一致；可能已部分写入，请重试")
        return current, changed

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
            return True
        return False
