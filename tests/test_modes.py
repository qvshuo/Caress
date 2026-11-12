"""Exercise final Emby DTOs and image bytes through the real write pipeline."""

import asyncio
import base64
import hashlib
import json
from copy import deepcopy
from datetime import date
from types import SimpleNamespace

import httpx
import pytest
from test_workflow import item

from caress.emby import EmbyClient, metadata_update, needs_enrichment, pixel_digest
from caress.library import filter_works, identify
from caress.models import Actor, SourceMetadata
from caress.processing import Prepared, WorkProcessor
from caress.service import Service
from caress.settings import Settings
from test_images import image_bytes


def complete_item(ident="1"):
    return item(
        ident,
        ServerId="s",
        OriginalTitle="original",
        Overview="manual plot",
        PremiereDate="2020-01-02T00:00:00Z",
        ProductionYear=2020,
        Studios=[{"Name": "manual studio", "Id": "studio"}],
        GenreItems=[{"Name": "manual genre"}],
        TagItems=[{"Name": "user tag"}],
        People=[
            {"Name": "Actor", "Type": "Actor", "Id": "p", "PrimaryImageTag": "tag"},
            {"Name": "Director", "Type": "Director"},
            {"Name": "Writer", "Type": "Writer"},
        ],
        ProviderIds={"caress": "ABC-123", "tmdb": "user"},
        LockData=True,
        LockedFields=["Name"],
        UserData={"Played": True, "PlaybackPositionTicks": 42, "IsFavorite": True},
    )


@pytest.fixture
async def flow(tmp_path, monkeypatch):
    state = SimpleNamespace(
        items={"1": complete_item()},
        images={
            "1": {
                "Primary": [image_bytes("red", size=(60, 90))],
                "Backdrop": [image_bytes("blue")],
            }
        },
        posts=[],
        prepared=0,
        during=None,
        fail_image=False,
        block_image=None,
        write_started=asyncio.Event(),
    )
    state.metadata = SourceMetadata(
        code="ABC-123",
        title="source title",
        original_title="source original",
        plot="source plot",
        year=2024,
        release_date=date(2024, 2, 3),
        studio="source studio",
        actors=[Actor(name="New Actor")],
        directors=["New Director"],
        genres=["source genre"],
        tags=["source tag"],
    )
    state.errors = []
    state.mappings = []
    state.artwork = [("Primary", image_bytes("red")), ("Backdrop", image_bytes("blue"))]

    async def handler(request):
        path = request.url.path.removeprefix("/emby/")
        parts = path.split("/")
        if path == "System/Info":
            return httpx.Response(
                200, json={"Id": "s", "ServerName": "Test", "Version": "test"}
            )
        if path == "Users":
            return httpx.Response(
                200, json=[{"Id": "u", "Policy": {"IsAdministrator": True}}]
            )
        if path == "Items":
            ids = request.url.params.get("Ids")
            rows = [v for k, v in state.items.items() if not ids or k == ids]
            return httpx.Response(
                200, json={"Items": rows, "TotalRecordCount": len(rows)}
            )
        if path.startswith("Users/u/Items/"):
            return httpx.Response(200, json=state.items[parts[-1]])
        ident = parts[1]
        if len(parts) == 2 and request.method == "POST":
            state.posts.append(path)
            state.items[ident] = json.loads(request.content)
            state.write_started.set()
            return httpx.Response(204)
        images = state.images.setdefault(ident, {})
        if len(parts) == 3:
            return httpx.Response(
                200,
                json=[
                    {"ImageType": kind, "ImageIndex": index}
                    for kind, values in images.items()
                    for index, _ in enumerate(values)
                ],
            )
        kind = parts[3]
        if request.method == "POST":
            if state.block_image:
                await state.block_image.wait()
            if state.fail_image:
                return httpx.Response(503)
            state.posts.append(path)
            data = base64.b64decode(request.content)
            if kind == "Primary":
                images[kind] = [data]
            else:
                images.setdefault(kind, []).append(data)
            return httpx.Response(204)
        try:
            data = images[kind][int(parts[4])]
        except KeyError, IndexError:
            return httpx.Response(404)
        return httpx.Response(200, content=data, headers={"Content-Type": "image/png"})

    def client(settings):
        return EmbyClient(settings, transport=httpx.MockTransport(handler))

    monkeypatch.setattr("caress.service.EmbyClient", client)

    async def prepare(self, code, source):
        state.prepared += 1
        if state.during:
            state.during()
        folder = self.temporary / hashlib.sha256((code + "dmm").encode()).hexdigest()
        folder.mkdir()
        images = []
        digests = {}
        for i, (kind, data) in enumerate(state.artwork):
            path = folder / str(i)
            path.write_bytes(data)
            images.append((kind, path))
            digests[path] = pixel_digest(data)
        return Prepared(
            state.metadata.model_copy(deep=True),
            "dmm",
            images=images,
            digests=digests,
            errors=state.errors,
            mappings=state.mappings,
        )

    monkeypatch.setattr(WorkProcessor, "_prepare", prepare)
    service = Service(
        Settings(
            database=tmp_path / "db",
            emby={"url": "http://emby", "api_key": "secret", "library_ids": ["A"]},
        )
    )
    state.service = service

    async def run(action="scrape", ids=None):
        service.batches.start(ids or ["1"], action, None)
        await service.batches.task
        return service.runtime.batch()["items"]

    state.run = run
    try:
        yield state
    finally:
        await service.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("replace", [False, True])
