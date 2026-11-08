import asyncio
from pathlib import Path

from . import artwork
from .actors import ActorAliases, actor_type, map_actor_names
from .logging import report_progress
from .models import (
    EDITABLE_FIELDS,
    Actor,
    ScrapeRequest,
    SourceMetadata,
    Status,
    Work,
)
from .portraits import actor_image
from .scraper import Scraper
from .storage import atomic_write, file_transaction, target_folder
from .translation import Translator, compose_title, variant_metadata


def prepare_variant(variant: dict, names: ActorAliases, category, *, code: str) -> tuple[dict, list[dict]]:
    raw = SourceMetadata.model_validate(variant["metadata"])
    pinned = variant.get("actor_mappings", []) or [
        {"original_name": actor.name, "actor_id": variant.get("actor_ids", {}).get(actor.name)}
        for actor in raw.actors
    ]
    actors, mappings, issues = map_actor_names(raw.actors, names,
        actor_type(category), pinned)
    return {**variant, "processed_fields": {**variant.get("processed_fields", {}), "code": code,
            "actors": [actor.model_dump() for actor in actors]},
            "original_fields": {**variant.get("original_fields", {}), "actors": [actor.model_dump() for actor in raw.actors]},
            "actor_mappings": mappings, "mapping_fingerprint": names.fingerprint}, issues


def manual_fields(selection):
    return set(selection.get("manual_fields", []))


def complete_report(report):
    stages = report.setdefault("stages", {})
    incomplete = [stage for stage, state in stages.items() if state in ("failed", "pending", "cancelled")]
    report["incomplete_stages"] = incomplete
    report["partial"] = bool(incomplete) and stages.get("metadata") == "success"
    return report


def retry_task_report(report, stages):
    """A repair task reports its own work; the cumulative output report stays in runtime."""
    states = {stage: report.get("stages", {}).get(stage, "skipped") for stage in stages}
    incomplete = [stage for stage, state in states.items() if state in ("failed", "pending", "cancelled")]
    result = {"code": report["code"], "status": report["status"], "stages": states,
              "sources": [], "incomplete_stages": incomplete, "partial": bool(incomplete)}
    if report.get("error"):
        result["error"] = report["error"]
    for stage, key in {"images": "images", "translation": "translation_errors", "portraits": "portraits"}.items():
        if stage in states:
            result[key] = report.get(key, [])
    if "translation" in states:
        result["warnings"] = [warning for warning in report.get("warnings", []) if warning.startswith("翻译")]
    return result


