from __future__ import annotations

import re

from ..catalog import matches, query_code
from ..http import FetchError, Page, json_error
from ..models import (
    Actor,
    Candidate,
    ScrapeRequest,
    SourceId,
    SourceMetadata,
    SourceResult,
    Status,
)
from .base import Source
from .parsing import artwork, parse_date, source_code, text, texts


class AVSOX(Source):
    id = SourceId.AVSOX
    default_url = "https://avsox.click"

    async def _fetch(self, request: ScrapeRequest) -> SourceResult:
        if request.source_url:
            url = self.checked_url(request.source_url)
        elif request.external_id:
            if not request.external_id.isalnum():
                raise FetchError(Status.UNSUPPORTED, "AVSOX 作品 ID 无效")
            url = self.base + "/cn/movies/" + request.external_id
        else:
            page = await self.client.post_json(
                self.id, self.base + "/javu/data/api/search",
                [{"search": query_code(request.code), "lang": "cn"}, 60, 1],
            )
            data = page.json()
            if data.get("code") != 200:
                raise FetchError(Status.PARSE_ERROR, json_error(data))
            if not isinstance(data.get("data"), list):
                raise FetchError(Status.PARSE_ERROR, "AVSOX 搜索数据无效")
            candidates = [
                Candidate(code=source_code(work["movieFanHao"], work.get("from")),
                          url=self.base + "/cn/movies/" + work["movieId"])
                for work in data["data"]
            ]
            selected = self.select(request, candidates)
            if not selected:
                return self.missed(request)
            url = self.checked_url(selected.url)
        ident = url.rstrip("/").rsplit("/", 1)[-1]
        identity_page = await self.client.post_json(
            self.id, self.base + "/javu/data/api/getMovie", [ident, "cn"]
        )
        data = identity_page.json()
        if data.get("code") != 200:
            raise FetchError(Status.PARSE_ERROR, json_error(data))
        work = data.get("data")
        if not isinstance(work, dict) or work.get("movieId") != ident:
            raise FetchError(Status.IDENTITY_MISMATCH, "AVSOX 作品身份无法确认")
        code = source_code(work["movieFanHao"], work.get("from"))
        if not matches(request.code, code):
            raise FetchError(Status.IDENTITY_MISMATCH, "AVSOX 作品番号不匹配")
        page = await self.document(url)
        return self.parse(request, page, work)

    async def document(self, url: str) -> Page:
        try:
            page = await self.client.get(self.id, url)
            selector = page.selector()
            if selector.css(".detail-row"):
                return page
        except FetchError as exc:
            if exc.status not in (Status.NETWORK_ERROR, Status.BLOCKED):
                raise
        # Current AVSOX is a Vue SPA. The remote browser renders its JSON-driven UI;
        # Wait for its async JSON response; an initial spinner is not an empty result.
        page = await self.client.get(self.id, url, rendered=True, wait_seconds=3)
        if page.selector().css(".list-loading"):
            raise FetchError(Status.TIMEOUT, "AVSOX 页面内容尚未加载完成")
        return page

    def parse(self, request: ScrapeRequest, page: Page, identity) -> SourceResult:
        selector = page.selector()

        def row(label: str) -> str | None:
            return text(
                selector,
                f'//*[contains(@class,"detail-row")][span[contains(@class,"detail-label")][contains(text(),"{label}")]]/*[contains(@class,"detail-value")]//text()',
            )

        code = row("识别码")
        title = text(selector, "//h1/text()")
        if not code or not title:
            raise FetchError(Status.PARSE_ERROR, "AVSOX 渲染后的详情页缺少必要信息")
        title = re.sub(r"^" + re.escape(code) + r"\s*", "", title).strip()
        raw_code = code
        namespace = identity.get("from")
        code = source_code(code, namespace)
        if not matches(code, source_code(identity["movieFanHao"], namespace)):
            raise FetchError(Status.IDENTITY_MISMATCH, "AVSOX 页面与作品身份不一致")
        metadata = SourceMetadata(
            code=code,
            title=title,
            original_title=title,
            release_date=parse_date(row("发行时间")),
            studio=row("制作商"),
            actors=[
                Actor(
                    name=str(node.get_all_text()).strip(),
                )
                for node in selector.css("a.actress-card[href]")
                if str(node.get_all_text()).strip()
            ],
            genres=texts(selector, '//a[contains(@href,"/genres/")]/text()'),
            artwork=artwork(
                self.id,
                page.url,
                texts(selector, '//img[@alt and @src][starts-with(@alt,"' + raw_code + '")]/@src'),
                texts(selector, '//*[contains(@class,"sample")]//img/@src'),
            ),
        )
        return self.found(request, metadata)
