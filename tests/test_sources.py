import asyncio

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
async def test_javdb_business_failure_keeps_complete_json_in_logs_and_fallback(caplog):
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
    assert json.loads(message.split("：", 1)[1]) == payload
    assert message in caplog.text
    source.app.fetch = AsyncMock(
        return_value=SourceResult(
            source=SourceId.JAVDB,
            requested_code="ABP-876",
            status=Status.BLOCKED,
            message=message,
        )
    )
    source.web.fetch = AsyncMock(
        return_value=SourceResult(
            source=SourceId.JAVDB, requested_code="ABP-876", status=Status.NOT_FOUND
        )
    )
    result = await source.fetch(ScrapeRequest(code="ABP-876"))
    assert message in result.warnings[0]