async def process_work(scraper: Scraper, work: Work, output: Path, names: ActorAliases,
                       translator: Translator, *, runtime, missing_only=False, portraits=None):
    result = await scraper.scrape(ScrapeRequest(code=work.code),
        on_result=lambda variant: runtime.save_variant(work.code, variant.model_dump(mode="json")))
    report = {"code": work.code, "status": result.status.value, "category": result.classification.category,
              "warnings": result.warnings, "selected_source": result.selected_source,
              "sources": [r.report() for r in result.results]}
    if result.status != Status.FOUND or result.metadata is None:
        return report
    selected = next(v.model_dump(mode="json") for v in result.results if v.source == result.selected_source)
    report["warnings"].extend(await scraper.resolve_actors(
        result.metadata.actors, names, result.classification.category, selected.get("actor_ids", {})))
    selection = runtime.selection(work.code)
    previous = runtime.metadata(work.code)
    protected = manual_fields(selection) & set(EDITABLE_FIELDS) if missing_only and previous else set()
    selected, report["actor_issues"] = prepare_variant(selected, names, result.classification.category, code=work.code)
    fields = tuple(field for field in ("title", "plot") if field not in protected)
    report_progress("translation" if translator.enabled and fields else "metadata")
    selected = await translator.variant(selected, fields=fields,
        title_actors=previous.actors if "actors" in protected else None)
    runtime.save_variant(work.code, selected)
    metadata = variant_metadata(selected)
    for field in protected:
        setattr(metadata, field, getattr(previous, field))
    if "title" in protected:
        metadata.original_title = previous.original_title
    report["translation_errors"] = [error for error in selected["translation_errors"] if error["field"] in fields]
    if previous:
        for error in report["translation_errors"]:
            field = error["field"]
            if getattr(previous, field) or field in manual_fields(selection):
                setattr(metadata, field, getattr(previous, field))
                if field == "title":
                    metadata.original_title = previous.original_title
    field_sources = {field: selection.get("field_sources", {}).get(field, "unknown")
                     if field in protected else result.selected_source for field in EDITABLE_FIELDS}
    translated, originals, fingerprints = [], {}, {}
    for field in ("title", "plot", "actors"):
        origin = selection if field in protected else selected
        if field in origin.get("translated_fields", []):
            translated.append(field)
        if field in origin.get("original_fields", {}):
            originals[field] = origin["original_fields"][field]
        if field in origin.get("translation_fingerprints", {}):
            fingerprints[field] = origin["translation_fingerprints"][field]
    selection.update(field_sources=field_sources, translated_fields=translated, original_fields=originals,
        translation_fingerprints=fingerprints, manual_fields=sorted(protected),
        actor_mappings=(selection if "actors" in protected else selected).get("actor_mappings", []),
        mapping_fingerprint=(selection if "actors" in protected else selected).get("mapping_fingerprint", ""),
        title_text=(selection if "title" in protected or (previous and any(e["field"] == "title" for e in report["translation_errors"])) else selected).get("title_text"))
    request = None
    if selected["metadata"].get("artwork"):
        request = selected["metadata"]["artwork"]
    else:
        report["warnings"].append("所选来源无图片" + ("，已保留旧图" if artwork.manifest(target_folder(output, work.code), work) else ""))
    artwork.stage(selection, selected["source"], request, refresh=not missing_only)
    report["stages"] = {"metadata": "success", "translation": "failed" if report["translation_errors"] else
        "success" if any(field in selected["translated_fields"] for field in fields) else "skipped",
        "images": "pending" if request else "skipped", "portraits": "pending" if portraits else "skipped"}
    report_progress("metadata")
    with file_transaction(target_folder(output, work.code)), runtime.transaction():
        runtime.save_metadata(work.code, metadata)
        runtime.record_result(work.code, complete_report({**report, "actor_issues": []}), selection=selection)
    report["images"] = []
    if request:
        with file_transaction(target_folder(output, work.code)):
            report["images"] = await artwork.write(scraper.client, work, output, selection)
            report["stages"]["images"] = "failed" if any(r["status"] == "error" for r in report["images"]) else "success"
            runtime.record_result(work.code, complete_report({**report, "actor_issues": []}), selection=selection)
    report["portraits"] = await portraits.save(metadata.actors, kind=actor_type(result.classification.category)) if portraits else []
    report["stages"]["portraits"] = "failed" if any(r["status"] == "error" for r in report["portraits"]) else "success" if portraits else "skipped"
    return complete_report(report)