async def test_final_dto_fill_or_replace_protects_user_data(flow, replace):
    flow.items["1"]["ProviderIds"].pop("caress")
    original = deepcopy(flow.items["1"])
    flow.items["1"]["Overview"] = "  "
    rows = await flow.run("force" if replace else "scrape")
    final = flow.items["1"]
    assert rows[0]["status"] == "success" and final["Overview"] == "source plot"
    assert final["Name"] == ("source title" if replace else original["Name"])
    assert final["People"] == (
        [
            original["People"][2],
            {"Name": "New Actor", "Type": "Actor"},
            {"Name": "New Director", "Type": "Director"},
        ]
        if replace
        else original["People"]
    )
    for key in [
        "Studios",
        "GenreItems",
        "OriginalTitle",
        "PremiereDate",
        "ProductionYear",
    ]:
        expected = {
            "Studios": [{"Name": "source studio"}],
            "GenreItems": [{"Name": "source genre"}],
            "OriginalTitle": "source original",
            "PremiereDate": "2024-02-03T00:00:00Z",
            "ProductionYear": 2024,
        }
        assert final[key] == (expected[key] if replace else original[key])
    assert final["TagItems"] == (
        [{"Name": "user tag"}, {"Name": "source tag"}]
        if replace
        else original["TagItems"]
    )
    for key in ["UserData", "LockData", "LockedFields", "Path", "DateCreated", "Id"]:
        assert final[key] == original[key]
    assert final["ProviderIds"] == {**original["ProviderIds"], "caress": "ABC-123"}
    assert len(flow.images["1"]["Backdrop"]) == 1
    assert not any("/Images/" in path for path in flow.posts)


@pytest.mark.asyncio
@pytest.mark.parametrize("name,path", [
    ("ABC-123", "/strm/ABC-123.strm"),
    ("ABC-123", "/strm/ABC-123-UC.strm"),
    ("ABC-123-cd1", "/strm/ABC-123-cd1.strm"),
])
async def test_scrape_replaces_filename_placeholder_even_after_success(flow, name, path):
    flow.items["1"].update(Name=name, Path=path, LockData=False, LockedFields=[])
    code, identity, _ = identify(flow.items["1"])
    flow.service.runtime.save_record("s", "1", identity, code, "success")
    rows = await flow.run()
    assert rows[0]["status"] == "success"
    assert flow.items["1"]["Name"] == "source title"
    assert flow.items["1"]["OriginalTitle"] == "original"
    assert flow.items["1"]["ProviderIds"]["tmdb"] == "user"


