from io import BytesIO

import pytest
from PIL import Image

from caress.images import crop_box, crop_poster, validate_image
from caress.models import SourceId


def image_bytes(color="red", size=(120, 90)):
    output = BytesIO()
    Image.new("RGB", size, color).save(output, format="PNG")
    return output.getvalue()


def test_crop_is_lossless_two_by_three_without_resampling():
    data = image_bytes()
    result = crop_poster(data, (20, 0, 60, 90))
    saved = Image.open(BytesIO(result["content"]))
    assert saved.size == (60, 90)
    assert saved.format == "PNG"
    assert saved.tobytes() == Image.open(BytesIO(data)).crop((20, 0, 80, 90)).tobytes()


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
