#!/usr/bin/env python3
"""Regenerate the golden pipeline output from the 2025 fixtures.

2025 is a completed season, so its fixtures never change and the golden file
is a genuine regression net. Run this only when a ranking change is intended,
and read the diff before committing it.

    python3 scripts/make_golden.py
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from cfbrank.config import load  # noqa: E402
from cfbrank.engine.pipeline import rank  # noqa: E402
from cfbrank.output.schema import build_payload  # noqa: E402
from cfbrank.output.writer import dumps_stable  # noqa: E402
from cfbrank.sources.fixtures import FixtureSource  # noqa: E402
from cfbrank.sources.loader import build_dataset  # noqa: E402

GOLDEN = Path("tests/fixtures/golden/rankings_2025.json")
GENERATED_AT = "2026-01-01T00:00:00Z"


def build() -> str:
    cfg = load("config/ranking.toml", ["season.year=2025"])
    dataset = build_dataset(FixtureSource("tests/fixtures/cfbd", 2025), 2025)
    payload = build_payload(rank(dataset, cfg), dataset, cfg, GENERATED_AT)
    return dumps_stable(payload, int(cfg["output.float_precision"]))


def main() -> int:
    GOLDEN.parent.mkdir(parents=True, exist_ok=True)
    GOLDEN.write_text(build(), encoding="utf-8")
    print(f"wrote {GOLDEN} ({GOLDEN.stat().st_size // 1024} KB)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
