import asyncio
import json

import pytest

from caress.catalog import CatalogRules
from caress.models import (
    Category,
    ScrapeRequest,
    SourceId,
    SourceMetadata,
    SourceResult,
    Status,
)
from caress.scraper import Scraper
from caress.settings import Settings
from caress.sources.base import Source


def test_http_json_error_keeps_source_details(caplog):
    from caress.http import Page, response_issue

    page = Page("https://example.invalid/api", 403, b'{"error":"denied","token":"secret"}')
    status, message = response_issue(page)
    assert status == Status.BLOCKED
    assert json.loads(message) == {"error": "denied", "token": "secret"}
    assert not caplog.records
    assert response_issue(Page("https://example.invalid", 404, b'{}'))[0] == Status.NOT_FOUND


@pytest.mark.asyncio
async def test_dmm_graphql_error_retains_original_response():
    from caress.http import FetchError, Page
    from caress.sources.dmm import DMM

    class Client:
        async def post_json(self, *args, **kwargs):
            return Page("https://example.invalid", 200, b'{"errors":[{"message":"denied"}],"data":null}')

    with pytest.raises(FetchError) as error:
        await DMM(Client(), Settings()).content("abc123")
    assert json.loads(str(error.value)) == {"errors": [{"message": "denied"}], "data": None}


@pytest.mark.asyncio
async def test_solver_error_preserves_original_json():
    from types import SimpleNamespace
    from unittest.mock import AsyncMock

    from caress.http import FetchError, WebClient

    payload = {"status": "error", "message": "challenge failed", "code": 123}
    client = WebClient()
    client._control = SimpleNamespace(post=AsyncMock(return_value=SimpleNamespace(json=lambda: payload)))
    with pytest.raises(FetchError) as error:
        await client._solver_call({"cmd": "request.get"})
    assert json.loads(str(error.value)) == payload


@pytest.mark.asyncio
async def test_failed_scrape_exposes_source_error_in_final_message():
    from caress.models import Classification, ScrapeResult
    from caress.processing import WorkProcessor

    class ScraperStub:
        async def scrape(self, request):
            return ScrapeResult(
                code=request.code,
                status=Status.BLOCKED,
                classification=Classification(),
                results=[SourceResult(source=SourceId.JAVDB, requested_code=request.code,
                                      status=Status.BLOCKED, message='{"error":"denied"}')],
            )

    processor = object.__new__(WorkProcessor)
    processor.scraper = ScraperStub()
    with pytest.raises(ValueError, match='{"error":"denied"}'):
        await processor._prepare("ABC-123", None)


def test_source_search_selects_first_exact_match_in_provider_ranking():
    from caress.models import Candidate

    class Stub(Source):
        id = SourceId.JAVBUS
        default_url = "https://example.invalid"

        @property
        def rules(self):
            return CatalogRules()

    source = Stub(None, Settings())
    candidates = [
        Candidate(code="ABC-999", url="wrong"),
        Candidate(code="ABC-123", url="first"),
        Candidate(code="ABC-123", url="second"),
    ]
    assert source.select(ScrapeRequest(code="ABC-123"), candidates).url == "first"


@pytest.mark.asyncio
async def test_sources_stop_after_first_priority_hit_without_field_borrowing(tmp_path):
    database = tmp_path / "data.sqlite3"
    settings = Settings(database=database)
    settings.routes["censored"] = [SourceId.DMM, SourceId.JAVDB]
    rules = CatalogRules.load(database)
    rules.remember("TESTABC-123", Category.CENSORED, database)
    scraper = Scraper(settings, client=object())
    completed = []

    class Stub:
        def __init__(self, source, delay):
            self.source, self.delay = source, delay

        async def fetch(self, request):
            await asyncio.sleep(self.delay)
            completed.append(self.source)
            return SourceResult(
                source=self.source,
                requested_code=request.code,
                status=Status.FOUND,
                metadata=SourceMetadata(
                    code=request.code,
                    title=str(self.source),
                    plot="only second plot" if self.source == SourceId.JAVDB else None,
                ),
            )

    scraper.sources = {
        SourceId.DMM: Stub(SourceId.DMM, 0.01),
        SourceId.JAVDB: Stub(SourceId.JAVDB, 0),
    }
    result = await scraper.scrape("TESTABC-123")
    assert completed == [SourceId.DMM]
    assert result.selected_source == SourceId.DMM
    assert result.metadata.title == "dmm"
    assert result.metadata.plot is None


