import asyncio
import json
from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import httpx
import pytest

from caress.actors import ActorAliases, map_actor_names
from caress.api import create_app
from caress.emby import EmbyClient
from caress.identifiers import parse_filename
from caress.library import identify, project
from caress.models import Actor, SourceMetadata
from caress.processing import WorkProcessor
from caress.runtime import Runtime
from caress.service import Service
from caress.settings import EmbySettings, Settings, TranslationSettings
from caress.translation import Translator


def item(ident="1", path="/strm/ABC-123.strm", **extra):
    return {
        "Id": ident,
        "Name": "Emby title",
        "Path": path,
        "DateCreated": "2026-01-01T00:00:00Z",
        **extra,
    }


@pytest.mark.parametrize(
    "stem,code,sub,unc,part",
    [
        ("ABC-123-C", "ABC-123", True, False, None),
        ("ABC-123-u", "ABC-123", False, True, None),
        ("ABC-123-Uc-cD2", "ABC-123", True, True, 2),
        ("ABC-123-cd1", "ABC-123", False, False, 1),
        ("123456_123", "123456_123", False, False, None),
        ("FC2PPV-123456", "FC2PPV-123456", False, False, None),
    ],
)
def test_filename_suffixes_and_strm(stem, code, sub, unc, part):
    actual, media = parse_filename(Path("/not-a-code") / (stem + ".strm"))
    assert (actual, media.subtitles, media.uncensored, media.part) == (
        code,
        sub,
        unc,
        part,
    )


@pytest.mark.parametrize(
    "name", ["ABC-123-ch", "ABC-123-4k", "bad", "ABC-123-cd1-cd2", " ABC-123"]
)
def test_filename_never_guesses(name):
    with pytest.raises(ValueError):
        parse_filename(Path("/ABC-123") / (name + ".mp4"))


def test_remote_strm_target_not_used_for_identity():
    code, _, media = identify(
        item(
            MediaSources=[
                {
                    "Path": "https://host/WRONG-999.mp4",
                    "Protocol": "Http",
                    "ItemId": "1",
                }
            ]
        )
    )
    assert code == "ABC-123" and len(media) == 1


def test_one_item_many_versions_and_conflicting_codes():
    movie = item(
        MediaSources=[
            {"Path": "/strm/ABC-123-U.mp4", "Protocol": "File", "ItemId": "1"},
            {"Path": "/strm/OTHER-123.mp4", "Protocol": "File", "ItemId": "2"},
        ]
    )
    assert len(identify(movie)[2]) == 2
    movie["MediaSources"][0]["Path"] = "/strm/WRONG-999.mp4"
    with pytest.raises(ValueError, match="不同番号"):
        identify(movie)


def test_server_and_rebuilt_item_identity_keep_success_separate(store):
    movie = item()
    code, identity, _ = identify(movie)
    store.save_record("server-a", "1", identity, code, "success")
    assert project(movie, store, "server-a")["status"] == "success"
    assert project(movie, store, "server-b")["status"] == "pending"
    assert project(item(DateCreated="new"), store, "server-a")["status"] == "pending"
    movie.update(Name="User edit", Overview="Manual plot")
    result = project(movie, store, "server-a")
    assert (
        result["title"] == "User edit"
        and result["plot"] == "Manual plot"
        and result["status"] == "success"
    )


@pytest.mark.asyncio
async def test_http_204_is_not_proof_of_persistence():
    def handler(request):
        if request.url.path.endswith("/Users"):
            return httpx.Response(
                200, json=[{"Id": "u", "Policy": {"IsAdministrator": True}}]
            )
        if request.method == "POST":
            return httpx.Response(204)
        return httpx.Response(200, json=item())

    client = EmbyClient(
        EmbySettings(url="http://emby", api_key="secret"),
        transport=httpx.MockTransport(handler),
    )
    try:
        with pytest.raises(ValueError, match="不一致|保存验证失败"):
            await client.write_metadata(
                "1", SourceMetadata(code="ABC-123", title="new"), [], replace=True
            )
    finally:
        await client.close()


@pytest.mark.asyncio
async def test_metadata_readback_checks_managed_fields_only():
    original = item(Name="old", LockData=False, LockedFields=[])
    current = {**original, "Name": "new", "LockData": True, "LockedFields": ["Name"],
               "ProviderIds": {"caress": "ABC-123"}}
    client = EmbyClient(EmbySettings(url="http://emby"))
    client.item = AsyncMock(side_effect=[original, current])
    client.request = AsyncMock()
    try:
        result, changed = await client.write_metadata(
            "1", SourceMetadata(code="ABC-123", title="new"), [], replace=True
        )
        assert changed and result == current
        sent = client.request.await_args.kwargs["json"]
        assert sent["LockData"] is False and sent["LockedFields"] == []
    finally:
        await client.close()


@pytest.mark.asyncio
async def test_emby_library_pagination_and_duplicate_items():
    starts = []

    def handler(request):
        start = int(request.url.params["StartIndex"])
        starts.append(start)
        rows = [item("1"), item("2")] if start == 0 else [item("3")]
        return httpx.Response(200, json={"Items": rows, "TotalRecordCount": 3})

    client = EmbyClient(
        EmbySettings(url="http://emby", library_ids=["lib", "overlap"]),
        transport=httpx.MockTransport(handler),
    )
    try:
        assert [i["Id"] async for i in client.items()] == ["1", "2", "3"]
        assert starts == [0, 2, 0, 2]
    finally:
        await client.close()


@pytest.mark.asyncio
async def test_translation_failure_retains_valid_source():
    translator = Translator(TranslationSettings(model="model", api_key="secret"))
    translator.text = AsyncMock(side_effect=ValueError("translation unavailable"))
    metadata = SourceMetadata(
        code="ABC-123", title="original title", plot="original description"
    )
    try:
        report = await translator.metadata(metadata, names=None)
        assert (
            metadata.title == "original title"
            and metadata.plot == "original description"
        )
        assert len(report.errors) == 2
    finally:
        await translator.close()


@pytest.mark.asyncio
async def test_translation_key_stays_in_header_and_out_of_logs(caplog):
    def handler(request):
        assert request.headers["authorization"] == "Bearer example-key"
        assert b"example-key" not in request.content
        return httpx.Response(401, json={"error": "denied"})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        translator = Translator(TranslationSettings(model="model", api_key="example-key"), client)
        with pytest.raises(ValueError, match="认证失败"):
            await translator.text("test", retries=0)
    assert "example-key" not in caplog.text


