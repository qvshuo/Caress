from __future__ import annotations

import json
import re
from urllib.parse import parse_qs, quote, urljoin, urlparse

from ..http import FetchError
from ..models import (
    Actor,
    Evidence,
    Metadata,
    ScrapeRequest,
    SourceId,
    SourceResult,
    Status,
)
from ..parsing import artwork, parse_date
from .base import Source

QUERY = """query FetchDigitalContent($id: ID!) {
 ppvContent(id: $id) { id title description deliveryStartDate duration
 actresses { name } directors { name } series { name } maker { name }
 genres { name } packageImage { largeUrl mediumUrl } sampleImages { imageUrl largeImageUrl } }
}"""


def cid_from_url(url: str) -> str | None:
    parsed = urlparse(url)
    if parsed.scheme != "https" or parsed.hostname not in ("video.dmm.co.jp", "www.dmm.co.jp"):
        return None
    query = parse_qs(parsed.query)
    match = re.search(r"(?:cid=|/id=)([a-z0-9_]+)", parsed.path, re.IGNORECASE)
    cid = query.get("id", query.get("cid", [None]))[0] or (match[1] if match else None)
    return cid.lower() if cid and re.fullmatch(r"[a-z0-9_]{3,80}", cid, re.IGNORECASE) else None


def cid_matches_number(cid: str, code: str, catalog_prefixes=()) -> bool:
    prefix, _, digits = code.rpartition("-")
    if not digits.isdigit():
        return False
    # A known numeric catalog prefix is not a manufacturer namespace.
    for known in catalog_prefixes:
        if re.fullmatch(r"\d+" + re.escape(prefix), known) and cid_matches_number(
            cid, known + "-" + digits
        ):
            return False
    # Namespace prefixes and digital edition suffixes occur in official IDs.
    # Match the full catalog prefix; never shorten 300MIUM to MIUM.
    match = re.fullmatch(
        r"(?:[a-z]_\d+|\d+)?" + re.escape(prefix.lower()) + r"(\d+)(?:so|sp|tk|hd|hhb|hib)?",
        cid.lower(),
    )
    return bool(match and int(match[1]) == int(digits))


