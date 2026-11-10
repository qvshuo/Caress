from __future__ import annotations

import json
import re
from datetime import datetime
from urllib.parse import urlparse

from ..http import FetchError, Page, json_error
from ..models import (
    Actor,
    ScrapeRequest,
    SourceId,
    SourceMetadata,
    SourceResult,
    Status,
)
from .base import Source
from .parsing import artwork, first, parse_date, text, texts


class Uncensored(Source):
    pattern: str
    detail_path: str

    async def document(self, request: ScrapeRequest) -> tuple[str, Page]:
        match = re.fullmatch(self.pattern, request.code)
        if not match:
            raise FetchError(Status.UNSUPPORTED, "番号不属于该官方来源")
        identity = match[1]
        if request.external_id and request.external_id != identity:
            raise FetchError(Status.IDENTITY_MISMATCH, "官方作品 ID 与番号不匹配")
        url = self.base + self.detail_path.format(identity)
        if request.source_url:
            url = self.checked_url(request.source_url)
            if urlparse(url).path.rstrip("/") != self.detail_path.format(identity).rstrip("/"):
                raise FetchError(Status.IDENTITY_MISMATCH, "官方作品地址与番号不匹配")
        page = await self.client.get(self.id, url)
        self.checked_url(page.url)
        if urlparse(page.url).path.rstrip("/") != self.detail_path.format(identity).rstrip("/"):
            raise FetchError(Status.IDENTITY_MISMATCH, "官方详情页跳转到了其他作品")
        return identity, page


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
                raise FetchError(Status.IDENTITY_MISMATCH, "一本道作品地址与番号不匹配")
        identity, page = await self.document(request.model_copy(update={"source_url": None}))
        data = page.json()
        if isinstance(data, dict) and (data.get("error") or data.get("errors") or data.get("success") is False):
            raise FetchError(Status.PARSE_ERROR, json_error(data))
        if not isinstance(data, dict) or data.get("MovieID") != identity:
            raise FetchError(Status.IDENTITY_MISMATCH, "一本道接口返回的作品 ID 与番号不匹配")
        if not data.get("Title"):
            raise FetchError(Status.PARSE_ERROR, "一本道作品缺少标题")
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
                if isinstance(rows, dict) and (rows.get("error") or rows.get("errors") or rows.get("success") is False):
                    raise FetchError(Status.PARSE_ERROR, json_error(rows))
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
            except FetchError as exc:
                warnings.append(f"剧照列表获取失败：{exc}")
        return self.found(
            request,
            SourceMetadata(
                code=data["MovieID"],
                title=data["Title"],
                original_title=data["Title"],
                plot=data.get("Desc"),
                release_date=parse_date(data.get("Release")),
                studio="一本道",
                actors=[
                    Actor(name=name)
                    for name in data.get("ActressesJa", [])
                    if name and name != "-"
                ],
                tags=data.get("UCNAME") or [],
                artwork=images,
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
            raise FetchError(Status.PARSE_ERROR, "加勒比作品详情页缺少标题")

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
        release = parse_date(first(selector, row("配信日") + "//text()", row("販売日") + "//text()"))
        if not release:
            # Older official pages omit the date; Caribbeancom IDs encode MMDDYY.
            try:
                release = datetime.strptime(identity[:6], "%m%d%y").date()
            except ValueError:
                pass
        return self.found(
            request,
            SourceMetadata(
                code=identity,
                title=title,
                original_title=title,
                plot=first(
                    selector,
                    '//p[@itemprop="description"]//text()',
                    '//*[@id="moviepages"]//p[1]//text()',
                ),
                release_date=release,
                studio=text(selector, row("スタジオ") + "/a//text()") or "カリビアンコム",
                actors=[
                    Actor(name=name)
                    for name in texts(selector, row("出演") + "//a//text()")
                    if name != "-"
                ],
                tags=texts(selector, row("タグ") + "//a//text()"),
                artwork=artwork(self.id, page.url, covers, samples[:100]),
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
            raise FetchError(Status.PARSE_ERROR, "HEYZO 作品详情页缺少标题")

        def row(label):
            return f'//table[@class="movieInfo"]//tr[td[1][normalize-space(.)="{label}"]]/td[2]'

        video = data.get("video") if isinstance(data.get("video"), dict) else {}
        release = data.get("releasedEvent") if isinstance(data.get("releasedEvent"), dict) else {}
        actors = texts(selector, row("出演") + "/a/span//text()")
        if not actors and isinstance(video.get("actor"), str):
            actors = [video["actor"]]
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
            SourceMetadata(
                code="HEYZO-" + identity,
                title=title,
                original_title=title,
                plot=data.get("description") or text(selector, '//p[@class="memo"]//text()'),
                release_date=parse_date(
                    text(selector, row("公開日") + "//text()") or release.get("startDate")
                ),
                studio=video.get("provider") if isinstance(video.get("provider"), str) else "HEYZO",
                actors=[Actor(name=name) for name in actors if name != "-"],
                tags=texts(selector, '//ul[@class="tag-keyword-list"]//li/a//text()'),
                artwork=artwork(self.id, page.url, covers, samples[:100]),
            ),
        )
