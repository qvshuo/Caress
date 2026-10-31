from __future__ import annotations

import asyncio
import hashlib
import json
import re
import time
from dataclasses import dataclass
from io import BytesIO
from pathlib import Path
from urllib.parse import quote, urlsplit, unquote

import httpx
from PIL import Image, UnidentifiedImageError

from .actors import comparison_name
from .http import FetchError, WebClient
from .logging import report_progress
from .models import Artwork, SourceId
from .storage import atomic_write, file_transaction


def validate_resources() -> None:
    from .actors import ActorAliases
    from .catalog import CatalogRules
    import cv2

    CatalogRules.load()
    ActorAliases.load()
    data = Path(__file__).parent / "data"
    if not (data / "yunet.LICENSE").read_text(encoding="utf-8").strip():
        raise ValueError("YuNet license is missing")
    try:
        cv2.FaceDetectorYN.create(str(data / "yunet.onnx"), "", (320, 320))
    except cv2.error as exc:
        raise ValueError("YuNet model cannot be loaded") from exc


@dataclass(frozen=True)
class ImageData:
    content: bytes
    format: str
    width: int
    height: int
    decoded: bool


def image_signature(data: bytes) -> bool:
    return data.startswith((b"\xff\xd8\xff", b"\x89PNG\r\n\x1a\n", b"GIF87a", b"GIF89a")) or (
        data.startswith(b"RIFF") and data[8:12] == b"WEBP"
    )


def placeholder_url(source, url):
    if source != SourceId.DMM:
        return False
    path = unquote(urlsplit(url).path).lower()
    return any(marker in path for marker in ("now_printing", "nowprinting", "noimage", "no_image"))


def validate_image(data: bytes, source: SourceId | None = None) -> ImageData:
    """Validate real image bytes; decode JavDB App's key-prefixed XOR envelope."""
    if len(data) > 32 * 1024 * 1024:
        raise ValueError("artwork exceeds 32 MiB")
    decoded = False
    if not image_signature(data) and source == SourceId.JAVDB and len(data) > 16:
        key = data[0]
        if image_signature(bytes(value ^ key for value in data[1:17])):
            data = bytes(value ^ key for value in data[1:])
            decoded = True
    if not image_signature(data):
        raise ValueError("artwork response is not a supported image")
    try:
        with Image.open(BytesIO(data)) as image:
            width, height = image.size
            image_format = image.format
            image.verify()
        # Header verification alone can accept JPEGs whose pixel stream is corrupt.
        with Image.open(BytesIO(data)) as image:
            image.load()
        if not image_format or width < 1 or height < 1:
            raise ValueError("invalid image dimensions")
        return ImageData(data, image_format.lower(), width, height, decoded)
    except (UnidentifiedImageError, OSError, SyntaxError, Image.DecompressionBombError) as exc:
        raise ValueError("artwork is corrupt") from exc


async def save_images(client, items, target, stems, missing_only=False, previous_files=(), protected_roles=()):
    from tempfile import TemporaryDirectory
    if not items:
        return []
    if missing_only:
        return await _save_images(client, items, target, stems, missing_only=True, protected_roles=protected_roles)
    # Stage the complete selection before replacing any existing image.
    with TemporaryDirectory(prefix="erosia-images-") as temporary:
        staged = Path(temporary)
        records = await _save_images(client, items, staged, stems, protected_roles=protected_roles)
        if any(record["status"] == "error" for record in records):
            return [record for record in records if record["status"] == "error"]
        written = {record["file"] for record in records}
        try:
            with file_transaction(target):
                for name in sorted(written):
                    atomic_write(target / name, (staged / name).read_bytes())
                for name in previous_files:
                    old = target / name
                    if name not in written and Path(name).name == name and old.is_file() and not old.is_symlink():
                        old.unlink()
        except (ValueError, OSError) as exc:
            return [{"file": "images", "status": "error", "message": str(exc)}]
        return records


