"""The data-source contract. Implementations: cfbd.CFBDClient, fixtures.FixtureSource."""

from __future__ import annotations

import hashlib
import json
from typing import Any, Protocol, Sequence

from cfbrank.models import EndpointProvenance


def payload_sha256(payload: Any) -> str:
    blob = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return "sha256:" + hashlib.sha256(blob.encode("utf-8")).hexdigest()


class DataSource(Protocol):
    """Returns raw camelCase records exactly as CFBD serves them."""

    def fpi_ratings(self, year: int) -> list[dict]: ...
    def games(self, year: int, season_type: str) -> list[dict]: ...
    def records(self, year: int) -> list[dict]: ...
    def calendar(self, year: int) -> list[dict]: ...
    def provenance(self) -> Sequence[EndpointProvenance]: ...
    @property
    def synthetic(self) -> bool: ...
