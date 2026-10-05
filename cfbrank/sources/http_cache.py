"""On-disk JSON cache keyed by canonical request URL.

Doubles as the graceful-degradation store: when the API is unreachable the
client falls back to a stale entry rather than publishing an empty ranking.
"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable, Mapping

from cfbrank.models import EndpointProvenance
from cfbrank.sources.base import payload_sha256


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


class HttpCache:
    def __init__(
        self,
        cache_dir: str | Path,
        ttl_minutes: int,
        clock: Callable[[], datetime] = _utcnow,
    ) -> None:
        self.dir = Path(cache_dir)
        self.ttl = timedelta(minutes=max(0, int(ttl_minutes)))
        self.clock = clock

    def key(self, path: str, params: Mapping[str, Any]) -> str:
        canonical = path + "?" + "&".join(f"{k}={params[k]}" for k in sorted(params))
        return hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:32]

    def _file(self, path: str, params: Mapping[str, Any]) -> Path:
        slug = path.strip("/").replace("/", "_") or "root"
        return self.dir / f"{slug}-{self.key(path, params)}.json"

    def _read(self, path: str, params: Mapping[str, Any]) -> dict | None:
        f = self._file(path, params)
        if not f.exists():
            return None
        try:
            with f.open("r", encoding="utf-8") as fh:
                env = json.load(fh)
        except (OSError, json.JSONDecodeError):
            return None
        if not isinstance(env, dict) or "payload" not in env:
            return None
        return env

    def get(
        self, path: str, params: Mapping[str, Any]
    ) -> tuple[Any, EndpointProvenance] | None:
        """Fresh entry only, or None when absent or past its TTL."""
        env = self._read(path, params)
        if env is None:
            return None
        try:
            stored = datetime.strptime(env["fetched_at"], "%Y-%m-%dT%H:%M:%SZ").replace(
                tzinfo=timezone.utc
            )
        except (KeyError, ValueError):
            return None
        if self.clock() - stored > self.ttl:
            return None
        return env["payload"], self._prov(path, params, env, "hit")

    def get_stale(
        self, path: str, params: Mapping[str, Any]
    ) -> tuple[Any, EndpointProvenance] | None:
        """Any entry, however old. The API-is-down path."""
        env = self._read(path, params)
        if env is None:
            return None
        return env["payload"], self._prov(path, params, env, "stale")

    def put(self, path: str, params: Mapping[str, Any], payload: Any) -> EndpointProvenance:
        self.dir.mkdir(parents=True, exist_ok=True)
        env = {
            "path": path,
            "params": {k: str(v) for k, v in params.items()},
            "fetched_at": iso(self.clock()),
            "payload": payload,
        }
        f = self._file(path, params)
        tmp = f.with_suffix(".tmp")
        with tmp.open("w", encoding="utf-8") as fh:
            json.dump(env, fh, ensure_ascii=False)
        tmp.replace(f)
        return self._prov(path, params, env, "miss")

    @staticmethod
    def _prov(
        path: str, params: Mapping[str, Any], env: Mapping[str, Any], cache: str
    ) -> EndpointProvenance:
        payload = env["payload"]
        return EndpointProvenance(
            path=path,
            params={k: str(v) for k, v in params.items()},
            fetched_at=str(env.get("fetched_at", "")),
            cache=cache,
            sha256=payload_sha256(payload),
            records=len(payload) if isinstance(payload, list) else 1,
        )
