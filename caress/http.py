from __future__ import annotations

import asyncio
import json
import re
import time
import uuid
from contextlib import AsyncExitStack, contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from email.utils import parsedate_to_datetime
from functools import cached_property
from math import ceil
from typing import Any, Protocol
from urllib.parse import urlparse
from weakref import WeakKeyDictionary

import httpx
from curl_cffi import CurlError
from pydantic import JsonValue
from scrapling.engines.toolbelt.custom import Response
from scrapling.fetchers import FetcherSession
from scrapling.parser import Selector

from .logging import report_progress
from .models import SourceId, Status

FLARESOLVERR_URL = "http://flaresolverr:8191/v1"
REQUEST_TIMEOUT_SECONDS = 25
NETWORK_RETRIES = 1
RETRY_DELAY_SECONDS = 5
SOURCE_INTERVALS = {
    SourceId.DMM: 5,
    SourceId.JAVDB: 60,
    SourceId.JAVBUS: 15,
    SourceId.FC2: 10,
    SourceId.AVSOX: 30,
    SourceId.AIRAV: 15,
    SourceId.ONEPONDO: 5,
    SourceId.CARIBBEANCOM: 5,
    SourceId.HEYZO: 5,
    SourceId.AVBASE: 15,
}


SOLVER_TIMEOUT_SECONDS = 45


@dataclass
class Page:
    url: str
    status: int
    body: bytes
    fetcher: str = "scrapling"

    @cached_property
    def text(self) -> str:
        # Browser output is UTF-8; Japanese HTML can declare EUC-JP or Shift_JIS.
        charset = re.search(rb'charset\s*=\s*["\']?([\w-]+)', self.body[:8192], re.I)
        encoding = (
            charset[1].decode("ascii")
            if charset and self.fetcher == "scrapling"
            else "utf-8"
        )
        try:
            return self.body.decode(encoding, errors="replace")
        except LookupError:
            return self.body.decode("utf-8", errors="replace")

    @cached_property
    def _selector(self) -> Selector:
        return Selector(self.text, url=self.url, adaptive=False)

    def selector(self) -> Selector:
        return self._selector

    def json(self) -> JsonValue:
        try:
            return json.loads(self.body)
        except (ValueError, UnicodeDecodeError) as exc:
            raise FetchError(Status.PARSE_ERROR, "来源返回了无法解析的 JSON", self) from exc


class FetchError(Exception):
    def __init__(self, status: Status, message: str, page: Page | None = None) -> None:
        super().__init__(message)
        self.status = status
        self.page = page


def json_error(value: JsonValue) -> str:
    def redact(item):
        if isinstance(item, dict):
            return {
                key: "[已隐藏]" if re.search(r"api.?key|token|secret|password|authorization|cookie", key, re.I) else redact(value)
                for key, value in item.items()
            }
        if isinstance(item, list):
            return [redact(value) for value in item]
        return item

    return json.dumps(redact(value), ensure_ascii=False)


class Session(Protocol):
    async def get(self, url: str, **kwargs: Any) -> Response: ...
    async def post(self, url: str, **kwargs: Any) -> Response: ...


class Client(Protocol):
    async def get(
        self,
        source: SourceId,
        url: str,
        *,
        headers: dict[str, str] | None = None,
        cookies: dict[str, str] | None = None,
        rendered: bool = False,
        wait_seconds: int = 0,
    ) -> Page: ...
    async def post_json(
        self,
        source: SourceId,
        url: str,
        payload: JsonValue,
        *,
        headers: dict[str, str] | None = None,
    ) -> Page: ...


