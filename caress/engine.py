from __future__ import annotations

import asyncio
import time
from contextlib import nullcontext
from typing import Any

from .catalog import CatalogRules
from .http import Client, WebClient
from .logging import report_progress
from .models import (
    Category,
    Evidence,
    ScrapeRequest,
    ScrapeResult,
    Status,
    SourceResult,
)
from .scanner import validate_code
from .settings import Settings
from .sources import SOURCES


class Scraper:
    def __init__(self, settings: Settings | None = None, *, client: Client | None = None, runtime=None):
        self.settings = settings or Settings()
        self.rules = CatalogRules.load(self.settings.catalog_rules_file)
        self.client = client or WebClient(self.settings, runtime=runtime)
        self._owns_client = client is None
        self.source_locks = {}
        self.sources = {
            source: factory(self.client, self.settings, self.rules)
            for source, factory in SOURCES.items()
        }

    async def __aenter__(self) -> Scraper:
        if self._owns_client:
            await self.client.__aenter__()
        return self

    async def __aexit__(self, *exc: Any) -> None:
        if self._owns_client:
            await self.client.__aexit__(*exc)

    async def scrape(self, request: ScrapeRequest | str, *, on_result=None) -> ScrapeResult:
        request = (
            ScrapeRequest(code=request)
            if isinstance(request, str)
            else request.model_copy(deep=True)
        )
        validate_code(request.code)
        if (request.external_id or request.source_url) and not request.source:
            raise ValueError("explicit movie ID or URL requires a source")
        classification = self.rules.classify(request.code)
        if request.category:
            if classification.category == Category.FC2 and request.category != Category.FC2:
                raise ValueError("category contradicts FC2 number")
            classification.category = request.category
            classification.evidence.append(Evidence(basis="user_category", value=request.category))
        route = (
            [request.source]
            if request.source
            else self.settings.routes[classification.category.value]
        )
        async def fetch(source):
            lock = self.source_locks.setdefault(source, asyncio.Lock())
            while lock.locked():
                seconds = self.client.source_wait_seconds(source) if isinstance(self.client, WebClient) else 0
                report_progress("wait", source=source, message=f"冷却 {seconds} 秒" if seconds else "等待来源可用")
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
                    with self.client.source_work() if isinstance(self.client, WebClient) else nullcontext():
                        async with asyncio.timeout(self.settings.source_timeout_seconds):
                            result = await self.sources[source].fetch(request)
                except TimeoutError:
                    result = SourceResult(source=source, requested_code=request.code, status=Status.TIMEOUT, message="超过来源处理时限")
            finally:
                lock.release()
            result.elapsed_seconds = time.monotonic() - started
            if on_result is not None:
                on_result(result)
            report_progress("source", result.status.value, source)
            return result

        # A failed source must finish cancelling its siblings before this work exits.
        async with asyncio.TaskGroup() as group:
            tasks = [group.create_task(fetch(source)) for source in route]
        results = [task.result() for task in tasks]
        selected = next(
            (r for r in results if r.status == Status.FOUND and r.metadata is not None), None
        )
        warnings = [warning for result in results for warning in result.warnings]
        warnings.extend(
            f"{result.source}: {result.status.value}: {result.message or ''}"
            for result in results
            if result.status not in (Status.FOUND, Status.NOT_FOUND, Status.UNSUPPORTED)
        )
        metadata = selected.metadata.model_copy(deep=True, update={"code": request.code}) if selected else None
        if metadata:
            if metadata.classification.category == Category.FC2 or (not request.category and any(e.basis == "catalog_type" for e in metadata.classification.evidence)):
                classification = metadata.classification
            if not metadata.plot:
                warnings.append(f"{selected.source}: missing plot")
            if not metadata.artwork:
                warnings.append(f"{selected.source}: missing artwork")
        failures = [r for r in results if r.status not in (Status.NOT_FOUND, Status.UNSUPPORTED)]
        status = Status.FOUND if selected else failures[0].status if failures else Status.NOT_FOUND
        return ScrapeResult(
            code=request.code, status=status, classification=classification,
            metadata=metadata, selected_source=selected.source if selected else None,
            results=results, warnings=warnings,
        )