def test_direct_numeric_and_fc2_families_are_never_persisted_as_prefixes(tmp_path):
    path = tmp_path / "data.sqlite3"
    rules = CatalogRules.load(path)
    before = rules.model_dump()
    rules.remember("123456-123", Category.UNCENSORED, path)
    rules.remember("FC2PPV-123456", Category.FC2, path)
    assert CatalogRules.load(path).model_dump() == before


def test_cooldowns_are_keyed_by_source_and_hostname_and_rebuild(store):
    from caress.http import FetchError, WebClient

    client = WebClient(runtime=store)
    key = client.cooldown_key(SourceId.JAVDB, "https://one.invalid/a")
    client.cool_down(key, 60, "blocked")
    rebuilt = WebClient(runtime=store)
    with pytest.raises(FetchError):
        rebuilt.check_cooldown(key)
    rebuilt.check_cooldown(
        rebuilt.cooldown_key(SourceId.JAVDB, "https://two.invalid/a")
    )


@pytest.mark.asyncio
async def test_separate_scrapers_share_whole_source_work_gate(tmp_path):
    from caress.http import WebClient

    settings = Settings(database=tmp_path / "data.sqlite3")
    settings.routes["censored"] = [SourceId.DMM]
    rules = CatalogRules.load(settings.database)
    rules.remember("TESTABC-123", Category.CENSORED, settings.database)
    active = maximum = 0

    class Stub:
        async def fetch(self, request):
            nonlocal active, maximum
            active += 1
            maximum = max(maximum, active)
            await asyncio.sleep(0.01)
            active -= 1
            return SourceResult(
                source=SourceId.DMM,
                requested_code=request.code,
                status=Status.FOUND,
                metadata=SourceMetadata(code=request.code, title="source"),
            )

    scrapers = [Scraper(settings, client=WebClient()) for _ in range(2)]
    for scraper in scrapers:
        scraper.sources = {SourceId.DMM: Stub()}
    await asyncio.gather(*(scraper.scrape("TESTABC-123") for scraper in scrapers))
    assert maximum == 1


@pytest.mark.asyncio
async def test_pacing_is_shared_between_clients_but_not_within_one_work(monkeypatch):
    from types import SimpleNamespace
    from unittest.mock import AsyncMock

    from caress.http import WebClient

    class Session:
        async def get(self, url, **kwargs):
            return SimpleNamespace(url=url, status=200, body=b"{}", headers={})

    batch, preview = WebClient(), WebClient()
    batch._session = AsyncMock(return_value=Session())
    preview._session = AsyncMock(return_value=Session())
    sleep = AsyncMock()
    monkeypatch.setattr("caress.http.asyncio.sleep", sleep)
    with batch.source_work():
        await batch.get(SourceId.JAVDB, "https://example.invalid/search")
        await batch.get(SourceId.JAVDB, "https://example.invalid/detail")
    sleep.assert_not_awaited()
    with preview.source_work():
        await preview.get(SourceId.JAVDB, "https://example.invalid/actor")
    sleep.assert_awaited_once()
    assert 0 < sleep.call_args.args[0] <= 60


@pytest.mark.asyncio
async def test_images_share_one_second_budget_by_hostname_across_ports(monkeypatch):
    from types import SimpleNamespace
    from unittest.mock import AsyncMock

    from caress.http import WebClient

    class Session:
        async def get(self, url, **kwargs):
            return SimpleNamespace(url=url, status=200, body=b"image", headers={})

    first, second = WebClient(), WebClient()
    first._session = AsyncMock(return_value=Session())
    second._session = AsyncMock(return_value=Session())
    sleep = AsyncMock()
    monkeypatch.setattr("caress.http.asyncio.sleep", sleep)
    await first.image_bytes(
        SourceId.JAVDB, "https://images.invalid:8443/one", "https://example.invalid"
    )
    await second.image_bytes(
        SourceId.JAVDB, "https://images.invalid:9443/two", "https://example.invalid"
    )
    sleep.assert_awaited_once()
    assert 0 < sleep.call_args.args[0] <= 1


