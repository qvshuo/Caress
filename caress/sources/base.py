from __future__ import annotations

from typing import ClassVar
from urllib.parse import urlparse

from ..catalog import CatalogRules
from ..http import Client, FetchError
from ..models import (
    Candidate,
    ScrapeRequest,
    SourceId,
    SourceMetadata,
    SourceResult,
    Status,
)
from ..settings import Settings


class Source:
    id: ClassVar[SourceId]
    default_url: ClassVar[str]

    def __init__(self, client: Client, settings: Settings) -> None:
        self.client = client
        self.settings = settings
        self.base = self.default_url.rstrip("/")

    @property
    def rules(self) -> CatalogRules:
        return CatalogRules.load(self.settings.database)

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
                Status.UNSUPPORTED, f"地址必须属于 {self.id} 来源"
            )
        return value

    async def fetch(self, request: ScrapeRequest) -> SourceResult:
        try:
            result = await self._fetch(request)
            if result.metadata and not self.rules.matches(request.code, result.metadata.code):
                raise FetchError(
                    Status.IDENTITY_MISMATCH, "来源作品番号与请求不一致"
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
                message=f"来源页面内容无法解析 ({type(exc).__name__})",
            )

    def found(self, request: ScrapeRequest, metadata: SourceMetadata, **kwargs: object) -> SourceResult:
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
        rules = self.rules
        return next((candidate for candidate in candidates if rules.matches(request.code, candidate.code)), None)

    async def _fetch(self, request: ScrapeRequest) -> SourceResult:
        raise NotImplementedError
