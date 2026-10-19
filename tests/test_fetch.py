from __future__ import annotations

import io
import unittest
import urllib.error
from email.message import Message

from model_lifecycle_radar.fetch import BoundedFetcher, FetchError, RedirectGuard


class FakeResponse:
    def __init__(self, data: bytes, url: str = "https://docs.example.test/page"):
        self.data = data
        self.url = url
        self.headers = Message()
        self.headers["Content-Type"] = "text/html; charset=utf-8"
        self.closed = False

    def read(self, size: int = -1) -> bytes:
        return self.data if size < 0 else self.data[:size]

    def geturl(self) -> str:
        return self.url

    def close(self) -> None:
        self.closed = True

    def __enter__(self): return self
    def __exit__(self, *args): self.close()


class SequenceOpener:
    def __init__(self, outcomes):
        self.outcomes = list(outcomes)
        self.calls = 0

    def open(self, request, timeout):
        self.calls += 1
        self.last_method = request.get_method()
        outcome = self.outcomes.pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome


class FetcherTests(unittest.TestCase):
    def make_fetcher(self, outcomes, sleeps=None, **kwargs):
        opener = SequenceOpener(outcomes)
        delays = sleeps if sleeps is not None else []
        fetcher = BoundedFetcher(
            opener=opener, sleeper=delays.append, random_value=lambda: 0.5,
            **kwargs,
        )
        return fetcher, opener, delays

    def test_https_only_rejects_before_network(self):
        fetcher, opener, _ = self.make_fetcher([FakeResponse(b"ok")])
        with self.assertRaises(FetchError):
            fetcher.fetch("http://docs.example.test/page", ("docs.example.test",))
        self.assertEqual(opener.calls, 0)

    def test_response_size_is_bounded_with_one_byte_overread(self):
        fetcher, _, _ = self.make_fetcher([FakeResponse(b"12345")], max_response_bytes=4)
        with self.assertRaises(FetchError):
            fetcher.fetch("https://docs.example.test/page", ("docs.example.test",))

    def test_transport_and_retryable_status_retry_but_404_does_not(self):
        too_many = urllib.error.HTTPError("u", 502, "bad gateway", Message(), io.BytesIO(b""))
        fetcher, opener, delays = self.make_fetcher([OSError("reset"), too_many, FakeResponse(b"ok")])
        response = fetcher.fetch("https://docs.example.test/page", ("docs.example.test",))
        self.assertEqual(response.body, b"ok")
        self.assertEqual(opener.calls, 3)
        self.assertEqual(len(delays), 2)
        not_found = urllib.error.HTTPError("u", 404, "missing", Message(), io.BytesIO(b""))
        fetcher, opener, _ = self.make_fetcher([not_found, FakeResponse(b"wrong")])
        with self.assertRaises(FetchError):
            fetcher.fetch("https://docs.example.test/page", ("docs.example.test",))
        self.assertEqual(opener.calls, 1)

    def test_retry_after_is_capped(self):
        headers = Message(); headers["Retry-After"] = "999"
        limited = urllib.error.HTTPError("u", 429, "busy", headers, io.BytesIO(b""))
        fetcher, _, delays = self.make_fetcher([limited, FakeResponse(b"ok")], max_retry_after=3.0)
        fetcher.fetch("https://docs.example.test/page", ("docs.example.test",))
        self.assertLessEqual(delays[0], 3.0)

    def test_redirect_target_must_remain_on_official_host(self):
        guard = RedirectGuard(("docs.example.test",), max_redirects=5)
        request = guard.redirect_request(
            type("Request", (), {"full_url": "https://docs.example.test/page"})(),
            None, 302, "Found", Message(), "https://evil.example.test/collect",
        )
        self.assertIsNone(request)

    def test_cross_origin_redirect_strips_authentication_headers(self):
        guard = RedirectGuard(("docs.example.test", "cdn.example.test"), max_redirects=5)
        request = type("Request", (), {
            "full_url": "https://docs.example.test/page",
            "header_items": lambda self: [("Authorization", "secret-value"), ("Cookie", "session=value"), ("User-agent", "radar")],
            "get_method": lambda self: "GET", "data": None,
        })()
        redirected = guard.redirect_request(request, None, 302, "Found", Message(), "https://cdn.example.test/page")
        headers = {key.lower(): value for key, value in redirected.header_items()}
        self.assertNotIn("authorization", headers)
        self.assertNotIn("cookie", headers)
        self.assertEqual(headers.get("user-agent"), "radar")

    def test_aggregate_byte_budget_and_auth_header_are_rejected(self):
        fetcher, opener, _ = self.make_fetcher([FakeResponse(b"1234"), FakeResponse(b"next")],
                                               max_response_bytes=4, max_total_bytes=4)
        fetcher.fetch("https://docs.example.test/page", ("docs.example.test",))
        with self.assertRaises(FetchError):
            fetcher.fetch("https://docs.example.test/page", ("docs.example.test",))
        self.assertEqual(opener.calls, 1)
        fetcher, opener, _ = self.make_fetcher([FakeResponse(b"unused")])
        with self.assertRaises(FetchError) as context:
            fetcher.fetch("https://docs.example.test/page", ("docs.example.test",), headers={"Authorization": "secret-value"})
        self.assertNotIn("secret-value", str(context.exception))
        self.assertEqual(opener.calls, 0)

    def test_successful_public_requests_are_get_only(self):
        fetcher, opener, _ = self.make_fetcher([FakeResponse(b"ok")])
        fetcher.fetch("https://docs.example.test/page", ("docs.example.test",))
        self.assertEqual(opener.last_method, "GET")


if __name__ == "__main__":
    unittest.main()