async def remap_work(work, output, names, runtime, selected_names, selected_ids, portraits):
    await asyncio.sleep(0)
    metadata = runtime.metadata(work.code)
    selection = runtime.selection(work.code)
    report = {"code": work.code, "status": "unchanged", "sources": [],
              "stages": {"mapping": "success"}, "actor_updates": [], "warnings": []}
    originals = selection.get("original_fields", {}).get("actors")
    if "actors" in manual_fields(selection) or originals is None:
        report.update(status="skipped", warnings=["保留手动演员" if "actors" in manual_fields(selection) else "未保留原始演员"])
        return report
    raw = [Actor.model_validate(actor) for actor in originals]
    pinned = selection.get("actor_mappings", [])
    _, mappings, issues = map_actor_names(raw, names, actor_type(work.category), pinned)
    previous = {row["original_name"]: row for row in pinned}
    active = set(selected_names)
    active_ids = set(selected_ids)
    selected_originals = set()
    for row in mappings:
        old = previous.get(row["original_name"], {**row, "name": row["original_name"]})
        if active and old["name"] not in active and row["original_name"] not in active and old.get("actor_id") not in active_ids:
            row.update(old)
            continue
        selected_originals.add(row["original_name"])
        if row["actor_id"] is None and names.resolve(row["original_name"], row["type"]) is None:
            row.update(old)
            continue
        if old["name"] != row["name"]:
            report["actor_updates"].append({"from": old["name"], "to": row["name"], "actor_id": row["actor_id"]})
    if not selected_originals:
        report.update(status="skipped", warnings=["未包含所选演员"])
        return report
    report["actor_issues"] = [issue for issue in issues if issue["name"] in selected_originals]
    metadata.actors = [Actor(name=name) for name in dict.fromkeys(row["name"] for row in mappings)]
    if "title" not in manual_fields(selection) and selection.get("title_text") is not None:
        metadata.title = compose_title(work.code, metadata.actors, selection["title_text"])
    elif report["actor_updates"] and "title" not in manual_fields(selection):
        report["warnings"].append("未保留标题译文，保留现有标题")
    report_progress("mapping")
    selection.update(actor_mappings=mappings, mapping_fingerprint=names.fingerprint)
    # No await in this commit boundary: works can share the portrait directory.
    copied = {}
    with file_transaction(output / "actors"), runtime.transaction():
        for update in report["actor_updates"]:
            old = actor_image(output, update["from"], index=portraits)
            if old and not actor_image(output, update["to"], index=portraits):
                target = output / "actors" / (update["to"] + old.suffix)
                atomic_write(target, old.read_bytes())
                copied[update["to"]] = target
        runtime.save_metadata(work.code, metadata)
        runtime.save_selection(work.code, selection)
    portraits.update(copied)
    report["status"] = "found" if report["actor_updates"] else "unchanged"
    if report["actor_issues"]:
        report["warnings"].append("部分演员匹配有歧义，保留现名")
    return report


async def retry_work(scraper, work, output, translator, runtime, portraits, stages):
    metadata = runtime.metadata(work.code)
    selection = runtime.selection(work.code)
    report = {**runtime.result(work.code), "code": work.code, "status": "found"}
    report["stages"] = {**report.get("stages", {}), "metadata": "success"}
    if "translation" in stages:
        report_progress("translation")
        failed = {error["field"] for error in report.get("translation_errors", [])}
        originals = selection.get("original_fields", {})
        fields = tuple(field for field in ("title", "plot")
                       if field not in manual_fields(selection) and originals.get(field)
                       and (field in failed or selection.get("translation_fingerprints", {}).get(field) != translator.field_fingerprint(field, metadata)))
        if fields and not translator.settings.enabled:
            raise ValueError("请先配置并验证翻译服务")
        raw = {**metadata.model_dump(mode="json"), **{field: originals[field] for field in fields}}
        if "title" in fields:
            raw["original_title"] = originals["title"]
        variant = await translator.variant({"metadata": raw,
            "original_fields": originals, "translated_fields": selection.get("translated_fields", []),
            "translation_fingerprints": selection.get("translation_fingerprints", {}),
            "translation_errors": report.get("translation_errors", [])}, fields=fields)
        updated = variant_metadata(variant)
        for error in variant["translation_errors"]:
            field = error["field"]
            setattr(updated, field, getattr(metadata, field))
            if field == "title":
                updated.original_title = metadata.original_title
        metadata = updated
        report["translation_errors"] = variant["translation_errors"]
        report["stages"]["translation"] = "failed" if variant["translation_errors"] else "success" if variant["translated_fields"] else "skipped"
        selection.update(translated_fields=variant["translated_fields"], original_fields=variant["original_fields"],
                         translation_fingerprints=variant["translation_fingerprints"])
        if "title" in fields and not any(e["field"] == "title" for e in variant["translation_errors"]):
            selection["title_text"] = variant["title_text"]
        with file_transaction(target_folder(output, work.code)), runtime.transaction():
            runtime.save_metadata(work.code, metadata)
            runtime.record_result(work.code, complete_report(report), selection=selection)
    if "images" in stages:
        with file_transaction(target_folder(output, work.code)):
            report["images"] = await artwork.write(scraper.client, work, output, selection)
            report["stages"]["images"] = "failed" if any(r["status"] == "error" for r in report["images"]) else "success"
            runtime.record_result(work.code, complete_report(report), selection=selection)
    if "portraits" in stages:
        report["portraits"] = await portraits.save(metadata.actors, kind=actor_type(work.category))
        report["stages"]["portraits"] = "failed" if any(r["status"] == "error" for r in report["portraits"]) else "success"
    runtime.save_selection(work.code, selection)
    return complete_report(report)