def test_actor_alias_ambiguity_and_verified_id():
    names = ActorAliases(
        {
            "a": {"type": 0, "name": "A", "other_name": ["Alias"]},
            "b": {"type": 0, "name": "B", "other_name": ["Alias"]},
        }
    )
    actors, _, issues = map_actor_names([Actor(name="Alias")], names, 0)
    assert actors[0].name == "Alias" and issues
    actors, _, _ = map_actor_names([Actor(name="Alias")], names, 0, {"Alias": "a"})
    assert actors[0].name == "A"


def test_restart_retains_confirmed_success_and_cleans_interruption(tmp_path):
    path = tmp_path / "db.sqlite3"
    runtime = Runtime(path)
    runtime.save_record("s", "1", "identity", "ABC-123", "success")
    runtime.save_record("s", "2", "identity", "ABC-123", "running")
    runtime.save_batch(
        {
            "status": "running",
            "items": [
                {"id": "1", "status": "success"},
                {"id": "2", "status": "running"},
            ],
        }
    )
    runtime.close()
    runtime = Runtime(path)
    try:
        assert runtime.record("s", "1", "identity", "ABC-123")["succeeded_at"]
        assert runtime.record("s", "2", "identity", "ABC-123")["status"] == "failed"
        assert runtime.batch()["items"][0]["status"] == "success"
        assert runtime.batch()["items"][1]["status"] == "cancelled"
    finally:
        runtime.close()


class FakeClient:
    movies = {}
    writes = []

    def __init__(self, settings):
        self.server = "s"

    async def test(self):
        return {"id": "s"}

    async def close(self):
        pass

    async def items(self):
        for value in self.movies.values():
            yield deepcopy(value)

    async def item(self, ident):
        return deepcopy(self.movies[ident])

    async def contains(self, ident):
        return ident in self.movies

    async def image_info(self, ident):
        return self.movies[ident].get("_images", [])

    async def parts(self, item):
        return []


class FakeProcessor:
    writes = []
    fail = set()
    block = None

    def __init__(self, *args):
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        pass

    async def prepare(self, *args):
        if self.block:
            await self.block.wait()
        return SimpleNamespace(source="dmm")

    async def write(self, client, ident, *args, replace):
        self.writes.append(ident)
        if ident in self.fail:
            raise ValueError("图片写入失败；元数据可能已写入")
        return [], True

    async def finish(self):
        pass

    async def release(self, *args):
        pass


@pytest.fixture
def fake_batch(tmp_path, monkeypatch):
    import caress.tasks as tasks

    FakeClient.movies = {"1": item("1"), "2": item("2")}
    FakeProcessor.writes = []
    FakeProcessor.fail = set()
    FakeProcessor.block = None
    import caress.service as service_module

    monkeypatch.setattr(service_module, "EmbyClient", FakeClient)
    monkeypatch.setattr(tasks, "WorkProcessor", FakeProcessor)
    service = Service(
        Settings(
            database=tmp_path / "db.sqlite3",
            emby={"url": "http://emby", "api_key": "test"},
        )
    )
    yield service
    service.runtime.close()


@pytest.mark.asyncio
async def test_cancellation_preserves_success_and_marks_only_started_records(
    fake_batch,
):
    service = fake_batch
    FakeProcessor.block = asyncio.Event()
    code, identity, _ = identify(item())
    service.runtime.save_record("s", "1", identity, code, "success")
    service.batches.start(["1", "2"], "scrape", None)
    for _ in range(100):
        await asyncio.sleep(0.001)
        record = service.runtime.record("s", "2", identity, code)
        if record:
            break
    await service.batches.cancel()
    assert service.runtime.record("s", "1", identity, code)["succeeded_at"]
    assert service.runtime.record("s", "2", identity, code)["status"] == "cancelled"
    assert service.runtime.batch()["status"] == "cancelled"


@pytest.mark.asyncio
async def test_api_secrets_confirmation_and_offline_response(tmp_path, monkeypatch):
    settings = Settings(
        database=tmp_path / "db.sqlite3",
        emby=EmbySettings(url="http://emby", api_key="hidden", library_ids=["1"]),
    )
    app = create_app(settings)
    async with (
        app.router.lifespan_context(app),
        httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://caress"
        ) as client,
    ):
        monkeypatch.setattr(
            app.state.service,
            "works",
            AsyncMock(side_effect=httpx.ConnectError("secret URL")),
        )
        assert (
            await client.post("/api/batch", json={"ids": ["1"], "action": "retry"})
        ).status_code == 422
        body = (await client.get("/api/settings")).json()
        assert "hidden" not in str(body) and body["emby"]["has_api_key"]
        assert (
            await client.post("/api/batch", json={"ids": ["1"], "action": "force"})
        ).status_code == 400
        assert (await client.put("/api/settings", json=body)).status_code == 200
        response = await client.get("/api/works")
        assert response.status_code == 502 and "secret URL" not in response.text
        assert (
            await client.put("/api/works/1", json={"title": "not allowed"})
        ).status_code == 405


@pytest.mark.asyncio
async def test_cancel_before_batch_task_starts(fake_batch):
    fake_batch.batches.start(["1"], "scrape", None)
    await fake_batch.batches.cancel()
    assert fake_batch.runtime.batch()["status"] == "cancelled"
    assert fake_batch.runtime.batch()["items"][0]["status"] == "cancelled"


def test_invalid_filename_still_exposes_emby_path(store):
    result = project(item(path="/strm/not a code.strm"), store, "s")
    assert result["status"] == "unsupported"
    assert result["media"][0]["path"] == "/strm/not a code.strm"


@pytest.mark.asyncio
async def test_item_writes_are_serialized_while_other_items_can_run(tmp_path):
    service = Service(Settings(database=tmp_path / "db.sqlite3"))
    active = maximum = 0

    async def mutate():
        nonlocal active, maximum
        async with service.item_lock("s", "1"):
            active += 1
            maximum = max(maximum, active)
            await asyncio.sleep(0.01)
            active -= 1

    await asyncio.gather(mutate(), mutate())
    assert maximum == 1
    await service.close()


