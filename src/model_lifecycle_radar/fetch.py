"""Bounded, HTTPS-only fetches for public official documentation."""
from __future__ import annotations

import random
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from typing import Callable, Mapping


class FetchError(RuntimeError):
    def __init__(self, code: str, message: str = "source request failed") -> None:
        super().__init__(message)
        self.code = code


@dataclass(frozen=True)
class FetchResponse:
    body: bytes
    url: str
    headers: Mapping[str, str]
    status_code: int = 200

    @property
    def not_modified(self) -> bool:
        return self.status_code == 304

    def header(self, name: str) -> str | None:
        for key, value in self.headers.items():
            if key.lower() == name.lower():
                return str(value)
        return None


class RedirectGuard(urllib.request.HTTPRedirectHandler):
    """Rejects redirects outside the per-source official host allowlist."""
    def __init__(self, allowed_hosts: tuple[str, ...], max_redirects: int = 5) -> None:
        super().__init__()
        self.allowed_hosts = {host.lower().rstrip(".") for host in allowed_hosts}
        self.max_redirections = max_redirects
        self.max_repeats = max_redirects

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        target = urllib.parse.urlsplit(newurl)
        if target.scheme != "https" or not target.hostname or target.hostname.lower().rstrip(".") not in self.allowed_hosts:
            return None
        original = urllib.parse.urlsplit(getattr(req, "full_url", ""))
        same_origin = (original.scheme, original.hostname, original.port) == (target.scheme, target.hostname, target.port)
        request_headers: dict[str, str] = {}
        if hasattr(req, "header_items"):
            request_headers = {str(k): str(v) for k, v in req.header_items()}
        if not same_origin:
            request_headers = {
                k: v for k, v in request_headers.items()
                if k.lower() not in {"authorization", "proxy-authorization", "cookie", "cookie2"}
            }
        method = req.get_method() if hasattr(req, "get_method") else "GET"
        data = getattr(req, "data", None)
        if code in (301, 302, 303) and method not in {"GET", "HEAD"}:
            method, data = "GET", None
        return urllib.request.Request(
            newurl, data=data, headers=request_headers, origin_req_host=getattr(req, "origin_req_host", None),
            unverifiable=True, method=method,
        )


