from __future__ import annotations

import asyncio
import json
import logging
import re
import time
import unicodedata
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from urllib.parse import quote, unquote, urlsplit

import httpx

from .http import NETWORK_RETRIES, REQUEST_TIMEOUT_SECONDS, RETRY_DELAY_SECONDS
from .images import validate_image
from .logging import report_progress
from .storage import atomic_write


def normalize_name(name: str) -> str:
    return re.sub(r"\s+", " ", unicodedata.normalize("NFKC", name).strip()).casefold()


@dataclass(frozen=True)
class PortraitMatch:
    name: str
    url: str


def actor_image(output: Path, name: str) -> Path | None:
    folder = output.resolve() / "actors"
    if (
        not name
        or name in {".", ".."}
        or Path(name).name != name
        or any(ord(c) < 32 or 127 <= ord(c) <= 159 for c in name)
    ):
        raise ValueError("演员名称无效")
    if folder.is_symlink():
        raise ValueError("演员头像目录不能是符号链接")
    for suffix in (".jpg", ".jpeg", ".png", ".webp", ".gif"):
        image = folder / (name + suffix)
        if image.is_file() and not image.is_symlink():
            return image
    return next(
        (
            p
            for p in folder.glob("*")
            if p.stem == name
            and p.suffix.lower() in {".jpg", ".jpeg", ".png", ".webp", ".gif"}
            and p.is_file()
            and not p.is_symlink()
        ),
        None,
    )


class Gfriends:
    """One transient file tree per task; portraits remain ordinary metadata files."""

    base = "https://raw.githubusercontent.com/gfriends/gfriends/master/"

    def __init__(self, output: Path, client=None):
        self.output = output
        self.client = client or httpx.AsyncClient(
            timeout=REQUEST_TIMEOUT_SECONDS, follow_redirects=True, trust_env=False
        )
        self.owns_client = client is None
        self.index = None
        self.index_error = None
        self.last_request = 0.0
        self.save_lock = asyncio.Lock()

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        if self.owns_client:
            await self.client.aclose()

    async def document(self, url: str, limit: int) -> bytes:
        for attempt in range(NETWORK_RETRIES + 1):
            await asyncio.sleep(max(0, 1 - (time.monotonic() - self.last_request)))
            self.last_request = time.monotonic()
            try:
                async with self.client.stream("GET", url) as response:
                    response.raise_for_status()
                    data = bytearray()
                    async for chunk in response.aiter_bytes():
                        data.extend(chunk)
                        if len(data) > limit:
                            raise ValueError("头像文件超过大小限制")
                    return bytes(data)
            except httpx.HTTPStatusError as exc:
                if exc.response.status_code < 500 and exc.response.status_code != 429:
                    raise ValueError(
                        f"Gfriends 请求失败（HTTP {exc.response.status_code}）"
                    ) from None
            except httpx.RequestError:
                pass
            if attempt < NETWORK_RETRIES:
                await asyncio.sleep(RETRY_DELAY_SECONDS)
        raise ValueError("头像来源请求失败")

    def _build_index(self, content):
        result = {}
        # Filetree banks are ordered low-to-high priority; the last bank wins.
        for bank, files in reversed(list(content.items())):
            if (
                not isinstance(files, dict)
                or PurePosixPath(bank).name != bank
                or bank in {".", ".."}
            ):
                continue
            for alias, value in files.items():
                if (
                    not isinstance(alias, str)
                    or not alias.endswith(".jpg")
                    or not isinstance(value, str)
                ):
                    continue
                parsed = urlsplit(value)
                path = unquote(parsed.path)
                if (
                    parsed.scheme
                    or parsed.netloc
                    or not path
                    or PurePosixPath(path).name != path
                ):
                    continue
                canonical = re.sub(
                    r"-\d+$", "", PurePosixPath(path).stem.removeprefix("AI-Fix-")
                )
                key = normalize_name(PurePosixPath(alias).stem)
                if key and canonical:
                    result.setdefault(
                        key,
                        PortraitMatch(
                            canonical,
                            self.base
                            + "Content/"
                            + quote(bank, safe="")
                            + "/"
                            + quote(path, safe="")
                            + ("?" + parsed.query if parsed.query else ""),
                        ),
                    )
        return result

    async def load_index(self):
        async with self.save_lock:
            if self.index is not None or self.index_error is not None:
                return
            try:
                tree = json.loads(
                    await self.document(self.base + "Filetree.json", 16 * 1024 * 1024)
                )
                if not isinstance(tree, dict) or not isinstance(
                    tree.get("Content"), dict
                ):
                    raise ValueError("Gfriends 索引无效")
                self.index = self._build_index(tree["Content"])
            except (ValueError, KeyError, TypeError) as exc:
                self.index_error = str(exc)
                report_progress(
                    "portraits",
                    "error",
                    source="gfriends",
                    message="Portrait index unavailable",
                    level=logging.WARNING,
                )

    async def resolve(self, name: str) -> PortraitMatch | None:
        await self.load_index()
        return self.index.get(normalize_name(name)) if self.index is not None else None

    async def save(self, match: PortraitMatch) -> dict:
        async with self.save_lock:
            name = match.name
            try:
                if actor_image(self.output, name):
                    result = {"name": name, "status": "existing"}
                else:
                    image = await asyncio.to_thread(
                        validate_image, await self.document(match.url, 32 * 1024 * 1024)
                    )
                    extension = ".jpg" if image.format == "jpeg" else "." + image.format
                    atomic_write(
                        self.output / "actors" / (name + extension), image.content
                    )
                    result = {"name": name, "status": "saved"}
            except (ValueError, OSError) as exc:
                result = {"name": name, "status": "error", "error": str(exc)}
            if result["status"] == "error":
                report_progress(
                    "portraits",
                    "error",
                    source="gfriends",
                    message="Portrait preparation failed",
                    level=logging.WARNING,
                )
            return result
