import asyncio
import json
from copy import deepcopy
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from caress.models import Actor, SourceId, SourceMetadata, Status
from caress.portraits import Gfriends, actor_image
from caress.processing import WorkProcessor
from caress.settings import Settings
from caress.translation import actor_name_rules
from test_images import image_bytes

# Actual Filetree entries; bank priority and file names belong to Gfriends.
CONTENT = {
    "z-ラグジュTV": {
        "Kaname Yuuna.jpg": "AI-Fix-黒川さりな.jpg?t=1607433809",
        "Kurokawa Sarina.jpg": "AI-Fix-黒川さりな.jpg?t=1607433809",
    },
    "y-Minnano": {"武藤あやか.jpg": "武藤あやか.jpg?t=1658331557"},
}


@pytest.mark.asyncio
async def test_filetree_aliases_normalization_and_bank_priority(tmp_path):
    async with Gfriends(tmp_path) as portraits:
        content = {
            **deepcopy(CONTENT),
            "preferred": {
                "黒川さりな.jpg": "AI-Fix-黒川さりな-2.jpg",
                "黑川紗里奈.jpg": "AI-Fix-黒川さりな.jpg",
                "Conflict.jpg": "AI-Fix-標準名.jpg",
            },
        }
        content["z-ラグジュTV"]["Conflict.jpg"] = "別名.jpg"
        portraits.document = AsyncMock(
            return_value=json.dumps({"Content": content}).encode()
        )
        matches = await asyncio.gather(
            *(
                portraits.resolve(name)
                for name in [
                    "Ｋａｎａｍｅ　 Yuuna ",
                    "Kurokawa Sarina",
                    "黒川さりな",
                    "黑川紗里奈",
                ]
            )
        )
        assert [match.name for match in matches] == ["黒川さりな"] * 4
        assert matches[0].url.endswith("?t=1607433809")
        assert (await portraits.resolve("conflict")).name == "標準名"
        assert (await portraits.resolve("武藤あやか")).name == "武藤あやか"
        assert await portraits.resolve("未收录演员") is None
        portraits.document.assert_awaited_once()


@pytest.mark.asyncio
async def test_portrait_download_failure_does_not_lose_mapping_or_prevent_retry(
    tmp_path,
):
    async with Gfriends(tmp_path) as portraits:
        portraits.index = portraits._build_index(CONTENT)
        match = await portraits.resolve("Kaname Yuuna")
        portraits.document = AsyncMock(
            side_effect=[ValueError("offline"), image_bytes()]
        )
        assert (await portraits.save(match))["status"] == "error"
        assert (await portraits.resolve("Kaname Yuuna")).name == "黒川さりな"
        assert (await portraits.save(match))["status"] == "saved"
        assert actor_image(tmp_path, match.name).is_file()
        await portraits.save(match)
        assert portraits.document.await_count == 2


@pytest.fixture
async def processor(tmp_path):
    processor = WorkProcessor(Settings(database=tmp_path / "db"), None, tmp_path)
    processor.portraits.document = AsyncMock(
        return_value=json.dumps({"Content": CONTENT}).encode()
    )
    processor.scraper.scrape = AsyncMock(
        return_value=SimpleNamespace(
            status=Status.FOUND,
            selected_source=SourceId.DMM,
            results=[],
            warnings=[],
            metadata=SourceMetadata(
                code="ABC-123",
                title="Kaname Yuuna title",
                plot="Kurokawa Sarina and 黒川さりな",
                actors=[
                    Actor(name=name)
                    for name in ["Kaname Yuuna", "Kurokawa Sarina", "Unknown"]
                ],
            ),
        )
    )
    try:
        yield processor
    finally:
        await processor.__aexit__(None, None, None)


@pytest.mark.asyncio
async def test_prepare_unifies_aliases_and_protects_both_source_and_standard_names(
    processor,
):
    processor.translator.settings.model = "test"
    processor.translator.settings.api_key = "test"
    processor.translator.verify = AsyncMock(return_value=True)
    processor.translator.text = AsyncMock(return_value="译文")
    prepared = await processor._prepare("ABC-123", None)
    assert [actor.name for actor in prepared.metadata.actors] == [
        "黒川さりな",
        "Unknown",
    ]
    assert prepared.metadata.title == "ABC-123 黒川さりな Unknown 译文"
    assert "Kurokawa Sarina" in prepared.metadata.plot
    assert "黒川さりな" in prepared.metadata.plot
    assert all(
        "Kurokawa Sarina" not in call.args[0] and "黒川さりな" not in call.args[0]
        for call in processor.translator.text.await_args_list
    )
    assert len(prepared.mappings) == 2
    assert prepared.mappings[0]["portrait"].name == "黒川さりな"