def test_unknown_actor_names_are_protected_without_a_cached_alias_pattern():
    from caress.translation import actor_name_rules, name_spans, without_names

    names = ActorAliases({})
    actors = [Actor(name="Original Actor")]
    pattern = actor_name_rules(names, actors)
    assert (
        without_names(
            "Original Actor source title",
            name_spans("Original Actor source title", pattern),
        )
        == " source title"
    )


@pytest.fixture
async def crop_service(tmp_path, monkeypatch):
    from contextlib import asynccontextmanager

    import caress.service as service_module
    from caress.images import validate_image
    from test_images import image_bytes

    service = Service(Settings(database=tmp_path / "db.sqlite3"))
    client = SimpleNamespace(
        server="server",
        image_info=AsyncMock(
            return_value=[
                {"ImageType": "Backdrop", "Width": 600, "Height": 300, "ImageIndex": 0}
            ]
        ),
        image=AsyncMock(return_value=validate_image(image_bytes(size=(600, 300)))),
    )

    @asynccontextmanager
    async def emby():
        yield client

    monkeypatch.setattr(service, "emby", emby)
    monkeypatch.setattr(service, "scoped_item", AsyncMock(return_value=item()))
    source = SimpleNamespace(cover=AsyncMock(return_value=image_bytes(size=(600, 300))))

    @asynccontextmanager
    async def scraper(*args, **kwargs):
        yield source

    monkeypatch.setattr(service_module, "Scraper", scraper)
    try:
        yield service, client, source
    finally:
        await service.close()
        assert service.crop is None


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "fetch_source,has_backdrop", [(False, True), (True, True), (False, False)]
)
async def test_crop_opens_local_backdrop_without_source_or_face_detection(
    crop_service, monkeypatch, fetch_source, has_backdrop
):
    import caress.service as service_module
    from caress.images import crop_poster

    service, client, source = crop_service
    if not has_backdrop:
        client.image_info.return_value = []
    calls = []

    def crop(data, box=None, center=False):
        calls.append(center)
        return crop_poster(data, box, center)

    monkeypatch.setattr(service_module, "crop_poster", crop)
    result = await service.crop_source("1", "dmm", fetch_source=fetch_source)
    assert calls == [True]
    assert result["box"][2] * 3 == result["box"][3] * 2
    if has_backdrop and not fetch_source:
        source.cover.assert_not_awaited()
    else:
        source.cover.assert_awaited_once_with("ABC-123", "dmm")
    if fetch_source:
        client.image_info.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ["fetch", "preview"])
async def test_crop_replaces_only_after_success_and_expires_lazily(
    crop_service, monkeypatch, failure
):
    import caress.service as module

    service, _, source = crop_service
    first = await service.crop_source("1")
    previous = service.crop_session("1", first["token"])
    with monkeypatch.context() as patch:
        if failure == "fetch":
            patch.setattr(source, "cover", AsyncMock(side_effect=ValueError("offline")))
        else:
            patch.setattr(
                module, "crop_poster", Mock(side_effect=ValueError("preview failed"))
            )
        with pytest.raises(ValueError):
            await service.crop_source("1", fetch_source=True)
    assert service.crop_session("1", first["token"]) is previous

    second = await service.crop_source("1")
    with pytest.raises(ValueError, match="裁剪已过期"):
        service.crop_session("1", first["token"])
    service.crop_close("1", first["token"])
    session = service.crop_session("1", second["token"])
    session.expires = 0
    with pytest.raises(ValueError, match="裁剪已过期"):
        service.crop_session("1", second["token"])
    assert service.crop is None


@pytest.mark.asyncio
@pytest.mark.parametrize("replace_during_upload", [False, True])
async def test_concurrent_crop_sources_keep_latest_token_and_save_only_primary(
    crop_service, replace_during_upload
):
    service, client, source = crop_service
    entered, resume = asyncio.Event(), asyncio.Event()
    original = source.cover.return_value

    async def cover(*args):
        entered.set()
        await resume.wait()
        return original

    source.cover.side_effect = cover
    first_task = asyncio.create_task(service.crop_source("1", fetch_source=True))
    await entered.wait()
    second_task = asyncio.create_task(service.crop_source("1", fetch_source=True))
    resume.set()
    first, second = await asyncio.gather(first_task, second_task)
    assert source.cover.await_count == 2
    with pytest.raises(ValueError, match="裁剪已过期"):
        service.crop_session("1", first["token"])
    service.crop_session("1", second["token"])
    uploading, finish = asyncio.Event(), asyncio.Event()

    async def upload(*args):
        uploading.set()
        await finish.wait()

    client.upload = AsyncMock(side_effect=upload)
    service.detail = AsyncMock(return_value={})
    saving = asyncio.create_task(
        service.crop_apply("1", second["token"], second["box"])
    )
    await uploading.wait()
    if replace_during_upload:
        newer = await service.crop_source("1")
    finish.set()
    await saving
    assert client.upload.await_args.args[:2] == ("1", "Primary")
    if replace_during_upload:
        service.crop_session("1", newer["token"])
    else:
        assert service.crop is None
    third = await service.crop_source("1")
    service.crop_close("1", third["token"])
    assert service.crop is None


@pytest.mark.asyncio
async def test_connection_validates_server_then_reads_libraries(tmp_path, monkeypatch):
    import caress.api as api_module

    upstream = SimpleNamespace(
        test=AsyncMock(
            return_value={"valid": True, "server": "Emby", "version": "4.10"}
        ),
        libraries=AsyncMock(
            return_value=[{"ItemId": "1", "Name": "Movies", "CollectionType": "movies"}]
        ),
        close=AsyncMock(),
    )
    monkeypatch.setattr(api_module, "EmbyClient", lambda settings: upstream)
    app = create_app(Settings(database=tmp_path / "db.sqlite3"))
    async with (
        app.router.lifespan_context(app),
        httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://caress"
        ) as client,
    ):
        response = await client.post("/api/settings/libraries", json={})
        assert response.json() == [{"id": "1", "name": "Movies", "type": "movies"}]
        assert upstream.test.await_count == 1 and upstream.libraries.await_count == 1
        upstream.test.side_effect = ValueError("invalid server")
        response = await client.post("/api/settings/libraries", json={})
        assert response.status_code == 400 and upstream.libraries.await_count == 1
        assert upstream.close.await_count == 2


