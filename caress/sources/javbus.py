from __future__ import annotations

import re
from urllib.parse import quote, urljoin

from ..catalog import normalize_code
from ..http import FetchError, Page, response_issue
from ..models import (
    Actor,
    Candidate,
    Metadata,
    ScrapeRequest,
    SourceId,
    SourceResult,
    Status,
)
from ..parsing import artwork, first, minutes, parse_date, text, texts
from .base import Source


class JavBus(Source):
    id = SourceId.JAVBUS
    default_url = "https://www.javbus.com"

    async def _fetch(self, request: ScrapeRequest) -> SourceResult:
        url = (
            self.checked_url(request.source_url)
            if request.source_url
            else self.base + "/" + quote(self.rules.query_code(request.code))
        )
        try:
            page = await self.client.get(self.id, url, cookies={"dv": "1"})
        except FetchError as exc:
            if exc.status != Status.NOT_FOUND or request.source_url:
                raise
            search = await self.client.get(
                self.id,
                self.base + "/search/" + quote(self.rules.query_code(request.code)),
                cookies={"dv": "1"},
            )
            selector = search.selector()
            if not selector.css(".movie-box") and not selector.css(".container"):
                raise FetchError(Status.PARSE_ERROR, "JavBus search document changed") from None
            candidates = [
                Candidate(
                    code=code,
                    title=text(node, ".//img/@title"),
                    url=urljoin(self.base, node.attrib["href"]),
                )
                for node in selector.css("a.movie-box[href]")
                if (
                    code := first(
                        node, ".//date[1]/text()", './/span[contains(@class,"date")]/text()'
                    )
                )
            ]
            selected = self.select(request, candidates)
            if not selected:
                return self.missed(request)
            page = await self.client.get(
                self.id, self.checked_url(selected.url), cookies={"dv": "1"}
            )
        return self.parse(request, page)

    def parse(self, request: ScrapeRequest, page: Page) -> SourceResult:
        if issue := response_issue(page):
            raise FetchError(*issue, page)
        selector = page.selector()

        def row(label: str, tail: str = "text()") -> str | None:
            return first(
                selector,
                f'//span[contains(@class,"header")][contains(text(),"{label}")]/following-sibling::{tail}',
            )

        code = row("識別碼", "span[1]/text()") or row("识别码", "span[1]/text()")
        title = text(selector, "//h3/text()")
        missing = [field for field, present in (("catalog", code), ("title", title), ("cover", selector.css("a.bigImage"))) if not present]
        if missing:
            raise FetchError(Status.PARSE_ERROR, "JavBus detail markers missing: " + ", ".join(missing))
        code = normalize_code(code)
        title = re.sub(r"^" + re.escape(code) + r"\s*", "", title).strip()
        actors = [
            Actor(
                name=str(node.get_all_text()).strip(),
            )
            for node in selector.css("div.star-name > a[href]")
            if str(node.get_all_text()).strip()
        ]
        metadata = Metadata(
            code=code,
            title=title,
            original_title=title,
            release_date=parse_date(row("發行日期")),
            runtime_minutes=minutes(row("長度")),
            studio=row("製作商", "a[1]/text()"),
            series=row("系列", "a[1]/text()"),
            directors=texts(
                selector, '//span[contains(text(),"導演")]/following-sibling::a/text()'
            ),
            actors=actors,
            genres=texts(selector, '//a[contains(@href,"/genre/")]/text()'),
            artwork=artwork(
                self.id,
                page.url,
                texts(selector, '//a[contains(@class,"bigImage")]/@href'),
                texts(selector, '//a[contains(@class,"sample-box")]/@href'),
            ),
            classification=self.rules.classify(code),
            source_url=page.url,
        )
        return self.found(request, metadata)
