from __future__ import annotations

import json
import re
from datetime import datetime
from urllib.parse import urlparse

from ..http import FetchError, Page
from ..models import (
    Actor,
    Category,
    Classification,
    Evidence,
    Metadata,
    ScrapeRequest,
    SourceId,
    SourceResult,
    Status,
)
from ..parsing import artwork, first, minutes, parse_date, text, texts
from .base import Source


class Uncensored(Source):
    pattern: str
    detail_path: str

    async def document(self, request: ScrapeRequest) -> tuple[str, Page]:
        match = re.fullmatch(self.pattern, request.code)
        if not match:
            raise FetchError(Status.UNSUPPORTED, "catalog does not belong to this official source")
        identity = match[1]
        if request.external_id and request.external_id != identity:
            raise FetchError(Status.IDENTITY_MISMATCH, "official movie ID differs from catalog")
        url = self.base + self.detail_path.format(identity)
        if request.source_url:
            url = self.checked_url(request.source_url)
            if urlparse(url).path.rstrip("/") != self.detail_path.format(identity).rstrip("/"):
                raise FetchError(Status.IDENTITY_MISMATCH, "official URL differs from catalog")
        page = await self.client.get(self.id, url)
        self.checked_url(page.url)
        if urlparse(page.url).path.rstrip("/") != self.detail_path.format(identity).rstrip("/"):
            raise FetchError(Status.IDENTITY_MISMATCH, "official detail redirected to another work")
        return identity, page

    def classification(self) -> Classification:
        return Classification(
            category=Category.UNCENSORED,
            evidence=[Evidence(basis="catalog_type", value="uncensored", source=self.id)],
        )


class OnePondo(Uncensored):
    id = SourceId.ONEPONDO
    default_url = "https://www.1pondo.tv"
    pattern = r"(\d{6}_\d{3})"
    detail_path = "/dyn/phpauto/movie_details/movie_id/{}.json"

    async def _fetch(self, request: ScrapeRequest) -> SourceResult:
        # User URLs point to the work page, while metadata comes from its JSON document.
        if request.source_url:
            match = re.fullmatch(self.pattern, request.code)
            url = self.checked_url(request.source_url)
            if not match or urlparse(url).path.rstrip("/") != "/movies/" + match[1]:
                raise FetchError(Status.IDENTITY_MISMATCH, "1Pondo URL differs from catalog")
        identity, page = await self.document(request.model_copy(update={"source_url": None}))
        data = page.json()
        if not isinstance(data, dict) or data.get("MovieID") != identity:
            raise FetchError(Status.IDENTITY_MISMATCH, "1Pondo JSON movie ID differs from catalog")
        if not data.get("Title"):
            raise FetchError(Status.PARSE_ERROR, "1Pondo title missing")
        url = self.base + "/movies/" + identity + "/"
        cover = next(
            (data[k] for k in ("ThumbUltra", "ThumbHigh", "ThumbMed", "ThumbLow") if data.get(k)), None
        )
        images = artwork(self.id, url, [cover] if cover else [], [])
        warnings = []
        if data.get("Gallery") or data.get("HasGallery"):
            gallery_url = (
                self.base + "/dyn/dla/json/movie_gallery/" + identity + ".json"
                if data.get("Gallery")
                else self.base + "/dyn/phpauto/movie_galleries/movie_id/" + identity + ".json"
            )
            try:
                gallery = await self.client.get(self.id, gallery_url)
                rows = gallery.json()
                if isinstance(rows, dict):
                    rows = rows.get("Rows", [])
                samples = []
                for row in rows if isinstance(rows, list) else []:
                    if not isinstance(row, dict) or row.get("Protected"):
                        continue
                    if data.get("Gallery") and row.get("Img"):
                        samples.append(self.base + "/dyn/dla/images/" + row["Img"])
                    elif row.get("Filename"):
                        samples.append(
                            self.base + "/assets/sample/" + identity + "/popu/" + row["Filename"]
                        )
                images += artwork(self.id, url, [], samples[:100])
            except FetchError:
                warnings.append("剧照列表暂不可用，已保留元数据。")
        return self.found(
            request,
            Metadata(
                code=data["MovieID"],
                title=data["Title"],
                original_title=data["Title"],
                plot=data.get("Desc"),
                release_date=parse_date(data.get("Release")),
                runtime_minutes=int(data["Duration"]) // 60 if data.get("Duration") else None,
                studio="一本道",
                series=data.get("Series") or None,
                actors=[
                    Actor(name=name)
                    for name in data.get("ActressesJa", [])
                    if name and name != "-"
                ],
                tags=data.get("UCNAME") or [],
                artwork=images,
                classification=self.classification(),
                external_id=identity,
                source_url=url,
            ),
            warnings=warnings,
        )