@pytest.mark.asyncio
@pytest.mark.parametrize("locks", [
    {"LockData": True, "LockedFields": []},
    {"LockData": False, "LockedFields": ["Name"]},
])
async def test_scrape_fills_filename_title_regardless_of_emby_locks(flow, locks):
    flow.items["1"].update(Name="ABC-123", Overview="", **locks)
    assert (await flow.run())[0]["status"] == "success"
    assert flow.items["1"]["Name"] == "source title"
    assert flow.items["1"]["Overview"] == "source plot"
    assert all(flow.items["1"][key] == value for key, value in locks.items())


@pytest.mark.asyncio
async def test_manual_title_changed_during_fetch_is_preserved(flow):
    flow.items["1"].update(Name="ABC-123", LockData=False, LockedFields=[])
    flow.during = lambda: flow.items["1"].update(Name="人工修改的标题")
    assert (await flow.run())[0]["status"] == "skipped"
    assert flow.items["1"]["Name"] == "人工修改的标题"
    assert not flow.posts


@pytest.mark.asyncio
async def test_complete_item_skips_source_and_history_does_not_block_missing_cover(
    flow,
    monkeypatch,
):
    from unittest.mock import AsyncMock

    flow.service.settings.translation.model = "model"
    flow.service.settings.translation.api_key = "secret"
    verify = AsyncMock(return_value=True)
    monkeypatch.setattr("caress.translation.Translator.verify", verify)
    code, identity, _ = identify(flow.items["1"])
    runtime = flow.service.runtime
    runtime.save_record("s", "1", identity, code, "success")
    old = runtime.record("s", "1", identity, code)
    assert (await flow.run())[0]["status"] == "skipped" and flow.prepared == 0
    verify.assert_not_awaited()
    assert runtime.record("s", "1", identity, code) == old
    flow.images["1"].pop("Primary")
    assert (await flow.run())[0]["status"] == "success" and flow.prepared == 1
    assert flow.posts == ["Items/1/Images/Primary"]
    assert flow.images["1"]["Primary"] == [image_bytes("red", size=(60, 90))]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "action,previous",
    [
        ("scrape", None),
        ("scrape", "success"),
        ("scrape", "failed"),
        ("scrape", "cancelled"),
        ("force", None),
    ],
)
async def test_no_source_values_skip_without_inventing_success(flow, action, previous):
    if action == "scrape":
        flow.items["1"]["Overview"] = ""
    original = deepcopy(flow.items["1"])
    code, identity, _ = identify(flow.items["1"])
    runtime = flow.service.runtime
    if previous:
        runtime.save_record("s", "1", identity, code, previous)
    old = runtime.record("s", "1", identity, code)
    flow.metadata = SourceMetadata(code="ABC-123", title=" \t")
    flow.artwork = []
    assert (await flow.run(action))[0]["status"] == "skipped"
    assert flow.items["1"] == original
    assert not flow.posts and runtime.record("s", "1", identity, code) == old


@pytest.mark.asyncio
async def test_manual_edit_during_fetch_is_preserved_and_operation_skips(flow):
    flow.items["1"]["Overview"] = ""

    def edited():
        flow.items["1"].update(Name="new manual title", Overview="new manual plot")

    flow.during = edited
    assert (await flow.run())[0]["status"] == "skipped"
    assert (
        flow.items["1"]["Name"] == "new manual title"
        and flow.items["1"]["Overview"] == "new manual plot"
    )
    assert not flow.posts


