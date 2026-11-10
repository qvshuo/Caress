from io import BytesIO

import pytest
from PIL import Image

from caress.images import crop_box, crop_poster, validate_image
from caress.models import SourceId


def image_bytes(color="red", size=(120, 90)):
    output = BytesIO()
    Image.new("RGB", size, color).save(output, format="PNG")
    return output.getvalue()


@pytest.mark.parametrize(
    "format,mode",
    [
        ("PNG", "RGBA"),
        ("JPEG", "RGB"),
        ("JPEG", "CMYK"),
        ("WEBP", "RGB"),
        ("GIF", "RGB"),
    ],
)
def test_crop_is_lossless_two_by_three_without_resampling(format, mode):
    output = BytesIO()
    Image.new(mode, (120, 90), "red").save(output, format=format)
    data = output.getvalue()
    validated = validate_image(data)
    assert validated.format == format.lower()
    assert (validated.width, validated.height) == (120, 90)
    result = crop_poster(data, (20, 0, 60, 90))
    saved = Image.open(BytesIO(result["content"]))
    assert saved.size == (60, 90)
    assert saved.format == "PNG"
    source = Image.open(BytesIO(data))
    if mode == "CMYK":
        source = source.convert("RGB")
    assert saved.tobytes() == source.crop((20, 0, 80, 90)).tobytes()


@pytest.mark.parametrize(
    "data", [b"<html>challenge</html>", b"\x89PNG\r\n\x1a\nbroken"]
)
def test_rejects_invalid_image_bytes(data):
    with pytest.raises(ValueError):
        validate_image(data)


def test_javdb_envelope_preserves_decoded_bytes():
    data = image_bytes()
    key = 42
    wrapped = bytes([key]) + bytes(value ^ key for value in data)
    decoded = validate_image(wrapped, SourceId.JAVDB)
    assert decoded.content == data
    assert decoded.decoded


@pytest.mark.parametrize(
    "width,height,faces",
    [
        (1200, 800, []),
        (3, 9, []),
        (500, 1000, [[490, 990, 5, 5, 0.9]]),
        (1000, 600, [[-20, -10, 100, 80, 0.9]]),
    ],
)
def test_auto_crop_stays_in_bounds(width, height, faces):
    x, y, w, h = crop_box(width, height, faces)
    assert 0 <= x <= width - w and 0 <= y <= height - h and w * 3 == h * 2


@pytest.mark.parametrize("box", [(0, 0, 30, 30), (-1, 0, 60, 90), (100, 0, 60, 90)])
def test_manual_crop_rejects_invalid_selection(box):
    with pytest.raises(ValueError):
        crop_poster(image_bytes(), box)


@pytest.mark.parametrize("format", ["JPEG", "PNG", "WEBP"])
def test_crop_source_retains_encoded_bytes_without_orientation(format):
    from caress.images import crop_source_image

    output = BytesIO()
    Image.new("RGB", (120, 90), "red").save(output, format=format)
    data = output.getvalue()
    assert crop_source_image(data) is data


def test_crop_exif_orientation_matches_normalized_source():
    from PIL import ImageOps

    from caress.images import crop_source_image

    output = BytesIO()
    source = Image.new("RGB", (120, 90), "red")
    source.paste("blue", (0, 0, 60, 90))
    exif = Image.Exif()
    exif[274] = 6
    source.save(output, format="JPEG", exif=exif)
    data = output.getvalue()
    normalized = crop_source_image(data)
    image = Image.open(BytesIO(normalized))
    assert image.size == (90, 120)
    assert (
        image.tobytes() == ImageOps.exif_transpose(Image.open(BytesIO(data))).tobytes()
    )
    assert (
        crop_poster(data, center=True)["content"]
        == crop_poster(normalized, center=True)["content"]
    )


def test_crop_source_checks_pixel_limit_before_decode(monkeypatch):
    from caress.images import crop_source_image

    original = Image.Image.load

    def forbidden(self, *args, **kwargs):
        if self.width * self.height > 40_000_000:
            raise AssertionError("decoded before limit check")
        return original(self, *args, **kwargs)

    # A header-only PNG is enough to exercise the resource boundary.
    import struct
    import zlib

    def chunk(kind, data):
        return (
            struct.pack(">I", len(data))
            + kind
            + data
            + struct.pack(">I", zlib.crc32(kind + data))
        )

    data = (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", struct.pack(">IIBBBBB", 10000, 5000, 8, 2, 0, 0, 0))
        + chunk(b"IDAT", b"")
        + chunk(b"IEND", b"")
    )
    monkeypatch.setattr(Image.Image, "load", forbidden)
    with pytest.raises(ValueError, match="4000 万像素"):
        crop_source_image(data)


def test_crop_rejects_oversized_lossless_output(monkeypatch):
    original = Image.Image.save

    def inflated(image, stream, *args, **kwargs):
        if kwargs.get("format") == "PNG":
            stream.write(b"x" * (32 * 1024 * 1024 + 1))
        else:
            original(image, stream, *args, **kwargs)

    data = image_bytes()
    monkeypatch.setattr(Image.Image, "save", inflated)
    with pytest.raises(ValueError, match="32 MiB"):
        crop_poster(data, center=True)