@pytest.mark.asyncio
async def test_identity_mismatch_tries_next_source_and_stops(tmp_path):
    settings = Settings(database=tmp_path / "db.sqlite3")
    settings.routes["censored"] = [SourceId.DMM, SourceId.JAVDB, SourceId.JAVBUS]
    CatalogRules.load(settings.database).remember(
        "TESTABC-123", Category.CENSORED, settings.database
    )
    scraper = Scraper(settings, client=object())
    calls = []

    class Stub:
        def __init__(self, source, code):
            self.source, self.code = source, code

        async def fetch(self, request):
            calls.append(self.source)
            return SourceResult(
                source=self.source,
                requested_code=request.code,
                status=Status.FOUND,
                metadata=SourceMetadata(code=self.code, title="source"),
            )

    scraper.sources = {
        s: Stub(s, "WRONG-999" if s == SourceId.DMM else "TESTABC-123")
        for s in settings.routes["censored"]
    }
    result = await scraper.scrape("TESTABC-123")
    assert calls == [SourceId.DMM, SourceId.JAVDB]
    assert result.results[0].status == Status.IDENTITY_MISMATCH
    assert result.selected_source == SourceId.JAVDB


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ["transport", "programming", "response"])
async def test_only_curl_transport_failures_are_retried(monkeypatch, failure):
    from types import SimpleNamespace
    from unittest.mock import AsyncMock

    from curl_cffi import CurlError

    from caress.http import FetchError, WebClient

    problem = (
        CurlError("connection failed") if failure == "transport" else TypeError("bug")
    )
    response = SimpleNamespace(
        url="https://example.invalid/", status=200, body=b"{}", headers={}
    )
    if failure == "response":
        response = object()
    session = SimpleNamespace(
        get=AsyncMock(
            side_effect=problem if failure != "response" else None,
            return_value=response,
        )
    )
    client = WebClient()
    client._session = AsyncMock(return_value=session)
    sleep = AsyncMock()
    monkeypatch.setattr("caress.http.asyncio.sleep", sleep)
    expected = (
        FetchError
        if failure == "transport"
        else AttributeError
        if failure == "response"
        else TypeError
    )
    with pytest.raises(expected):
        await client.get(SourceId.DMM, "https://example.invalid/")
    assert session.get.await_count == (2 if failure == "transport" else 1)
    assert sleep.await_count == (1 if failure == "transport" else 0)


@pytest.mark.asyncio
async def test_solver_uses_builtin_address_and_destroys_session():
    from unittest.mock import AsyncMock

    import httpx

    from caress.http import FLARESOLVERR_URL, WebClient

    commands = []

    async def handler(request):
        import json

        assert str(request.url) == FLARESOLVERR_URL
        command = json.loads(request.content)
        commands.append(command["cmd"])
        await asyncio.sleep(0)
        return httpx.Response(
            200,
            json={
                "status": "ok",
                "solution": {
                    "url": "https://example.invalid/",
                    "status": 200,
                    "response": "<title>Work</title>",
                },
            },
        )

    client = WebClient()
    async with client:
        await client._control.aclose()
        client._control = await client._stack.enter_async_context(
            httpx.AsyncClient(transport=httpx.MockTransport(handler))
        )
        client._session = AsyncMock()
        pages = await asyncio.gather(
            client._render(SourceId.DMM, "https://example.invalid/", {}),
            client._render(SourceId.DMM, "https://example.invalid/", {}),
        )
        assert all(
            page.fetcher == "flaresolverr" and page.status == 200 for page in pages
        )
        client._session.assert_not_awaited()
    assert commands == [
        "sessions.create",
        "request.get",
        "request.get",
        "sessions.destroy",
    ]


@pytest.mark.asyncio
async def test_javdb_business_failure_keeps_complete_json_and_uses_web(caplog):
    import json
    import logging
    from types import SimpleNamespace
    from unittest.mock import AsyncMock

    from caress.http import FetchError, Page
    from caress.sources.javdb import JavDB

    payload = {"success": 0, "message": "請登錄帳號", "data": None, "code": 401}
    source = JavDB(
        SimpleNamespace(
            get=AsyncMock(
                return_value=Page(
                    "https://jdforrepam.com/api/v2/search",
                    200,
                    json.dumps(payload).encode(),
                )
            )
        ),
        Settings(),
    )
    caplog.set_level(logging.INFO, logger="caress.progress")
    with pytest.raises(FetchError) as error:
        await source.app.api("/api/v2/search", q="ABP-876")
    message = str(error.value)
    assert json.loads(message) == payload
    assert not caplog.records
    source.app.api = AsyncMock(
        side_effect=AssertionError("JWT-only API must not be used")
    )
    source.web.fetch = AsyncMock(
        return_value=SourceResult(
            source=SourceId.JAVDB, requested_code="ABP-876", status=Status.NOT_FOUND
        )
    )
    result = await source.fetch(ScrapeRequest(code="ABP-876"))
    assert result.status == Status.NOT_FOUND
    source.app.api.assert_not_awaited()