class BoundedFetcher:
    """Fetches small documentation responses under per-request and run budgets.

    Compression is not requested. Encoded responses are rejected so the byte
    cap applies to the complete entity body without an unbounded decompressor.
    """
    RETRYABLE_STATUS = {429, 500, 502, 503, 504}

    def __init__(
        self,
        *,
        request_timeout: float = 15.0,
        attempts: int = 3,
        max_response_bytes: int = 2 * 1024 * 1024,
        max_total_bytes: int = 16 * 1024 * 1024,
        max_run_seconds: float = 150.0,
        max_redirects: int = 5,
        max_retry_after: float = 8.0,
        opener=None,
        sleeper: Callable[[float], None] = time.sleep,
        random_value: Callable[[], float] = random.random,
        monotonic: Callable[[], float] = time.monotonic,
    ) -> None:
        self.request_timeout = request_timeout
        self.attempts = attempts
        self.max_response_bytes = max_response_bytes
        self.max_total_bytes = max_total_bytes
        self.max_run_seconds = max_run_seconds
        self.max_redirects = max_redirects
        self.max_retry_after = max_retry_after
        self.opener = opener
        self.sleeper = sleeper
        self.random_value = random_value
        self.monotonic = monotonic
        self.run_started = monotonic()
        self.total_bytes = 0
        if (isinstance(request_timeout, bool) or not isinstance(request_timeout, (int, float))
                or not 0 < request_timeout <= 15 or not isinstance(attempts, int)
                or isinstance(attempts, bool) or not 1 <= attempts <= 3):
            raise ValueError("request timeout/attempt count exceeds safe limits")
        if (not isinstance(max_response_bytes, int) or isinstance(max_response_bytes, bool)
                or not isinstance(max_total_bytes, int) or isinstance(max_total_bytes, bool)
                or not 1 <= max_response_bytes <= 2 * 1024 * 1024
                or not max_response_bytes <= max_total_bytes <= 64 * 1024 * 1024):
            raise ValueError("response or aggregate byte limit exceeds safe bounds")
        if (isinstance(max_run_seconds, bool) or not isinstance(max_run_seconds, (int, float))
                or not 1 <= max_run_seconds <= 300 or not isinstance(max_redirects, int)
                or isinstance(max_redirects, bool) or not 1 <= max_redirects <= 5):
            raise ValueError("aggregate time or redirect limit exceeds safe bounds")

    def _ensure_budget(self) -> float:
        remaining = self.max_run_seconds - (self.monotonic() - self.run_started)
        if remaining <= 0:
            raise FetchError("run_budget_exhausted", "aggregate request time budget exhausted")
        if self.total_bytes >= self.max_total_bytes:
            raise FetchError("byte_budget_exhausted", "aggregate response byte budget exhausted")
        return remaining

    def _opener_for(self, allowed_hosts: tuple[str, ...]):
        if self.opener is not None:
            return self.opener
        return urllib.request.build_opener(RedirectGuard(allowed_hosts, self.max_redirects))

    def _retry_delay(self, attempt: int, retry_after: str | None = None) -> float:
        if retry_after:
            try:
                value = max(0.0, float(retry_after.strip()))
                return min(value, self.max_retry_after)
            except ValueError:
                try:
                    when = parsedate_to_datetime(retry_after)
                    if when.tzinfo is None:
                        when = when.replace(tzinfo=timezone.utc)
                    value = max(0.0, (when.astimezone(timezone.utc) - datetime.now(timezone.utc)).total_seconds())
                    return min(value, self.max_retry_after)
                except (TypeError, ValueError, OverflowError):
                    pass
        base = min(0.5 * (2 ** (attempt - 1)), self.max_retry_after)
        return min(base + 0.25 * max(0.0, min(self.random_value(), 1.0)), self.max_retry_after)

    @staticmethod
    def _validate_url(url: str, allowed_hosts: tuple[str, ...]) -> None:
        parsed = urllib.parse.urlsplit(url)
        if parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password:
            raise FetchError("invalid_url", "only HTTPS source URLs without credentials are allowed")
        if parsed.query or parsed.fragment:
            raise FetchError("invalid_url", "query strings and fragments are not accepted for monitored sources")
        if parsed.hostname.lower().rstrip(".") not in {h.lower().rstrip(".") for h in allowed_hosts}:
            raise FetchError("host_not_allowed", "source host is not in its official allowlist")

    def fetch(
        self,
        url: str,
        allowed_hosts: tuple[str, ...],
        *,
        headers: Mapping[str, str] | None = None,
        max_bytes: int | None = None,
    ) -> FetchResponse:
        self._validate_url(url, allowed_hosts)
        limit = min(max_bytes or self.max_response_bytes, self.max_response_bytes)
        request_headers = {"User-Agent": "model-lifecycle-radar/0.1 (public documentation monitor)"}
        for key, value in (headers or {}).items():
            if key.lower() in {"authorization", "proxy-authorization", "cookie", "cookie2"}:
                raise FetchError("auth_not_supported", "authenticated fetch is not enabled")
            request_headers[key] = value
        opener = self._opener_for(allowed_hosts)
        last_code = "request_failed"
        for attempt in range(1, self.attempts + 1):
            remaining = self._ensure_budget()
            request = urllib.request.Request(url, headers=request_headers, method="GET")
            try:
                response = opener.open(request, timeout=min(self.request_timeout, remaining))
                try:
                    final_url = response.geturl()
                    self._validate_url(final_url, allowed_hosts)
                    response_headers = {str(k): str(v) for k, v in response.headers.items()}
                    encoding = next((v for k, v in response_headers.items() if k.lower() == "content-encoding"), "identity")
                    if encoding.lower() not in {"", "identity"}:
                        raise FetchError("unsupported_content_encoding", "compressed response was rejected")
                    body = response.read(limit + 1)
                    if len(body) > limit:
                        raise FetchError("response_too_large", "response exceeded its byte limit")
                    if self.total_bytes + len(body) > self.max_total_bytes:
                        raise FetchError("byte_budget_exhausted", "aggregate response byte budget exhausted")
                    self.total_bytes += len(body)
                    status = getattr(response, "status", getattr(response, "code", 200))
                    return FetchResponse(body, final_url, response_headers, int(status))
                finally:
                    close = getattr(response, "close", None)
                    if close:
                        close()
            except urllib.error.HTTPError as exc:
                if exc.code == 304:
                    final_url = exc.geturl() or url
                    self._validate_url(final_url, allowed_hosts)
                    headers_out = {str(k): str(v) for k, v in (exc.headers.items() if exc.headers else [])}
                    return FetchResponse(b"", final_url, headers_out, 304)
                last_code = f"http_{exc.code}"
                if exc.code not in self.RETRYABLE_STATUS or attempt >= self.attempts:
                    raise FetchError(last_code, f"source returned HTTP {exc.code}") from None
                delay = self._retry_delay(attempt, exc.headers.get("Retry-After") if exc.headers else None)
            except FetchError:
                raise
            except (urllib.error.URLError, TimeoutError, OSError) as exc:
                last_code = "transport_error"
                if attempt >= self.attempts:
                    raise FetchError(last_code, "source transport failed") from None
                delay = self._retry_delay(attempt)
            if self.monotonic() - self.run_started + delay >= self.max_run_seconds:
                raise FetchError("run_budget_exhausted", "aggregate request time budget exhausted")
            self.sleeper(delay)
        raise FetchError(last_code)