class WorkProcessor:
    """Batch resources and single-work stages, independent of task scheduling."""

    def __init__(self, service, request):
        self.service = service
        self.request = request

    async def __aenter__(self):
        from contextlib import AsyncExitStack

        from .http import WebClient
        from .portraits import Gfriends, actor_image_index

        self.stack = AsyncExitStack()
        try:
            service = self.service
            self.names = ActorAliases.load(service.settings.database)
            service.runtime.save_issues(self.names.pending(service.runtime.issues))
            self.scraper = None
            if self.request.type in ("fill_missing", "refresh"):
                await service.translator.verify(force=True)
            if self.request.type == "remap_actors":
                self.portraits = actor_image_index(service.output)
            else:
                self.scraper = await self.stack.enter_async_context(service.scraper_factory(service.settings))
                if isinstance(self.scraper.client, WebClient):
                    self.scraper.client.runtime = service.runtime
                self.portraits = await self.stack.enter_async_context(Gfriends(service.settings, self.names, service.output))
            return self
        except BaseException:
            await self.stack.aclose()
            raise

    async def __aexit__(self, *exc):
        return await self.stack.__aexit__(*exc)

    async def process(self, work):
        service, request = self.service, self.request
        previous = service.runtime.result(work.code)
        stages = previous.get("incomplete_stages", []) if request.retry_only and previous.get("partial") else []
        report = await self._process(work, stages)
        if request.type in ("fill_missing", "refresh") and (report["status"] in ("found", "unchanged") or not previous):
            service.runtime.record_result(work.code, report)
        if report["status"] in ("found", "unchanged") or report.get("partial"):
            await service.emby.sync(work.code)
        return retry_task_report(report, stages) if stages else report

    async def _process(self, work, stages):
        service, request = self.service, self.request
        if request.type == "remap_actors":
            return await remap_work(work, service.output, self.names, service.runtime,
                                    request.actor_names, request.actor_ids, self.portraits)
        if request.type == "update_portraits":
            metadata = service.runtime.metadata(work.code)
            actors = [Actor(name=name) for name in dict.fromkeys(
                actor.name for actor in metadata.actors if actor.name in request.actor_names)] if metadata else []
            warnings = await self.scraper.resolve_actors(actors, self.names, work.category, {})
            records = await self.portraits.save(actors, replace=True, kind=actor_type(work.category))
            failed = any(record["status"] == "error" for record in records)
            report = complete_report({"code": work.code, "status": "error" if failed else "found",
                "portraits": records, "warnings": warnings, "stages": {"portraits": "failed" if failed else "success"}})
            report["partial"] = failed and any(record["status"] in ("saved", "existing") for record in records)
            if failed:
                report["error"] = "部分头像更新失败" if report["partial"] else "头像更新失败"
            return report
        if stages:
            return await retry_work(self.scraper, work, service.output, service.translator,
                                    service.runtime, self.portraits, stages)
        return await process_work(self.scraper, work, service.output, self.names, service.translator,
            missing_only=request.type == "fill_missing", portraits=self.portraits, runtime=service.runtime)
