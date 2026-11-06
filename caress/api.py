from __future__ import annotations

import asyncio
import base64
import hashlib
import re
import sqlite3
import time
from contextlib import asynccontextmanager
from datetime import datetime
from pathlib import Path
from typing import Literal

from fastapi import FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from starlette.middleware.gzip import GZipMiddleware
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from . import __version__
from .metadata_fields import EDITABLE_FIELDS
from .actors import ActorAliases, actor_type, library_actors
from .catalog import CatalogRules
from .directories import Directories
from .images import crop_poster, save_image_choices, selected_image, validate_resources
from .portraits import Gfriends, actor_image
from .models import Actor, ActorMapping, Category, Metadata, SourceId
from .nfo import (
    existing_nfo,
    media_stems,
    read_nfo,
    serialize_nfo,
    target_folder,
    write_nfo_files,
)
from .processing import complete_report, prepare_variant, manual_fields
from .http import FetchError
from .scanner import Work
from .runtime import now
from .settings import SOURCE_INTERVALS, Settings, TranslationSettings
from .sources import SOURCES
from .storage import atomic_write, file_transaction
from .tasks import TaskQueue, TaskRequest
from .translation import Translator, variant_metadata


def redact(value: object) -> object:
    if isinstance(value, dict):
        return {
            key: "***"
            if key in {"api_key", "cookie", "signature_prefix"} and item
            else redact(item)
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [redact(item) for item in value]
    return value


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


def create_app(
    settings: Settings,
    *,
    runner_factory=TaskQueue,
    settings_path: Path | None = None,
    directories: Directories | None = None,
) -> FastAPI:
    @asynccontextmanager
    async def lifespan(app: FastAPI):
        validate_resources()
        app.state.settings = settings
        app.state.configuring = False
        app.state.runner = None
        if directories:
            directories.validate(settings)
        if settings.media_directories and settings.metadata_directory:
            app.state.runner = runner_factory(settings)
            if not app.state.runner.runtime.has_scan():
                await app.state.runner.scan_async()
        try:
            yield
        finally:
            if app.state.runner:
                await app.state.runner.close()

    app = FastAPI(title="Caress", lifespan=lifespan)
    web = Path(__file__).parent / "web/dist"
    app.mount(
        "/assets",
        GZipMiddleware(StaticFiles(directory=web), minimum_size=1024, compresslevel=5),
        name="assets",
    )

    @app.get("/", response_class=HTMLResponse, include_in_schema=False)
    async def home():
        return HTMLResponse(
            (web / "index.html").read_text(encoding="utf-8"),
            headers={"Cache-Control": "no-cache"},
        )

    @app.exception_handler(ValidationError)
    @app.exception_handler(RequestValidationError)
    async def validation_error(_request, exc):
        return JSONResponse(
            status_code=422,
            content={
                "detail": [
                    {key: item[key] for key in ("loc", "msg", "type") if key in item}
                    for item in exc.errors()
                ]
            },
        )

    def runner() -> TaskQueue:
        if app.state.configuring:
            raise ValueError("configuration change in progress")
        if app.state.runner is None:
            raise ValueError("configure media and metadata directories first")
        return app.state.runner

    def current_settings():
        return (app.state.runner.pending_settings or app.state.runner.settings) if app.state.runner else app.state.settings

    def public_settings():
        return {
            **redact(current_settings().model_dump(mode="json")),
            "configured": app.state.runner is not None,
            "pending_settings": bool(app.state.runner and app.state.runner.pending_settings),
            "translation_status": {"valid": app.state.runner.translator.available, "message": app.state.runner.translator.validation_error} if app.state.runner else {"valid": None, "message": "尚未验证"},
            "default_routes": Settings().model_dump(mode="json")["routes"],
            "source_defaults": {
                source.value: factory.default_url for source, factory in SOURCES.items()
            },
            "source_intervals": {
                source.value: interval for source, interval in SOURCE_INTERVALS.items()
            },
        }

    @app.exception_handler(OSError)
    async def filesystem_error(_request, _exc):
        return JSONResponse(status_code=409, content={"detail": "filesystem access failed"})

    @app.exception_handler(sqlite3.Error)
    async def database_error(_request, _exc):
        return JSONResponse(status_code=503, content={"detail": "runtime storage unavailable"})

    @app.exception_handler(ValueError)
    async def invalid_request(_request, exc):
        return JSONResponse(status_code=409, content={"detail": str(exc)})

    @app.exception_handler(KeyError)
    async def not_found(_request, _exc):
        return JSONResponse(status_code=404, content={"detail": "not found"})

    @app.get("/api/health")
    async def health():
        return {"status": "ok", "version": __version__, "storage": "sqlite"}

    @app.get("/api/settings")
    async def read_settings():
        return public_settings()

    @app.get("/api/sources/cooldowns")
    async def source_cooldowns():
        if app.state.runner is None:
            return {}
        return {key: value for key, value in runner().runtime.cooldowns().items()
                if value.get("cooldown_until", 0) > time.time()}

    @app.delete("/api/sources/{source}/cooldown")
    async def clear_source_cooldown(source: SourceId):
        runner().runtime.clear_cooldown(source)
        return await source_cooldowns()

    @app.post("/api/translation/test")
    async def test_translation(value: dict):
        options = {**current_settings().translation.model_dump(), **value}
        if options.get("api_key") == "***":
            options["api_key"] = current_settings().translation.api_key
        translator = Translator(TranslationSettings.model_validate(options))
        started = time.monotonic()
        try:
            valid = await translator.verify(force=True)
            if app.state.runner and not app.state.runner.active() and translator.settings.model_dump() == app.state.runner.settings.translation.model_dump():
                app.state.runner.translator.available = valid
                app.state.runner.translator.validation_error = translator.validation_error
            return {"valid": valid, "message": "测试通过" if valid else translator.validation_error, "sample": translator.validation_sample if valid else None, "elapsed_seconds": time.monotonic() - started}
        finally:
            await translator.close()

    @app.put("/api/settings")
    async def save_settings(value: dict):
        if app.state.configuring:
            raise ValueError("configuration change in progress")
        service = app.state.runner
        if service and service.task_lock.locked():
            raise ValueError("cannot change configuration while metadata is being edited")

        def merge(old, new):
            if isinstance(old, dict) and isinstance(new, dict):
                return {
                    **old,
                    **{
                        k: old.get(k)
                        if k in {"api_key", "cookie"} and v == "***"
                        else merge(old.get(k), v)
                        for k, v in new.items()
                    },
                }
            return new

        previous = current_settings()
        updated = Settings.model_validate(merge(previous.model_dump(mode="json"), value))
        if directories:
            directories.validate(updated)
        ready = bool(updated.media_directories and updated.metadata_directory)
        path = settings_path or Path("/data/settings.json")
        same_output = bool(
            service and ready and updated.metadata_directory.resolve() == service.output
        )
        if same_output:
            updated.actor_aliases_file = previous.actor_aliases_file
            updated.catalog_rules_file = previous.catalog_rules_file
        if service and service.active():
            atomic_write(path, updated.model_dump_json(indent=2))
            service.pending_settings = updated
            app.state.settings = updated
            async def activate_saved_settings():
                pending = service.pending_settings
                if pending is not None:
                    await save_settings(pending.model_dump(mode="json"))
            service.on_idle = activate_saved_settings
            return public_settings()
        app.state.configuring = True
        candidate = None
        try:
            if ready:
                if same_output:
                    preview = await service.scan_async(settings=updated, persist=False)
                else:
                    candidate = runner_factory(updated)
                    await candidate.scan_async()
            previous_catalog = service.runtime.works_data if same_output else None
            had_scan = service.runtime.has_scan() if same_output else False
            scan_saved = False
            try:
                if same_output:
                    service.runtime.save_scan([Work.model_validate(row) for row in preview])
                    scan_saved = True
                atomic_write(path, updated.model_dump_json(indent=2))
            except OSError, ValueError:
                if scan_saved:
                    if had_scan:
                        service.runtime.save_catalog(previous_catalog)
                    else:
                        service.runtime.clear_scan()
                raise
            if same_output:
                old_translator = service.translator
                service.settings = updated
                service.pending_settings = None
                if service._artwork_client is not None:
                    service._artwork_client.settings = updated
                service.translator = Translator(updated.translation, actor_aliases_file=updated.actor_aliases_file)
                app.state.settings = updated
                await old_translator.close()
            else:
                if service:
                    await service.close()
                app.state.runner = candidate
                app.state.settings = updated
                candidate = None
        finally:
            if candidate:
                await candidate.close()
            app.state.configuring = False
        return public_settings()

    @app.get("/api/directories")
    async def directory_list(kind: str = "media", path: Path | None = None):
        if directories is None:
            raise ValueError("directory browser is not configured")
        return directories.list(kind, path)

    @app.post("/api/scan")
    async def scan(request: Request):
        if await request.body():
            raise HTTPException(422, "scan does not accept a request body")
        service = runner()
        return await service.scan_async()

    @app.get("/api/works")
    async def works(status: str | None = None):
        return runner().runtime.works(status) if app.state.runner else []

    @app.get("/api/works/updates")
    async def work_updates(task_ids: str, after: datetime | None = None):
        service = runner()
        if after is not None and after.tzinfo is None:
            raise HTTPException(422, "progress timestamp requires a timezone")
        timestamp = now()
        codes = set()
        for ident in task_ids.split(","):
            task = service.runtime.task_data.get(ident)
            if task is None:
                continue
            for item in task["items"]:
                event = item.get("stage")
                if item["status"] == "running" or (event and (after is None or datetime.fromisoformat(event["time"]) >= after)):
                    codes.add(item["code"])
        return {"time": timestamp, "works": service.runtime.works(codes=sorted(codes))}

    @app.get("/api/works/detail")
    async def work_details(path: str, task_id: str | None = None):
        service = runner()
        row = service.runtime.work_at(path)
        if row is None:
            raise HTTPException(404, "work not found")
        metadata, error, artwork = None, None, []
        artwork_versions = {}
        editable = row["status"] not in ("invalid_key", "conflict")
        if editable:
            work = service.runtime.work(row["code"])
            nfo = existing_nfo(service.output, work)
            if nfo.is_file():
                try:
                    metadata = read_nfo(nfo, work.code).model_dump(mode="json")
                except ValueError as exc:
                    error = str(exc)
            folder = target_folder(service.output, work.code)
            if folder.is_dir():
                for image in folder.iterdir():
                    if not image_file(work, image):
                        continue
                    try:
                        version = image.stat()
                    except FileNotFoundError:
                        continue
                    artwork.append(image.name)
                    artwork_versions[image.name] = f"{version.st_mtime_ns}-{version.st_size}"
        tasks = ([service.runtime.task_data[task_id]] if task_id in service.runtime.task_data else []) if task_id else sorted(service.runtime.task_data.values(), key=lambda t: t["created_at"], reverse=True)
        process = next(({"task_id": t["id"], "type": t["type"], "sequence": t.get("sequence"), "created_at": t["created_at"], "finished_at": t.get("finished_at"), **item} for t in tasks for item in t["items"] if item["code"] == row["code"]), None)
        route = service.settings.routes.get(row["category"], service.settings.routes["unknown"])
        route_sources = route
        if process:
            if sources := (process.get("result") or {}).get("sources"):
                route_sources = [source["source"] for source in sources]
            if process["type"] == "remap_actors":
                route_sources = []
        selection = service.runtime.selection(row["code"])
        return {
            **row,
            "metadata": metadata,
            "variants": sorted(service.runtime.variants(row["code"]), key=lambda v: route.index(v["source"]) if v["source"] in route else 999),
            "selection": selection,
            "route_sources": route_sources,
            "artwork": sorted(artwork),
            "artwork_versions": artwork_versions,
            "editable": editable and not service.work_running(row["code"]),
            "metadata_error": error,
            "last_result": service.runtime.results.get(row["code"]),
            "process": process,
        }

    def image_file(work, path):
        return (
            path.is_file()
            and not path.is_symlink()
            and path.suffix.lower() in {".jpg", ".jpeg", ".png", ".webp", ".gif"}
            and any(path.stem.startswith(stem + "-") for stem in media_stems(work))
        )

    def artwork_file(service, key, file):
        work = service.runtime.work(key)
        folder = target_folder(service.output, key)
        path = folder / file
        if Path(file).name != file or not image_file(work, path):
            raise HTTPException(404, "image not found")
        return work, path

    @app.put("/api/works/{key}/metadata")
    async def save_metadata(key: str, value: dict):
        service = runner()
        inputs = FieldInputs.model_validate(value.pop("field_inputs")) if "field_inputs" in value else None
        if inputs and set(inputs.original_fields) - {"title", "plot", "actors"}:
            raise HTTPException(422, "invalid original fields")
        fields = set(EDITABLE_FIELDS)
        field_sources = value.pop("field_sources", {})
        image_choices = value.pop("image_choices", {})
        sample_source = value.pop("sample_source", None)
        if sample_source is not None and (not isinstance(sample_source, str) or sample_source not in SourceId):
            raise HTTPException(422, "剧照来源不可用")
        if not isinstance(field_sources, dict) or set(field_sources) - fields or not isinstance(image_choices, dict) or len(image_choices) > 102:
            raise HTTPException(422, "invalid field or image selection")
        if any(not isinstance(source, str) for source in field_sources.values()) or any(not isinstance(choice, dict) for choice in image_choices.values()):
            raise HTTPException(422, "invalid selection")
        if any(set(choice) - {"source", "index", "file", "box", "fingerprint"} for choice in image_choices.values()):
            raise HTTPException(422, "invalid image selection")
        image_choices = {role: CropRequest.model_validate(choice).model_dump(exclude_none=True, exclude={"center"}) for role, choice in image_choices.items()}
        if set(value) != fields:
            raise HTTPException(422, "完整填写可编辑字段，番号不可编辑")
        if service.work_running(key):
            raise ValueError("该作品正在刮削")
        async with service.task_lock, service.work_lock(key):
            work = service.runtime.work(key)
            path = existing_nfo(service.output, work)
            previous = {"code": key, "title": key, "source_url": ""}
            if path.is_file():
                try:
                    previous = read_nfo(path, key).model_dump()
                except ValueError:
                    pass
            selection = service.runtime.selection(key)
            variants = {v["source"]: v for v in service.runtime.variants(key)}
            if sample_source:
                variant = variants.get(sample_source)
                items = (variant.get("metadata") or {}).get("artwork", []) if variant and variant.get("status") == "found" else []
                indexes = [i for i, item in enumerate(items) if item["role"] == "sample"][:100]
                if not indexes:
                    raise HTTPException(422, "剧照来源不可用")
                image_choices = {role: choice for role, choice in image_choices.items() if not re.fullmatch(r"fanart[1-9][0-9]{0,2}", role)}
                image_choices.update({f"fanart{i+1}": {"source": sample_source, "index": index} for i, index in enumerate(indexes)})
            metadata = Metadata.model_validate({"code": key, "source_url": "", "original_title": previous.get("original_title"), **value})
            if inputs:
                validated_inputs = Metadata.model_validate({**metadata.model_dump(), **inputs.original_fields}).model_dump(mode="json")
                inputs.original_fields = {key: validated_inputs[key] for key in inputs.original_fields}
            if not metadata.title.strip():
                raise HTTPException(422, "title must not be empty")
            before = Metadata.model_validate(previous)
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
            if "title" in originals and "title" in changed:
                metadata.original_title = originals["title"]
            fingerprints = dict(selection.get("translation_fingerprints", {}))
            errors = {error["field"]: error for error in service.runtime.results.get(key, {}).get("translation_errors", [])}
            for field in changed:
                source = field_sources.get(field, "custom")
                variant = variants.get(source)
                if source != "custom" and (not variant or variant.get("status") != "found" or not variant.get("metadata")):
                    raise HTTPException(422, "字段来源不可用")
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
                            folder, media_stems(work), replace_samples=bool(sample_source))
                    except (FetchError, ValueError, OSError) as exc:
                        raise HTTPException(502, "图片保存失败，保留旧图：" + str(exc)) from exc
                    if sample_source:
                        selection["image_choices"] = {role: choice for role, choice in selection.get("image_choices", {}).items() if not re.fullmatch(r"fanart[1-9][0-9]{0,2}", role)}
                    selection["image_choices"] = {**selection.get("image_choices", {}), **{role: {"source": "custom"} if choice.get("box") is not None or choice.get("file") else choice for role, choice in image_choices.items()}}
                write_nfo_files(service.output, work, serialize_nfo(metadata))
                selection.update(field_sources=provenance,
                    translated_fields=sorted(translated), original_fields=originals, translation_fingerprints=fingerprints,
                    manual_fields=sorted(manual_fields(selection) | changed), actor_mappings=mappings,
                    mapping_fingerprint=mapping_fingerprint, title_text=title_text,
                    manual_image_roles=sorted(set(selection.get("manual_image_roles", [])) | set(image_choices)))
                if sample_source:
                    selection["manual_image_roles"] = sorted(set(selection["manual_image_roles"]) | {"samples"})
                report = {**service.runtime.results.get(key, {}), "status": "found"}
                report.pop("error", None)
                report["stages"] = {**report.get("stages", {}), "metadata": "success"}
                if image_choices:
                    replaced = set(image_choices)
                    if sample_source:
                        replaced.update(f"fanart{i}" for i in range(1, 101))
                    def replaced_image(record):
                        name = record.get("file", "")
                        return name in replaced or any(Path(name).stem == f"{stem}-{role}"
                            for stem in media_stems(work) for role in replaced)
                    report["images"] = [record for record in report.get("images", []) if not replaced_image(record)] + records
                    pending_images = selection.get("pending_images")
                    if any(record.get("status") == "error" for record in report["images"]):
                        report["stages"]["images"] = "failed"
                    elif not pending_images or report["stages"].get("images") not in ("pending", "cancelled"):
                        report["stages"]["images"] = "success"
                        selection.pop("pending_images", None)
                    if selection.get("pending_images"):
                        # A later repair must not overwrite groups explicitly selected in this save.
                        pending_images["protected_roles"] = sorted(set(pending_images.get("protected_roles", [])) | replaced)
                report["translation_errors"] = list(errors.values())
                report["stages"]["translation"] = "failed" if errors else "success" if translated else "skipped"
                service.runtime.record_result(key, complete_report(report), selection=selection)
        service.refresh_work(key)
        return await work_details(str(work.path))

    @app.post("/api/works/{key}/variants/{source}/preview")
    async def preview_variant(key: str, source: SourceId):
        service = runner()
        if service.work_running(key):
            raise ValueError("该作品正在刮削")
        async with service.task_lock, service.work_lock(key):
            variant = next((v for v in service.runtime.variants(key) if v["source"] == source), None)
            if not variant or variant["status"] != "found" or not variant.get("metadata") or "processed_fields" not in variant:
                raise HTTPException(422, "source variant unavailable")
            category = CatalogRules.load(service.settings.catalog_rules_file).classify(key).category
            names = ActorAliases.load(service.settings.actor_aliases_file)
            await service.resolve_actors(Metadata.model_validate(variant["metadata"]).actors, names, category, variant.get("actor_ids", {}))
            variant, _issues = prepare_variant(variant, names, category, code=key)
            await service.translator.verify()
            variant = await service.translator.variant(variant)
            service.runtime.save_variant(key, variant)
        return variant

    @app.get("/api/works/{key}/variants/{source}/artwork")
    async def variant_artwork(key: str, source: SourceId, index: int, poster: bool = False):
        service = runner()
        variant = next((v for v in service.runtime.variants(key) if v["source"] == source), None)
        items = (variant.get("metadata") or {}).get("artwork", []) if variant else []
        if not 0 <= index < len(items):
            raise HTTPException(404, "图片候选不存在")
        item = items[index]
        from .images import validate_image
        client = await service.artwork_client()
        data = await client.image_bytes(source, item["url"], item["referer"])
        image = await asyncio.to_thread(validate_image, data, source)
        if poster:
            image = await asyncio.to_thread(validate_image, (await asyncio.to_thread(crop_poster, image.content))["preview"])
        return Response(image.content, media_type="image/" + image.format, headers={"Cache-Control": "private, max-age=60"})

    @app.get("/api/works/{key}/artwork")
    async def artwork(key: str, file: str):
        _, path = artwork_file(runner(), key, file)
        return FileResponse(path, headers={"Cache-Control": "no-store"})

    async def cropped_image(service, key, request):
        work = service.runtime.work(key)
        choice = request.model_dump(exclude_none=True, exclude={"box", "center"})
        if request.file:
            artwork_file(service, key, request.file)
            choice["source"] = "custom"
        variants = {v["source"]: v for v in service.runtime.variants(key)}
        image = await selected_image(await service.artwork_client(), choice, variants, target_folder(service.output, key), media_stems(work))
        data = image.content
        fingerprint = hashlib.sha256(data).hexdigest()
        result = await asyncio.to_thread(crop_poster, data, request.box, request.center)
        return fingerprint, result

    @app.post("/api/works/{key}/crop")
    async def preview_crop(key: str, request: CropRequest):
        fingerprint, result = await cropped_image(runner(), key, request)
        return {
            **{name: result[name] for name in ("box", "faces", "width", "height")},
            "fingerprint": fingerprint,
            "preview": "data:image/png;base64," + base64.b64encode(result["preview"]).decode(),
        }

    @app.post("/api/tasks")
    async def create_task(request: TaskRequest):
        return runner().create(request)

    @app.get("/api/tasks")
    async def tasks(ids: str | None = None):
        return runner().runtime.tasks(ids.split(",") if ids is not None else None) if app.state.runner else []

    @app.get("/api/tasks/{task_id}")
    async def task(task_id: str, page: int | None = None, page_size: int = 100, status: str = "all", query: str = ""):
        record = runner().runtime.task(task_id)
        if page is None:
            return record
        if page < 1 or page_size not in (100, 1000, 10000) or status not in (
            "all",
            "success",
            "failed",
            "pending",
            "running",
            "cancelled",
            "skipped",
            "partial",
            "todo",
            "done",
        ):
            raise HTTPException(422, "invalid task result filter")
        items = sorted(
            record.pop("items"),
            key=lambda item: (item["status"] != "failed", item["code"]),
        )
        groups = {"todo": {"pending", "failed", "partial", "cancelled"}, "done": {"success", "skipped"}}
        search = query.casefold()
        def matches_query(item):
            if not search:
                return True
            rows = runner().runtime.works(codes=[item["code"]])
            row = rows[0] if len(rows) == 1 else {}
            return search in " ".join([item["code"], row.get("title") or "", row.get("path") or "", *row.get("actors", [])]).casefold()
        items = [
            item
            for item in items
            if (status == "all" or item["status"] in groups.get(status, {status}))
            and matches_query(item)
        ]
        pages = max(1, (len(items) + page_size - 1) // page_size)
        page = min(page, pages)
        return {
            **record,
            "items": items[(page - 1) * page_size : page * page_size],
            "page": page,
            "pages": pages,
            "matched": len(items),
        }

    @app.delete("/api/tasks/{task_id}")
    async def delete_task(task_id: str):
        return runner().delete(task_id)

    @app.post("/api/tasks/{task_id}/cancel")
    async def cancel_task(task_id: str):
        return await runner().cancel(task_id)

    @app.post("/api/tasks/{task_id}/retry")
    async def retry(task_id: str):
        return runner().retry(task_id)

    @app.get("/api/actors/library")
    async def actors_library(query: str = ""):
        service = runner()
        works = [service.runtime.work(row["code"]) for row in service.runtime.works() if row["status"] not in ("invalid_key", "conflict")]
        return await asyncio.to_thread(library_actors, works, service.output, ActorAliases.load(service.settings.actor_aliases_file), query)

    @app.get("/api/actors/{name}/portrait")
    async def actor_portrait(name: str):
        path = actor_image(runner().output, name)
        if not path:
            raise HTTPException(404, "暂无头像")
        return FileResponse(path, headers={"Cache-Control": "no-store"})

    @app.post("/api/actors/{name}/portrait")
    async def update_actor_portrait(name: str):
        service = runner()
        async with service.task_lock:
            if service.active():
                raise ValueError("cannot update portraits while tasks are active")
            names = ActorAliases.load(service.settings.actor_aliases_file)
            works = [service.runtime.work(row["code"]) for row in service.runtime.works() if row["status"] not in ("invalid_key", "conflict")]
            rows = await asyncio.to_thread(library_actors, works, service.output, names, name)
            row = next((r for r in rows if r["name"] == name), None)
            if not row or not row["portrait_eligible"]:
                raise HTTPException(422, "演员身份不明确或不适用")
            kinds = {actor_type(work.category) for work in works if work.code in row["codes"]}
            kind = next(iter(kinds)) if len(kinds) == 1 else None
            actors = [Actor(name=n) for n in (row["actors"] or [name])]
            await service.resolve_actors(actors, names, {0: Category.CENSORED, 1: Category.UNCENSORED}.get(kind, Category.UNKNOWN), {})
            async with Gfriends(service.settings, names, service.output) as portraits:
                records = await portraits.save(actors, replace=True, kind=kind)
            return {"portraits": records}

    return app