def test_navigation_order_uses_status_then_code_with_current_filter():
    from caress.library import filter_works, navigation_order

    rows = [
        {"id": ident, "code": code, "title": title, "status": status}
        for ident, code, title, status in [
            ("p2", "XYZ-200", "title", "pending"),
            ("s2", "DEF-999", "title", "success"),
            ("f2", "DEF-100", "title", "rescrape_failed"),
            ("u2", None, "Z film", "unsupported"),
            ("p1", "ABC-300", "title", "cancelled"),
            ("s1", "ABC-200", "title", "success"),
            ("f1", "ABC-100", "title", "failed"),
            ("u1", None, "A film", "unsupported"),
        ]
    ]
    order = navigation_order(filter_works(rows))
    assert [row["id"] for row in order] == [
        "u1",
        "u2",
        "f1",
        "f2",
        "s1",
        "s2",
        "p1",
        "p2",
    ]
    assert [row["position"] for row in order] == list(range(8))
    assert [row["id"] for row in filter_works(rows, status="failed")] == ["f1", "f2"]
    assert navigation_order(filter_works(rows, "abc", "pending")) == [
        {"id": "p1", "label": "ABC-300", "position": 0},
    ]


def test_settings_public_format_roundtrips_without_extra_configuration(tmp_path):
    from caress.api import safe_settings, updated_settings

    settings = Settings(
        database=tmp_path / "db",
        emby={"url": "http://emby", "api_key": "secret", "library_ids": ["lib"]},
    )
    public = safe_settings(settings)
    assert set(public) == {"configured", "emby", "translation", "routes"}
    assert "api_key" not in public["emby"]
    saved = updated_settings(settings, public)
    saved.save()
    assert Settings.load(settings.database) == settings


@pytest.mark.parametrize(
    "invalid", ["missing", "empty", "duplicate", "unknown", "avsox"]
)
def test_invalid_source_routes_are_rejected(invalid):
    from pydantic import ValidationError

    routes = Settings().model_dump()["routes"]
    if invalid == "missing":
        del routes["unknown"]
    else:
        routes["censored"] = {
            "empty": [],
            "duplicate": ["dmm", "dmm"],
            "unknown": ["invalid"],
            "avsox": ["avsox"],
        }[invalid]
    with pytest.raises(ValidationError):
        Settings(routes=routes)


@pytest.mark.asyncio
async def test_related_scope_deduplicates_without_blocking_valid_items(
    fake_batch, monkeypatch
):
    async def movies_only(self):
        yield self.movies["1"]

    FakeClient.movies["2"]["Type"] = "Video"
    monkeypatch.setattr(FakeClient, "items", movies_only)

    async def parts(self, parent):
        return [item("2"), item("outside"), item("2")] if parent["Id"] == "1" else []

    monkeypatch.setattr(FakeClient, "parts", parts)
    fake_batch.batches.start(["1", "2"], "scrape", None)
    await fake_batch.batches.task
    assert sorted(FakeProcessor.writes) == ["1", "2"]
    rows = fake_batch.runtime.batch()["items"]
    assert len(rows) == 3
    excluded = next(r for r in rows if r["id"] == "outside")
    assert excluded["status"] == "skipped" and "归属" in excluded["error"]


@pytest.mark.asyncio
async def test_detail_reads_only_scoped_item_and_latest_metadata(tmp_path, monkeypatch):
    from contextlib import asynccontextmanager

    service = Service(Settings(database=tmp_path / "db"))
    client = SimpleNamespace(
        server="s",
        contains=AsyncMock(return_value=True),
        item=AsyncMock(return_value=item(Name="Emby edited")),
        parts=AsyncMock(return_value=[]),
        image_info=AsyncMock(return_value=[]),
    )

    @asynccontextmanager
    async def connection():
        yield client

    monkeypatch.setattr(service, "emby", connection)
    try:
        assert (await service.detail("1"))["title"] == "Emby edited"
        client.contains.assert_awaited_once_with("1")
        client.contains.return_value = False
        with pytest.raises(ValueError, match="媒体库"):
            await service.detail("1")
        assert client.item.await_count == 1
    finally:
        await service.close()


@pytest.mark.asyncio
async def test_connection_leases_survive_configuration_change(tmp_path, monkeypatch):
    import caress.service as module

    created = []

    class Client:
        def __init__(self, settings):
            self.test = AsyncMock()
            self.close = AsyncMock()
            created.append(self)

    monkeypatch.setattr(module, "EmbyClient", Client)
    settings = Settings(
        database=tmp_path / "db", emby={"url": "http://emby", "api_key": "secret"}
    )
    service = Service(settings)
    async with service.emby() as old:
        async with service.emby() as same:
            assert old is same
            old.test.assert_awaited_once()
        changed = settings.model_copy(deep=True)
        changed.emby.url = "http://other"
        await service.configure(changed)
        old.close.assert_not_awaited()
        async with service.emby() as new:
            assert new is not old
    old.close.assert_awaited_once()
    await service.close()
    new.close.assert_awaited_once()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "failure,recovers", [("upload", True), ("upload", False), ("fetch", False)]
)
async def test_portrait_retry_is_bounded_and_success_is_shared(
    tmp_path, failure, recovers
):
    from caress.processing import Prepared
    from test_images import image_bytes

    processor = WorkProcessor(Settings(database=tmp_path / "db"), None, tmp_path)
    avatar = tmp_path / "actors" / "Name.png"
    avatar.parent.mkdir()
    avatar.write_bytes(image_bytes())
    processor.portraits.save = AsyncMock(
        return_value=[{"status": "saved"}],
        side_effect=ValueError("offline") if failure == "fetch" else None,
    )
    client = SimpleNamespace(
        write_metadata=AsyncMock(
            return_value=(
                {"People": [{"Name": "Name", "Id": "person", "Type": "Actor"}]},
                False,
            )
        ),
        image_info=AsyncMock(
            side_effect=lambda ident: (
                [] if ident == "person" else [{"ImageType": "Backdrop"}]
            )
        ),
        upload=AsyncMock(
            side_effect=[ValueError("temporary"), None]
            if recovers
            else ValueError("offline")
        ),
    )
    prepared = Prepared(
        SourceMetadata(code="ABC-123", title="title"),
        "dmm",
        mappings=[{"actor_id": "verified", "name": "Name"}],
    )
    try:
        await processor.write(client, "1", prepared, [], replace=False)
        assert not processor.people_done
        await asyncio.gather(
            *(
                processor.write(client, str(i), prepared, [], replace=False)
                for i in range(2, 8)
            )
        )
        assert client.upload.await_count == (0 if failure == "fetch" else 2)
        assert processor.portraits.save.await_count == 2
        assert ("person" in processor.people_done) == recovers
    finally:
        await processor.__aexit__(None, None, None)


