"""CollegeFootballData.com REST client.

Quota discipline: a full run makes at most four calls, all cacheable. The
retry policy distinguishes transient failures (retry with backoff) from
permanent ones (fail immediately), and treats an HTTP 200 carrying an empty
list as unavailable -- a wrong or future year returns [] with status 200, and
ranking from that would publish an empty Top 25.
"""

from __future__ import annotations

import time
from typing import Any, Callable, Mapping, Sequence

from cfbrank.errors import UpstreamUnavailable
from cfbrank.models import EndpointProvenance
from cfbrank.sources.http_cache import HttpCache

RETRY_STATUS = frozenset({429, 500, 502, 503, 504})
FATAL_STATUS = frozenset({400, 401, 403, 404})
REQUIRED_NONEMPTY = frozenset({"/ratings/fpi", "/games"})


class CFBDClient:
    def __init__(
        self,
        api_key: str,
        base_url: str,
        cache: HttpCache,
        timeout: float = 20.0,
        max_retries: int = 4,
        backoff: float = 1.5,
        session: Any = None,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        if not api_key:
            raise UpstreamUnavailable(
                "no CFBD API key. Set CFBD_API_KEY, or run with --offline to use fixtures."
            )
        self.api_key = api_key
        self.base_url = base_url.rstrip("/")
        self.cache = cache
        self.timeout = timeout
        self.max_retries = max(0, int(max_retries))
        self.backoff = backoff
        self.sleep = sleep
        self._prov: list[EndpointProvenance] = []
        self._session = session
        self.served_stale = False

    @property
    def synthetic(self) -> bool:
        return False

    def provenance(self) -> Sequence[EndpointProvenance]:
        return tuple(self._prov)

    def _get_session(self):
        if self._session is None:
            import requests  # imported lazily so --offline never needs it

            self._session = requests.Session()
        return self._session

    def _get(self, path: str, params: Mapping[str, Any]) -> Any:
        cached = self.cache.get(path, params)
        if cached is not None:
            payload, prov = cached
            self._prov.append(prov)
            return payload

        session = self._get_session()
        url = f"{self.base_url}{path}"
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Accept": "application/json",
        }
        last_error = "unknown error"

        for attempt in range(self.max_retries + 1):
            try:
                resp = session.get(url, params=dict(params), headers=headers, timeout=self.timeout)
            except Exception as exc:  # connection reset, DNS, timeout
                last_error = f"{type(exc).__name__}: {exc}"
            else:
                status = resp.status_code
                if status in FATAL_STATUS:
                    detail = "invalid or unauthorized API key" if status in (401, 403) else "bad request"
                    raise UpstreamUnavailable(f"{path}: HTTP {status} ({detail}); not retrying")
                if status == 200:
                    try:
                        payload = resp.json()
                    except ValueError as exc:
                        last_error = f"malformed JSON: {exc}"
                    else:
                        if path in REQUIRED_NONEMPTY and isinstance(payload, list) and not payload:
                            raise UpstreamUnavailable(
                                f"{path} returned an empty list for params {dict(params)!r}; "
                                "refusing to rank from no data"
                            )
                        self._prov.append(self.cache.put(path, params, payload))
                        return payload
                elif status in RETRY_STATUS:
                    last_error = f"HTTP {status}"
                    retry_after = resp.headers.get("Retry-After") if hasattr(resp, "headers") else None
                    if retry_after:
                        try:
                            self.sleep(min(float(retry_after), 60.0))
                        except (TypeError, ValueError):
                            pass
                else:
                    raise UpstreamUnavailable(f"{path}: unexpected HTTP {status}")

            if attempt < self.max_retries:
                self.sleep(self.backoff * (2 ** attempt))

        stale = self.cache.get_stale(path, params)
        if stale is not None:
            payload, prov = stale
            self._prov.append(prov)
            self.served_stale = True
            return payload
        raise UpstreamUnavailable(f"{path} unavailable after {self.max_retries + 1} attempts: {last_error}")

    # -- endpoints ---------------------------------------------------------
    def fpi_ratings(self, year: int) -> list[dict]:
        return self._get("/ratings/fpi", {"year": year})

    def games(self, year: int, season_type: str = "both") -> list[dict]:
        return self._get("/games", {"year": year, "seasonType": season_type, "classification": "fbs"})

    def records(self, year: int) -> list[dict]:
        return self._get("/records", {"year": year})

    def calendar(self, year: int) -> list[dict]:
        return self._get("/calendar", {"year": year})

    def poll_rankings(self, year: int, season_type: str = "regular") -> list[dict]:
        """Human polls, used ONLY by scripts/evaluate.py as an external yardstick.

        The ranking pipeline never calls this and must stay poll-free -- the
        whole point is to derive a ranking from results, not to copy one.
        """
        return self._get("/rankings", {"year": year, "seasonType": season_type})
