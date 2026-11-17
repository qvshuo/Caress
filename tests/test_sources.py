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
@pytest.mark.parametrize("data", [None, {"ppvContent": {"id": "abc123", "title": "partial"}}])
async def test_dmm_graphql_error_retains_original_response(data):
    from caress.http import FetchError, Page
    from caress.sources.dmm import DMM

    payload = {"errors": [{"message": "denied"}], "data": data}

    class Client:
        async def post_json(self, *args, **kwargs):
            return Page("https://example.invalid", 200, json.dumps(payload).encode())

    with pytest.raises(FetchError) as error:
        await DMM(Client(), Settings()).content("abc123")
    assert json.loads(str(error.value)) == payload


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
    observed = []
    result = await scraper.scrape("ABP-123", on_result=lambda result: observed.append(result.status))
    assert observed == [Status.IDENTITY_MISMATCH, Status.FOUND]
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
    assert set(rules) == {"updated_at", "censored", "uncensored"}
    from datetime import date

    assert date.fromisoformat(rules["updated_at"]).isoformat() == rules["updated_at"]
    assert not set(rules["censored"]) & set(rules["uncensored"])
    for prefixes in (rules["censored"], rules["uncensored"]):
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


def test_catalog_builder_rejects_other_sources_and_preserves_full_prefixes():
    from scripts.catalog import build

    rows = [
        {"code": "300MIUM-123", "category": "censored", "url": "https://javdb.com/v/a"},
        {"code": "MIUM-123", "category": "uncensored", "url": "https://avsox.click/cn/movies/b"},
        {"code": "MKBD-S123", "category": "uncensored", "url": "https://avsox.click/cn/movies/c"},
    ]
    rules, conflicts = build(rows)
    assert rules == {"censored": ["300MIUM"], "uncensored": ["MIUM", "MKBD-S"]}
    assert not conflicts
    rules, conflicts = build([*rows, {**rows[0], "category": "uncensored", "url": "https://avsox.click/cn/movies/d"}])
    assert conflicts == ["300MIUM"] and "300MIUM" not in rules["censored"] + rules["uncensored"]
    with pytest.raises(ValueError, match="Evidence must come from"):
        build([{**rows[0], "url": "https://example.com/v/a"}])


@pytest.mark.asyncio
async def test_catalog_collection_stops_at_login_or_wrong_category(monkeypatch):
    import httpx

    from scripts.catalog import collect

    original = httpx.AsyncClient
    response = httpx.Response(302, headers={"Location": "/login"})
    monkeypatch.setattr(
        "scripts.catalog.httpx.AsyncClient",
        lambda **kwargs: original(transport=httpx.MockTransport(lambda request: response), **kwargs),
    )
    with pytest.raises(ValueError, match="HTTP 302"):
        await collect("javdb", [1])
    response = httpx.Response(200, text='<div class="main-tabs"><li class="is-active"><a href="/uncensored">wrong</a></li></div><div class="movie-list"></div>')
    with pytest.raises(ValueError, match="censored listing unavailable"):
        await collect("javdb", [1])


def test_prefix_extraction_keeps_namespaces_and_removes_numeric_segments():
    from caress.catalog import catalog_prefix

    for code, expected in {
        "SSIS-123": "SSIS", "MKBD-S123": "MKBD-S",
        "300MIUM-123": "300MIUM", "HEYDOUGA-4037-1002": "HEYDOUGA",
        "T38-075": "T38", "3DSVR-2040": "3DSVR",
    }.items():
        assert catalog_prefix(code) == expected
    assert classify("HEYZO-0780") == classify("HEYDOUGA-4037-1002") == Category.UNCENSORED


@pytest.mark.asyncio
async def test_catalog_collection_deduplicates_pages_and_reports_empty_pages(monkeypatch, capsys):
    from unittest.mock import AsyncMock

    import httpx

    from scripts.catalog import collect

    original = httpx.AsyncClient
    responses = iter([
        {"code": 200, "data": [{"movieFanHao": "CWP-001", "movieId": "a"}]},
        {"code": 200, "data": [{"movieFanHao": "CWP-001", "movieId": "b"}]},
        {"code": 200, "data": []},
    ])
    monkeypatch.setattr("scripts.catalog.httpx.AsyncClient", lambda **kwargs: original(
        transport=httpx.MockTransport(lambda request: httpx.Response(200, json=next(responses))), **kwargs))
    monkeypatch.setattr("scripts.catalog.asyncio.sleep", AsyncMock())
    rows = await collect("avsox", [1, 2, 3])
    assert len(rows) == 1 and rows[0]["code"] == "CWP-001"
    report = json.loads(capsys.readouterr().out.splitlines()[-1])
    assert report == {"source": "avsox", "valid_pages": 2, "empty_pages": 1,
                      "duplicate_pages": 1, "unique_codes": 1, "new_prefixes": 1}


