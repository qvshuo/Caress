from __future__ import annotations

from typing import ClassVar
from urllib.parse import urlparse

from ..catalog import CatalogRules
from ..http import Client, FetchError
from ..models import (
    Candidate,
    Metadata,
    ScrapeRequest,
    SourceId,
    SourceResult,
    Status,
)
from ..settings import Settings


class Source:
    id: ClassVar[SourceId]
    default_url: ClassVar[str]

    def __init__(self, client: Client, settings: Settings, rules: CatalogRules) -> None:
        self.client = client
        self.settings = settings
        self.rules = rules
        settings = settings.sites.get(self.id)
        self.base = (
            settings.base_url if settings and settings.base_url else self.default_url
        ).rstrip("/")

    def checked_url(self, value: str) -> str:
        parsed = urlparse(value)
        if (
            parsed.scheme != "https"
            or parsed.username
            or parsed.password
            or parsed.netloc.lower().removesuffix(":443")
            != urlparse(self.base).netloc.lower().removesuffix(":443")
        ):
            raise FetchError(
                Status.UNSUPPORTED, f"URL must belong to the configured {self.id} origin"
            )
        return value

    async def fetch(self, request: ScrapeRequest) -> SourceResult:
        try:
            result = await self._fetch(request)
            if result.metadata and not self.rules.matches(request.code, result.metadata.code):
                raise FetchError(
                    Status.IDENTITY_MISMATCH, "detail number differs from requested number"
                )
            return result
        except FetchError as exc:
            return SourceResult(
                source=self.id,
                requested_code=request.code,
                status=exc.status,
                message=str(exc),
            )
        except (ValueError, TypeError, KeyError, AttributeError, IndexError) as exc:
            return SourceResult(
                source=self.id,
                requested_code=request.code,
                status=Status.PARSE_ERROR,
                message=f"source document changed ({type(exc).__name__})",
            )

    def found(self, request: ScrapeRequest, metadata: Metadata, **kwargs: object) -> SourceResult:
        return SourceResult(
            source=self.id,
            requested_code=request.code,
            status=Status.FOUND,
            metadata=metadata,
            **kwargs,
        )

    def missed(self, request: ScrapeRequest) -> SourceResult:
        return SourceResult(source=self.id, requested_code=request.code, status=Status.NOT_FOUND)

    def select(self, request: ScrapeRequest, candidates: list[Candidate]) -> Candidate | None:
        # All accepted matches identify the same catalog; retain provider ranking on ties.
        return next((candidate for candidate in candidates if self.rules.matches(request.code, candidate.code)), None)

    async def _fetch(self, request: ScrapeRequest) -> SourceResult:
        raise NotImplementedError
