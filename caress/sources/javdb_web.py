from __future__ import annotations

from urllib.parse import urlencode, urljoin

from ..catalog import normalize_code
from ..http import FetchError, Page
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


class JavDBWeb(Source):
    id = SourceId.JAVDB
    default_url = "https://javdb.com"

    async def _fetch(self, request: ScrapeRequest) -> SourceResult:
        if request.source_url:
            url = self.checked_url(request.source_url)
        elif request.external_id:
            if not request.external_id.isalnum():
                raise FetchError(Status.UNSUPPORTED, "invalid JavDB movie ID")
            url = self.base + "/v/" + request.external_id
        else:
            search = await self.client.get(
                self.id,
                self.base
                + "/search?"
                + urlencode({"q": self.rules.query_code(request.code), "f": "all", "locale": "zh"}),
            )
            selector = search.selector()
            empty = selector.css(".empty-message")
            if any(
                str(node.get_all_text()).strip() in ("暫無內容", "暂无内容", "No results", "No results found")
                for node in empty
            ):
                return self.missed(request)
            if not selector.css(".movie-list"):
                raise FetchError(Status.PARSE_ERROR, "JavDB search container missing")
            candidates = [
                Candidate(
                    code=code,
                    title=text(node, './/div[contains(@class,"video-title")]//text()'),
                    url=urljoin(self.base, node.attrib["href"]),
                )
                for node in selector.css("a.box[href]")
                if (code := text(node, './/div[contains(@class,"video-title")]/strong/text()'))
            ]
            selected = self.select(request, candidates)
            if not selected:
                return self.missed(request)
            url = self.checked_url(selected.url)
        page = await self.client.get(self.id, url + ("&" if "?" in url else "?") + "locale=zh")
        return self.parse(request, page)

    def parse(self, request: ScrapeRequest, page: Page) -> SourceResult:
        selector = page.selector()

        def row(label: str):
            return selector.xpath(
                f'//nav[contains(@class,"movie-panel-info")]//div[contains(@class,"panel-block")][strong[contains(text(),"{label}")]]/span[contains(@class,"value")]'
            )

        code = first(selector, '//a[contains(@class,"copy-to-clipboard")]/@data-clipboard-text')
        original = text(selector, '//h2//span[contains(@class,"origin-title")]//text()')
        title = original or text(selector, '//h2//strong[contains(@class,"current-title")]//text()')
        if not code or not title or not selector.css("nav.movie-panel-info"):
            raise FetchError(Status.PARSE_ERROR, "JavDB detail markers missing")

        def value(label: str) -> str | None:
            nodes = row(label)
            return str(nodes[0].get_all_text()).strip() if nodes else None

        actors = []
        nodes = row("演員")
        if nodes:
            for node in nodes[0].css('a[href*="/actors/"]'):
                actors.append(
                    Actor(
                        name=str(node.get_all_text()).strip(),

                    )
                )
        canonical = normalize_code(code)
        metadata = Metadata(
            code=canonical,
            title=title,
            original_title=original,
            release_date=parse_date(value("日期")),
            runtime_minutes=minutes(value("時長")),
            studio=value("片商"),
            series=value("系列"),
            directors=[value("導演")] if value("導演") else [],
            actors=actors,
            tags=texts(
                selector, '//nav[contains(@class,"movie-panel-info")]//a[contains(@href,"/tags/")]/text()'
            ),
            artwork=artwork(
                self.id,
                page.url,
                texts(selector, '//img[contains(@class,"video-cover")]/@src'),
                texts(
                    selector, '//*[contains(@class,"preview-images")]//a[contains(@class,"tile-item")]/@href'
                ),
            ),
            classification=self.rules.classify(canonical),
            external_id=page.url.split("?", 1)[0].rstrip("/").rsplit("/", 1)[-1],
            source_url=page.url,
        )
        return self.found(request, metadata)