@pytest.mark.asyncio
async def test_targeted_membership_query_and_redirect_does_not_forward_key():
    import httpx

    requests = []

    def handler(request):
        requests.append(request)
        if request.url.path.endswith("/Items"):
            assert request.url.params["Ids"] == "1"
            assert request.url.params["Recursive"] == "true"
            assert request.url.params["IncludeItemTypes"] == "Movie,Video"
            return httpx.Response(
                200,
                json={
                    "Items": [{"Id": "1"}]
                    if request.url.params["ParentId"] == "A"
                    else []
                },
            )
        return httpx.Response(
            302, headers={"Location": "https://external.invalid/image"}
        )

    settings = Settings(
        emby={"url": "http://emby", "api_key": "private", "library_ids": ["B", "A"]}
    )
    client = EmbyClient(settings.emby, transport=httpx.MockTransport(handler))
    try:
        assert await client.contains("1")
        assert len(requests) == 2
        with pytest.raises(httpx.HTTPStatusError):
            await client.image("1", "Primary")
        assert len(requests) == 3
        assert all(r.url.host == "emby" for r in requests)
    finally:
        await client.close()


@pytest.mark.asyncio
async def test_backdrop_dedup_compares_content_and_preserves_unknown_images():
    from caress.emby import pixel_digest
    from caress.images import validate_image
    from test_images import image_bytes

    client = EmbyClient(Settings(emby={"url": "http://emby"}).emby)
    red, blue = image_bytes(), image_bytes("blue")
    client.image_info = AsyncMock(
        return_value=[{"ImageType": "Backdrop", "ImageIndex": 0}]
    )
    client.image = AsyncMock(return_value=validate_image(red))
    client.upload = AsyncMock()
    try:
        signatures = await client.backdrop_signatures("1")
        await client.append_backdrop("1", red, signatures, pixel_digest(red))
        await client.append_backdrop("1", blue, signatures, pixel_digest(blue))
        await client.append_backdrop("1", blue, signatures, pixel_digest(blue))
        client.upload.assert_awaited_once_with("1", "Backdrop", blue)
        assert len(signatures) == 2
    finally:
        await client.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", [False, True])
async def test_cancellation_after_write_boundary_waits_for_completion(
    fake_batch, monkeypatch, failure
):
    started, finish = asyncio.Event(), asyncio.Event()

    async def write(*args, **kwargs):
        started.set()
        await finish.wait()
        if failure:
            raise ValueError("写入失败")
        return [], True

    monkeypatch.setattr(FakeProcessor, "write", write)
    fake_batch.batches.start(["1"], "force", None)
    await started.wait()
    fake_batch.batches.start(["2"], "force", None)
    cancelling = asyncio.create_task(fake_batch.batches.cancel())
    await asyncio.sleep(0)
    assert not cancelling.done()
    finish.set()
    await asyncio.wait_for(cancelling, 2)
    code, identity, _ = identify(item())
    record = fake_batch.runtime.record("s", "1", identity, code)
    assert record["status"] == ("failed" if failure else "success")
    if failure:
        assert "部分" in record["error"]
    assert fake_batch.runtime.batch()["status"] == "cancelled"
    assert fake_batch.runtime.batch()["items"][1]["status"] == "cancelled"


@pytest.mark.asyncio
async def test_fresh_database_health_and_settings_round_trip(tmp_path):
    from caress import __version__

    path = tmp_path / "caress.sqlite3"
    app = create_app(Settings(database=path))
    assert not path.exists()
    async with (
        app.router.lifespan_context(app),
        httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://caress"
        ) as client,
    ):
        assert (await client.get("/api/health")).json() == {
            "status": "ok",
            "version": __version__,
        }
        before = (await client.get("/api/settings")).json()
        assert not before["configured"] and not before["emby"]["has_api_key"]
        response = await client.put(
            "/api/settings",
            json={
                "emby": {
                    "url": "http://emby",
                    "api_key": "private",
                    "library_ids": ["A"],
                }
            },
        )
        assert response.status_code == 200
        assert response.json()["configured"] and "private" not in response.text
        assert app.state.service.runtime.batch() is None
        assert (
            app.state.service.runtime.connection.execute(
                "SELECT count(*) FROM scrapes"
            ).fetchone()[0]
            == 0
        )
    assert Settings.load(path).emby.api_key == "private"
    assert path.stat().st_mode & 0o777 == 0o600


@pytest.mark.asyncio
async def test_image_proxy_reuses_validation_and_preserves_mime(tmp_path, monkeypatch):
    from contextlib import asynccontextmanager

    from caress.images import validate_image
    from test_images import image_bytes

    image = validate_image(image_bytes())
    app = create_app(Settings(database=tmp_path / "db"))

    @asynccontextmanager
    async def connection():
        yield SimpleNamespace(
            contains=AsyncMock(return_value=True), image=AsyncMock(return_value=image)
        )

    async with (
        app.router.lifespan_context(app),
        httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://caress"
        ) as client,
    ):
        monkeypatch.setattr(app.state.service, "emby", connection)
        # Revalidating already validated bytes would decode a full image again.
        monkeypatch.setattr(
            "caress.images.validate_image",
            lambda data: pytest.fail("duplicate validation"),
        )
        response = await client.get("/api/images/1/Primary/0")
        assert response.status_code == 200 and response.content == image.content
        assert response.headers["content-type"] == "image/png"


