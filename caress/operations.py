"""Explicit work edits and previews. HTTP handlers only translate requests/results."""
import asyncio
import hashlib
import re
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from . import artwork
from .actors import ActorAliases, actor_type, library_actors
from .catalog import CatalogRules
from .http import FetchError
from .images import crop_poster, save_image_choices, selected_image
from .models import (
    EDITABLE_FIELDS,
    Actor,
    ActorMapping,
    Category,
    SourceId,
    WorkMetadata,
)
from .portraits import Gfriends
from .processing import complete_report, manual_fields, prepare_variant
from .scanner import validate_roots
from .storage import file_transaction, image_stems, target_folder
from .translation import variant_metadata


class OperationError(ValueError):
    def __init__(self, status_code, message):
        super().__init__(message)
        self.status_code = status_code


class CropRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    file: str | None = None
    source: SourceId | Literal["custom"] | None = None
    index: int | None = None
    box: tuple[int, int, int, int] | None = None
    center: bool = False
    fingerprint: str | None = None


class FieldInputs(BaseModel):
    model_config = ConfigDict(extra="forbid")
    original_fields: dict = Field(default_factory=dict)
    actor_mappings: list[ActorMapping] = Field(default_factory=list)
    title_text: str | None = None
    mapping_fingerprint: str = ""


