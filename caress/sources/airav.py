from __future__ import annotations

import re
from urllib.parse import quote, urljoin

from ..http import FetchError
from ..models import (
    Actor,
    Candidate,
    Metadata,
    ScrapeRequest,
    SourceId,
    SourceResult,
    Status,
)
from ..parsing import artwork, parse_date, text, texts
from .base import Source


class AirAV(Source):
    id = SourceId.AIRAV
    default_url = "https://airav.io"

    async def _fetch(self, request: ScrapeRequest) -> SourceResult:
        if request.source_url:
            url = self.checked_url(request.source_url)
        elif request.external_id:
            url = self.base + "/video?hid=" + quote(request.external_id, safe="")
        else:
            term = request.code.replace("FC2PPV-", "FC2-PPV-", 1)
            page = await self.client.get(self.id, self.base + "/search_result?kw=" + quote(term))
            selector = page.selector()
            cards = selector.css("div.oneVideo")
            candidates = []
            for node in cards:
                heading = text(node, ".//h5//text()") or ""
                href = text(node, ".//a/@href")
                if href and (match := re.match(r"^(\S+)", heading)):
                    candidates.append(
                        Candidate(code=match[1], title=heading, url=urljoin(self.base, href))
                    )
            selected = self.select(request, candidates)
            if not selected:
                # Empty pages omit the result grid, but retain the query and zero-page form.
                heading = " ".join((text(selector, "//h1//text()") or "").split())
                empty = (heading == f"{term} 的搜尋結果"
                         and bool(selector.css('input[name="idx"][max="0"]'))
                         and any(node.attrib.get("value") == term for node in selector.css('input[name="kw"]')))
                if cards and not candidates:
                    raise FetchError(Status.PARSE_ERROR, "AirAV search cards lack catalog numbers or links")
                if not cards and not empty:
                    raise FetchError(Status.PARSE_ERROR, "AirAV search markers missing")
                return self.missed(request)
            url = self.checked_url(selected.url)
        page = await self.client.get(self.id, url)
        selector = page.selector()
        code = text(selector, '//li[contains(text(),"番")]/span/text()')
        heading = text(selector, '//div[contains(@class,"video-title")]//h1//text()')
        if not code or not heading:
            raise FetchError(Status.PARSE_ERROR, "AirAV detail markers missing")
        title = (
            re.sub(r"^\S+\s+", "", heading) if self.rules.matches(code, heading.split()[0]) else heading
        )
        return self.found(
            request,
            Metadata(
                code=code,
                title=title,
                # Chinese editorial titles are not the Japanese original title.
                plot=text(selector, '//div[contains(@class,"video-info")]/p//text()'),
                actors=[
                    Actor(name=name)
                    for name in texts(selector, '//a[contains(@href,"actor?id=")]/text()')
                ],
                studio=text(selector, '//a[contains(@href,"tag?fid=")]/text()'),
                release_date=parse_date(
                    text(
                        selector,
                        '//div[contains(@class,"video-item")]//i[contains(@class,"fa-clock")]/parent::*/text()',
                    )
                ),
                tags=texts(selector, '//a[contains(@href,"tag?tid=")]/text()'),
                artwork=artwork(
                    self.id, page.url, texts(selector, '//meta[@property="og:image"]/@content'), []
                ),
                classification=self.rules.classify(request.code),
                source_url=page.url,
            ),
        )
