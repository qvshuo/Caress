import asyncio
import json

import pytest

from caress.catalog import classify, dmm_cid, matches
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
    from caress.models import ScrapeResult
    from caress.processing import WorkProcessor

    class ScraperStub:
        async def scrape(self, request):
            return ScrapeResult(
                code=request.code,
                status=Status.BLOCKED,
                classification=Category.UNKNOWN,
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
    settings.routes["unknown"] = [SourceId.DMM, SourceId.JAVDB]
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
    assert result.classification == Category.UNKNOWN
    assert not database.exists()


def test_number_families_and_exact_static_prefix_classification():
    assert classify("FC2PPV-123456") == Category.FC2
    assert classify("123456_123") == Category.UNCENSORED
    assert classify("012318-589") == Category.UNCENSORED
    assert classify("HEYZO-0780") == Category.UNCENSORED
    assert classify("SSIS-001") == Category.CENSORED
    assert classify("ABP-123") == Category.CENSORED
    assert classify("MKBD-S123") == Category.UNCENSORED
    assert classify("N1234") == Category.UNCENSORED
    assert classify("TESTABC-123") == Category.UNKNOWN
    assert dmm_cid("TESTABC-123") == "testabc00123"
    assert matches("FC2PPV-123456", "FC2-123456")


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
    await asyncio.gather(*(scraper.scrape("ABP-123") for scraper in scrapers))
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
        s: Stub(s, "WRONG-999" if s == SourceId.DMM else "ABP-123")
        for s in settings.routes["censored"]
    }
    result = await scraper.scrape("ABP-123")
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


def test_javdb_movie_actor_links_provide_names():
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
    assert result.metadata.actors[0].name == "大橋未久"


def test_dmm_cid_preserves_numeric_namespaces_and_exact_work_number():
    from caress.sources.dmm import cid_matches_number

    assert cid_matches_number("ssis00001", "SSIS-001")
    assert cid_matches_number("ssis00001hhb", "SSIS-001")
    assert cid_matches_number("300mium00123", "300MIUM-123")
    assert not cid_matches_number("300mium00123", "MIUM-123")
    assert not cid_matches_number("mium00123", "300MIUM-123")
    assert not cid_matches_number("ssis00002", "SSIS-001")
    assert not cid_matches_number("118abp00123", "ABP-123")


@pytest.mark.asyncio
async def test_dmm_constructed_cid_then_exact_official_search_fallback():
    from caress.http import Page
    from caress.sources.dmm import DMM

    calls = []

    class Client:
        async def post_json(self, source, url, payload, **kwargs):
            cid = payload["variables"]["id"]
            calls.append(cid)
            work = {"id": cid, "title": "official title"} if cid.endswith("so") else None
            return Page(url, 200, json.dumps({"data": {"ppvContent": work}}).encode())

        async def get(self, source, url, **kwargs):
            calls.append("search")
            return Page(url, 200, b'<input name="searchstr"><a href="https://video.dmm.co.jp/av/content/?id=300mium00123">wrong namespace</a><a href="https://video.dmm.co.jp/av/content/?id=mium00124">wrong work</a><a href="https://video.dmm.co.jp/av/content/?id=mium00123so">exact</a>')

    source = DMM(Client(), Settings())
    result = await source.fetch(ScrapeRequest(code="MIUM-123"))
    assert result.status == Status.FOUND and result.metadata.code == "MIUM-123"
    assert calls == ["mium00123", "search", "mium00123so"]
    calls.clear()
    result = await source.fetch(ScrapeRequest(code="MIUM-123", external_id="300mium00123"))
    assert result.status == Status.IDENTITY_MISMATCH and not calls


def test_filename_version_attributes_do_not_classify_the_release():
    from pathlib import Path

    from caress.identifiers import parse_filename

    code, media = parse_filename(Path("SSIS-123-UC-cd2.mp4"))
    assert code == "SSIS-123" and media.subtitles and media.uncensored and media.part == 2
    assert classify(code) == Category.CENSORED


def test_static_catalog_has_unique_exact_prefixes_without_category_conflicts():
    import re
    from pathlib import Path

    rules = json.loads((Path(__file__).parents[1] / "caress/data/catalog_rules.json").read_text())
    assert set(rules) == {"censored", "uncensored"}
    assert not set(rules["censored"]) & set(rules["uncensored"])
    for prefixes in rules.values():
        assert prefixes == sorted(set(prefixes))
        assert all(re.fullmatch(r"[A-Z0-9]+(?:-[A-Z]+)?", prefix) for prefix in prefixes)
    assert classify("3DSVR-2040") == Category.CENSORED
    assert classify("MAAN-1196") == Category.CENSORED
    assert classify("MKBD-S123") == classify("MKBD-S-123") == Category.UNCENSORED
    for code in ["XSSIS-001", "SSISX-001", "13DSVR-2040", "300MAAN-1196", "MKBD-UNKNOWN-123"]:
        assert classify(code) == Category.UNKNOWN
    assert not matches("MIUM-123", "300MIUM-123")


@pytest.mark.asyncio
async def test_all_four_routes_use_the_users_category_specific_priority():
    settings = Settings()
    settings.routes.update(
        censored=[SourceId.JAVBUS],
        uncensored=[SourceId.AVSOX],
        fc2=[SourceId.FC2],
        unknown=[SourceId.JAVDB],
    )
    calls = []

    class Stub:
        def __init__(self, source):
            self.source = source

        async def fetch(self, request):
            calls.append((self.source, request.code))
            return SourceResult(
                source=self.source,
                requested_code=request.code,
                status=Status.FOUND,
                metadata=SourceMetadata(code=request.code, title="title"),
            )

    scraper = Scraper(settings, client=object())
    scraper.sources = {source: Stub(source) for source in SourceId}
    for code, category, source in [
        ("SSIS-001", Category.CENSORED, SourceId.JAVBUS),
        ("CWP-123", Category.UNCENSORED, SourceId.AVSOX),
        ("FC2PPV-123456", Category.FC2, SourceId.FC2),
        ("UNLISTEDPREFIX-123", Category.UNKNOWN, SourceId.JAVDB),
    ]:
        result = await scraper.scrape(code)
        assert result.classification == category and result.selected_source == source
        assert calls[-1] == (source, code)
    assert len(calls) == 4


def test_verified_catalog_samples_and_ambiguous_prefixes():
    from pathlib import Path

    samples = json.loads((Path(__file__).parent / "fixtures/catalog_samples.json").read_text())
    for sample in samples:
        assert classify(sample["code"]) == sample["category"], sample
    for code in ["DSAM-020", "EMP-007", "HAME-028", "HHF-121"]:
        assert classify(code) == Category.UNKNOWN