@pytest.mark.asyncio
async def test_identical_force_is_noop_and_new_images_append_once(flow):
    original = flow.items["1"]
    flow.metadata = SourceMetadata(
        code="ABC-123",
        title=original["Name"],
        original_title=original["OriginalTitle"],
        plot=original["Overview"],
        year=2020,
        release_date=date(2020, 1, 2),
        studio="manual studio",
        actors=[Actor(name="Actor")],
        directors=["Director"],
        genres=["manual genre"],
        tags=["user tag"],
    )
    assert (await flow.run("force"))[0]["status"] == "skipped" and not flow.posts
    flow.artwork = [
        ("Primary", image_bytes("green")),
        ("Backdrop", image_bytes("yellow")),
        ("Backdrop", image_bytes("yellow")),
    ]
    assert (await flow.run("force"))[0]["status"] == "success"
    assert len(flow.images["1"]["Backdrop"]) == 2 and flow.images["1"]["Backdrop"][
        0
    ] == image_bytes("blue")
    assert flow.images["1"]["Primary"] == [image_bytes("green", size=(60, 90))]


@pytest.mark.asyncio
async def test_partial_write_can_be_completed_by_scrape(flow):
    flow.items["1"]["Overview"] = ""
    flow.images["1"].pop("Primary")
    code, identity, _ = identify(flow.items["1"])
    flow.service.runtime.save_record("s", "1", identity, code, "success")
    flow.fail_image = True
    rows = await flow.run()
    flow.fail_image = False
    assert rows[0]["status"] == "failed" and "部分" in rows[0]["error"]
    assert flow.service.runtime.record("s", "1", identity, code)["succeeded_at"]
    assert (
        flow.items["1"]["Overview"] == "source plot"
        and "Primary" not in flow.images["1"]
    )
    from caress.library import project

    assert (
        project(flow.items["1"], flow.service.runtime, "s")["status"]
        == "rescrape_failed"
    )
    assert (await flow.run())[0]["status"] == "success"
    assert flow.posts.count("Items/1") == 1 and flow.images["1"]["Primary"]


@pytest.mark.asyncio
async def test_cancel_finishes_image_upload_and_readback(flow):
    flow.items["1"]["Overview"] = ""
    flow.images["1"].pop("Primary")
    flow.block_image = asyncio.Event()
    flow.service.batches.start(["1"], "scrape", None)
    await asyncio.wait_for(flow.write_started.wait(), 2)
    cancelling = asyncio.create_task(flow.service.batches.cancel())
    await asyncio.sleep(0)
    assert not cancelling.done()
    flow.block_image.set()
    await asyncio.wait_for(cancelling, 2)
    batch = flow.service.runtime.batch()
    assert batch["status"] == "cancelled"
    assert batch["items"][0]["status"] == "success"
    assert flow.items["1"]["Overview"] == "source plot"
    assert flow.images["1"]["Primary"]


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", [False, True])
async def test_same_code_items_with_different_missing_fields_and_posters(flow, failure):
    flow.items["1"]["Overview"] = ""
    flow.items["2"] = deepcopy(flow.items["1"])
    flow.items["2"]["Id"] = "2"
    flow.images["2"] = deepcopy(flow.images["1"])
    flow.images["2"].pop("Primary")
    flow.items["2"]["Overview"] = "keep this plot"
    flow.items["2"]["Studios"] = []
    flow.fail_image = failure
    rows = await flow.run(ids=["1", "2"])
    assert [row["status"] for row in rows] == [
        "success",
        "failed" if failure else "success",
    ]
    assert flow.prepared == 1 and flow.items["1"]["Overview"] == "source plot"
    assert flow.items["2"]["Overview"] == "keep this plot" and flow.items["2"][
        "Studios"
    ] == [{"Name": "source studio"}]
    assert flow.images["1"]["Primary"] == [image_bytes("red", size=(60, 90))]
    if failure:
        assert "Primary" not in flow.images["2"]
    else:
        assert flow.images["2"]["Primary"] == flow.images["1"]["Primary"]
        assert [p for p in flow.posts if "/Images/Primary" in p] == [
            "Items/2/Images/Primary"
        ]