@pytest.mark.asyncio
@pytest.mark.parametrize("mismatch", [False, True])
async def test_uploaded_image_requires_matching_readback(mismatch):
    import base64

    from test_images import image_bytes

    original = image_bytes()
    returned = image_bytes("blue") if mismatch else original

    def handler(request):
        if request.method == "POST":
            assert request.headers["content-type"] == "image/png"
            assert base64.b64decode(request.content) == original
            return httpx.Response(204)
        if request.url.path.endswith("/Images"):
            return httpx.Response(200, json=[{"ImageType": "Primary"}])
        return httpx.Response(200, content=returned)

    client = EmbyClient(
        EmbySettings(url="http://emby"), transport=httpx.MockTransport(handler)
    )
    try:
        if mismatch:
            with pytest.raises(ValueError, match="不一致|保存验证失败"):
                await client.upload("1", "Primary", original)
        else:
            await client.upload("1", "Primary", original)
    finally:
        await client.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("action", ["scrape", "force"])
async def test_batch_settles_skipped_and_processed_groups(
    fake_batch, monkeypatch, action
):
    FakeClient.movies = {str(i): item(str(i)) for i in range(1, 6)}
    code, identity, _ = identify(item())
    for ident in FakeClient.movies:
        fake_batch.runtime.save_record(
            "s", ident, identity, code, "success" if ident == "1" else "failed"
        )

    async def parts(self, movie):
        return [{"Id": "outside"}] if movie["Id"] == "1" else []

    monkeypatch.setattr(FakeClient, "parts", parts)
    release = AsyncMock()
    monkeypatch.setattr(FakeProcessor, "release", release)
    FakeProcessor.fail = {"2"}
    fake_batch.batches.start(list(FakeClient.movies), action, None)
    await fake_batch.batches.task
    assert release.await_count == 2
    release.assert_any_await("ABC-123", None)
    release.assert_any_await(None, None)
    assert "outside" not in FakeProcessor.writes
    assert "1" in FakeProcessor.writes
    assert fake_batch.runtime.batch()["status"] == "finished"
    assert any(row["status"] == "failed" for row in fake_batch.runtime.batch()["items"])


@pytest.mark.asyncio
async def test_queued_cancellation_settles_once_even_when_cancel_repeated(
    fake_batch, monkeypatch
):
    FakeClient.movies = {str(i): item(str(i)) for i in range(1, 6)}
    FakeProcessor.block = asyncio.Event()
    release = AsyncMock()
    monkeypatch.setattr(FakeProcessor, "release", release)
    fake_batch.batches.start(list(FakeClient.movies), "scrape", None)
    for _ in range(100):
        if len(fake_batch.runtime.batch()["current"]) == 4:
            break
        await asyncio.sleep(0.001)
    await asyncio.gather(fake_batch.batches.cancel(), fake_batch.batches.cancel())
    release.assert_awaited_once_with("ABC-123", None)
    assert all(r["status"] == "cancelled" for r in fake_batch.runtime.batch()["items"])


@pytest.mark.asyncio
async def test_changed_code_does_not_escape_original_group_accounting(
    fake_batch, monkeypatch
):
    original = FakeClient.item
    calls = {}

    async def changed(self, ident):
        calls[ident] = calls.get(ident, 0) + 1
        if ident == "1" and calls[ident] > 1:
            return item("1", path="/strm/XYZ-999.strm")
        return await original(self, ident)

    monkeypatch.setattr(FakeClient, "item", changed)
    release = AsyncMock()
    monkeypatch.setattr(FakeProcessor, "release", release)
    fake_batch.batches.start(["1", "2"], "scrape", None)
    await fake_batch.batches.task
    release.assert_awaited_once_with("ABC-123", None)
    assert FakeProcessor.writes == ["2"]
    assert "番号已变化" in fake_batch.runtime.batch()["items"][0]["error"]


@pytest.mark.asyncio
async def test_release_cancels_and_drains_inflight_shared_preparation(tmp_path):
    processor = object.__new__(WorkProcessor)
    processor.prepared = {}
    processor.temporary = tmp_path
    started, finished = asyncio.Event(), asyncio.Event()

    async def prepare(*args):
        started.set()
        try:
            await asyncio.Event().wait()
        finally:
            finished.set()

    processor._prepare = prepare
    waiter = asyncio.create_task(processor.prepare("ABC-123"))
    await started.wait()
    await processor.release("ABC-123", None)
    with pytest.raises(asyncio.CancelledError):
        await waiter
    assert finished.is_set() and not processor.prepared


@pytest.mark.asyncio
async def test_image_scope_allows_only_selected_movies_and_confirmed_people(
    tmp_path, monkeypatch
):
    from contextlib import asynccontextmanager

    from caress.images import validate_image
    from test_images import image_bytes

    upstream = SimpleNamespace(
        contains=AsyncMock(side_effect=lambda ident: ident == "movie"),
        contains_person=AsyncMock(side_effect=lambda ident: ident == "person"),
        image=AsyncMock(return_value=validate_image(image_bytes())),
    )

    @asynccontextmanager
    async def connection():
        yield upstream

    app = create_app(Settings(database=tmp_path / "db"))
    async with (
        app.router.lifespan_context(app),
        httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://caress"
        ) as client,
    ):
        monkeypatch.setattr(app.state.service, "emby", connection)
        for ident, kind, expected in [
            ("movie", "Primary", 200),
            ("movie", "Backdrop", 200),
            ("person", "Primary", 200),
            ("person", "Backdrop", 404),
            ("outside", "Primary", 404),
        ]:
            response = await client.get(f"/api/images/{ident}/{kind}/0")
            assert response.status_code == expected
        assert upstream.image.await_count == 3


@pytest.mark.asyncio
async def test_person_scope_checks_returned_identity_not_only_query_filter():
    def handler(request):
        assert request.url.params["PersonIds"] == "person"
        assert request.url.params["ParentId"] == "A"
        return httpx.Response(200, json={"Items": [{"People": [{"Id": "other"}]}]})

    client = EmbyClient(
        EmbySettings(url="http://emby", library_ids=["A"]),
        transport=httpx.MockTransport(handler),
    )
    try:
        assert not await client.contains_person("person")
    finally:
        await client.close()


