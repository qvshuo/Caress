from io import BytesIO

import pytest
from PIL import Image

from caress import artwork
from caress.images import crop_poster, save_image_choices, validate_image
from caress.models import SourceId, Work
from caress.storage import atomic_write, file_transaction


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


@pytest.mark.parametrize("data", [b"<html>challenge</html>", b"\x89PNG\r\n\x1a\nbroken"])
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


def test_handled_file_rollback_restores_original_and_removes_new(tmp_path):
    original = tmp_path / "ABC-123-poster.png"
    original.write_bytes(image_bytes())
    with pytest.raises(RuntimeError), file_transaction(tmp_path):
        atomic_write(original, image_bytes("blue"))
        atomic_write(tmp_path / "new.png", image_bytes())
        raise RuntimeError("database commit failed")
    assert original.read_bytes() == image_bytes()
    assert sorted(path.name for path in tmp_path.iterdir()) == [original.name]


@pytest.mark.asyncio
async def test_all_choices_validate_before_any_file_changes(tmp_path):
    original = tmp_path / "ABC-123-fanart.png"
    original.write_bytes(image_bytes())
    class Client:
        async def image_bytes(self, source, url, referer):
            return image_bytes("blue") if url.endswith("good") else b"bad"
    variants = {"dmm": {"status": "found", "metadata": {"artwork": [
        {"url": "https://example.invalid/good", "referer": "https://example.invalid"},
        {"url": "https://example.invalid/bad", "referer": "https://example.invalid"}]}}}
    with pytest.raises(ValueError):
        await save_image_choices(Client(), {"fanart": {"source": "dmm", "index": 0},
            "fanart1": {"source": "dmm", "index": 1}}, variants, tmp_path, ["ABC-123"])
    assert original.read_bytes() == image_bytes()
    assert not (tmp_path / "ABC-123-fanart1.png").exists()


@pytest.mark.asyncio
async def test_retry_preserves_manual_selection_and_database_rollback(store, monkeypatch):
    work = Work(code="ABC-123")
    folder = store.root / "images" / work.code
    folder.mkdir(parents=True)
    original = folder / "ABC-123-fanart.png"
    original.write_bytes(image_bytes())
    selection = {}
    candidate = {"url": "https://example.invalid/a", "role": "cover", "source": "dmm", "referer": "https://example.invalid"}
    artwork.stage(selection, "dmm", [candidate], refresh=False)
    (folder / "ABC-123-poster.png").write_bytes(image_bytes("green"))
    artwork.select_manual(selection, {"poster": {"source": "dmm", "index": 0}},
        [{"file": "ABC-123-poster.png"}], replace_samples=False,
        variants={"dmm": {"metadata": {"artwork": [candidate]}}}, folder=folder)
    class Client:
        async def image_bytes(self, *args):
            return image_bytes("blue")
    with pytest.raises(RuntimeError), file_transaction(folder):
        await artwork.write(Client(), work, store.root / "images", selection)
        with store.transaction():
            store.save_selection(work.code, selection)
            raise RuntimeError("commit failed")
    assert original.read_bytes() == image_bytes()
    assert (folder / "ABC-123-poster.png").read_bytes() == image_bytes("green")
    assert store.selection(work.code) == {}
