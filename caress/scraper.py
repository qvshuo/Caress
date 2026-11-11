from __future__ import annotations

import asyncio
import logging
import time
from contextlib import nullcontext
from typing import Any

from .actors import actor_type
from .catalog import CatalogRules, catalog_prefix
from .http import Client, WebClient
from .identifiers import validate_code
from .logging import report_progress
from .models import (
    Category,
    ScrapeRequest,
    ScrapeResult,
    SourceId,
    SourceResult,
    Status,
)
from .settings import Settings
from .sources import SOURCES

SOURCE_TIMEOUT_SECONDS = 180

logger = logging.getLogger(__name__)


class Scraper:
    def __init__(
        self,
        settings: Settings | None = None,
        *,
        client: Client | None = None,
        runtime=None,
    ):
        self.settings = settings or Settings()
        self.client = client or WebClient(runtime=runtime)
        self._owns_client = client is None
        self._source_locks = {}
        self.prefix_locks = {}
        self.sources = {
            source: factory(self.client, self.settings)
            for source, factory in SOURCES.items()
        }

    @property
    def source_locks(self):
        if isinstance(self.client, WebClient):
            self.client.share_limits()
            return self.client.source_work_locks
        return self._source_locks

    @property
    def rules(self) -> CatalogRules:
        return CatalogRules.load(self.settings.database)

    async def __aenter__(self) -> Scraper:
        if self._owns_client:
            await self.client.__aenter__()
        return self

    async def __aexit__(self, *exc: Any) -> None:
        if self._owns_client:
            await self.client.__aexit__(*exc)

    async def scrape(
        self, request: ScrapeRequest | str, *, on_result=None
    ) -> ScrapeResult:
        request = (
            ScrapeRequest(code=request)
            if isinstance(request, str)
            else request.model_copy(deep=True)
        )
        validate_code(request.code)
        if (request.external_id or request.source_url) and not request.source:
            raise ValueError("指定作品 ID 或地址时必须选择来源")
        classification = self.rules.classify(request.code)

        async def fetch(source):
            lock = self.source_locks.setdefault(source, asyncio.Lock())
            while lock.locked():
                seconds = (
                    self.client.source_wait_seconds(source)
                    if isinstance(self.client, WebClient)
                    else 0
                )
                report_progress(
                    "wait",
                    source=source,
                    message=f"Source cooldown: {seconds} seconds" if seconds else "Waiting for source availability",
                )
                try:
                    await asyncio.wait_for(lock.acquire(), timeout=1)
                except TimeoutError:
                    continue
                else:
                    break
            else:
                await lock.acquire()
            try:
                report_progress("source", source=source)
                started = time.monotonic()
                try:
                    with (
                        self.client.source_work()
                        if isinstance(self.client, WebClient)
                        else nullcontext()
                    ):
                        async with asyncio.timeout(SOURCE_TIMEOUT_SECONDS):
                            result = await self.sources[source].fetch(request)
                except TimeoutError:
                    result = SourceResult(
                        source=source,
                        requested_code=request.code,
                        status=Status.TIMEOUT,
                        message="超过来源处理时限",
                    )
            finally:
                lock.release()
            result.elapsed_seconds = time.monotonic() - started
            failed = result.status not in (Status.FOUND, Status.NOT_FOUND, Status.UNSUPPORTED)
            logger.log(
                logging.WARNING if failed else logging.INFO,
                "Source request failed" if failed else "Source request completed",
                extra={
                    "event": "source.fetched",
                    "source": source,
                    "stage": "fetch",
                    "result": result.status,
                    "elapsed_seconds": result.elapsed_seconds,
                },
            )
            if on_result is not None:
                on_result(result)
            report_progress("source", result.status.value, source)
            return result

        fetched = {}
        if classification.category == Category.UNKNOWN and request.source is None:
            prefix = catalog_prefix(request.code)
            async with self.prefix_locks.setdefault(prefix, asyncio.Lock()):
                classification = self.rules.classify(request.code)
                if classification.category == Category.UNKNOWN:
                    fetched[SourceId.JAVDB] = await fetch(SourceId.JAVDB)
                    classification = self.rules.classify(request.code)
        route = (
            [request.source]
            if request.source
            else self.settings.routes[classification.category.value]
        )
        results = []
        selected = None
        for source in route:
            result = fetched.get(source) or await fetch(source)
            if result.metadata and not self.rules.matches(
                request.code, result.metadata.code
            ):
                result = SourceResult(
                    source=source,
                    requested_code=request.code,
                    status=Status.IDENTITY_MISMATCH,
                    message="来源番号不匹配",
                )
            fetched[source] = result
            results.append(result)
            if result.status == Status.FOUND and result.metadata is not None:
                selected = result
                break
        classification = self.rules.classify(request.code)
        executed = [
            *results,
            *(value for source, value in fetched.items() if source not in route),
        ]
        warnings = [warning for result in executed for warning in result.warnings]
        warnings.extend(
            f"{result.source}：{result.message or '来源请求失败'}"
            for result in executed
            if result.status not in (Status.FOUND, Status.NOT_FOUND, Status.UNSUPPORTED)
        )
        metadata = (
            selected.metadata.model_copy(deep=True, update={"code": request.code})
            if selected
            else None
        )
        if metadata:
            if not metadata.plot:
                warnings.append(f"{selected.source}：来源未提供简介")
            if not metadata.artwork:
                warnings.append(f"{selected.source}：来源未提供图片")
        failures = [
            r for r in results if r.status not in (Status.NOT_FOUND, Status.UNSUPPORTED)
        ]
        status = (
            Status.FOUND
            if selected
            else failures[0].status
            if failures
            else Status.NOT_FOUND
        )
        return ScrapeResult(
            code=request.code,
            status=status,
            classification=classification,
            metadata=metadata,
            selected_source=selected.source if selected else None,
            results=executed,
            warnings=warnings,
        )

    async def cover(self, code, source=None):
        from .images import fetch_artwork

        result = await self.scrape(ScrapeRequest(code=code, source=source))
        if result.status != Status.FOUND or result.metadata is None:
            raise ValueError("来源未找到完整原始封面")
        candidate = next(
            (a for a in result.metadata.artwork if a.role == "cover"), None
        )
        if candidate is None:
            raise ValueError("来源未提供完整封面，无法恢复已裁剪的画面")
        return await fetch_artwork(self.client, candidate)

    async def resolve_actors(self, actors, names, category, ids):
        if self.settings.database:
            names.reload(self.settings.database)
        kind = actor_type(category)
        if not any(
            (actor.name not in ids or ids[actor.name] is not None)
            and names.needs_lookup(actor.name, kind, ids.get(actor.name))
            for actor in actors
        ):
            return []
        lock = self.source_locks.setdefault(SourceId.JAVDB, asyncio.Lock())
        async with lock:
            with (
                self.client.source_work()
                if isinstance(self.client, WebClient)
                else nullcontext()
            ):
                try:
                    async with asyncio.timeout(SOURCE_TIMEOUT_SECONDS):
                        javdb = self.sources[SourceId.JAVDB]
                        return await javdb.app.resolve_actors(
                            actors, names, category, ids, javdb.web.find_actor_ids
                        )
                except TimeoutError:
                    return ["演员查询超时，保留原名"]
