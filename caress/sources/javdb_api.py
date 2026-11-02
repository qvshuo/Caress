from __future__ import annotations

import hashlib
import re
import time
import uuid
from urllib.parse import urlencode

from ..catalog import normalize_code
from ..http import FetchError
from ..models import (
    Actor,
    Candidate,
    Category,
    Classification,
    Evidence,
    Metadata,
    ScrapeRequest,
    SourceId,
    SourceResult,
    Status,
)
from ..parsing import artwork, minutes, parse_date
from .base import Source


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
        category = work.get("type")
        classification = self.rules.classify(code)
        if category in (0, 1, 3, "0", "1", "3"):
            category = int(category)
            classification = Classification(
                category={0: Category.CENSORED, 1: Category.UNCENSORED, 3: Category.FC2}[category],
                evidence=[Evidence(basis="catalog_type", value=str(category), source=self.id)],
            )
        if code.startswith("FC2PPV-") and classification.category != Category.FC2:
            raise FetchError(Status.PARSE_ERROR, "API type contradicts FC2 number family")
        title = work.get("origin_title") or work["title"]
        source_url = self.base + f"/api/v4/movies/{movie_id}"
        actors = [
            Actor(
                name=item["name"],
            )
            for item in work.get("actors") or []
            if isinstance(item, dict) and item.get("name")
        ]
        metadata = Metadata(
            code=code,
            title=title,
            original_title=work.get("origin_title") or None,
            plot=work.get("summary") or None,
            release_date=parse_date(work.get("release_date")),
            runtime_minutes=minutes(work.get("duration")),
            studio=work.get("maker_name") or None,
            series=work.get("series_name") or None,
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
            classification=classification,
            external_id=movie_id,
            source_url=source_url,
        )
        return self.found(request, metadata)
