"""HTTP boundary: live reads and a small set of explicit processing operations."""

import asyncio
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Literal

import httpx
from fastapi import FastAPI, HTTPException, Query
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from . import __version__
from .emby import EmbyClient, sync_error
from .library import filter_works
from .models import SourceId
from .service import Service
from .settings import Settings
from .sources import SOURCES
from .translation import Translator


class BatchRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    ids: list[str] = Field(min_length=1, max_length=5000)
    action: Literal["scrape", "force", "retry"] = "scrape"
    source: SourceId | None = None
    confirmed: bool = False


class CropRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    token: str = Field(max_length=64)
    box: tuple[int, int, int, int] | None = None
    center: bool = False


def safe_settings(settings):
    data = settings.model_dump(mode="json")
    for name in ("emby", "translation"):
        data[name]["has_api_key"] = bool(data[name].pop("api_key"))
    data["source_defaults"] = {
        str(ident): source.default_url for ident, source in SOURCES.items()
    }
    data["configured"] = bool(
        settings.emby.url and settings.emby.api_key and settings.emby.library_ids
    )
    return data


def updated_settings(current, body):
    data = current.model_dump(mode="json")
    body = dict(body)
    body.pop("configured", None)
    body.pop("source_defaults", None)
    for key in ("emby", "translation"):
        if key in body:
            value = {**body[key]}
            value.pop("has_api_key", None)
            if value.get("api_key") is None:
                value["api_key"] = getattr(current, key).api_key
            body[key] = value
    data.update(body)
    try:
        return Settings.model_validate({**data, "database": current.database})
    except ValidationError, ValueError:
        raise ValueError(
            "设置无效，请检查地址、来源顺序和参数；密钥不会在错误中显示"
        ) from None


def create_app(settings: Settings | None = None):
    settings = settings or Settings()
    service = None

    @asynccontextmanager
    async def lifespan(app):
        nonlocal service
        service = Service(settings)
        app.state.service = service
        try:
            yield
        finally:
            await service.close()

    app = FastAPI(title="Caress", version=__version__, lifespan=lifespan)

    @app.exception_handler(RequestValidationError)
    async def bad_request(request, exc):
        return JSONResponse({"detail": "请求参数无效，请检查输入"}, status_code=422)

    @app.exception_handler(ValueError)
    async def invalid(request, exc):
        return JSONResponse({"detail": str(exc)}, status_code=400)

    @app.exception_handler(httpx.HTTPError)
    async def upstream(request, exc):
        return JSONResponse({"detail": sync_error(exc)}, status_code=502)

    @app.get("/api/health")
    async def health():
        return {"status": "ok", "version": __version__}

    @app.get("/api/settings")
    async def get_settings():
        return safe_settings(service.settings)

    @app.put("/api/settings")
    async def save_settings(body: dict):
        async with service.writer_lock:
            value = updated_settings(service.settings, body)
            if service.batches.running and value.emby != service.settings.emby:
                raise ValueError("批次运行期间不能更换 Emby 连接或目标媒体库")
            value.save()
            service.settings = value
            service.crops.clear()
            return safe_settings(value)

    @app.post("/api/settings/test/emby")
    async def test_emby(body: dict):
        value = updated_settings(service.settings, body)
        client = EmbyClient(value.emby)
        try:
            return await client.test()
        finally:
            await client.close()

    @app.post("/api/settings/libraries")
    async def read_libraries(body: dict):
        value = updated_settings(service.settings, body)
        client = EmbyClient(value.emby)
        try:
            return [
                {
                    "id": str(v["ItemId"]),
                    "name": v["Name"],
                    "type": v.get("CollectionType"),
                }
                for v in await client.libraries()
                if v.get("ItemId")
            ]
        finally:
            await client.close()

    @app.post("/api/settings/test/translation")
    async def test_translation(body: dict):
        value = updated_settings(service.settings, body)
        translator = Translator(value.translation)
        try:
            valid = await translator.verify()
            return {
                "valid": valid,
                "sample": translator.validation_sample,
                "error": translator.validation_error,
            }
        finally:
            await translator.close()

    @app.get("/api/works")
    async def works(
        q: str = "",
        status: str = "",
        offset: int = Query(0, ge=0),
        limit: int = Query(100, ge=1, le=10000),
    ):
        rows = await service.works()
        rows = filter_works(rows, q, status)
        return {"items": rows[offset : offset + limit], "total": len(rows)}

    @app.get("/api/works/{ident}")
    async def detail(ident: str, q: str = "", status: str = ""):
        return await service.detail(ident, q, status)

    @app.get("/api/batch")
    async def batch():
        return service.runtime.batch()

    @app.post("/api/batch")
    async def start(body: BatchRequest):
        if not (
            service.settings.emby.url
            and service.settings.emby.api_key
            and service.settings.emby.library_ids
        ):
            raise ValueError("请先配置 Emby 并选择目标媒体库")
        if body.action == "force" and not body.confirmed:
            raise ValueError("强制重刮会覆盖相关手动修改，必须明确确认")
        if body.action == "retry" and not body.confirmed:
            # A failed force operation can already have changed some fields.
            raise ValueError("重试可能继续此前的部分写入，必须明确确认")
        ids = list(dict.fromkeys(body.ids))
        if any(not i.isalnum() for i in ids):
            raise ValueError("无效的 Emby Item ID")
        return service.batches.start(ids, body.action, body.source)

    @app.post("/api/batch/cancel")
    async def cancel():
        await service.batches.cancel()
        return service.runtime.batch()

    @app.get("/api/images/{ident}/{kind}/{index}")
    async def image(
        ident: str, kind: Literal["Primary", "Backdrop"], index: int, tag: str = ""
    ):
        if not ident.isalnum() or not 0 <= index <= 1000:
            raise HTTPException(404)
        async with service.emby() as client:
            data = await client.image(ident, kind, index, tag=tag)
        from .images import validate_image

        info = await asyncio.to_thread(validate_image, data)
        mime = "image/jpeg" if info.format == "jpeg" else "image/" + info.format
        return Response(data, media_type=mime, headers={"Cache-Control": "no-cache"})

    @app.post("/api/works/{ident}/crop/source")
    async def crop_source(
        ident: str, source: SourceId | None = None, fetch_source: bool = False
    ):
        return await service.crop_source(ident, source, fetch_source=fetch_source)

    @app.get("/api/works/{ident}/crop/{token}/image")
    async def crop_image(ident: str, token: str):
        return Response(
            service.crop_session(ident, token).data,
            media_type="image/png",
            headers={"Cache-Control": "no-store"},
        )

    @app.delete("/api/works/{ident}/crop/{token}")
    async def crop_close(ident: str, token: str):
        service.crop_session(ident, token)
        service.crops.pop(token, None)
        return {"closed": True}

    @app.post("/api/works/{ident}/crop/preview")
    async def crop_preview(ident: str, body: CropRequest):
        return await service.crop_preview(ident, body.token, body.box, body.center)

    @app.post("/api/works/{ident}/crop/apply")
    async def crop_apply(ident: str, body: CropRequest):
        if body.box is None:
            raise ValueError("请选择裁剪范围")
        return await service.crop_apply(ident, body.token, body.box)

    static = Path(__file__).parent / "web/dist"
    if static.is_dir():
        app.mount("/assets", StaticFiles(directory=static), name="assets")

        @app.get("/")
        async def index():
            return FileResponse(
                static / "index.html", headers={"Cache-Control": "no-cache"}
            )

    return app
