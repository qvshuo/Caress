from __future__ import annotations

import asyncio
import json
import re
import time
from pathlib import Path
from urllib.parse import quote, urlsplit

import httpx

from .actors import comparison_name
from .http import NETWORK_RETRIES, REQUEST_TIMEOUT_SECONDS, RETRY_DELAY_SECONDS
from .images import validate_image
from .logging import report_progress
from .storage import atomic_write, file_transaction


def actor_image(output: Path, name: str) -> Path | None:
    folder = output.resolve() / "actors"
    if (
        not name
        or name in {".", ".."}
        or Path(name).name != name
        or any(ord(c) < 32 or 127 <= ord(c) <= 159 for c in name)
    ):
        raise ValueError("invalid actor name")
    if folder.is_symlink():
        raise ValueError("actor image directory must not be a symlink")
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

    def __init__(self, names, output: Path, client=None):
        self.names, self.output = names, output
        self.client = client or httpx.AsyncClient(
            timeout=REQUEST_TIMEOUT_SECONDS, follow_redirects=True, trust_env=False
        )
        self.owns_client = client is None
        self.index = None
        self.index_error = None
        self.last_request = 0.0
        self.completed = {}
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
                            raise ValueError("gfriends response exceeds size limit")
                    return bytes(data)
            except httpx.HTTPStatusError as exc:
                if exc.response.status_code < 500 and exc.response.status_code != 429:
                    raise ValueError(
                        f"gfriends HTTP {exc.response.status_code}"
                    ) from None
            except httpx.RequestError:
                pass
            if attempt < NETWORK_RETRIES:
                await asyncio.sleep(RETRY_DELAY_SECONDS)
        raise ValueError("gfriends request failed")

    def select(self, name: str, kind: int | None = None) -> str | None:
        if len(self.names.candidates(name, kind)) > 1:
            raise ValueError("演员别名对应多人")
        primary = self.names.resolve(name, kind) or name
        candidates = [primary, *self.names.aliases_for(name, kind)]
        for candidate in dict.fromkeys(candidates):
            # Content is ordered low-to-high quality; the last bank wins.
            for bank, files in reversed(list(self.index.items())):
                if not isinstance(files, dict):
                    continue
                value = files.get(candidate + ".jpg")
                if not isinstance(value, str):
                    continue
                parsed = urlsplit(value)
                if (
                    parsed.scheme
                    or parsed.netloc
                    or Path(parsed.path).name != parsed.path
                    or Path(bank).name != bank
                    or bank in {".", ".."}
                ):
                    continue
                canonical = Path(parsed.path).stem.removeprefix("AI-Fix-")
                canonical = re.sub(r"-\d+$", "", canonical)
                if (
                    comparison_name(canonical) != comparison_name(primary)
                    and self.names.resolve(canonical, kind) != primary
                ):
                    continue
                return (
                    self.base
                    + "Content/"
                    + quote(bank, safe="")
                    + "/"
                    + quote(parsed.path, safe="")
                    + ("?" + parsed.query if parsed.query else "")
                )
        return None

    async def save(
        self, actors, replace: bool = False, *, kind: int | None = None
    ) -> list[dict]:
        async with self.save_lock:
            return await self._save(actors, replace, kind)

    async def _save(self, actors, replace: bool, kind: int | None) -> list[dict]:
        records = []
        for actor in actors:
            name = actor.name
            if (name, replace, kind) in self.completed:
                records.append(self.completed[(name, replace, kind)])
                continue
            try:
                existing = actor_image(self.output, name)
                if existing and not replace:
                    records.append({"name": name, "status": "existing"})
                    self.completed[(name, replace, kind)] = records[-1]
                    continue
                if len(self.names.candidates(name, kind)) > 1:
                    raise ValueError("演员别名对应多人")
                report_progress("portraits", source="gfriends")
                if self.index is None and self.index_error is None:
                    try:
                        tree = json.loads(
                            await self.document(
                                self.base + "Filetree.json", 16 * 1024 * 1024
                            )
                        )
                        if not isinstance(tree, dict):
                            raise ValueError("invalid gfriends file tree")
                        self.index = tree["Content"]
                        if not isinstance(self.index, dict):
                            raise ValueError("invalid gfriends file tree")
                    except (ValueError, KeyError) as exc:
                        self.index_error = str(exc)
                if self.index_error:
                    raise ValueError(self.index_error)
                url = self.select(name, kind)
                if not url:
                    records.append({"name": name, "status": "not_found"})
                    self.completed[(name, replace, kind)] = records[-1]
                    report_progress(
                        "portraits", "not_found", source="gfriends", message=name
                    )
                    continue
                image = await asyncio.to_thread(
                    validate_image, await self.document(url, 32 * 1024 * 1024)
                )
                extension = ".jpg" if image.format == "jpeg" else "." + image.format
                target = self.output / "actors" / (name + extension)
                with file_transaction(self.output / "actors"):
                    atomic_write(target, image.content)
                    if existing and existing != target:
                        existing.unlink()
                records.append({"name": name, "status": "saved"})
            except (ValueError, OSError) as exc:
                records.append({"name": name, "status": "error", "error": str(exc)})
            if records[-1]["status"] != "error":
                self.completed[(name, replace, kind)] = records[-1]
            report_progress(
                "portraits", records[-1]["status"], source="gfriends", message=name
            )
        return records