@pytest.mark.asyncio
async def test_javdb_actor_search_uses_exact_names_and_same_origin():
    from types import SimpleNamespace
    from unittest.mock import AsyncMock

    from caress.http import Page
    from caress.sources.javdb_web import JavDBWeb

    html = """<div class="actor-box">
      <a href="/actors/0JEb" title="大橋未久"><strong>大橋未久</strong></a>
      <a href="/actors/21Jp" title="大橋未久"><strong>大橋未久</strong></a>
      <a href="/actors/wrong" title="大橋未久子"><strong>大橋未久子</strong></a>
      <a href="https://evil.invalid/actors/fake" title="大橋未久"><strong>大橋未久</strong></a>
    </div>"""
    client = SimpleNamespace(
        get=AsyncMock(return_value=Page("https://javdb.com/search", 200, html.encode()))
    )
    source = JavDBWeb(client, Settings())
    assert await source.find_actor_ids("大橋未久") == ["0JEb", "21Jp"]


@pytest.mark.asyncio
@pytest.mark.parametrize("ambiguous", [False, True])
async def test_javdb_actor_details_are_verified_before_remembering(tmp_path, ambiguous):
    from unittest.mock import AsyncMock

    from caress.actors import ActorAliases
    from caress.models import Actor
    from caress.sources.javdb_api import JavDBApi

    settings = Settings(database=tmp_path / "rules.sqlite3")
    names = ActorAliases.load(settings.database)
    source = JavDBApi(None, settings)
    source.api = AsyncMock(
        side_effect=lambda path: {
            "actor": {
                "id": path.rsplit("/", 1)[1],
                "name": "测试演员甲",
                "type": 0 if path.endswith("a") or ambiguous else 1,
            }
        }
    )
    search = AsyncMock(return_value=["a", "b"])
    warnings = await source.resolve_actors(
        [Actor(name="测试演员甲")], names, Category.CENSORED, {}, search
    )
    assert all(
        call.args[0].startswith("/api/v1/actors/")
        for call in source.api.await_args_list
    )
    if ambiguous:
        assert warnings and not names.candidates("测试演员甲", 0)
    else:
        assert not warnings and names.resolve_id("测试演员甲", 0) == "a"
        assert ActorAliases.load(settings.database).resolve_id("测试演员甲", 0) == "a"


def test_javdb_movie_actor_links_preserve_verified_ids():
    from caress.http import Page
    from caress.sources.javdb_web import JavDBWeb

    html = """<a class="copy-to-clipboard" data-clipboard-text="ABP-967"></a>
      <h2><strong class="current-title">Title</strong></h2>
      <nav class="movie-panel-info"><div class="panel-block"><strong>演員</strong>
      <span class="value"><a href="/actors/0JEb">大橋未久</a></span></div></nav>"""
    result = JavDBWeb(None, Settings()).parse(
        ScrapeRequest(code="ABP-967"),
        Page("https://javdb.com/v/7YkA1", 200, html.encode()),
    )
    assert result.actor_ids == {"大橋未久": "0JEb"}
    assert result.metadata.actors[0].name == "大橋未久"


@pytest.mark.asyncio
async def test_javdb_actor_candidate_failure_never_saves_partial_match(tmp_path):
    from unittest.mock import AsyncMock

    from caress.actors import ActorAliases
    from caress.http import FetchError
    from caress.models import Actor
    from caress.sources.javdb_api import JavDBApi

    settings = Settings(database=tmp_path / "rules.sqlite3")
    names = ActorAliases.load(settings.database)
    source = JavDBApi(None, settings)
    source.api = AsyncMock(
        side_effect=[
            {"actor": {"id": "a", "name": "测试演员乙", "type": 0}},
            FetchError(Status.BLOCKED, "演员详情不可用"),
        ]
    )
    warnings = await source.resolve_actors(
        [Actor(name="测试演员乙")],
        names,
        Category.CENSORED,
        {},
        AsyncMock(return_value=["a", "b"]),
    )
    assert warnings and "演员详情不可用" in warnings[0]
    assert not ActorAliases.load(settings.database).candidates("测试演员乙", 0)