class WorkOperations:
    def __init__(self, service):
        self.service = service

    def task_parameters(self, request):
        service = self.service
        validate_roots(service.settings.media_directories, service.output)
        if request.actor_ids and request.type != "remap_actors":
            raise ValueError("actor_ids is only supported for actor remapping")
        eligible = {
            m["code"]
            for m in service.runtime.works()
            if m["status"] != "invalid_key"
        }
        if request.type in ("update_portraits", "remap_actors"):
            names = ActorAliases.load(service.settings.database)
            actors = library_actors([service.runtime.work(code) for code in eligible], service.output,
                                   names)
            eligible_actors = {actor["name"]: actor for actor in actors
                               if request.type == "remap_actors" or actor["portrait_eligible"]}
            selected_names = set(request.actor_names)
            if request.type == "remap_actors":
                # Pin identities across predecessor renames and partial retries.
                selected_ids = set(request.actor_ids)
                selected_ids.update(ident for name in selected_names if (ident := names.resolve_id(name)))
                for code in eligible:
                    for row in service.runtime.selection(code).get("actor_mappings", []):
                        if row["name"] in selected_names or row.get("actor_id") in selected_ids:
                            selected_names.add(row["name"])
                            if row.get("actor_id"):
                                selected_ids.add(row["actor_id"])
                if request.retry_only:
                    selected_names.intersection_update(eligible_actors)
            if not selected_names or selected_names - eligible_actors.keys():
                raise ValueError("no eligible actors or requested actor is unavailable")
            eligible = {code for name in selected_names for code in eligible_actors[name]["codes"]}
        elif request.actor_names:
            raise ValueError("actor_names is only supported for actor tasks")
        keys = sorted(set(request.codes) if request.codes else eligible)
        if not keys or any(k not in eligible for k in keys):
            raise ValueError("no eligible works or requested filename is invalid")
        params = request.model_dump(mode="json")
        params["codes"] = keys
        if request.type in ("update_portraits", "remap_actors"):
            params["actor_names"] = sorted(selected_names)
        if request.type == "remap_actors":
            params["actor_ids"] = sorted(selected_ids)
        return params

    def image_file(self, work, path):
        return (
            path.is_file()
            and not path.is_symlink()
            and path.suffix.lower() in {".jpg", ".jpeg", ".png", ".webp", ".gif"}
            and any(path.stem.startswith(stem + "-") for stem in image_stems(work))
        )

    def artwork_file(self, key, file):
        service = self.service
        work = service.runtime.work(key)
        folder = target_folder(service.output, key)
        path = folder / file
        if Path(file).name != file or not self.image_file(work, path):
            raise OperationError(404, "image not found")
        return work, path

    async def save_metadata(self, key: str, value: dict):
        service = self.service
        inputs = FieldInputs.model_validate(value.pop("field_inputs")) if "field_inputs" in value else None
        if inputs and set(inputs.original_fields) - {"title", "plot", "actors"}:
            raise OperationError(422, "invalid original fields")
        fields = set(EDITABLE_FIELDS)
        field_sources = value.pop("field_sources", {})
        image_choices = value.pop("image_choices", {})
        sample_source = value.pop("sample_source", None)
        if sample_source is not None and (not isinstance(sample_source, str) or sample_source not in SourceId):
            raise OperationError(422, "剧照来源不可用")
        if not isinstance(field_sources, dict) or set(field_sources) - fields or not isinstance(image_choices, dict) or len(image_choices) > 102:
            raise OperationError(422, "invalid field or image selection")
        if any(not isinstance(source, str) for source in field_sources.values()) or any(not isinstance(choice, dict) for choice in image_choices.values()):
            raise OperationError(422, "invalid selection")
        if any(set(choice) - {"source", "index", "file", "box", "fingerprint"} for choice in image_choices.values()):
            raise OperationError(422, "invalid image selection")
        image_choices = {role: CropRequest.model_validate(choice).model_dump(exclude_none=True, exclude={"center"}) for role, choice in image_choices.items()}
        if set(value) != fields:
            raise OperationError(422, "完整填写可编辑字段，番号不可编辑")
        if service.work_running(key):
            raise ValueError("该作品正在刮削")
        async with service.work_lock(key):
            async with service.operation_lock:
                work = service.runtime.work(key)
                saved = service.runtime.metadata(key)
                previous = saved.model_dump() if saved else {"code": key, "title": key}
                selection = service.runtime.selection(key)
                variants = {v["source"]: v for v in service.runtime.variants(key)}
                if sample_source:
                    variant = variants.get(sample_source)
                    items = (variant.get("metadata") or {}).get("artwork", []) if variant and variant.get("status") == "found" else []
                    indexes = [i for i, item in enumerate(items) if item["role"] == "sample"][:100]
                    if not indexes:
                        raise OperationError(422, "剧照来源不可用")
                    image_choices = {role: choice for role, choice in image_choices.items() if not re.fullmatch(r"fanart[1-9][0-9]{0,2}", role)}
                    image_choices.update({f"fanart{i+1}": {"source": sample_source, "index": index} for i, index in enumerate(indexes)})
                metadata = WorkMetadata.model_validate({"code": key, "original_title": previous.get("original_title"), **value})
                if inputs:
                    validated_inputs = WorkMetadata.model_validate({**metadata.model_dump(), **inputs.original_fields}).model_dump(mode="json")
                    inputs.original_fields = {key: validated_inputs[key] for key in inputs.original_fields}
                if not metadata.title.strip():
                    raise OperationError(422, "title must not be empty")
                before = WorkMetadata.model_validate(previous)
                changed = set(field_sources) | {field for field in fields if getattr(metadata, field) != getattr(before, field)}
                provenance = dict(selection.get("field_sources", {}))
                translated = set(selection.get("translated_fields", []))
                pinned = inputs.model_dump(mode="json") if inputs else selection
                originals = dict(pinned.get("original_fields", {}))
                mappings = pinned.get("actor_mappings", [])
                mapping_fingerprint = pinned.get("mapping_fingerprint", "")
                title_text = pinned.get("title_text")
                if "title" in changed and field_sources.get("title", "custom") == "custom":
                    title_text = None
                fingerprints = dict(selection.get("translation_fingerprints", {}))
                errors = {error["field"]: error for error in service.runtime.result(key).get("translation_errors", [])}
                for field in changed:
                    source = field_sources.get(field, "custom")
                    variant = variants.get(source)
                    if source != "custom" and (not variant or variant.get("status") != "found" or not variant.get("metadata")):
                        raise OperationError(422, "字段来源不可用")
                    provenance[field] = source
                    translated.discard(field)
                    fingerprints.pop(field, None)
                    errors.pop(field, None)
                    if source != "custom":
                        # Preview inputs stay pinned when later candidates arrive.
                        same_input = inputs is None or field not in {"title", "plot", "actors"} or inputs.original_fields.get(field) == variant.get("original_fields", {}).get(field)
                        if same_input and variant_metadata(variant).model_dump(mode="json").get(field) == value[field]:
                            if field in variant.get("translated_fields", []):
                                translated.add(field)
                            if inputs is None and field in ("title", "plot", "actors"):
                                originals[field] = variant.get("original_fields", {}).get(field)
                            if inputs is None and field == "title":
                                metadata.original_title = originals[field]
                                title_text = variant.get("title_text")
                            if inputs is None and field == "actors":
                                mappings = variant.get("actor_mappings", [])
                                mapping_fingerprint = variant.get("mapping_fingerprint", "")
                            if field in variant.get("translation_fingerprints", {}):
                                fingerprints[field] = variant["translation_fingerprints"][field]
                            errors.update({error["field"]: error for error in variant.get("translation_errors", []) if error["field"] == field})
                folder = target_folder(service.output, key)
                # File rollback encloses the single SQLite output commit as well.
                with file_transaction(folder):
                    records = []
                    if image_choices:
                        try:
                            records = await save_image_choices(await service.artwork_client(), image_choices, variants,
                                folder, image_stems(work), replace_samples=bool(sample_source))
                        except (FetchError, ValueError, OSError) as exc:
                            raise OperationError(502, "图片保存失败，保留旧图：" + str(exc)) from exc
                        artwork.select_manual(selection, image_choices, records, replace_samples=bool(sample_source), variants=variants, folder=folder)
                    with service.runtime.transaction():
                        service.runtime.save_metadata(key, metadata)
                        selection.update(field_sources=provenance,
                            translated_fields=sorted(translated), original_fields=originals, translation_fingerprints=fingerprints,
                            manual_fields=sorted(manual_fields(selection) | changed), actor_mappings=mappings,
                            mapping_fingerprint=mapping_fingerprint, title_text=title_text)
                        report = {**service.runtime.result(key), "status": "found"}
                        report.pop("error", None)
                        report["stages"] = {**report.get("stages", {}), "metadata": "success"}
                        if image_choices:
                            replaced = set(image_choices)
                            if sample_source:
                                replaced.update(f"fanart{i}" for i in range(1, 101))
                            def replaced_image(record):
                                name = record.get("file", "")
                                return name in replaced or any(Path(name).stem == f"{stem}-{role}"
                                    for stem in image_stems(work) for role in replaced)
                            report["images"] = [record for record in report.get("images", []) if not replaced_image(record)] + records
                            report["stages"]["images"] = "pending" if artwork.state(selection).get("pending") else "success"
                        report["translation_errors"] = list(errors.values())
                        report["stages"]["translation"] = "failed" if errors else "success" if translated else "skipped"
                        service.runtime.record_result(key, complete_report(report), selection=selection)
            await service.emby.sync(key)
            service.refresh_work(key)
        return work.code

    async def preview_variant(self, key: str, source: SourceId):
        service = self.service
        if service.work_running(key):
            raise ValueError("该作品正在刮削")
        async with service.work_lock(key), service.operation_lock:
            variant = next((v for v in service.runtime.variants(key) if v["source"] == source), None)
            if not variant or variant["status"] != "found" or not variant.get("metadata") or "processed_fields" not in variant:
                raise OperationError(422, "source variant unavailable")
            category = CatalogRules.load(service.settings.database).classify(key).category
            names = ActorAliases.load(service.settings.database)
            await service.resolve_actors(variant_metadata(variant).actors, names, category, variant.get("actor_ids", {}))
            variant, _issues = prepare_variant(variant, names, category, code=key)
            await service.translator.verify()
            variant = await service.translator.variant(variant)
            service.runtime.save_variant(key, variant)
        return variant


    async def cropped_image(self, key, request):
        service = self.service
        async with service.operation_lock:
            work = service.runtime.work(key)
            choice = request.model_dump(exclude_none=True, exclude={"box", "center"})
            if request.file:
                service.operations.artwork_file(key, request.file)
                choice["source"] = "custom"
            variants = {v["source"]: v for v in service.runtime.variants(key)}
            image = await selected_image(await service.artwork_client(), choice, variants, target_folder(service.output, key), image_stems(work))
            data = image.content
            fingerprint = hashlib.sha256(data).hexdigest()
            result = await asyncio.to_thread(crop_poster, data, request.box, request.center)
            return fingerprint, result


    async def update_actor_portrait(self, name: str):
        service = self.service
        async with service.operation_lock:
            if service.tasks.active():
                raise ValueError("cannot update portraits while tasks are active")
            names = ActorAliases.load(service.settings.database)
            works = [service.runtime.work(row["code"]) for row in service.runtime.works() if row["status"] != "invalid_key"]
            rows = await asyncio.to_thread(library_actors, works, service.output, names, name)
            row = next((r for r in rows if r["name"] == name), None)
            if not row or not row["portrait_eligible"]:
                raise OperationError(422, "演员身份不明确或不适用")
            kinds = {actor_type(work.category) for work in works if work.code in row["codes"]}
            kind = next(iter(kinds)) if len(kinds) == 1 else None
            actors = [Actor(name=n) for n in (row["actors"] or [name])]
            await service.resolve_actors(actors, names, {0: Category.CENSORED, 1: Category.UNCENSORED}.get(kind, Category.UNKNOWN), {})
            async with Gfriends(service.settings, names, service.output) as portraits:
                records = await portraits.save(actors, replace=True, kind=kind)
            if any(record["status"] == "saved" for record in records):
                for code in row["codes"]:
                    await service.emby.sync(code)
            return {"portraits": records}