async def _save_images(
    client: WebClient,
    items: list[Artwork],
    target: Path,
    stems: list[str],
    missing_only: bool = False,
    protected_roles=(),
) -> list[dict]:
    """Write stem-named images; crop posters and preserve other originals."""
    covers = [item for item in items if item.role == "cover"]
    posters = [item for item in items if item.role == "poster"]
    plan = []
    if posters or covers:
        plan.append((covers[0] if covers else posters[0], "poster"))
    if covers or posters:
        plan.append((covers[0] if covers else posters[0], "fanart"))
    samples = list({(item.source, item.url): item for item in items if item.role == "sample"}.values())
    plan.extend((item, f"fanart{i + 1}") for i, item in enumerate(samples[:100]))
    records, downloaded, download_errors = [], {}, {}

    def existing(base):
        return (
            [
                path
                for path in base.parent.iterdir()
                if path.stem == base.name
                and path.suffix.lower() in {".jpg", ".jpeg", ".png", ".gif", ".webp"}
                and path.is_file()
                and not path.is_symlink()
            ]
            if base.parent.is_dir()
            else []
        )

    for item, role in plan:
        if role in protected_roles:
            continue
        paths = [target / f"{stem}-{role}" for stem in stems]
        if missing_only and all(existing(path) for path in paths):
            continue
        try:
            if placeholder_url(item.source, item.url):
                raise ValueError("来源返回占位图")
            identity = (item.source, item.url)
            if identity in download_errors:
                raise ValueError(download_errors[identity])
            if identity not in downloaded:
                report_progress("images", source=item.source, message=f"下载 {role}")
                try:
                    downloaded[identity] = await asyncio.to_thread(validate_image,
                        await client.image_bytes(item.source, item.url, item.referer),
                        item.source,
                    )
                except (FetchError, ValueError, OSError) as exc:
                    download_errors[identity] = str(exc)
                    raise
            image = downloaded[identity]
            if role == "poster":
                report_progress("crop")
                cropped = await asyncio.to_thread(crop_poster, image.content)
                image = await asyncio.to_thread(validate_image, cropped["content"], item.source)
            extension = ".jpg" if image.format == "jpeg" else "." + image.format
            for base in paths:
                if missing_only and existing(base):
                    continue
                # Append an extension: dotted video names must retain their full stem.
                path = base.parent / (base.name + extension)
                if not missing_only or not path.is_file():
                    atomic_write(path, image.content)
                    for old in existing(base):
                        if old != path and old.is_file() and not old.is_symlink():
                            old.unlink()
                records.append({"file": path.name, "status": "saved"})
        except (FetchError, ValueError, OSError) as exc:
            records.append({"file": role, "status": "error", "message": str(exc)})
    return records