def test_semantic_missing_fields_and_year_does_not_replace_date():
    original = complete_item()
    original.update(
        Overview=" \n",
        ProductionYear=0,
        PremiereDate="invalid",
        Studios=[{"Name": " "}],
    )
    metadata = SourceMetadata(code="ABC-123", title=" ", year=2025, studio="new")
    update = metadata_update(original, metadata, [], replace=False)
    assert (
        update["ProductionYear"] == 2025
        and update["Studios"] == [{"Name": "new"}]
        and "Name" not in update
    )
    original["PremiereDate"] = "2020-01-02T00:00:00Z"
    assert "PremiereDate" not in metadata_update(original, metadata, [], replace=True)
    assert needs_enrichment(
        original, [{"ImageType": "Primary"}, {"ImageType": "Backdrop"}], []
    )


def test_status_filter_groups_do_not_lose_internal_results():
    rows = [
        {"id": s, "code": "ABC-123", "title": "title", "status": s}
        for s in ["pending", "cancelled", "failed", "rescrape_failed", "success"]
    ]
    assert [r["status"] for r in filter_works(rows, status="pending")] == [
        "pending",
        "cancelled",
    ]
    assert [r["status"] for r in filter_works(rows, status="failed")] == [
        "failed",
        "rescrape_failed",
    ]


@pytest.mark.asyncio
@pytest.mark.parametrize("replace", [False, True])
async def test_failed_poster_is_only_fatal_when_that_poster_is_requested(flow, replace):
    flow.items["1"]["Overview"] = ""
    flow.artwork = []
    flow.errors = [("Primary", "poster: crop failed")]
    rows = await flow.run("force" if replace else "scrape")
    assert rows[0]["status"] == ("failed" if replace else "success")
    assert flow.items["1"]["Overview"] == "source plot"
    assert flow.images["1"]["Primary"] == [image_bytes("red", size=(60, 90))]
    if not replace:
        assert "poster: crop failed" in rows[0]["warnings"]


def test_fill_merges_only_filename_tags_into_nonempty_user_tags():
    original = complete_item()
    _, _, media = identify(item(path="/strm/ABC-123-UC-cd2.strm"))
    changes = metadata_update(
        original,
        SourceMetadata(code="ABC-123", title="source", tags=["source tag"]),
        media,
        replace=False,
    )
    assert changes["TagItems"] == [
        {"Name": "user tag"},
        {"Name": "中文字幕"},
        {"Name": "无码破解"},
    ]


@pytest.mark.asyncio
@pytest.mark.parametrize("replace", [False, True])
@pytest.mark.parametrize(
    "existing,valid", [(True, False), (False, False), (False, True)]
)
async def test_optional_backdrop_failure_is_nonfatal(
    flow, replace, existing, valid
):
    flow.items["1"]["Overview"] = ""
    if not existing:
        flow.images["1"].pop("Backdrop")
    flow.artwork = [("Backdrop", image_bytes("yellow"))] if valid else []
    flow.errors = [("Backdrop", "sample unavailable")]
    rows = await flow.run("force" if replace else "scrape")
    assert flow.items["1"]["Overview"] == "source plot"
    assert rows[0]["status"] == "success" and "sample unavailable" in rows[0]["warnings"]
    if existing or valid:
        assert flow.images["1"]["Backdrop"]
    else:
        assert "Backdrop" not in flow.images["1"]


@pytest.mark.asyncio
async def test_no_backdrop_candidate_is_not_a_failure(flow):
    flow.items["1"]["Overview"] = ""
    flow.images["1"].pop("Backdrop")
    flow.artwork = []
    flow.errors = []

    rows = await flow.run("scrape")

    assert rows[0]["status"] == "success"
    assert flow.items["1"]["Overview"] == "source plot"
    assert "Backdrop" not in flow.images["1"]
    assert not rows[0].get("warnings")


