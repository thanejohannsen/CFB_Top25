"""HTTP client retry policy and the on-disk cache, with no network at all."""

from __future__ import annotations

import tempfile
import unittest
from datetime import datetime, timedelta, timezone

from cfbrank.errors import UpstreamUnavailable
from cfbrank.sources.cfbd import CFBDClient
from cfbrank.sources.fixtures import FixtureSource
from cfbrank.sources.http_cache import HttpCache


class FakeResponse:
    def __init__(self, status, payload=None, headers=None):
        self.status_code = status
        self._payload = payload
        self.headers = headers or {}

    def json(self):
        if self._payload is None:
            raise ValueError("no json")
        return self._payload


class FakeSession:
    """Replays a scripted list of responses (or raises) and records calls."""

    def __init__(self, script):
        self.script = list(script)
        self.calls = []

    def get(self, url, params=None, headers=None, timeout=None):
        self.calls.append((url, dict(params or {}), dict(headers or {})))
        item = self.script.pop(0) if self.script else FakeResponse(500)
        if isinstance(item, Exception):
            raise item
        return item


def client(script, cache_dir, **kw):
    slept = []
    c = CFBDClient(
        api_key="test-key",
        base_url="https://example.invalid",
        cache=HttpCache(cache_dir, kw.pop("ttl", 60)),
        max_retries=kw.pop("max_retries", 2),
        backoff=0.0,
        session=FakeSession(script),
        sleep=lambda s: slept.append(s),
        **kw,
    )
    return c, slept


class TestCFBDClient(unittest.TestCase):
    def test_requires_a_key(self):
        with self.assertRaises(UpstreamUnavailable):
            CFBDClient("", "https://example.invalid", HttpCache("/tmp/x", 1))

    def test_sends_a_bearer_token(self):
        with tempfile.TemporaryDirectory() as d:
            c, _ = client([FakeResponse(200, [{"team": "A"}])], d)
            c.fpi_ratings(2026)
            self.assertEqual(c._session.calls[0][2]["Authorization"], "Bearer test-key")

    def test_success_is_cached_and_not_refetched(self):
        with tempfile.TemporaryDirectory() as d:
            c, _ = client([FakeResponse(200, [{"team": "A"}])], d)
            self.assertEqual(c.fpi_ratings(2026), [{"team": "A"}])
            self.assertEqual(c.fpi_ratings(2026), [{"team": "A"}])
            self.assertEqual(len(c._session.calls), 1, "second call must come from cache")
            self.assertEqual([p.cache for p in c.provenance()], ["miss", "hit"])

    def test_401_fails_immediately_without_retrying(self):
        with tempfile.TemporaryDirectory() as d:
            c, _ = client([FakeResponse(401), FakeResponse(200, [{"team": "A"}])], d)
            with self.assertRaises(UpstreamUnavailable) as cm:
                c.fpi_ratings(2026)
            self.assertIn("unauthorized", str(cm.exception))
            self.assertEqual(len(c._session.calls), 1, "a bad key must not be retried")

    def test_404_is_not_retried(self):
        with tempfile.TemporaryDirectory() as d:
            c, _ = client([FakeResponse(404)], d)
            with self.assertRaises(UpstreamUnavailable):
                c.fpi_ratings(2026)
            self.assertEqual(len(c._session.calls), 1)

    def test_500_is_retried_then_succeeds(self):
        with tempfile.TemporaryDirectory() as d:
            c, _ = client([FakeResponse(500), FakeResponse(200, [{"team": "A"}])], d)
            self.assertEqual(c.fpi_ratings(2026), [{"team": "A"}])
            self.assertEqual(len(c._session.calls), 2)

    def test_429_honours_retry_after(self):
        with tempfile.TemporaryDirectory() as d:
            c, slept = client(
                [FakeResponse(429, headers={"Retry-After": "7"}), FakeResponse(200, [{"t": 1}])], d
            )
            c.fpi_ratings(2026)
            self.assertIn(7.0, slept)

    def test_connection_errors_are_retried(self):
        with tempfile.TemporaryDirectory() as d:
            c, _ = client([OSError("reset"), FakeResponse(200, [{"t": 1}])], d)
            self.assertEqual(c.fpi_ratings(2026), [{"t": 1}])

    def test_empty_list_on_a_required_endpoint_is_unavailable(self):
        # A future or mistyped year answers 200 with []. Ranking from that
        # would publish an empty Top 25.
        with tempfile.TemporaryDirectory() as d:
            c, _ = client([FakeResponse(200, [])], d)
            with self.assertRaises(UpstreamUnavailable):
                c.fpi_ratings(2099)

    def test_empty_list_is_fine_on_an_optional_endpoint(self):
        with tempfile.TemporaryDirectory() as d:
            c, _ = client([FakeResponse(200, [])], d)
            self.assertEqual(c.records(2026), [])

    def test_falls_back_to_a_stale_cache_when_everything_fails(self):
        with tempfile.TemporaryDirectory() as d:
            now = [datetime(2026, 10, 5, tzinfo=timezone.utc)]
            cache = HttpCache(d, 60, clock=lambda: now[0])
            cache.put("/ratings/fpi", {"year": 2026}, [{"team": "cached"}])
            now[0] += timedelta(days=2)
            c = CFBDClient("k", "https://example.invalid", cache, max_retries=1, backoff=0.0,
                           session=FakeSession([FakeResponse(503), FakeResponse(503)]),
                           sleep=lambda s: None)
            self.assertEqual(c.fpi_ratings(2026), [{"team": "cached"}])
            self.assertTrue(c.served_stale)
            self.assertEqual([p.cache for p in c.provenance()], ["stale"])

    def test_raises_when_exhausted_with_no_cache(self):
        with tempfile.TemporaryDirectory() as d:
            c, _ = client([FakeResponse(503), FakeResponse(503), FakeResponse(503)], d)
            with self.assertRaises(UpstreamUnavailable):
                c.fpi_ratings(2026)