def response_issue(page: Page) -> tuple[Status, str] | None:
    text = page.text.lower()
    path = urlparse(page.url).path.lower()
    if page.status >= 400:
        try:
            payload = json.loads(page.body)
        except (ValueError, UnicodeDecodeError):
            pass
        else:
            if isinstance(payload, (dict, list)):
                status = (
                    Status.NOT_FOUND if page.status == 404
                    else Status.BLOCKED if page.status in (401, 403)
                    else Status.NETWORK_ERROR
                )
                return status, json_error(payload)
    if "error 1005" in text or (
        "access denied" in text and "autonomous system" in text
    ):
        return Status.IP_BLOCKED, "来源拒绝了当前出口 IP 或网络"
    if "not-available-in-your-region" in path or "not available in your region" in text:
        return Status.REGION_BLOCKED, "来源在当前出口地区不可用"
    if "/login" in path or "log in  fc2" in text[:1000] or "此內容需要登入" in text:
        return Status.LOGIN_REQUIRED, "来源要求登录"
    if text.lstrip("\ufeff \t\r\n").startswith("<"):
        title = page.selector().css("title::text").get("").strip().lower()
        if re.search(r"login|sign in|登录|登入", title) and page.selector().css(
            'form input[type="password"]'
        ):
            return Status.LOGIN_REQUIRED, "来源要求登录"
        if title.startswith(
            ("checking your browser", "security verification", "安全验证", "安全驗證")
        ):
            return Status.BLOCKED, "来源仍在要求浏览器验证"
    if "generated by cloudfront" in text and "request blocked" in text:
        return Status.BLOCKED, "CloudFront 拒绝了请求"
    if "/doc/driver-verify" in path:
        return Status.BLOCKED, "JavBus 要求验证"
    if page.status == 404:
        return Status.NOT_FOUND, "source returned HTTP 404"
    if page.status >= 400:
        return Status.BLOCKED if page.status in (
            401,
            403,
        ) else Status.NETWORK_ERROR, f"来源请求失败（HTTP {page.status}）"
    return None


def is_cloudflare(page: Page) -> bool:
    text = page.text.lower()
    # Successful pages also embed Cloudflare's challenge-platform JS. A script URL
    # alone is not evidence that the requested document is a challenge page.
    title = (
        page.selector().css("title::text").get("").strip().lower()
        if "<" in text[:200]
        else ""
    )
    return (
        title.startswith(("just a moment", "attention required"))
        or (
            "cloudflare" in text
            and ("autonomous system" in text or "access denied" in title)
        )
        or (
            "cf-chl-" in text
            and (
                "enable javascript and cookies" in text
                or "verify you are human" in text
            )
        )
    )