class DMM(Source):
    id = SourceId.DMM
    default_url = "https://api.video.dmm.co.jp"

    async def content(self, cid: str) -> dict | None:
        try:
            page = await self.client.post_json(
                self.id,
                self.base + "/graphql",
                {
                    "operationName": "FetchDigitalContent",
                    "variables": {"id": cid},
                    "query": QUERY,
                },
                headers={"Origin": "https://video.dmm.co.jp", "Referer": "https://video.dmm.co.jp/"},
            )
        except FetchError as exc:
            raise FetchError(exc.status, "DMM metadata API: " + str(exc), exc.page) from exc
        document = page.json()
        if not isinstance(document, dict) or not isinstance(document.get("data"), dict):
            raise FetchError(Status.PARSE_ERROR, "DMM GraphQL returned no data")
        work = document["data"].get("ppvContent")
        if work is None and not document.get("errors"):
            return None
        if not isinstance(work, dict) or not work.get("title"):
            raise FetchError(Status.PARSE_ERROR, "DMM GraphQL content schema changed")
        if work.get("id") != cid:
            raise FetchError(Status.IDENTITY_MISMATCH, "DMM returned a different CID")
        return work

    async def search_cids(self, request: ScrapeRequest) -> list[str]:
        try:
            page = await self.client.get(
                self.id,
                "https://www.dmm.co.jp/search/=/searchstr=" + quote(request.code) + "/",
                cookies={"age_check_done": "1"},
            )
        except FetchError as exc:
            raise FetchError(exc.status, "DMM web search: " + str(exc), exc.page) from exc
        selector = page.selector()
        urls = selector.css("a[href]::attr(href)").getall()
        # Modern DMM embeds detailUrl strings in its search-state scripts.
        for escaped in re.findall(r'"detailUrl"\s*:\s*("(?:\\.|[^"\\])*")', page.text):
            urls.append(json.loads(escaped))
        candidates = []
        for value in urls:
            url = urljoin(page.url, value)
            parsed = urlparse(url)
            if parsed.hostname == "video.dmm.co.jp" or "/digital/" in parsed.path:
                if (cid := cid_from_url(url)) and cid_matches_number(
                    cid,
                    request.code,
                    [*self.rules.censored_prefixes, *self.rules.uncensored_prefixes],
                ):
                    if cid not in candidates:
                        candidates.append(cid)
        if not candidates and not selector.css(
            'input[name="searchstr"], input[name="keyword"], #searchstr'
        ):
            raise FetchError(
                Status.PARSE_ERROR, "DMM search contains no verified content links or search UI"
            )
        return candidates

    async def _fetch(self, request: ScrapeRequest) -> SourceResult:
        if request.code.startswith("FC2PPV-"):
            raise FetchError(Status.UNSUPPORTED, "DMM does not resolve FC2 article IDs")
        cid = request.external_id
        if request.source_url:
            from_url = cid_from_url(request.source_url)
            if not from_url:
                raise FetchError(
                    Status.UNSUPPORTED, "expected an official DMM content URL with CID"
                )
            if cid and cid.lower() != from_url:
                raise FetchError(Status.IDENTITY_MISMATCH, "explicit CID differs from URL CID")
            cid = from_url
        explicit = bool(cid)
        work = None
        basis = "validated_cid_candidate"
        if cid:
            cid = cid.lower()
            if not re.fullmatch(r"[a-z0-9_]{3,80}", cid):
                raise FetchError(Status.UNSUPPORTED, "invalid DMM CID")
            if not cid_matches_number(
                cid, request.code, [*self.rules.censored_prefixes, *self.rules.uncensored_prefixes]
            ):
                raise FetchError(
                    Status.IDENTITY_MISMATCH,
                    "CID does not encode the full requested catalog number",
                )
            work = await self.content(cid)
        else:
            cid = self.rules.dmm_cid(request.code)
            if not cid:
                raise FetchError(
                    Status.UNSUPPORTED, "DMM needs an alphanumeric catalog or explicit CID"
                )
            work = await self.content(cid)
            if work is None:
                candidates = await self.search_cids(request)
                candidates = [value for value in candidates if value != cid]
                if candidates:
                    cid = candidates[0]
                    work = await self.content(cid)
                    basis = "official_search_cid"
        if work is None:
            return self.missed(request)
        classification = self.rules.classify(request.code)
        # Adult PPV identifies the catalog, but this response has no censorship field.
        # Preserve maintained number hints rather than inventing native censorship.
        classification.evidence.append(
            Evidence(basis="official_adult_ppv_content", value=cid, source=self.id)
        )
        classification.evidence.append(
            Evidence(
                basis=basis if not explicit else "explicit_verified_cid", value=cid, source=self.id
            )
        )
        package = work.get("packageImage") or {}
        source_url = f"https://video.dmm.co.jp/av/content/?id={cid}"

        def names(key: str) -> list[str]:
            value = work.get(key) or []
            value = [value] if isinstance(value, dict) else value
            return [item["name"] for item in value if isinstance(item, dict) and item.get("name")]

        metadata = Metadata(
            code=request.code,
            title=work["title"],
            original_title=work["title"],
            plot=work.get("description") or None,
            release_date=parse_date(work.get("deliveryStartDate")),
            runtime_minutes=int(work["duration"]) // 60 if work.get("duration") else None,
            studio=next(iter(names("maker")), None),
            series=next(iter(names("series")), None),
            directors=names("directors"),
            actors=[Actor(name=name) for name in names("actresses")],
            genres=names("genres"),
            artwork=artwork(
                self.id,
                source_url,
                [package.get("largeUrl") or package.get("mediumUrl")] if package else [],
                [
                    item["largeImageUrl"] if item.get("largeImageUrl") else item["imageUrl"]
                    for item in work.get("sampleImages") or []
                    if item.get("largeImageUrl") or item.get("imageUrl")
                ],
            ),
            classification=classification,
            external_id=cid,
            source_url=source_url,
        )
        return self.found(request, metadata)