@pytest.mark.asyncio
async def test_unavailable_filetree_preserves_source_metadata_and_names(processor):
    processor.portraits.document.side_effect = ValueError("offline")
    prepared = await processor._prepare("ABC-123", None)
    assert [a.name for a in prepared.metadata.actors] == [
        "Kaname Yuuna",
        "Kurokawa Sarina",
        "Unknown",
    ]
    assert prepared.metadata.plot == "Kurokawa Sarina and 黒川さりな"
    assert prepared.warnings == ["Gfriends 演员索引不可用，已保留来源原名：offline"]
    assert all(mapping["portrait"] is None for mapping in prepared.mappings)
    processor.portraits.document.assert_awaited_once()


@pytest.mark.asyncio
async def test_standard_emby_person_receives_portrait_without_javdb_id(processor):
    prepared = await processor._prepare("ABC-123", None)
    processor.portraits.document = AsyncMock(return_value=image_bytes())
    people = [
        {"Name": a.name, "Type": "Actor", "Id": str(i)}
        for i, a in enumerate(prepared.metadata.actors)
    ]
    client = SimpleNamespace(
        write_metadata=AsyncMock(return_value=({"People": people}, True)),
        image_info=AsyncMock(return_value=[]),
        upload=AsyncMock(),
    )
    preparation_warnings = await processor.prepare_write(
        client, {}, prepared, [], replace=False
    )
    warnings, changed = await processor.write(
        client, "work", prepared, [], replace=False
    )
    warnings = preparation_warnings + warnings
    assert changed and not warnings
    client.upload.assert_awaited_once_with("0", "Primary", image_bytes())
    await processor.write(client, "other", prepared, [], replace=False)
    assert client.upload.await_count == 1
    assert processor.portraits.document.await_count == 1


@pytest.mark.asyncio
async def test_failed_portrait_keeps_canonical_name_and_other_metadata(processor):
    prepared = await processor._prepare("ABC-123", None)
    processor.portraits.document = AsyncMock(side_effect=ValueError("offline"))
    client = SimpleNamespace(
        write_metadata=AsyncMock(
            return_value=(
                {
                    "People": [
                        {"Name": "黒川さりな", "Type": "Actor", "Id": "person"},
                    ]
                },
                True,
            )
        ),
        image_info=AsyncMock(return_value=[]),
        upload=AsyncMock(),
    )
    preparation_warnings = await processor.prepare_write(
        client, {}, prepared, [], replace=False
    )
    warnings, changed = await processor.write(
        client, "work", prepared, [], replace=False
    )
    warnings = preparation_warnings + warnings
    assert changed and warnings == ["演员头像获取失败：offline"]
    assert prepared.metadata.actors[0].name == "黒川さりな"
    client.upload.assert_not_awaited()
    assert client.write_metadata.call_args.args[1].actors[-1].name == "Unknown"


def test_translation_pattern_ignores_empty_names():
    assert actor_name_rules([]) is None
    assert actor_name_rules([""]) is None


@pytest.mark.asyncio
async def test_alias_supplies_portrait_when_standard_name_appears_first(processor):
    metadata = processor.scraper.scrape.return_value.metadata
    metadata.actors.insert(0, Actor(name="黒川さりな"))
    prepared = await processor._prepare("ABC-123", None)
    assert [a.name for a in prepared.metadata.actors] == ["黒川さりな", "Unknown"]
    assert prepared.mappings[0]["portrait"].name == "黒川さりな"


@pytest.mark.asyncio
async def test_translation_outage_still_composes_standard_actor_title(processor):
    processor.translator.settings.model = "test"
    processor.translator.settings.api_key = "test"
    processor.translator.available = False
    processor.translator.validation_error = "offline"
    processor.translator.verify = AsyncMock(return_value=False)
    prepared = await processor._prepare("ABC-123", None)
    assert prepared.metadata.title == "ABC-123 黒川さりな Unknown title"
    assert prepared.metadata.plot == "Kurokawa Sarina and 黒川さりな"
    assert [a.name for a in prepared.metadata.actors] == ["黒川さりな", "Unknown"]
    assert "offline" in prepared.warnings
