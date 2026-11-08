from unittest.mock import AsyncMock

import httpx
import pytest

from caress.actors import ActorAliases, map_actor_names
from caress.models import (
    Actor,
    Category,
    Classification,
    ScrapeResult,
    SourceId,
    SourceMetadata,
    SourceResult,
    Status,
    Work,
    WorkMetadata,
)
from caress.processing import process_work, remap_work
from caress.settings import TranslationSettings
from caress.translation import Translator


def test_actor_ambiguity_preserves_original_and_id_remapping_is_offline():
    names = ActorAliases({"1": {"name": "primary a", "type": 0, "other_name": ["ambiguous"]},
                          "2": {"name": "primary b", "type": 0, "other_name": ["ambiguous"]}})
    actors, mappings, issues = map_actor_names([Actor(name="ambiguous")], names, 0)
    assert actors[0].name == "ambiguous"
    assert mappings[0]["actor_id"] is None
    assert issues[0]["status"] == "ambiguous"
    actors, mappings, issues = map_actor_names([Actor(name="ambiguous")], names, 0, [{"original_name": "ambiguous", "actor_id": "2"}])
    assert actors[0].name == "primary b"
    assert mappings[0]["actor_id"] == "2"


@pytest.mark.asyncio
@pytest.mark.parametrize("missing_only,expected", [(True, "manual"), (False, "ABC-123 new title")])
async def test_fill_protects_complete_manual_form_refresh_replaces(store, missing_only, expected):
    old = WorkMetadata(code="ABC-123", title="manual", plot="", tags=[])
    store.save_metadata(old.code, old)
    store.save_selection(old.code, {"manual_fields": ["title", "plot", "tags"], "original_fields": {"title": "pinned original"}})
    raw = SourceMetadata(code=old.code, title="new title", plot="new plot", tags=["new"])
    source = SourceResult(source=SourceId.DMM, requested_code=old.code, status=Status.FOUND, metadata=raw)
    result = ScrapeResult(code=old.code, status=Status.FOUND, classification=Classification(category=Category.CENSORED), metadata=raw,
                          selected_source=SourceId.DMM, results=[source])
    scraper = AsyncMock()
    scraper.scrape.return_value = result
    scraper.resolve_actors.return_value = []
    translator = Translator(TranslationSettings())
    try:
        report = await process_work(scraper, Work(code=old.code), store.root / "images", ActorAliases({}), translator,
                                    runtime=store, missing_only=missing_only)
        saved = store.metadata(old.code)
        assert saved.title == expected
        assert saved.plot == ("" if missing_only else "new plot")
        assert saved.tags == ([] if missing_only else ["new"])
        assert report["stages"]["metadata"] == "success"
        assert store.selection(old.code)["original_fields"]["title"] == ("pinned original" if missing_only else "new title")
        assert "artwork" not in saved.model_dump()
    finally:
        await translator.close()


@pytest.mark.asyncio
async def test_translation_failure_does_not_replace_previous_valid_output(store):
    raw = SourceMetadata(code="ABC-123", title="Japanese title", plot="Japanese plot")
    source = SourceResult(source=SourceId.DMM, requested_code=raw.code, status=Status.FOUND, metadata=raw)
    scraper = AsyncMock()
    scraper.scrape.return_value = ScrapeResult(code=raw.code, status=Status.FOUND, classification=Classification(), metadata=raw,
                                               selected_source=SourceId.DMM, results=[source])
    scraper.resolve_actors.return_value = []
    store.save_metadata(raw.code, WorkMetadata(code=raw.code, title="valid prior title", plot="valid prior plot"))
    translator = Translator(TranslationSettings(model="test", api_key="test"))
    translator.available = False
    translator.validation_error = "mock failure"
    try:
        report = await process_work(scraper, Work(code=raw.code), store.root / "images", ActorAliases({}), translator, runtime=store)
        assert store.metadata(raw.code).title == "valid prior title"
        assert store.metadata(raw.code).plot == "valid prior plot"
        assert {error["field"] for error in report["translation_errors"]} == {"title", "plot"}
    finally:
        await translator.close()


@pytest.mark.asyncio
async def test_translation_fingerprint_reuses_success_and_preserves_names(monkeypatch):
    names = ActorAliases({"id": {"type": 0, "name": "Alice"}})
    monkeypatch.setattr(ActorAliases, "load", lambda path: names)
    inputs = []
    async def respond(request):
        import json
        value = json.loads(request.content)["messages"][-1]["content"]
        inputs.append(value)
        return httpx.Response(200, json={"choices": [{"message": {"content": "译文"}}]})
    async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
        translator = Translator(TranslationSettings(model="test", api_key="test", interval_seconds=0), client)
        raw = {"metadata": SourceMetadata(code="ABC-123", title="Alice hello", plot="  hello Alice world  ", actors=[Actor(name="Alice")]).model_dump(mode="json")}
        processed = await translator.variant(raw)
        repeated = await translator.variant(processed)
        assert repeated == processed
        assert len(inputs) == 3
        assert all("Alice" not in text for text in inputs)
        assert processed["processed_fields"]["title"] == "ABC-123 Alice 译文"
        assert processed["processed_fields"]["plot"] == "  译文 Alice 译文  "


@pytest.mark.asyncio
async def test_remap_uses_saved_identity_without_fetch(store):
    store.save_metadata("ABC-123", WorkMetadata(code="ABC-123", title="ABC-123 old title", actors=[Actor(name="old")]))
    store.save_selection("ABC-123", {"original_fields": {"actors": [{"name": "original"}]},
        "actor_mappings": [{"original_name": "original", "name": "old", "actor_id": "id", "type": 0}], "title_text": "title"})
    names = ActorAliases({"id": {"name": "new", "type": 0}})
    report = await remap_work(Work(code="ABC-123", category=Category.CENSORED), store.root / "images", names, store, ["old"], ["id"], {})
    assert report["status"] == "found"
    assert store.metadata("ABC-123").actors[0].name == "new"
    assert store.metadata("ABC-123").title == "ABC-123 new title"


@pytest.mark.asyncio
async def test_stage_repair_uses_pinned_failed_input_without_refetch(store):
    from caress.processing import retry_work
    store.save_metadata("ABC-123", WorkMetadata(code="ABC-123", title="prior output", plot="successful plot"))
    store.save_selection("ABC-123", {"original_fields": {"title": "failed input"}})
    store.record_result("ABC-123", {"code": "ABC-123", "status": "found", "partial": True,
        "stages": {"metadata": "success", "translation": "failed", "images": "success"},
        "translation_errors": [{"field": "title", "error": "test failure"}]})
    translator = Translator(TranslationSettings(model="test", api_key="test", interval_seconds=0))
    translator.text = AsyncMock(return_value="修复译文")
    scraper = AsyncMock()
    try:
        result = await retry_work(scraper, Work(code="ABC-123"), store.root / "images", translator, store, None, ["translation"])
        assert not result["partial"]
        assert store.metadata("ABC-123").title == "ABC-123 修复译文"
        assert store.metadata("ABC-123").plot == "successful plot"
        translator.text.assert_awaited_once_with("failed input")
        scraper.scrape.assert_not_awaited()
    finally:
        await translator.close()
