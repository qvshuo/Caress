from __future__ import annotations

import hashlib
import re
import sqlite3
import time
import uuid
from urllib.parse import urlencode

from ..actors import ActorAliases, actor_record, actor_type, comparison_name
from ..catalog import normalize_code
from ..http import FetchError
from ..models import (
    Actor,
    Candidate,
    Category,
    ScrapeRequest,
    SourceId,
    SourceMetadata,
    SourceResult,
    Status,
)
from .base import Source
from .parsing import artwork, parse_date


class JavDBApi(Source):
    id = SourceId.JAVDB
    default_url = "https://jdforrepam.com"

    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self.base = self.settings.javdb_api.base_url.rstrip("/")
        self.device_uuid = str(uuid.uuid4())

    async def api(self, path: str, **query: str | int) -> dict:
        settings = self.settings.javdb_api
        stamp = str(int(time.time()))
        signature = hashlib.md5(
            (stamp + settings.signature_prefix).encode(), usedforsecurity=False
        ).hexdigest()
        params = {
            "app_channel": "official",
            "app_version": settings.app_version,
            "app_version_number": settings.app_version_number,
            "platform": "android",
            "system_version": "13",
            "device_model": "Pixel 6",
            "device_name": "Pixel",
            "device_uuid": self.device_uuid,
            **query,
        }
        page = await self.client.get(
            self.id,
            self.base + path + "?" + urlencode(params),
            headers={
                "jdsignature": f"{stamp}.{settings.signature_version}.{signature}",
                "User-Agent": "Dart/3.4 (dart:io)",
                "Accept": "application/json",
                "Accept-Language": "zh-TW",
            },
        )
        payload = page.json()
        if not isinstance(payload, dict):
            raise FetchError(Status.PARSE_ERROR, "JavDB API envelope changed")
        if payload.get("success") not in (1, True):
            raise FetchError(Status.BLOCKED, "JavDB API rejected the signed request")
        if not isinstance(payload.get("data"), dict):
            raise FetchError(Status.PARSE_ERROR, "JavDB API returned no data object")
        return payload["data"]

    async def _fetch(self, request: ScrapeRequest) -> SourceResult:
        movie_id = request.external_id
        if request.source_url:
            self.checked_url(request.source_url)
            match = re.search(r"/api/v4/movies/([A-Za-z0-9]+)(?:$|\?)", request.source_url)
            if not match:
                raise FetchError(Status.UNSUPPORTED, "expected a JavDB API movie URL")
            movie_id = movie_id or match[1]
        if not movie_id:
            data = await self.api(
                "/api/v2/search", q=self.rules.query_code(request.code), page=1, limit=100
            )
            works = data.get("movies")
            if not isinstance(works, list):
                raise FetchError(Status.PARSE_ERROR, "JavDB API search schema changed")
            candidates = [
                Candidate(
                    code=item["number"],
                    title=item.get("origin_title") or item.get("title"),
                    external_id=str(item["id"]),
                    url=self.base + f"/api/v4/movies/{item['id']}",
                )
                for item in works
                if isinstance(item, dict) and item.get("id") and item.get("number")
            ]
            selected = self.select(request, candidates)
            if not selected:
                if len(works) >= 100:
                    raise FetchError(Status.PARSE_ERROR, "search truncated; supply movie ID to disambiguate")
                return self.missed(request)
            movie_id = selected.external_id
        if not movie_id or not re.fullmatch(r"[A-Za-z0-9]{1,40}", movie_id):
            raise FetchError(Status.UNSUPPORTED, "invalid JavDB movie ID")
        data = await self.api(f"/api/v4/movies/{movie_id}")
        work = data.get("movie")
        if not isinstance(work, dict) or not work.get("number") or not work.get("title"):
            raise FetchError(Status.PARSE_ERROR, "JavDB API detail schema changed")
        if str(work.get("id")) != movie_id:
            raise FetchError(Status.IDENTITY_MISMATCH, "JavDB API returned a different movie ID")
        code = normalize_code(work["number"])
        if not self.rules.matches(request.code, code):
            raise FetchError(Status.IDENTITY_MISMATCH, "JavDB API returned a different catalog")
        kind = work.get("type")
        category = {0: Category.CENSORED, 1: Category.UNCENSORED, 3: Category.FC2}.get(kind, Category.UNKNOWN) if type(kind) is int else Category.UNKNOWN
        warnings = []
        if not code.startswith("FC2PPV-"):
            try:
                self.rules.remember(code, category, self.settings.database)
            except (OSError, sqlite3.Error) as exc:
                warnings.append(f"番号规则保存失败：{exc}")
        title = work.get("origin_title") or work["title"]
        work_actors = work.get("actors") or []
        if not isinstance(work_actors, list):
            raise FetchError(Status.PARSE_ERROR, "JavDB API actor list schema changed")
        actors, actor_ids = [], {}
        for item in work_actors:
            if isinstance(item, dict) and item.get("name"):
                actors.append(Actor(name=item["name"]))
                if item.get("id"):
                    ident = str(item["id"])
                    if item["name"] in actor_ids and actor_ids[item["name"]] != ident:
                        raise FetchError(Status.PARSE_ERROR, "JavDB API returned different actor IDs for the same name")
                    actor_ids[item["name"]] = ident
        metadata = SourceMetadata(
            code=code,
            title=title,
            original_title=work.get("origin_title") or None,
            plot=work.get("summary") or None,
            release_date=parse_date(work.get("release_date")),
            studio=work.get("maker_name") or None,
            directors=[work["director_name"]] if work.get("director_name") else [],
            actors=actors,
            tags=[
                item["name"] for item in work.get("tags") or [] if isinstance(item, dict) and item.get("name")
            ],
            artwork=artwork(
                self.id,
                self.base + "/",
                [work["cover_url"]] if work.get("cover_url") else [],
                [item["large_url"] for item in work.get("preview_images") or [] if item.get("large_url")],
                [work["thumb_url"]] if work.get("thumb_url") else [],
            ),
        )
        return self.found(request, metadata, warnings=warnings, actor_ids=actor_ids)

    async def resolve_actors(self, actors, names: ActorAliases, category, ids) -> list[str]:
        warnings = []
        if self.settings.database:
            names.reload(self.settings.database)
        kind = actor_type(category)
        for actor in actors:
            ident = ids.get(actor.name)
            if not names.needs_lookup(actor.name, kind, ident):
                continue
            try:
                if ident:
                    if not re.fullmatch(r"[A-Za-z0-9]{1,40}", ident):
                        continue
                    data = await self.api(f"/api/v1/actors/{ident}")
                    row = data.get("actor")
                    if not isinstance(row, dict) or str(row.get("id")) != ident:
                        raise ValueError("actor detail identity mismatch")
                    rows = [row]
                else:
                    data = await self.api("/api/v2/search", q=actor.name, type="actor", page=1, limit=100)
                    rows = data.get("actors")
                    if not isinstance(rows, list) or len(rows) >= 100:
                        continue
                records = {}
                for row in rows:
                    if not isinstance(row, dict) or not re.fullmatch(r"[A-Za-z0-9]{1,40}", str(row.get("id", ""))):
                        continue
                    record = actor_record(row)
                    if record and (ident or comparison_name(actor.name) in {
                        comparison_name(name) for name in ActorAliases.record_names(record)
                    }):
                        records[str(row["id"])] = record
                names.remember(records, self.settings.database)
            except (FetchError, OSError, sqlite3.Error, ValueError, TypeError) as exc:
                warnings.append(f"{actor.name}: 演员查询失败（{exc}）")
        return warnings
