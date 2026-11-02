from __future__ import annotations

import re
from datetime import date
from urllib.parse import urljoin

from scrapling.parser import Selector

from .models import Artwork, SourceId


def texts(page: Selector, expression: str) -> list[str]:
    return list(
        dict.fromkeys(str(value).strip() for value in page.xpath(expression).getall() if str(value).strip())
    )


def text(page: Selector, expression: str) -> str | None:
    return " ".join(texts(page, expression)) or None


def first(page: Selector, *expressions: str) -> str | None:
    return next((value for expression in expressions if (value := text(page, expression))), None)


def parse_date(value: str | None) -> date | None:
    if value and (match := re.search(r"(\d{4})[-/年](\d{1,2})[-/月](\d{1,2})", value)):
        try:
            return date(*map(int, match.groups()))
        except ValueError:
            pass
    return None


def minutes(value: str | int | float | None) -> int | None:
    if value is None:
        return None
    match = re.search(r"\d+", str(value))
    return int(match[0]) if match else None


def artwork(
    source: SourceId, referer: str, covers: list[str], samples: list[str], posters: list[str] | None = None
) -> list[Artwork]:
    images = []
    seen: set[tuple[str, str]] = set()
    for role, urls in (("cover", covers), ("poster", posters or []), ("sample", samples)):
        for value in urls:
            url = urljoin(referer, value)
            if (
                not url.startswith("https://")
                or (role, url) in seen
                or "now_printing" in url
                or "nowprinting" in url
            ):
                continue
            seen.add((role, url))
            images.append(Artwork(source=source, role=role, url=url, referer=referer))
    return images
