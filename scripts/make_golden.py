#!/usr/bin/env python3
"""Regenerate the golden pipeline output from the 2025 fixtures.

2025 is a completed season, so its fixtures never change and the golden file
is a genuine regression net for the ENGINE. Run this only when a change to the
engine is intended, and read the diff before committing it.

    python3 scripts/make_golden.py

## Why this pins its own weights

It deliberately does NOT use the weights in config/ranking.toml. Those are the
owner's dial settings, meant to be retuned from the GitHub web UI whenever they
like -- and if the golden file tracked them, every such edit would fail CI and
have to be followed by a regeneration the owner has no way to run.

So the weights below are frozen. The golden file then answers the question it
should answer -- "does the engine still turn these inputs into these outputs?"
-- rather than "has anybody changed their mind about how much the resume counts?"

Changing FROZEN is a deliberate act: it means the engine's behaviour has moved
and the net needs resetting. Changing config/ranking.toml is not.
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

# Frozen on purpose -- see the module docstring. These are weights, not engine
# behaviour, so they are pinned here rather than read from the live config.
FROZEN = [
    "stage1.w_sor=0.55",
    "stage1.w_market=0.30",
    "stage1.w_perf=0.15",
    "stage1.w_adjust=1.0",
    "stage1.w_fpi=0.0",
    "stage1.w_sos=0.0",
    "stage2.w_cover=4.0",
    "stage2.w_loss_quality=3.0",
    "stage2.w_best_win=1.0",
    "stage2.w_game_control=0.5",
    "stage3.gap=15",
    "stage3.strength=0.5",
    "stage4.strength=14.0",
    "stage4.drift_weight=1.0",
    "stage4.drift_exponent=1.5",
    "stage4.evidence.w_margin=0.50",
    "stage4.evidence.w_rating_gap=0.30",
    "stage4.evidence.w_recency=1.20",
    "stage4.evidence.w_common_opponents=0.15",
    "stage4.evidence.recency_half_life_good=8.0",
    "stage4.evidence.recency_half_life_bad=6.0",
    "stage4.grounds.enabled=true",
    "stage4.grounds.impressive_bar=6.0",
    "stage4.grounds.min_weeks=2",
    "stage4.grounds.band_start=0.5",
    "stage4.grounds.band_step=0.25",
    "stage4.grounds.band_max=1.5",
    "stage4.max_block=4",
    "market.horizon_weeks=1",
    "market.recency_half_life=0.0",
    "resume.reference_place=25",
    "resume.margin_sigma=13.5",
    # EVERYTHING ELSE THE ENGINE READS, pinned for the same reason -- the list
    # has to be exhaustive or it does not do its job. `stage4.evidence.margin_cap`
    # was missing, so when the owner moved it from 28 to 21 from the web UI the
    # golden tracked the live config and CI went red on main: exactly the
    # failure this file exists to prevent. A knob the engine reads and this list
    # omits is a trap, not a default.
    "stage1.pool_size=40",
    "stage1.output_size=25",
    "stage1.require_fpi=true",
    "stage1.fbs_only=true",
    "market.min_games=3",
    "market.fit_home_field=true",
    "market.home_field_points=2.5",
    "performance.min_games=3",
    "performance.fit_home_edge=true",
    "stage2.best_win_place=25",
    "stage2.cover_game_cap=0.0",
    "stage2.fit_cover_venue=true",
    "resume.min_games=3",
    "stage3.enabled=true",
    "stage4.split_series=most_recent",
    "stage4.max_passes=400",
    "stage4.evidence.home_field_points=2.5",
    "stage4.evidence.fit_home_field=true",
    "stage4.evidence.margin_cap=28",
    "stage4.evidence.weight_floor=1.0",
    "stage4.evidence.conviction_floor=-2.0",
    "stage4.evidence.recency_floor=0.15",
]


def build() -> str:
    cfg = load("config/ranking.toml", ["season.year=2025", *FROZEN])
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
