from __future__ import annotations

import re
from urllib.parse import urljoin

from ..http import FetchError
from ..models import (
    Metadata,
    ScrapeRequest,
    SourceId,
    SourceResult,
    Status,
)
from ..parsing import artwork, first, parse_date, text, texts
from .base import Source


class FC2(Source):
    id = SourceId.FC2
    default_url = "https://adult.contents.fc2.com"

    async def _fetch(self, request: ScrapeRequest) -> SourceResult:
        if not request.code.startswith("FC2PPV-"):
            raise FetchError(Status.UNSUPPORTED, "FC2 official only accepts FC2PPV numbers")
        article_id = request.code.removeprefix("FC2PPV-")
        url = (
            self.checked_url(request.source_url)
            if request.source_url
            else self.base + f"/article/{article_id}/?lang=ja"
        )
        try:
            page = await self.client.get(self.id, url, cookies={"language": "ja", "wei6H": "1"})
        except FetchError as exc:
            if exc.status == Status.LOGIN_REQUIRED:
                raise FetchError(exc.status, "FC2 requires an authenticated session", exc.page) from exc
            raise
        selector = page.selector()
        if "couldn't find products" in page.text.lower() or "商品が見つかりません" in page.text:
            return self.missed(request)
        identity = text(selector, '//meta[@property="og:url"]/@content')
        match = re.search(r"/article/(\d+)/?", identity or "")
        title = first(
            selector,
            '//meta[@property="og:title"]/@content',
            '//div[contains(@class,"items_article_headerInfo")]/h3[1]/text()',
        )
        if title:
            title = re.sub(r"^FC2[- ](?:PPV[- ])?\d+\s*", "", title).strip()
        if not match or not title or not selector.css(".items_article_MainitemThumb"):
            raise FetchError(Status.PARSE_ERROR, "FC2 detail identity or title missing")
        plot = text(selector, '//*[contains(@class,"items_article_Description")]//text()')
        warnings = []
        description = text(selector, '//iframe[@data-iframe="description"]/@src')
        if description:
            description_url = self.checked_url(urljoin(page.url, description))
            if f"/article/{article_id}/description" not in description_url:
                raise FetchError(
                    Status.IDENTITY_MISMATCH, "FC2 description iframe belongs to another article"
                )
            try:
                detail = await self.client.get(
                    self.id, description_url, cookies={"language": "ja", "wei6H": "1"}
                )
                plot = text(
                    detail.selector(),
                    "//body//text()[not(ancestor::script or ancestor::style or ancestor::noscript)]",
                )
                if plot and "preparing for translation" in plot.lower():
                    plot = None
                    warnings.append("FC2 description is a translation placeholder")
            except FetchError as exc:
                warnings.append(f"description iframe unavailable: {exc.status}")
        runtime = text(
            selector,
            '//*[contains(@class,"items_article_MainitemThumb")]//p[contains(@class,"items_article_info")]/text()',
        )
        length = re.fullmatch(r"(?:(\d+):)?(\d+):(\d+)", runtime or "")
        runtime_minutes = (
            ((int(length[1] or 0) * 3600 + int(length[2]) * 60 + int(length[3]) + 59) // 60)
            if length
            else None
        )
        metadata = Metadata(
            code="FC2PPV-" + match[1],
            title=title,
            original_title=title,
            plot=plot,
            release_date=parse_date(
                first(
                    selector,
                    '//*[contains(@class,"items_article_Releasedate")]//p//text()',
                    '//div[contains(@class,"items_article_headerInfo")]//p[contains(text(),"Sale Day") or contains(text(),"販売日") or contains(text(),"発売日")]//text()',
                )
            ),
            runtime_minutes=runtime_minutes,
            tags=texts(selector, "//a[@data-article-tag]/@data-tag"),
            artwork=artwork(
                self.id,
                page.url,
                [cover]
                if (
                    cover := first(
                        selector,
                        '//meta[@property="og:image"]/@content',
                        '//*[contains(@class,"items_article_MainitemThumb")]//img/@src',
                    )
                )
                else [],
                texts(selector, '//*[contains(@class,"items_article_SampleImagesArea")]//a/@href'),
            ),
            classification=self.rules.classify(request.code),
            external_id=match[1],
            source_url=page.url,
        )
        return self.found(request, metadata, warnings=warnings)