@pytest.fixture
async def preparing(tmp_path):
    from caress.models import (
        ArtworkCandidate,
        Category,
        Classification,
        SourceId,
        Status,
    )

    folder = tmp_path / "images"
    folder.mkdir()
    processor = WorkProcessor(Settings(database=tmp_path / "db"), None, folder)
    metadata = SourceMetadata(
        code="ABC-123",
        title="title",
        artwork=[
            ArtworkCandidate(
                url="https://source/cover",
                source=SourceId.DMM,
                role="cover",
                referer="https://source",
            ),
            ArtworkCandidate(
                url="https://source/cover",
                source=SourceId.DMM,
                role="sample",
                referer="https://source",
            ),
        ],
    )
    processor.scraper.scrape = AsyncMock(
        return_value=SimpleNamespace(
            status=Status.FOUND,
            metadata=metadata,
            selected_source=SourceId.DMM,
            warnings=['JavDB API 返回失败：{"success": 0, "message": "請登錄帳號"}'],
            classification=Classification(category=Category.CENSORED),
            results=[SimpleNamespace(source=SourceId.DMM, actor_ids={})],
        )
    )
    processor.scraper.resolve_actors = AsyncMock(return_value=[])
    processor.translator.metadata = AsyncMock(return_value=SimpleNamespace(errors=[]))
    try:
        yield processor, folder
    finally:
        await processor.finish()
        await processor.__aexit__(None, None, None)


@pytest.mark.asyncio
async def test_cancelled_preparation_removes_partial_image_directory(
    preparing, monkeypatch
):
    processor, folder = preparing
    started = asyncio.Event()

    async def fetch(*args):
        started.set()
        await asyncio.Event().wait()

    monkeypatch.setattr("caress.processing.fetch_artwork", fetch)
    waiter = asyncio.create_task(processor.prepare("ABC-123"))
    try:
        await started.wait()
        assert list(folder.iterdir())
        await processor.release("ABC-123", None)
        with pytest.raises(asyncio.CancelledError):
            await waiter
        assert not list(folder.iterdir())
    finally:
        await processor.finish()


@pytest.mark.asyncio
async def test_shared_original_is_downloaded_once_and_primary_is_cropped_only_on_demand(
    preparing, monkeypatch
):
    from unittest.mock import Mock

    from caress.emby import pixel_digest
    from caress.images import crop_poster
    from test_images import image_bytes

    processor, folder = preparing
    fetch = AsyncMock(return_value=image_bytes())
    crop = Mock(wraps=crop_poster)
    monkeypatch.setattr("caress.processing.fetch_artwork", fetch)
    monkeypatch.setattr("caress.processing.crop_poster", crop)
    prepared = await processor.prepare("ABC-123")
    assert processor.scraper.scrape.return_value.warnings[0] in prepared.warnings
    fetch.assert_awaited_once()
    crop.assert_not_called()
    client = SimpleNamespace(
        write_metadata=AsyncMock(return_value=({"People": []}, False)),
        image_info=AsyncMock(
            side_effect=lambda ident: (
                [{"ImageType": "Primary"}, {"ImageType": "Backdrop"}]
                if ident == "A"
                else [{"ImageType": "Backdrop"}]
            )
        ),
        backdrop_signatures=AsyncMock(return_value={pixel_digest(image_bytes())}),
        append_backdrop=AsyncMock(return_value=False),
        upload=AsyncMock(),
    )
    await processor.write(client, "A", prepared, [], replace=False)
    crop.assert_not_called()
    await asyncio.gather(
        *(
            processor.write(client, ident, prepared, [], replace=False)
            for ident in ["B", "C"]
        )
    )
    assert crop.call_count == 1
    assert {call.args[0] for call in client.upload.await_args_list} == {"B", "C"}
    await processor.release("ABC-123", None)
    assert not list(folder.iterdir())


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "candidates,expected_primary,expected_backdrops",
    [
        ([("poster", (90, 120))], True, []),
        ([("cover", (120, 90))], True, [(120, 90)]),
        ([("cover", (90, 120))], True, []),
        ([("sample", (120, 90)), ("sample", (90, 120))], False, [(120, 90)]),
    ],
)
async def test_only_landscape_artwork_is_uploaded_as_backdrop(
    preparing, monkeypatch, candidates, expected_primary, expected_backdrops
):
    from io import BytesIO

    from PIL import Image

    from caress.models import ArtworkCandidate, SourceId
    from test_images import image_bytes

    processor, _ = preparing
    metadata = processor.scraper.scrape.return_value.metadata
    metadata.artwork = [
        ArtworkCandidate(url=f"https://source/{i}", source=SourceId.DMM,
                         role=role, referer="https://source")
        for i, (role, _) in enumerate(candidates)
    ]
    async def fetch(client, candidate):
        return image_bytes(size=candidates[int(candidate.url.rsplit("/", 1)[1])][1])

    monkeypatch.setattr("caress.processing.fetch_artwork", fetch)
    monkeypatch.setattr("caress.processing.crop_poster", lambda data: {"content": image_bytes(size=(60, 90))})
    prepared = await processor.prepare("ABC-123")
    client = SimpleNamespace(
        write_metadata=AsyncMock(return_value=({"People": []}, False)),
        image_info=AsyncMock(return_value=[]),
        backdrop_signatures=AsyncMock(return_value=set()),
        append_backdrop=AsyncMock(return_value=True),
        upload=AsyncMock(),
    )
    await processor.write(client, "1", prepared, [], replace=False)
    assert bool(client.upload.await_count) == expected_primary
    assert [Image.open(BytesIO(call.args[1])).size for call in client.append_backdrop.await_args_list] == expected_backdrops


@pytest.mark.asyncio
@pytest.mark.parametrize("size,backdrop", [((90, 120), True), ((120, 90), False)])
async def test_backdrop_orientation_uses_exif(preparing, monkeypatch, size, backdrop):
    from io import BytesIO

    from PIL import Image

    processor, _ = preparing
    metadata = processor.scraper.scrape.return_value.metadata
    metadata.artwork = metadata.artwork[:1]
    output = BytesIO()
    exif = Image.Exif()
    exif[274] = 6
    Image.new("RGB", size, "red").save(output, format="JPEG", exif=exif)
    monkeypatch.setattr("caress.processing.fetch_artwork", AsyncMock(return_value=output.getvalue()))
    prepared = await processor.prepare("ABC-123")
    assert any(kind == "Primary" for kind, _ in prepared.images)
    assert any(kind == "Backdrop" for kind, _ in prepared.images) is backdrop


