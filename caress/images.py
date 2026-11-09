from __future__ import annotations

from dataclasses import dataclass
from io import BytesIO
from pathlib import Path
from urllib.parse import unquote, urlsplit

from PIL import Image, UnidentifiedImageError

from .models import SourceId


def validate_resources() -> None:
    import cv2

    from .actors import ActorAliases
    from .catalog import CatalogRules

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

    @property
    def mime(self):
        return "image/jpeg" if self.format == "jpeg" else "image/" + self.format


class PngOutput(BytesIO):
    """Stop lossless encoding before an expanded image exhausts the output budget."""

    def write(self, data):
        if self.tell() + len(data) > 32 * 1024 * 1024:
            raise ValueError("PNG 图像超过 32 MiB")
        return super().write(data)


def image_signature(data: bytes) -> bool:
    return data.startswith(
        (b"\xff\xd8\xff", b"\x89PNG\r\n\x1a\n", b"GIF87a", b"GIF89a")
    ) or (data.startswith(b"RIFF") and data[8:12] == b"WEBP")


def placeholder_url(source, url):
    if source != SourceId.DMM:
        return False
    path = unquote(urlsplit(url).path).lower()
    return any(
        marker in path
        for marker in ("now_printing", "nowprinting", "noimage", "no_image")
    )


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
            if width * height > 40_000_000:
                raise ValueError("image exceeds 40 megapixels")
            image.verify()
        # Header verification alone can accept JPEGs whose pixel stream is corrupt.
        with Image.open(BytesIO(data)) as image:
            image.load()
        if not image_format or width < 1 or height < 1 or width * height > 40_000_000:
            raise ValueError("invalid image dimensions")
        return ImageData(data, image_format.lower(), width, height, decoded)
    except (
        UnidentifiedImageError,
        OSError,
        SyntaxError,
        Image.DecompressionBombError,
    ) as exc:
        raise ValueError("artwork is corrupt") from exc


def image_plan(items):
    covers = [item for item in items if item.role == "cover"]
    posters = [item for item in items if item.role == "poster"]
    plan = []
    if posters or covers:
        plan.append((covers[0] if covers else posters[0], "poster"))
    if covers or posters:
        plan.append((covers[0] if covers else posters[0], "fanart"))
    samples = list(
        {
            (item.source, item.url): item for item in items if item.role == "sample"
        }.values()
    )
    plan.extend((item, f"fanart{i + 1}") for i, item in enumerate(samples[:100]))
    return plan


async def fetch_artwork(client, candidate):
    if placeholder_url(candidate.source, candidate.url):
        raise ValueError("来源返回占位图")
    import asyncio

    return (
        await asyncio.to_thread(
            validate_image,
            await client.image_bytes(
                candidate.source, candidate.url, candidate.referer
            ),
            candidate.source,
        )
    ).content


def crop_box(
    width: int, height: int, faces: list[list[float]]
) -> tuple[int, int, int, int]:
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


def crop_source_image(data: bytes) -> bytes:
    """Keep original encoding unless EXIF actually changes browser pixel coordinates."""
    from PIL import ImageOps

    validate_image(data)
    with Image.open(BytesIO(data)) as image:
        if image.getexif().get(274, 1) not in range(2, 9):
            return data
        output = PngOutput()
        ImageOps.exif_transpose(image, in_place=True)
        image.save(output, format="PNG")
        result = output.getvalue()
        return result


def crop_poster(
    data: bytes, box: tuple[int, int, int, int] | None = None, center: bool = False
) -> dict:
    from PIL import ImageOps

    if len(data) > 32 * 1024 * 1024:
        raise ValueError("artwork exceeds 32 MiB")
    with Image.open(BytesIO(data)) as source:
        if source.width * source.height > 40_000_000:
            raise ValueError("image exceeds 40 megapixels")
        ImageOps.exif_transpose(source, in_place=True)
        image = source.copy()
    if image.mode == "CMYK":
        image = image.convert("RGB")
    width, height = image.size
    faces = []
    if box is None and not center:
        import cv2
        import numpy as np

        scale = min(1, 960 / max(width, height))
        small = image.resize(
            (max(32, round(width * scale)), max(32, round(height * scale)))
        )
        frame = cv2.cvtColor(np.asarray(small.convert("RGB")), cv2.COLOR_RGB2BGR)
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
    if (
        min(x, y) < 0
        or min(w, h) < 1
        or x + w > width
        or y + h > height
        or w * 3 != h * 2
    ):
        raise ValueError("crop must be an in-bounds 2:3 rectangle")
    cropped = image.crop((x, y, x + w, y + h))
    output = PngOutput()
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