def test_manual_build_does_not_advance_update_date(tmp_path, monkeypatch):
    from scripts.catalog import main

    source = tmp_path / "input.json"
    source.write_text(json.dumps([{"code": "CWP-001", "category": "uncensored", "url": "https://avsox.click/cn/movies/a"}]))
    rules = tmp_path / "rules.json"
    previous = {"updated_at": "2026-01-01", "censored": [], "uncensored": ["CWP"]}
    rules.write_text(json.dumps(previous))
    monkeypatch.setattr("sys.argv", ["catalog", "build", str(source), "--rules", str(rules)])
    main()
    assert json.loads(rules.read_text()) == previous
    rules.unlink()
    main()
    assert "updated_at" not in json.loads(rules.read_text())


def test_identity_is_stricter_than_source_query_format():
    from caress.catalog import query_code
    from caress.sources.parsing import source_code

    assert query_code("010115_001") == query_code("010115-001")
    assert not matches("010115_001", "010115-001")
    assert matches("010115_001", "010115_001")
    for alias in ["FC2-123456", "FC2-PPV-123456", "fc2ppv_123456"]:
        assert matches("FC2PPV-123456", alias)
    assert query_code("HEYDOUGA-4037-428") == "4037-428"
    assert not matches("HEYDOUGA-4037-428", "4037-428")
    assert source_code("4037-428", "heydouga") == "HEYDOUGA-4037-428"
    assert source_code("4037-428") == source_code("4037-428", "unknown") == "4037-428"


@pytest.mark.asyncio
async def test_avsox_restores_explicit_identity_and_checks_detail_and_page():
    from unittest.mock import AsyncMock

    from caress.http import Page
    from caress.sources.avsox import AVSOX

    identity = {"movieId": "a", "movieFanHao": "4037-428", "from": "heydouga"}
    search_identity = dict(identity)

    calls = []

    class Client:
        async def post_json(self, source, url, payload):
            calls.append(url.rsplit("/", 1)[-1])
            if url.endswith("/search"):
                assert payload[0]["search"] == "4037-428"
                data = [search_identity]
            else:
                assert payload == ["a", "cn"]
                data = identity
            return Page(url, 200, json.dumps({"code": 200, "data": data}).encode())

    source = AVSOX(Client(), Settings())
    html = '<h1>4037-428 Title</h1><div class="detail-row"><span class="detail-label">识别码</span><span class="detail-value">4037-428</span></div><img alt="4037-428 cover" src="https://image.invalid/a.jpg">'
    source.document = AsyncMock(return_value=Page("https://avsox.click/cn/movies/a", 200, html.encode()))
    request = ScrapeRequest(code="HEYDOUGA-4037-428")
    result = await source.fetch(request)
    assert result.status == Status.FOUND and result.metadata.code == request.code
    assert result.metadata.artwork[0].url == "https://image.invalid/a.jpg"
    assert calls == ["search"]
    for target in [
        {"external_id": "a"},
        {"source_url": "https://avsox.click/cn/movies/a"},
        {"source_url": "https://avsox.click/cn/movies/a?lang=cn"},
    ]:
        calls.clear()
        assert (await source.fetch(ScrapeRequest(code=request.code, **target))).status == Status.FOUND
        assert calls == ["getMovie"]
    identity["movieId"] = "wrong"
    assert (await source.fetch(ScrapeRequest(code=request.code, external_id="a"))).status == Status.IDENTITY_MISMATCH
    identity["movieId"] = "a"
    calls.clear()
    assert (await source.fetch(ScrapeRequest(code=request.code, external_id="b", source_url="https://avsox.click/cn/movies/a"))).status == Status.IDENTITY_MISMATCH
    assert not calls
    source.document.return_value = Page("https://avsox.click/cn/movies/a", 200, html.replace("4037-428", "HEYDOUGA-4037-428").encode())
    assert (await source.fetch(request)).status == Status.FOUND
    identity.pop("from")
    assert (await source.fetch(ScrapeRequest(code=request.code, external_id="a"))).status == Status.IDENTITY_MISMATCH
    search_identity.pop("from")
    assert (await source.fetch(request)).status == Status.NOT_FOUND
    search_identity["from"] = identity["from"] = "heydouga"
    source.document.return_value = Page("https://avsox.click/cn/movies/a", 200, html.replace("4037-428", "4037-429").encode())
    assert (await source.fetch(request)).status == Status.IDENTITY_MISMATCH
    error = {"code": 403, "message": "source access denied", "data": None}
    source.client.post_json = AsyncMock(return_value=Page("https://avsox.click/javu/data/api/search", 200, json.dumps(error).encode()))
    result = await source.fetch(request)
    assert result.status == Status.PARSE_ERROR and json.loads(result.message) == error