@pytest.mark.asyncio
async def test_finish_drains_other_groups_when_directory_cleanup_fails(
    tmp_path, monkeypatch
):
    import hashlib
    import shutil

    processor = object.__new__(WorkProcessor)
    processor.temporary = tmp_path
    processor.prepared = {}
    folders = []

    async def ready():
        return SimpleNamespace(source="dmm")

    for code in ["ABC-123", "XYZ-999"]:
        folder = tmp_path / hashlib.sha256((code + "dmm").encode()).hexdigest()
        folder.mkdir()
        folders.append(folder)
        processor.prepared[(code, None)] = asyncio.create_task(ready())
    await asyncio.gather(*processor.prepared.values())
    original = shutil.rmtree

    def cleanup(path, *args, **kwargs):
        if path == folders[0]:
            raise OSError("cleanup failed")
        return original(path, *args, **kwargs)

    monkeypatch.setattr(shutil, "rmtree", cleanup)
    with pytest.raises(OSError, match="cleanup failed"):
        await processor.finish()
    assert not processor.prepared and not folders[1].exists()


@pytest.mark.asyncio
@pytest.mark.parametrize("cancel", [False, True])
@pytest.mark.parametrize("queued_id", ["1", "2"])
async def test_running_batch_accepts_deduplicated_jobs_with_independent_options(
    fake_batch, monkeypatch, cancel, queued_id
):
    entered = asyncio.Event()
    FakeProcessor.block = asyncio.Event()
    prepared, written, scans = [], [], []
    original_prepare, original_write = FakeProcessor.prepare, FakeProcessor.write
    original_items = FakeClient.items

    async def items(self):
        scans.append(True)
        async for movie in original_items(self):
            yield movie

    async def prepare(self, code, source):
        prepared.append(source)
        entered.set()
        return await original_prepare(self, code, source)

    async def write(self, client, ident, *args, replace):
        written.append((ident, replace))
        return await original_write(self, client, ident, *args, replace=replace)

    monkeypatch.setattr(FakeProcessor, "prepare", prepare)
    monkeypatch.setattr(FakeProcessor, "write", write)
    monkeypatch.setattr(FakeClient, "items", items)
    runner = fake_batch.batches
    first = runner.start(["1"], "scrape", None)
    await asyncio.wait_for(entered.wait(), 2)
    second = runner.start([queued_id], "force", "javdb")
    runner.start([queued_id], "force", "javdb")
    runner.start(["1"], "scrape", None)
    assert first["id"] == second["id"] and len(second["items"]) == 2
    assert [row["status"] for row in second["items"]] == ["running", "pending"]
    if cancel:
        await runner.cancel()
        assert not written and not runner.queue
        assert all(
            row["status"] == "cancelled" for row in fake_batch.runtime.batch()["items"]
        )
    else:
        FakeProcessor.block.set()
        await runner.task
        assert prepared == [None, "javdb"]
        assert written == [("1", False), (queued_id, True)]
        assert len(scans) == 1
        assert all(
            row["status"] == "success" for row in fake_batch.runtime.batch()["items"]
        )
        assert fake_batch.runtime.batch()["status"] == "finished"


@pytest.mark.asyncio
async def test_failed_job_does_not_discard_later_submissions(fake_batch):
    FakeProcessor.fail = {"1"}
    runner = fake_batch.batches
    runner.start(["1"], "scrape", None)
    runner.start(["2"], "force", "javdb")
    await runner.task
    rows = fake_batch.runtime.batch()["items"]
    assert [(row["id"], row["status"]) for row in rows] == [
        ("1", "failed"),
        ("2", "success"),
    ]


@pytest.mark.asyncio
async def test_related_item_merges_matching_queued_submission(fake_batch, monkeypatch):
    async def parts(self, parent):
        return [item("2")] if parent["Id"] == "1" else []

    monkeypatch.setattr(FakeClient, "parts", parts)
    runner = fake_batch.batches
    runner.start(["1"], "scrape", None)
    runner.start(["2"], "scrape", None)
    await runner.task
    assert sorted(FakeProcessor.writes) == ["1", "2"]
    rows = fake_batch.runtime.batch()["items"]
    assert [row["status"] for row in rows].count("success") == 2
    assert all(row["action"] == "scrape" and row["source"] is None for row in rows)
    assert any(row.get("note") == "已合并到关联作品" for row in rows)


def test_emby_failure_returns_json_and_logs_operation_without_key(caplog):
    from caress.emby import sync_error

    request = httpx.Request(
        "POST",
        "http://emby/emby/Items/1429/Images/Primary?api_key=hidden-query",
        headers={"X-Emby-Token": "secret-key"},
    )
    response = httpx.Response(
        500,
        request=request,
        json={
            "ResponseStatus": {
                "ErrorCode": "IOException",
                "Message": "Read-only file system",
                "code": "example",
            }
        },
    )
    with pytest.raises(httpx.HTTPStatusError) as error:
        response.raise_for_status()
    message = sync_error(error.value)
    assert "POST /emby/Items/1429/Images/Primary" in caplog.text
    assert json.loads(message)["ResponseStatus"]["Message"] == "Read-only file system"
    assert "Emby request failed" in caplog.text and "HTTP 500" in caplog.text
    assert "secret-key" not in caplog.text + message
    assert "hidden-query" not in caplog.text + message


def test_emby_error_log_does_not_expose_html_or_response(caplog):
    from caress.emby import sync_error

    request = httpx.Request("GET", "http://emby/Items")
    for content in ('<html>private page</html>', '{"password":"secret-value"}'):
        response = httpx.Response(500, request=request, text=content)
        with pytest.raises(httpx.HTTPStatusError) as error:
            response.raise_for_status()
        sync_error(error.value)
    assert "private page" not in caplog.text
    assert "secret-value" not in caplog.text


def test_emby_http_and_connection_errors_use_chinese_fallbacks():
    from caress.emby import sync_error

    request = httpx.Request("GET", "http://emby/Items")
    for status, expected in [(401, "认证失败或权限不足"), (500, "Emby 请求失败")]:
        response = httpx.Response(status, request=request, text="<html>error</html>")
        with pytest.raises(httpx.HTTPStatusError) as error:
            response.raise_for_status()
        assert expected in sync_error(error.value)
        assert f"HTTP {status}" in sync_error(error.value)
    assert sync_error(httpx.ReadTimeout("timeout", request=request)) == "Emby 请求超时"
    assert "无法连接 Emby" in sync_error(httpx.ConnectError("offline", request=request))
