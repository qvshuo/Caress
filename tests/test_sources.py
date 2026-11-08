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
    candidates = [Candidate(code="ABC-999", url="wrong"), Candidate(code="ABC-123", url="first"), Candidate(code="ABC-123", url="second")]
    assert source.select(ScrapeRequest(code="ABC-123"), candidates).url == "first"


@pytest.mark.asyncio
async def test_concurrent_sources_choose_configured_priority_without_field_borrowing(tmp_path):
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
            return SourceResult(source=self.source, requested_code=request.code, status=Status.FOUND,
                metadata=SourceMetadata(code=request.code, title=str(self.source), plot="only second plot" if self.source == SourceId.JAVDB else None))
    scraper.sources = {SourceId.DMM: Stub(SourceId.DMM, .01), SourceId.JAVDB: Stub(SourceId.JAVDB, 0)}
    result = await scraper.scrape("TESTABC-123")
    assert completed == [SourceId.JAVDB, SourceId.DMM]
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
    settings = Settings(database=store.root / "caress.sqlite3")
    client = WebClient(settings, runtime=store)
    key = client.cooldown_key(SourceId.JAVDB, "https://one.invalid/a")
    client.cool_down(key, 60, "blocked")
    rebuilt = WebClient(settings, runtime=store)
    with pytest.raises(FetchError):
        rebuilt.check_cooldown(key)
    rebuilt.check_cooldown(rebuilt.cooldown_key(SourceId.JAVDB, "https://two.invalid/a"))
    store.clear_cooldown(SourceId.JAVDB)
    rebuilt.check_cooldown(key)


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
            await asyncio.sleep(.01)
            active -= 1
            return SourceResult(source=SourceId.DMM, requested_code=request.code, status=Status.FOUND,
                                metadata=SourceMetadata(code=request.code, title="source"))
    scrapers = [Scraper(settings, client=WebClient(settings)) for _ in range(2)]
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
    batch, preview = WebClient(Settings()), WebClient(Settings())
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
    first, second = WebClient(Settings()), WebClient(Settings())
    first._session = AsyncMock(return_value=Session())
    second._session = AsyncMock(return_value=Session())
    sleep = AsyncMock()
    monkeypatch.setattr("caress.http.asyncio.sleep", sleep)
    await first.image_bytes(SourceId.JAVDB, "https://images.invalid:8443/one", "https://example.invalid")
    await second.image_bytes(SourceId.JAVDB, "https://images.invalid:9443/two", "https://example.invalid")
    sleep.assert_awaited_once()
    assert 0 < sleep.call_args.args[0] <= 1