@pytest.mark.asyncio
async def test_portraits_only_fetch_missing_actor_once_per_batch_and_retry_on_next_run(
    flow, monkeypatch
):
    people = [
        {"Id": name, "Name": name, "Type": "Actor", "PrimaryImageTag": "tag"}
        for name in ["A", "B"]
    ] + [{"Id": "C", "Name": "C", "Type": "Actor"}]
    flow.items["1"]["People"] = people
    flow.metadata.actors = [Actor(name=name) for name in ["A", "B", "C"]]
    flow.items["1"]["Overview"] = ""
    flow.items["2"] = deepcopy(flow.items["1"])
    flow.items["2"]["Id"] = "2"
    flow.images["2"] = deepcopy(flow.images["1"])
    for name in ["A", "B"]:
        flow.images[name] = {"Primary": [image_bytes("red")]}
    original = deepcopy({name: flow.images[name] for name in ["A", "B"]})
    flow.mappings = [
        {"name": name, "actor_id": "verified-" + name} for name in ["A", "B", "C"]
    ]
    calls = []

    async def save(self, actors, *, kind=None):
        calls.extend(actor.name for actor in actors)
        if len(calls) == 1:
            return [{"status": "not_found"}]
        folder = self.output / "actors"
        folder.mkdir(exist_ok=True)
        (folder / "C.png").write_bytes(image_bytes("blue"))
        return [{"status": "saved"}]

    monkeypatch.setattr("caress.portraits.Gfriends.save", save)
    await flow.run(ids=["1", "2"])
    assert calls == ["C"] and not flow.images.get("C")
    await flow.run(ids=["1", "2"])
    assert calls == ["C", "C"] and flow.images["C"]["Primary"] == [image_bytes("blue")]
    assert {name: flow.images[name] for name in ["A", "B"]} == original


@pytest.mark.asyncio
async def test_uncroppable_required_primary_preserves_old_poster_and_reports_partial_write(
    flow,
):
    flow.items["1"]["Overview"] = ""
    flow.artwork = [("Primary", image_bytes(size=(1, 1)))]
    rows = await flow.run("force")
    assert rows[0]["status"] == "failed" and "Primary" in rows[0]["error"]
    assert flow.items["1"]["Overview"] == "source plot"
    assert flow.images["1"]["Primary"] == [image_bytes("red", size=(60, 90))]


@pytest.mark.asyncio
async def test_failed_scrape_retries_missing_backdrop_without_overwriting(flow, monkeypatch):
    flow.items['1']['Overview'] = ''
    flow.artwork = [('Backdrop', image_bytes('yellow')), ('Backdrop', image_bytes('green'))]
    original_primary = flow.images['1']['Primary'][:]
    append = EmbyClient.append_backdrop

    async def fail_second(client, ident, data, signatures, digest=None):
        if len(flow.images[ident]['Backdrop']) >= 2:
            raise ValueError('upload failed')
        return await append(client, ident, data, signatures, digest)

    with monkeypatch.context() as patch:
        patch.setattr(EmbyClient, 'append_backdrop', fail_second)
        assert (await flow.run())[0]['status'] == 'failed'
    assert len(flow.images['1']['Backdrop']) == 2
    assert (await flow.run())[0]['status'] == 'success'
    assert len(flow.images['1']['Backdrop']) == 3
    assert flow.images['1']['Primary'] == original_primary
    assert flow.items['1']['Overview'] == 'source plot'
    assert flow.prepared == 2
    assert (await flow.run())[0]['status'] == 'skipped'
    assert flow.prepared == 2


@pytest.mark.asyncio
async def test_failed_scrape_without_changes_preserves_failure(flow):
    code, identity, _ = identify(flow.items['1'])
    flow.service.runtime.save_record('s', '1', identity, code, 'failed', error='previous failure')
    assert (await flow.run())[0]['status'] == 'skipped'
    assert flow.prepared == 1
    record = flow.service.runtime.record('s', '1', identity, code)
    assert record['status'] == 'failed' and record['error'] == 'previous failure'
