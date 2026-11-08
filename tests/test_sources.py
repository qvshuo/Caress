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