def test_javdb_movie_same_name_different_ids_is_not_guessed():
    from caress.actors import ActorAliases, map_actor_names
    from caress.http import Page
    from caress.sources.javdb_web import JavDBWeb

    html = """<a class="copy-to-clipboard" data-clipboard-text="ABP-967"></a>
      <h2><strong class="current-title">Title</strong></h2>
      <nav class="movie-panel-info"><div class="panel-block"><strong>演員</strong>
      <span class="value"><a href="/actors/0JEb">大橋未久</a>
      <a href="/actors/rJ7k">大橋未久</a></span></div></nav>"""
    result = JavDBWeb(None, Settings()).parse(
        ScrapeRequest(code="ABP-967"),
        Page("https://javdb.com/v/7YkA1", 200, html.encode()),
    )
    names = ActorAliases({"0JEb": {"type": 0, "name": "大橋未久"}})
    _, mappings, issues = map_actor_names(
        result.metadata.actors, names, 0, result.actor_ids
    )
    assert result.actor_ids == {"大橋未久": None}
    assert all(mapping["actor_id"] is None for mapping in mappings)
    assert issues


@pytest.mark.asyncio
async def test_javdb_scrape_uses_web_and_public_actor_detail_without_jwt(tmp_path):
    import json
    from urllib.parse import urlsplit

    from caress.actors import ActorAliases
    from caress.http import Page

    class Client:
        def __init__(self):
            self.paths = []

        async def get(self, source, url, **kwargs):
            path = urlsplit(url).path
            self.paths.append(path)
            if path == "/search":
                body = """<div class="movie-list"><a class="box" href="/v/film1">
                  <div class="video-title"><strong>ABP-967</strong></div></a></div>"""
            elif path == "/v/film1":
                body = """<a class="copy-to-clipboard" data-clipboard-text="ABP-967"></a>
                  <h2><strong class="current-title">Title</strong></h2>
                  <nav class="movie-panel-info"><div class="panel-block"><strong>演員</strong>
                  <span class="value"><a href="/actors/public1">测试演员甲</a></span></div></nav>"""
            elif path == "/api/v1/actors/public1":
                body = json.dumps(
                    {
                        "success": 1,
                        "data": {
                            "actor": {
                                "id": "public1",
                                "name": "测试演员甲",
                                "type": 0,
                            }
                        },
                    }
                )
            else:
                raise AssertionError(f"unexpected or JWT-only endpoint: {path}")
            return Page(url, 200, body.encode())

    settings = Settings(database=tmp_path / "rules.sqlite3")
    client = Client()
    scraper = Scraper(settings, client=client)
    result = await scraper.scrape(ScrapeRequest(code="ABP-967", source=SourceId.JAVDB))
    assert result.status == Status.FOUND
    assert result.metadata.code == "ABP-967"
    selected = result.results[0]
    assert selected.actor_ids == {"测试演员甲": "public1"}
    names = ActorAliases.load(settings.database)
    warnings = await scraper.resolve_actors(
        result.metadata.actors, names, Category.CENSORED, selected.actor_ids
    )
    assert not warnings and names.resolve_id("测试演员甲", 0) == "public1"
    assert client.paths == ["/search", "/v/film1", "/api/v1/actors/public1"]


@pytest.mark.asyncio
async def test_explicit_ambiguous_actor_never_triggers_lookup(tmp_path):
    from unittest.mock import AsyncMock

    from caress.actors import ActorAliases
    from caress.models import Actor

    settings = Settings(database=tmp_path / "rules.sqlite3")
    names = ActorAliases.load(settings.database)
    scraper = Scraper(settings, client=object())
    scraper.sources[SourceId.JAVDB].app.resolve_actors = AsyncMock()
    result = await scraper.resolve_actors(
        [Actor(name="测试演员甲")], names, Category.CENSORED, {"测试演员甲": None}
    )
    assert result == []
    scraper.sources[SourceId.JAVDB].app.resolve_actors.assert_not_awaited()
