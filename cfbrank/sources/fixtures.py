"""Reads checked-in CFBD responses. No network, no API key.

This is the primary development and test path: the whole pipeline, the golden
test and the site all run from these files.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Sequence

from cfbrank.errors import UpstreamUnavailable
from cfbrank.models import EndpointProvenance
from cfbrank.sources.base import payload_sha256

FILES = {
    "/ratings/fpi": "ratings_fpi.json",
    "/games": "games.json",
    "/records": "records.json",
    "/calendar": "calendar.json",
    "/lines": "lines.json",
    "/ppa/games": "ppa_games.json",
    "/rankings": "rankings_polls.json",
}


class FixtureSource:
    def __init__(self, root: str | Path, year: int | str) -> None:
        self.root = Path(root)
        self.dir = self.root / str(year)
        if not self.dir.is_dir():
            available = sorted(p.name for p in self.root.glob("*") if p.is_dir()) if self.root.is_dir() else []
            raise UpstreamUnavailable(
                f"fixture directory not found: {self.dir}"
                + (f" (available: {', '.join(available)})" if available else "")
            )
        self._prov: list[EndpointProvenance] = []

    @property
    def synthetic(self) -> bool:
        return False

    def provenance(self) -> Sequence[EndpointProvenance]:
        return tuple(self._prov)

    def _load(self, path: str) -> Any:
        f = self.dir / FILES[path]
        if not f.exists():
            raise UpstreamUnavailable(f"missing fixture {f}")
        with f.open("r", encoding="utf-8") as fh:
            payload = json.load(fh)
        self._prov.append(
            EndpointProvenance(
                path=path,
                params={"fixture": str(self.dir)},
                fetched_at="",
                cache="fixture",
                sha256=payload_sha256(payload),
                records=len(payload) if isinstance(payload, list) else 1,
            )
        )
        return payload

    def fpi_ratings(self, year: int) -> list[dict]:
        return self._load("/ratings/fpi")

    def games(self, year: int, season_type: str = "both") -> list[dict]:
        return self._load("/games")

    def records(self, year: int) -> list[dict]:
        return self._load("/records")

    def calendar(self, year: int) -> list[dict]:
        return self._load("/calendar")

    def lines(self, year: int, season_type: str = "both") -> list[dict]:
        """A season with no lines fixture still ranks; the market term drops out."""
        try:
            return self._load("/lines")
        except UpstreamUnavailable:
            return []

    def ppa_games(self, year: int, season_type: str = "both") -> list[dict]:
        """As with lines: absent is a missing term, not a failed run."""
        try:
            return self._load("/ppa/games")
        except UpstreamUnavailable:
            return []

    def poll_rankings(self, year: int, season_type: str = "regular") -> list[dict]:
        """Only scripts/evaluate.py reads this; absent fixtures are not fatal."""
        try:
            return self._load("/rankings")
        except UpstreamUnavailable:
            return []
