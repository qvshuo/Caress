import base64
from copy import deepcopy
from types import SimpleNamespace

import httpx
import pytest

from caress.emby import EmbyClient, EmbySync, plan_sync
from caress.models import Actor, WorkMetadata
from caress.scanner import scan
from caress.settings import EmbySettings
from test_images import image_bytes


def test_plan_is_pure_preserves_unmanaged_metadata_people_tags_and_provider_ids():
    metadata = WorkMetadata(code="ABC-123", title="managed", actors=[Actor(name="actor")], tags=["managed"])
    original = {"Name": "prior", "People": [{"Name": "guest", "Type": "Actor"}, {"Name": "writer", "Type": "Writer"}],
                "ProviderIds": {"imdb": "id"}, "TagItems": [{"Name": "personal"}], "CustomRating": "personal"}
    before = deepcopy(original)
    plan = plan_sync("1", metadata, original, {}, [{"subtitles": True, "uncensored": True}], [], [])
    assert original == before
    assert plan.metadata["ProviderIds"] == {"imdb": "id", "caress": "ABC-123"}
    assert {person["Name"] for person in plan.metadata["People"]} == {"actor", "guest", "writer"}
    assert {tag["Name"] for tag in plan.metadata["TagItems"]} == {"managed", "personal", "中文字幕", "无码破解"}
    assert "CustomRating" not in plan.metadata
    assert plan.update_metadata
    same = plan_sync("1", metadata, {**original, **plan.metadata}, {"managed_people": plan.managed_people,
                      "managed_tags": plan.managed_tags}, [{"subtitles": True, "uncensored": True}], [], [])
    assert not same.update_metadata


@pytest.mark.asyncio
async def test_exact_paths_include_media_sources_and_additional_parts():
    async def respond(request):
        if request.url.path.endswith("AdditionalParts"):
            return httpx.Response(200, json={"Items": [{"Id": "part", "Path": "/mnt/strm/ABC-123-cd2.strm"}]})
        return httpx.Response(200, json={"TotalRecordCount": 1, "Items": [{"Id": "movie", "Path": "/mnt/strm/ABC-123.strm", "PartCount": 2,
            "MediaSources": [{"Path": "/mnt/strm/ABC-123-C.strm", "ItemId": "version"}]}]})
    client = EmbyClient(EmbySettings(url="http://emby.invalid", api_key="test"))
    await client.client.aclose()
    client.client = httpx.AsyncClient(base_url="http://emby.invalid/emby/", transport=httpx.MockTransport(respond))
    try:
        index = await client.index()
        assert index["/mnt/strm/ABC-123-C.strm"] == {"version"}
        assert index["/mnt/strm/ABC-123-cd2.strm"] == {"part"}
        assert index.get("/different/ABC-123.strm") is None
    finally:
        await client.close()


@pytest.mark.asyncio
async def test_upload_is_base64_real_mime_and_requires_verification():
    image = image_bytes()
    async def respond(request):
        if request.method == "POST":
            assert request.headers["Content-Type"] == "image/png"
            assert base64.b64decode(request.content) == image
            return httpx.Response(204)
        return httpx.Response(200, json=[])
    client = EmbyClient(EmbySettings(url="http://emby.invalid", api_key="test"))
    await client.client.aclose()
    client.client = httpx.AsyncClient(base_url="http://emby.invalid/emby/", transport=httpx.MockTransport(respond))
    try:
        with pytest.raises(ValueError, match="回读"):
            await client.upload("movie", "Primary", image)
    finally:
        await client.close()


@pytest.mark.asyncio
async def test_sync_failure_retry_readback_and_content_idempotency(store, tmp_path, monkeypatch):
    root = tmp_path / "media"
    root.mkdir()
    for file in ("ABC-123.strm", "ABC-123-C.strm"):
        (root / file).touch()
    store.save_scan(scan([root]))
    store.save_metadata("ABC-123", WorkMetadata(code="ABC-123", title="new title"))
    service = SimpleNamespace(runtime=store, settings=SimpleNamespace(emby=EmbySettings(url="http://emby.invalid", api_key="secret")), output=store.root / "images")
    item = {"Id": "movie", "Name": "old title", "ServerId": "server", "People": [], "ProviderIds": {}, "TagItems": []}
    requests = []
    persist_writes = False
    class Client:
        async def index(self):
            requests.append("index")
            return {row["path"]: {"movie"} for row in store.media("ABC-123")}
        async def item(self, ident):
            requests.append("read")
            return deepcopy(item)
        async def request(self, method, path, **kwargs):
            requests.append(method)
            if persist_writes and method == "POST":
                item.update(kwargs["json"])
        async def close(self):
            pass
    monkeypatch.setattr("caress.emby.EmbyClient", lambda settings: Client())
    sync = EmbySync(service)
    await sync.sync("ABC-123")
    assert all(row["sync"]["status"] == "failed" for row in store.media("ABC-123"))
    assert all("content" not in row["sync"] for row in store.media("ABC-123"))
    persist_writes = True
    await sync.sync("ABC-123")
    assert all(row["sync"]["status"] == "synced" for row in store.media("ABC-123"))
    assert item["Name"] == "new title"
    assert item["TagItems"] == [{"Name": "中文字幕"}]
    count = len(requests)
    await sync.sync("ABC-123")
    assert len(requests) == count
    await sync.sync("ABC-123", relink=True)
    assert len(requests) > count


@pytest.mark.asyncio
@pytest.mark.parametrize("actual_color,valid", [("red", True), ("blue", False)])
async def test_upload_verifies_actual_image_pixels_instead_of_existing_slot(actual_color, valid):
    expected = image_bytes("red")
    async def respond(request):
        if request.method == "POST":
            return httpx.Response(204)
        if request.url.path.endswith("/Images"):
            return httpx.Response(200, json=[{"ImageType": "Primary", "Width": 120, "ImageIndex": 0}])
        assert request.url.params["Format"] == "original"
        return httpx.Response(200, content=image_bytes(actual_color), headers={"Content-Type": "image/png"})
    client = EmbyClient(EmbySettings(url="http://emby.invalid", api_key="test"))
    await client.client.aclose()
    client.client = httpx.AsyncClient(base_url="http://emby.invalid/emby/", transport=httpx.MockTransport(respond))
    try:
        if valid:
            await client.upload("movie", "Primary", expected)
        else:
            with pytest.raises(ValueError, match="内容回读"):
                await client.upload("movie", "Primary", expected)
    finally:
        await client.close()
