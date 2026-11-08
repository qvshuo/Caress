from __future__ import annotations

import asyncio
import base64
import sqlite3
import time
from contextlib import asynccontextmanager
from datetime import datetime
from pathlib import Path

import httpx
from fastapi import FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import ValidationError
from starlette.middleware.gzip import GZipMiddleware

from . import __version__
from .actors import ActorAliases, library_actors
from .directories import Directories
from .images import crop_poster
from .models import (
    SourceId,
)
from .operations import CropRequest, OperationError
from .portraits import actor_image
from .runtime import now
from .service import Application, Caress
from .settings import SOURCE_INTERVALS, Settings, TranslationSettings
from .sources import SOURCES
from .storage import target_folder
from .tasks import TaskRequest
from .translation import Translator


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


def create_app(
    settings: Settings,
    *,
    service_factory=Caress,
    directories: Directories | None = None,
    static_directory: Path | None = None,
) -> FastAPI:
    application = Application(settings, service_factory, directories)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        await application.start()
        try:
            yield
        finally:
            await application.close()

    app = FastAPI(title="Caress", lifespan=lifespan)
    web = static_directory or Path(__file__).parent / "web/dist"
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

    def configured_service() -> Caress:
        if application.configuring:
            raise ValueError("configuration change in progress")
        if application.service is None:
            raise ValueError("configure media directories first")
        return application.service

    def current_settings():
        return application.current_settings()

    def public_settings():
        options = current_settings()
        return {
            **redact(options.model_dump(mode="json")),
            "configured": application.service is not None,
            "pending_settings": bool(application.service and options.model_dump() != application.service.settings.model_dump()),
            "translation_status": {"valid": application.service.translator.available, "message": application.service.translator.validation_error} if application.service else {"valid": None, "message": "尚未验证"},
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
        return JSONResponse(status_code=exc.status_code if isinstance(exc, OperationError) else 409, content={"detail": str(exc)})

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
        if application.service is None:
            return {}
        return {key: value for key, value in configured_service().runtime.cooldowns().items()
                if value.get("cooldown_until", 0) > time.time()}

    @app.delete("/api/sources/{source}/cooldown")
    async def clear_source_cooldown(source: SourceId):
        configured_service().runtime.clear_cooldown(source)
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
            if application.service and not application.service.tasks.active() and translator.settings.model_dump() == application.service.settings.translation.model_dump():
                application.service.translator.available = valid
                application.service.translator.validation_error = translator.validation_error
            return {"valid": valid, "message": "测试通过" if valid else translator.validation_error, "sample": translator.validation_sample if valid else None, "elapsed_seconds": time.monotonic() - started}
        finally:
            await translator.close()

    @app.put("/api/settings")
    async def save_settings(value: dict):
        await application.configure(value)
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
        service = configured_service()
        return await service.scan_async()

    @app.get("/api/works")
    async def works(status: str | None = None):
        return configured_service().runtime.works(status) if application.service else []

    @app.get("/api/works/updates")
    async def work_updates(task_ids: str, after: datetime | None = None):
        service = configured_service()
        if after is not None and after.tzinfo is None:
            raise HTTPException(422, "progress timestamp requires a timezone")
        timestamp = now()
        progress_at = service.runtime.task_updates(task_ids.split(","), after)
        return {"time": timestamp, "works": [{**work, "progress_at": progress_at.get(work["code"])}
                for work in service.runtime.works(codes=sorted(progress_at))]}

    @app.get("/api/works/detail")
    async def work_details(code: str, task_id: str | None = None):
        service = configured_service()
        row = service.runtime.find_work(code)
        if row is None:
            raise HTTPException(404, "work not found")
        metadata, artwork = None, []
        artwork_versions = {}
        editable = row["status"] != "invalid_key"
        if editable:
            work = service.runtime.work(row["code"])
            saved = service.runtime.metadata(work.code)
            metadata = saved.model_dump(mode="json") if saved else None
            folder = target_folder(service.output, work.code)
            if folder.is_dir():
                for image in folder.iterdir():
                    if not service.operations.image_file(work, image):
                        continue
                    try:
                        version = image.stat()
                    except FileNotFoundError:
                        continue
                    artwork.append(image.name)
                    artwork_versions[image.name] = f"{version.st_mtime_ns}-{version.st_size}"
        process = service.runtime.work_process(row["code"], task_id)
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
            "media": service.runtime.media(row["code"]),
            "metadata": metadata,
            "variants": sorted(service.runtime.variants(row["code"]), key=lambda v: route.index(v["source"]) if v["source"] in route else 999),
            "selection": selection,
            "route_sources": route_sources,
            "artwork": sorted(artwork),
            "artwork_versions": artwork_versions,
            "editable": editable and not service.work_running(row["code"]),
            "last_result": service.runtime.result(row["code"]) or None,
            "health": service.health(code).model_dump() if editable else None,
            "process": process,
        }

    @app.put("/api/works/{key}/metadata")
    async def save_metadata(key: str, value: dict):
        await configured_service().operations.save_metadata(key, value)
        return await work_details(key)

    @app.post("/api/works/{key}/variants/{source}/preview")
    async def preview_variant(key: str, source: SourceId):
        return await configured_service().operations.preview_variant(key, source)

    @app.get("/api/works/{key}/variants/{source}/artwork")
    async def variant_artwork(key: str, source: SourceId, index: int, poster: bool = False):
        service = configured_service()
        async with service.operation_lock:
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
        _, path = configured_service().operations.artwork_file(key, file)
        return FileResponse(path, headers={"Cache-Control": "no-store"})

    @app.post("/api/works/{key}/crop")
    async def preview_crop(key: str, request: CropRequest):
        fingerprint, result = await configured_service().operations.cropped_image(key, request)
        return {
            **{name: result[name] for name in ("box", "faces", "width", "height")},
            "fingerprint": fingerprint,
            "preview": "data:image/png;base64," + base64.b64encode(result["preview"]).decode(),
        }

    @app.post("/api/emby/test")
    async def test_emby(value: dict):
        from .emby import EmbyClient, sync_error
        from .settings import EmbySettings
        options = {**current_settings().emby.model_dump(), **value}
        if options.get("api_key") == "***":
            options["api_key"] = current_settings().emby.api_key
        client = EmbyClient(EmbySettings.model_validate(options))
        try:
            try:
                return await client.test()
            except (httpx.HTTPError, ValueError) as exc:
                return {"valid": False, "message": sync_error(exc)}
        finally:
            await client.close()

    @app.post("/api/works/{key}/sync")
    async def sync_work(key: str, relink: bool = False):
        service = configured_service()
        service.runtime.work_row(key)
        if service.runtime.metadata(key) is None:
            raise ValueError("尚无作品元数据")
        async with service.work_lock(key):
            return await service.emby.sync(key, relink=relink, force=True)

    @app.post("/api/tasks")
    async def create_task(request: TaskRequest):
        return configured_service().tasks.create(request)

    @app.get("/api/tasks")
    async def tasks(ids: str | None = None):
        return configured_service().runtime.tasks(ids.split(",") if ids is not None else None) if application.service else []

    @app.get("/api/tasks/{task_id}")
    async def task(task_id: str, page: int | None = None, page_size: int = 100, status: str = "all", query: str = ""):
        if page is None:
            return configured_service().runtime.task(task_id)
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
        groups = {"todo": {"pending", "failed", "partial", "cancelled"}, "done": {"success", "skipped"}}
        statuses = None if status == "all" else groups.get(status, {status})
        return configured_service().runtime.task_page(task_id, page, page_size, statuses, query)

    @app.delete("/api/tasks/{task_id}")
    async def delete_task(task_id: str):
        return configured_service().tasks.delete(task_id)

    @app.post("/api/tasks/{task_id}/cancel")
    async def cancel_task(task_id: str):
        return await configured_service().tasks.cancel(task_id)

    @app.post("/api/tasks/{task_id}/retry")
    async def retry(task_id: str):
        return configured_service().tasks.retry(task_id)

    @app.get("/api/actors/library")
    async def actors_library(query: str = ""):
        service = configured_service()
        works = [service.runtime.work(row["code"]) for row in service.runtime.works() if row["status"] != "invalid_key"]
        return await asyncio.to_thread(library_actors, works, service.output, ActorAliases.load(service.settings.database), query)

    @app.get("/api/actors/{name}/portrait")
    async def actor_portrait(name: str):
        path = actor_image(configured_service().output, name)
        if not path:
            raise HTTPException(404, "暂无头像")
        return FileResponse(path, headers={"Cache-Control": "no-store"})

    @app.post("/api/actors/{name}/portrait")
    async def update_actor_portrait(name: str):
        return await configured_service().operations.update_actor_portrait(name)

    return app
