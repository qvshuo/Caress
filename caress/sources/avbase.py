from __future__ import annotations

import json
from datetime import datetime
from urllib.parse import quote

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
from ..parsing import artwork, minutes, parse_date, text
from .base import Source


class AVbase(Source):
    id = SourceId.AVBASE
    default_url = "https://www.avbase.net"

    @staticmethod
    def props(page: Page) -> dict:
        data = text(page.selector(), '//script[@id="__NEXT_DATA__"]/text()')
        if not data:
            raise FetchError(Status.PARSE_ERROR, "AVbase structured data missing")
        data = json.loads(data)
        for key in ("props", "pageProps"):
            if not isinstance(data, dict) or not isinstance(data.get(key), dict):
                raise FetchError(Status.PARSE_ERROR, "invalid AVbase page properties")
            data = data[key]
        return data

    async def _fetch(self, request: ScrapeRequest) -> SourceResult:
        if request.source_url:
            url = self.checked_url(request.source_url)
        elif request.external_id:
            url = self.base + "/works/" + quote(request.external_id, safe=":")
        else:
            page = await self.client.get(
                self.id, self.base + "/works?q=" + quote(self.rules.query_code(request.code))
            )
            works = self.props(page).get("works")
            if not isinstance(works, list):
                raise FetchError(Status.PARSE_ERROR, "AVbase search results missing")
            candidates = []
            for work in works:
                if not isinstance(work, dict):
                    continue
                code = work.get("work_id")
                if not isinstance(code, str):
                    continue
                identity = (str(work["prefix"]) + ":" if work.get("prefix") else "") + code
                candidates.append(
                    Candidate(
                        code=code,
                        title=work.get("title"),
                        external_id=identity,
                        url=self.base + "/works/" + quote(identity, safe=":"),
                    )
                )
            selected = self.select(request, candidates)
            if not selected:
                return self.missed(request)
            url = self.checked_url(selected.url)
        page = await self.client.get(self.id, url)
        work = self.props(page).get("work")
        if not isinstance(work, dict) or not work.get("work_id") or not work.get("title"):
            raise FetchError(Status.PARSE_ERROR, "AVbase work fields missing")
        products = [p for p in work.get("products") or [] if isinstance(p, dict)]

        def product_name(key):
            return next(
                (
                    p[key]["name"]
                    for p in products
                    if isinstance(p.get(key), dict) and p[key].get("name")
                ),
                None,
            )

        info = next((p["iteminfo"] for p in products if isinstance(p.get("iteminfo"), dict)), {})
        release = parse_date(work.get("min_date"))
        if not release and work.get("min_date"):
            try:
                release = datetime.strptime(work["min_date"][:15], "%a %b %d %Y").date()
            except ValueError:
                pass
        volume = str(info.get("volume") or "")
        runtime = (
            int(volume.split(":")[0]) * 60 + int(volume.split(":")[1])
            if volume.count(":") == 2
            else minutes(volume)
        )
        product = next((p for p in products if p.get("image_url")), {})
        samples = [
            image.get("l") or image.get("s")
            for image in product.get("sample_image_urls") or []
            if isinstance(image, dict) and (image.get("l") or image.get("s"))
        ]
        return self.found(
            request,
            Metadata(
                code=work["work_id"],
                title=work["title"],
                original_title=work["title"],
                release_date=release,
                runtime_minutes=runtime,
                studio=product_name("maker"),
                series=product_name("series"),
                directors=[info["director"]] if info.get("director") else [],
                actors=[
                    Actor(
                        name=c["actor"]["name"],
                    )
                    for c in work.get("casts") or []
                    if isinstance(c, dict)
                    and isinstance(c.get("actor"), dict)
                    and c["actor"].get("name")
                ],
                genres=[
                    g["name"]
                    for g in work.get("genres") or []
                    if isinstance(g, dict) and g.get("name")
                ],
                # Product descriptions may be truncated; never use them as a full plot.
                artwork=artwork(
                    self.id, page.url, [product["image_url"]] if product else [], samples[:100]
                ),
                classification=self.rules.classify(request.code),
                external_id=url.rsplit("/", 1)[-1],
                source_url=page.url,
            ),
        )