class TestHttpCache(unittest.TestCase):
    def test_expiry_and_stale_access(self):
        with tempfile.TemporaryDirectory() as d:
            now = [datetime(2026, 10, 5, tzinfo=timezone.utc)]
            cache = HttpCache(d, 60, clock=lambda: now[0])
            self.assertIsNone(cache.get("/games", {"year": 2026}))
            cache.put("/games", {"year": 2026}, [1, 2, 3])
            self.assertIsNotNone(cache.get("/games", {"year": 2026}))
            now[0] += timedelta(hours=3)
            self.assertIsNone(cache.get("/games", {"year": 2026}), "past the TTL")
            self.assertIsNotNone(cache.get_stale("/games", {"year": 2026}))

    def test_key_is_order_independent(self):
        with tempfile.TemporaryDirectory() as d:
            cache = HttpCache(d, 60)
            self.assertEqual(
                cache.key("/games", {"a": 1, "b": 2}), cache.key("/games", {"b": 2, "a": 1})
            )

    def test_different_params_do_not_collide(self):
        with tempfile.TemporaryDirectory() as d:
            cache = HttpCache(d, 60)
            cache.put("/games", {"year": 2025}, ["a"])
            cache.put("/games", {"year": 2026}, ["b"])
            self.assertEqual(cache.get("/games", {"year": 2025})[0], ["a"])
            self.assertEqual(cache.get("/games", {"year": 2026})[0], ["b"])


class TestFixtureSource(unittest.TestCase):
    def test_reads_a_known_season(self):
        src = FixtureSource("tests/fixtures/cfbd", 2025)
        self.assertGreater(len(src.fpi_ratings(2025)), 100)
        self.assertGreater(len(src.games(2025)), 500)
        self.assertEqual([p.cache for p in src.provenance()], ["fixture", "fixture"])

    def test_unknown_year_lists_what_is_available(self):
        with self.assertRaises(UpstreamUnavailable) as cm:
            FixtureSource("tests/fixtures/cfbd", 1999)
        self.assertIn("2025", str(cm.exception))


if __name__ == "__main__":
    unittest.main()