class Caribbeancom(Uncensored):
    id = SourceId.CARIBBEANCOM
    default_url = "https://www.caribbeancom.com"
    pattern = r"(\d{6}-\d{3})"
    detail_path = "/moviepages/{}/index.html"

    async def _fetch(self, request: ScrapeRequest) -> SourceResult:
        identity, page = await self.document(request)
        selector = page.selector()
        title = first(selector, '//h1[@itemprop="name"]//text()', '//*[@id="moviepages"]//h1[1]//text()')
        if not title:
            raise FetchError(Status.PARSE_ERROR, "Caribbeancom detail title missing")

        def row(label):
            return f'//*[@id="moviepages"]//li[span[1][normalize-space(.)="{label}"]]/span[2]'

        covers = []
        if match := re.search(r"emimg\s*=\s*'([^']+)'", page.text):
            covers.append(match[1])
        elif match := re.search(
            r"posterImage\s*=\s*'([^']+)'\s*\+\s*movie_id\s*\+\s*'([^']+)'", page.text
        ):
            covers.append(match[1] + identity + match[2])
        samples = [
            u
            for u in texts(selector, '//div[contains(@class,"gallery-ratio")]/a/@href')
            if "member" not in u
        ]
        runtime = text(selector, row("再生時間") + "//text()")
        duration = re.fullmatch(r"(\d+):(\d{2}):(\d{2})", runtime or "")
        release = parse_date(first(selector, row("配信日") + "//text()", row("販売日") + "//text()"))
        if not release:
            # Older official pages omit the date; Caribbeancom IDs encode MMDDYY.
            try:
                release = datetime.strptime(identity[:6], "%m%d%y").date()
            except ValueError:
                pass
        return self.found(
            request,
            Metadata(
                code=identity,
                title=title,
                original_title=title,
                plot=first(
                    selector,
                    '//p[@itemprop="description"]//text()',
                    '//*[@id="moviepages"]//p[1]//text()',
                ),
                release_date=release,
                runtime_minutes=int(duration[1]) * 60 + int(duration[2])
                if duration
                else minutes(runtime),
                studio=text(selector, row("スタジオ") + "/a//text()") or "カリビアンコム",
                series=text(selector, row("シリーズ") + "/a//text()"),
                actors=[
                    Actor(name=name)
                    for name in texts(selector, row("出演") + "//a//text()")
                    if name != "-"
                ],
                tags=texts(selector, row("タグ") + "//a//text()"),
                artwork=artwork(self.id, page.url, covers, samples[:100]),
                classification=self.classification(),
                external_id=identity,
                source_url=page.url,
            ),
        )


class Heyzo(Uncensored):
    id = SourceId.HEYZO
    default_url = "https://www.heyzo.com"
    pattern = r"HEYZO-(\d{4})"
    detail_path = "/moviepages/{}/index.html"

    async def _fetch(self, request: ScrapeRequest) -> SourceResult:
        identity, page = await self.document(request)
        selector = page.selector()
        data = {}
        for value in texts(selector, '//script[@type="application/ld+json"]/text()'):
            try:
                entry = json.loads(value)
            except ValueError:
                continue
            if isinstance(entry, dict) and entry.get("name") and entry.get("video"):
                data = entry
                break
        title = data.get("name") or text(selector, '//*[@id="movie"]/h1//text()')
        if not title:
            raise FetchError(Status.PARSE_ERROR, "HEYZO detail title missing")

        def row(label):
            return f'//table[@class="movieInfo"]//tr[td[1][normalize-space(.)="{label}"]]/td[2]'

        video = data.get("video") if isinstance(data.get("video"), dict) else {}
        release = data.get("releasedEvent") if isinstance(data.get("releasedEvent"), dict) else {}
        actors = texts(selector, row("出演") + "/a/span//text()")
        if not actors and isinstance(video.get("actor"), str):
            actors = [video["actor"]]
        duration = video.get("duration") or data.get("duration") or ""
        if match := re.search(r"\bo\s*=\s*(\{[^;]+\});", page.text):
            try:
                duration = json.loads(match[1]).get("full") or duration
            except (ValueError, AttributeError):
                pass
        runtime = None
        if match := re.fullmatch(r"PT(?:(\d+)H)?(?:(\d+)M)?(?:(\d+)S)?", duration):
            runtime = int(match[1] or 0) * 60 + int(match[2] or 0)
        elif match := re.fullmatch(r"(\d+):(\d{2}):(\d{2})", duration):
            runtime = int(match[1]) * 60 + int(match[2])
        covers = (
            [data["image"]]
            if isinstance(data.get("image"), str)
            else texts(selector, '//meta[@property="og:image"]/@content')
        )
        samples = re.findall(
            r'["\'](/contents/[^"\']+/\d+\.\w+)["\']',
            " ".join(texts(selector, '//div[contains(@class,"sample-images")]/script/text()')),
        )
        return self.found(
            request,
            Metadata(
                code="HEYZO-" + identity,
                title=title,
                original_title=title,
                plot=data.get("description") or text(selector, '//p[@class="memo"]//text()'),
                release_date=parse_date(
                    text(selector, row("公開日") + "//text()") or release.get("startDate")
                ),
                runtime_minutes=runtime or None,
                studio=video.get("provider") if isinstance(video.get("provider"), str) else "HEYZO",
                series=(text(selector, row("シリーズ") + "//text()") or "").strip(" -") or None,
                actors=[Actor(name=name) for name in actors if name != "-"],
                tags=texts(selector, '//ul[@class="tag-keyword-list"]//li/a//text()'),
                artwork=artwork(self.id, page.url, covers, samples[:100]),
                classification=self.classification(),
                external_id=identity,
                source_url=page.url,
            ),
        )