def crop_box(width: int, height: int, faces: list[list[float]]) -> tuple[int, int, int, int]:
    """Keep the largest 2:3 window; protect the main face with hair/chin margins."""
    unit = min(width // 2, height // 3)
    if unit < 1:
        raise ValueError("image is too small for a portrait cover")
    w, h = unit * 2, unit * 3
    x, y = (width - w) / 2, (height - h) / 2
    if faces:

        def score(face):
            fx, _, fw, fh, confidence = face
            # Landscape covers usually place the main portrait on the right.
            weight = 2.75 if width > height and fx + fw / 2 >= width / 2 else 1
            return fw * fh * confidence**2 * weight

        fx, fy, fw, fh, _ = max(faces, key=score)
        x, y = fx + fw / 2 - w / 2, fy - h * 0.15
        for start, end, size, total, axis in (
            (max(0, fx - fw * 0.25), min(width, fx + fw * 1.25), w, width, "x"),
            (max(0, fy - fh * 0.35), min(height, fy + fh * 1.2), h, height, "y"),
        ):
            low, high = max(0, end - size), min(start, total - size)
            if low <= high:
                if axis == "x":
                    x = min(max(x, low), high)
                else:
                    y = min(max(y, low), high)
    return round(min(max(x, 0), width - w)), round(min(max(y, 0), height - h)), w, h


def crop_poster(data: bytes, box: tuple[int, int, int, int] | None = None, center: bool = False) -> dict:
    import cv2
    import numpy as np
    from PIL import ImageOps

    if len(data) > 32 * 1024 * 1024:
        raise ValueError("artwork exceeds 32 MiB")
    with Image.open(BytesIO(data)) as source:
        image = ImageOps.exif_transpose(source).copy()
    if image.mode == "CMYK":
        image = image.convert("RGB")
    width, height = image.size
    faces = []
    if box is None and not center:
        scale = min(1, 960 / max(width, height))
        frame = cv2.cvtColor(np.asarray(image.convert("RGB")), cv2.COLOR_RGB2BGR)
        frame = cv2.resize(frame, (max(32, round(width * scale)), max(32, round(height * scale))))
        try:
            detector = cv2.FaceDetectorYN.create(
                str(Path(__file__).parent / "data/yunet.onnx"),
                "",
                frame.shape[1::-1],
                0.75,
                0.3,
                5000,
            )
            _, detected = detector.detect(frame)
        except cv2.error as exc:
            raise ValueError("face detection failed; check the YuNet model") from exc
        if detected is not None:
            sx, sy = width / frame.shape[1], height / frame.shape[0]
            faces = [
                [
                    float(f[0] * sx),
                    float(f[1] * sy),
                    float(f[2] * sx),
                    float(f[3] * sy),
                    float(f[14]),
                ]
                for f in detected
                if f[2] > 0 and f[3] > 0
            ]
    box = box or crop_box(width, height, faces)
    x, y, w, h = box
    if min(x, y) < 0 or min(w, h) < 1 or x + w > width or y + h > height or w * 3 != h * 2:
        raise ValueError("crop must be an in-bounds 2:3 rectangle")
    cropped = image.crop((x, y, x + w, y + h))
    output = BytesIO()
    # Preserve the selected source pixels; JPEG/WebP encoding would add loss.
    cropped.save(output, format="PNG")
    preview = BytesIO()
    cropped.thumbnail((640, 960))
    cropped.save(preview, format="PNG")
    return {
        "content": output.getvalue(),
        "preview": preview.getvalue(),
        "box": box,
        "faces": faces,
        "width": width,
        "height": height,
        "extension": ".png",
    }


def actor_image_index(output: Path) -> dict[str, Path]:
    folder = output.resolve() / "actors"
    index = {}
    if folder.is_dir() and not folder.is_symlink():
        for image in folder.iterdir():
            if image.suffix.lower() in {".jpg", ".jpeg", ".png", ".webp", ".gif"} and image.is_file() and not image.is_symlink():
                index.setdefault(image.stem, image)
    return index


def actor_image(output: Path, name: str, *, index=None) -> Path | None:
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
    if index is not None:
        return index.get(name)
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

    def __init__(self, settings, names, output: Path, client=None):
        self.settings, self.names, self.output = settings, names, output
        self.client = client or httpx.AsyncClient(
            timeout=settings.timeout_seconds, follow_redirects=True, trust_env=False
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
        for attempt in range(self.settings.retries + 1):
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
                    raise ValueError(f"gfriends HTTP {exc.response.status_code}") from None
            except httpx.RequestError:
                pass
            if attempt < self.settings.retries:
                await asyncio.sleep(self.settings.retry_delay_seconds)
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

    async def save(self, actors, replace: bool = False, *, kind: int | None = None) -> list[dict]:
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
                        tree = json.loads(await self.document(self.base + "Filetree.json", 16 * 1024 * 1024))
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
                    report_progress("portraits", "not_found", source="gfriends", message=name)
                    continue
                image = await asyncio.to_thread(validate_image, await self.document(url, 32 * 1024 * 1024))
                extension = ".jpg" if image.format == "jpeg" else "." + image.format
                target = self.output / "actors" / (name + extension)
                atomic_write(target, image.content)
                if existing and existing != target:
                    existing.unlink()
                records.append({"name": name, "status": "saved"})
            except (ValueError, OSError) as exc:
                records.append({"name": name, "status": "error", "error": str(exc)})
            self.completed[(name, replace, kind)] = records[-1]
            report_progress("portraits", records[-1]["status"], source="gfriends", message=name)
        return records


async def selected_image(client, choice, variants, target, stems):
    """Resolve saved artwork or a source candidate without writing any files."""
    file = choice.get("file")
    if file is not None:
        path = target / file if isinstance(file, str) else target
        if (not isinstance(file, str) or Path(file).name != file or choice.get("source") != "custom"
            or choice.get("index") is not None or not path.is_file() or path.is_symlink()
            or not any(re.fullmatch(re.escape(stem) + r"-(?:poster|fanart(?:[1-9][0-9]{0,2})?)", path.stem) for stem in stems)):
            raise ValueError("图片文件不可用")
        if path.stat().st_size > 32 * 1024 * 1024:
            raise ValueError("artwork exceeds 32 MiB")
        image = await asyncio.to_thread(lambda: validate_image(path.read_bytes()))
    else:
        source = choice.get("source")
        variant = variants.get(source)
        if not variant or variant.get("status") != "found":
            raise ValueError("图片来源不可用")
        items = (variant.get("metadata") or {}).get("artwork", [])
        index = choice.get("index")
        if not isinstance(index, int) or isinstance(index, bool) or not 0 <= index < len(items):
            raise ValueError("图片候选不可用")
        item = items[index]
        if placeholder_url(source, item["url"]):
            raise ValueError("来源返回占位图")
        image = await asyncio.to_thread(validate_image, await client.image_bytes(SourceId(source), item["url"], item["referer"]), SourceId(source))
    if choice.get("fingerprint") and hashlib.sha256(image.content).hexdigest() != choice["fingerprint"]:
        raise ValueError("source image changed; preview again")
    return image


async def save_image_choices(client, choices, variants, target, stems, *, replace_samples=False):
    """Validate all chosen images before replacing any current image."""
    staged, downloaded = [], {}
    for role, choice in choices.items():
        if not re.fullmatch(r"poster|fanart(?:[1-9][0-9]{0,2})?", role):
            raise ValueError("invalid artwork role")
        identity = (choice.get("file"), choice.get("source"), choice.get("index"))
        if identity not in downloaded:
            downloaded[identity] = await selected_image(client, choice, variants, target, stems)
        image = downloaded[identity]
        if choice.get("fingerprint") and hashlib.sha256(image.content).hexdigest() != choice["fingerprint"]:
            raise ValueError("source image changed; preview again")
        box = choice.get("box")
        if box is not None and (role != "poster" or not choice.get("fingerprint")):
            raise ValueError("invalid crop selection")
        if role == "poster":
            image = await asyncio.to_thread(validate_image, (await asyncio.to_thread(crop_poster, image.content, tuple(box) if box is not None else None))["content"])
        extension = ".jpg" if image.format == "jpeg" else "." + image.format
        staged.append((role, extension, image.content))
    records = []
    with file_transaction(target):
        for role, extension, content in staged:
            for stem in stems:
                base = f"{stem}-{role}"
                name = base + extension
                atomic_write(target / name, content)
                for old in target.iterdir():
                    if old.stem == base and old.name != name and old.suffix.lower() in {".jpg", ".jpeg", ".png", ".gif", ".webp"} and old.is_file() and not old.is_symlink():
                        old.unlink()
                records.append({"file": name, "status": "saved"})
        if replace_samples:
            written = {record["file"] for record in records}
            for old in target.iterdir():
                if (old.name not in written and old.is_file() and not old.is_symlink()
                    and old.suffix.lower() in {".jpg", ".jpeg", ".png", ".webp", ".gif"}
                    and any(re.fullmatch(re.escape(stem) + r"-fanart[1-9][0-9]{0,2}", old.stem) for stem in stems)):
                    old.unlink()
    return records