class WebClient:
    """Pooled Scrapling sessions; solver cookies never cross network exits."""

    _budgets = WeakKeyDictionary()

    def __init__(self, *, runtime=None) -> None:
        self.runtime = runtime
        self._cooldowns = {}
        self._stack = AsyncExitStack()
        self._sessions: dict[tuple[SourceId, str], Session] = {}
        self._session_lock = asyncio.Lock()
        self.source_work_locks: dict[SourceId, asyncio.Lock] = {}
        self._source_locks: dict[SourceId, asyncio.Lock] = {}
        self._host_locks: dict[str, asyncio.Lock] = {}
        self._last_request: dict[str, float] = {}
        self._metadata_last: dict[str, float] = {}
        self._work_scope = ContextVar("source_work", default=None)
        self._paced_work = {}
        self._limit = asyncio.Semaphore(8)
        self._solver_limit = asyncio.Semaphore(1)
        self._solver_sessions: dict[tuple[SourceId, str], str] = {}
        self._control: httpx.AsyncClient | None = None

    def share_limits(self):
        # Task workers and image previews share the same host pacing budget.
        loop = asyncio.get_running_loop()
        if loop not in self._budgets:
            self._budgets[loop] = (
                {},
                {},
                {},
                {},
                {},
                {},
                asyncio.Semaphore(8),
                asyncio.Semaphore(1),
            )
        # Batch scrapers and manual previews share whole-work gates and pacing,
        # independently of the per-request connection and host locks.
        (
            self.source_work_locks,
            self._source_locks,
            self._host_locks,
            self._last_request,
            self._metadata_last,
            self._paced_work,
            self._limit,
            self._solver_limit,
        ) = self._budgets[loop]

    @property
    def cooldowns(self):
        # Batches and manual previews read the same cooldown state.
        return self.runtime.cooldowns() if self.runtime is not None else self._cooldowns

    def _save_cooldown(self, source, row):
        if self.runtime is not None:
            self.runtime.save_cooldown(source, row)
        else:
            self._cooldowns[str(source)] = row

    @staticmethod
    def cooldown_key(source: SourceId, url: str) -> str:
        return f"{source}:{urlparse(url).hostname}"

    def cool_down(self, source, seconds, reason):
        row = dict(self.cooldowns.get(str(source), {}))
        row.update(
            cooldown_until=max(row.get("cooldown_until", 0), time.time() + seconds),
            cooldown_reason=reason,
        )
        self._save_cooldown(source, row)

    def record_outcome(self, source, status):
        if status in (Status.IP_BLOCKED, Status.REGION_BLOCKED, Status.BLOCKED):
            self.cool_down(source, 900, str(status))
        elif status == Status.LOGIN_REQUIRED:
            self.cool_down(source, 300, str(status))

    def check_cooldown(self, source):
        row = self.cooldowns.get(str(source), {})
        remaining = row.get("cooldown_until", 0) - time.time()
        if remaining > 0:
            reason = {
                "region_blocked": "当前出口受地区限制",
                "ip_blocked": "当前出口 IP 被拒绝",
                "blocked": "来源拒绝访问",
                "login_required": "来源要求登录",
                "rate_limited": "来源限制请求频率",
            }.get(row.get("cooldown_reason"), "来源暂停请求")
            if (
                str(source).partition(":")[0] == SourceId.FC2
                and row.get("cooldown_reason") == "login_required"
            ):
                reason = "FC2 要求登录"
            wait = (
                f"{ceil(remaining / 60)} 分钟"
                if remaining >= 60
                else f"{ceil(remaining)} 秒"
            )
            raise FetchError(Status.COOLDOWN, f"{reason}，暂停重复请求，剩余 {wait}")

    async def __aenter__(self) -> WebClient:
        await self._stack.__aenter__()
        self._control = await self._stack.enter_async_context(
            httpx.AsyncClient(
                timeout=SOLVER_TIMEOUT_SECONDS + 15,
                trust_env=False,
            )
        )
        return self

    async def __aexit__(self, *exc: Any) -> None:
        try:
            if self._control:
                for session in self._solver_sessions.values():
                    try:
                        await self._control.post(
                            FLARESOLVERR_URL,
                            json={"cmd": "sessions.destroy", "session": session},
                            timeout=10,
                        )
                    except httpx.HTTPError:
                        pass
        finally:
            await self._stack.__aexit__(*exc)
            self._sessions.clear()
            self._solver_sessions.clear()

    async def _session(self, source: SourceId, url: str) -> Session:
        key = source, urlparse(url).netloc
        async with self._session_lock:
            if key not in self._sessions:
                self._sessions[key] = await self._stack.enter_async_context(
                    FetcherSession(
                        impersonate="chrome",
                        timeout=REQUEST_TIMEOUT_SECONDS,
                        retries=0,
                        selector_config={"adaptive": False},
                    )
                )
            return self._sessions[key]

    @contextmanager
    def source_work(self):
        token = self._work_scope.set(object())
        try:
            yield
        finally:
            self._work_scope.reset(token)

    def source_wait_seconds(self, source: SourceId) -> int:
        previous = self._metadata_last.get(f"source:{source}")
        interval = SOURCE_INTERVALS[source]
        remaining = (
            interval - (time.monotonic() - previous) if previous is not None else 0
        )
        # The source queue has no request URL; domain cooldowns are checked per request.
        return max(0, ceil(remaining))

    async def _throttle(
        self, source: SourceId, host: str, binary: bool = False
    ) -> None:
        interval = 1 if binary else SOURCE_INTERVALS[source]
        key = f"image:{host}" if binary else f"source:{source}"
        history = self._last_request if binary else self._metadata_last
        previous = history.get(key)
        delay = interval - (time.monotonic() - previous) if previous is not None else 0
        if delay > 0:
            report_progress(
                "wait",
                source=source,
                message=f"{'图片间隔' if binary else '冷却'} {ceil(delay)} 秒",
            )
            await asyncio.sleep(delay)
        history[key] = time.monotonic()

    async def get(
        self,
        source: SourceId,
        url: str,
        *,
        headers: dict[str, str] | None = None,
        cookies: dict[str, str] | None = None,
        rendered: bool = False,
        wait_seconds: int = 0,
    ) -> Page:
        return await self._request(
            source,
            url,
            "GET",
            headers=headers,
            cookies=cookies,
            rendered=rendered,
            wait_seconds=wait_seconds,
        )

    async def post_json(
        self,
        source: SourceId,
        url: str,
        payload: JsonValue,
        *,
        headers: dict[str, str] | None = None,
    ) -> Page:
        return await self._request(
            source, url, "POST", payload=payload, headers=headers
        )

    async def image_bytes(self, source: SourceId, url: str, referer: str) -> bytes:
        page = await self._request(
            source, url, "GET", headers={"Referer": referer}, binary=True
        )
        return page.body

    async def _request(
        self, source: SourceId, url: str, method: str, **kwargs: Any
    ) -> Page:
        try:
            return await self._fetch(source, url, method, **kwargs)
        except FetchError as exc:
            if not kwargs.get("binary", False):
                self.record_outcome(self.cooldown_key(source, url), exc.status)
            raise

    async def _fetch(
        self,
        source: SourceId,
        url: str,
        method: str,
        *,
        payload: JsonValue = None,
        headers: dict[str, str] | None = None,
        cookies: dict[str, str] | None = None,
        rendered: bool = False,
        wait_seconds: int = 0,
        binary: bool = False,
    ) -> Page:
        self.share_limits()
        parsed = urlparse(url)
        if (
            parsed.scheme != "https"
            or not parsed.hostname
            or parsed.username
            or parsed.password
        ):
            raise FetchError(Status.UNSUPPORTED, "来源地址必须使用 HTTPS")
        cooldown_key = self.cooldown_key(source, url)
        if not binary:
            self.check_cooldown(cooldown_key)
        host_lock = self._host_locks.setdefault(parsed.hostname, asyncio.Lock())
        # Only protocol-specific age/language cookies are used; no user session Cookie setting.
        combined_cookies = {} if binary else (cookies or {})
        source_lock = self._source_locks.setdefault(source, asyncio.Lock())
        async with AsyncExitStack() as locks:
            if not binary:
                await locks.enter_async_context(source_lock)
                self.check_cooldown(cooldown_key)
                scope = self._work_scope.get()
                if scope is None or self._paced_work.get(source) is not scope:
                    await self._throttle(source, parsed.hostname)
                    self._paced_work[source] = scope
            await locks.enter_async_context(host_lock)
            await locks.enter_async_context(self._limit)
            if (
                method == "GET"
                and not binary
                and (rendered or (source, parsed.netloc) in self._solver_sessions)
            ):
                return await self._render(
                    source, url, combined_cookies, wait_seconds=wait_seconds
                )
            session = await self._session(source, url)
            for attempt in range(NETWORK_RETRIES + 1):
                if binary:
                    await self._throttle(source, parsed.hostname, True)
                report_progress(
                    "request",
                    source=source,
                    message=f"{'图片' if binary else method} {parsed.netloc}{parsed.path}",
                )
                try:
                    kwargs = {
                        "headers": headers or {},
                        "cookies": combined_cookies,
                        "timeout": REQUEST_TIMEOUT_SECONDS,
                    }
                    response = (
                        await session.get(url, **kwargs)
                        if method == "GET"
                        else await session.post(url, json=payload, **kwargs)
                    )
                except CurlError as exc:
                    if attempt < NETWORK_RETRIES:
                        report_progress(
                            "retry", source=source, message="网络请求失败，准备重试"
                        )
                        await asyncio.sleep(RETRY_DELAY_SECONDS)
                        continue
                    raise FetchError(
                        Status.NETWORK_ERROR, f"来源网络连接失败（{type(exc).__name__}）"
                    ) from exc
                page = Page(str(response.url), response.status, response.body)
                report_progress(
                    "response", source=source, message=f"HTTP {page.status}"
                )
                if is_cloudflare(page):
                    if binary:
                        raise FetchError(
                            Status.BLOCKED,
                            "图片服务器要求验证，无法获取图片",
                            page,
                        )
                    if method == "GET":
                        return await self._render(
                            source, url, combined_cookies, wait_seconds=wait_seconds
                        )
                    raise FetchError(
                        Status.BLOCKED,
                        "接口受到 Cloudflare 验证拦截",
                        page,
                    )
                if page.status == 429:
                    retry_after = response.headers.get("retry-after", "")
                    try:
                        seconds = (
                            float(retry_after)
                            if retry_after.isdigit()
                            else parsedate_to_datetime(retry_after).timestamp()
                            - time.time()
                        )
                    except ValueError, TypeError, OverflowError:
                        seconds = 900
                    if not binary:
                        self.cool_down(
                            cooldown_key, max(1, min(seconds, 86400)), "rate_limited"
                        )
                    issue = response_issue(page)
                    raise FetchError(Status.RATE_LIMITED, issue[1] if issue else "来源限流，请等待冷却", page)
                if page.status >= 500:
                    if attempt < NETWORK_RETRIES:
                        retry_after = response.headers.get("retry-after", "")
                        seconds = (
                            min(int(retry_after), 30)
                            if retry_after.isdigit()
                            else RETRY_DELAY_SECONDS
                        )
                        delay = 1 if seconds <= 1 else ((seconds + 4) // 5) * 5
                        await asyncio.sleep(delay)
                        continue
                if binary:
                    from .images import placeholder_url

                    if placeholder_url(source, page.url):
                        raise FetchError(Status.NOT_FOUND, "来源返回占位图", page)
                if issue := response_issue(page):
                    raise FetchError(*issue, page)
                return page
        raise AssertionError("unreachable")

    async def _solver_call(self, data: dict[str, JsonValue]) -> dict[str, JsonValue]:
        if not self._control:
            raise FetchError(Status.BLOCKED, "FlareSolverr 连接未打开")
        try:
            response = await self._control.post(FLARESOLVERR_URL, json=data)
            payload = response.json()
        except (httpx.HTTPError, ValueError) as exc:
            raise FetchError(
                Status.NETWORK_ERROR, f"FlareSolverr 无法连接（{type(exc).__name__}）"
            ) from exc
        if not isinstance(payload, dict):
            raise FetchError(Status.PARSE_ERROR, "FlareSolverr 返回了无效响应")
        if payload.get("status") != "ok":
            message = str(payload.get("message", "FlareSolverr failed"))
            status = (
                Status.IP_BLOCKED if "banned" in message.lower() else Status.BLOCKED
            )
            raise FetchError(status, json_error(payload))
        return payload

    async def _render(
        self,
        source: SourceId,
        url: str,
        cookies: dict[str, str],
        *,
        wait_seconds: int = 0,
    ) -> Page:
        report_progress("challenge", source=source)
        key = (source, urlparse(url).netloc)
        async with self._solver_limit:
            if key not in self._solver_sessions:
                session = "caress-" + uuid.uuid4().hex
                command: dict[str, JsonValue] = {
                    "cmd": "sessions.create",
                    "session": session,
                }
                await self._solver_call(command)
                self._solver_sessions[key] = session
            payload = await self._solver_call(
                {
                    "cmd": "request.get",
                    "url": url,
                    "session": self._solver_sessions[key],
                    "maxTimeout": SOLVER_TIMEOUT_SECONDS * 1000,
                    "waitInSeconds": wait_seconds,
                    # Browser-copied CF cookies belong to its original exit/UA.
                    # Forward login cookies while keeping the solver's own CF state.
                    "cookies": [
                        {"name": name, "value": value}
                        for name, value in cookies.items()
                        if name not in ("cf_clearance", "__cf_bm")
                    ],
                }
            )
            solution = payload.get("solution")
            if not isinstance(solution, dict) or not isinstance(
                solution.get("response"), str
            ):
                raise FetchError(
                    Status.PARSE_ERROR, "FlareSolverr 未返回网页内容"
                )
            page = Page(
                str(solution.get("url", url)),
                int(solution.get("status", 200)),
                solution["response"].encode(),
                "flaresolverr",
            )
            if is_cloudflare(page):
                raise FetchError(
                    Status.BLOCKED, "FlareSolverr 未能通过 Cloudflare 验证", page
                )
            if issue := response_issue(page):
                raise FetchError(*issue, page)
            return page
