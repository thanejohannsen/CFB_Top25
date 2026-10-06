#!/usr/bin/env python3
"""Grade the ranking against the human polls.

The AP poll is a *yardstick, not ground truth* -- the point of this project is
to disagree with the polls in a principled, explainable way. But disagreeing
with AP and with FPI at the same time is usually a bug, not a brave take. That
is how the original Strength-of-Schedule double-count was caught: it put a 3-2
team at #16 and a 5-0 team with the country's #2 FPI at #23.

    python3 scripts/evaluate.py --year 2026              # current config
    python3 scripts/evaluate.py --year 2025 --grid       # sweep the weights
    python3 scripts/evaluate.py --year 2026 --offline    # no network

Metrics, over the teams appearing in both top 25s:
    overlap   how many of the 25 we agree belong there at all
    gap       mean absolute rank difference
    tau       Kendall rank correlation (-1 reversed, 0 unrelated, +1 identical)
    top10     how many of AP's top 10 we also place in our top 10
"""

from __future__ import annotations

import argparse
import itertools
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from cfbrank.config import load  # noqa: E402
from cfbrank.engine.pipeline import rank  # noqa: E402
from cfbrank.errors import CfbRankError  # noqa: E402
from cfbrank.sources.loader import build_dataset  # noqa: E402

DEFAULT_GRID = [
    (1.00, 0.00, 0.00),
    (0.85, 0.00, 0.15),
    (0.75, 0.00, 0.25),
    (0.60, 0.00, 0.40),
    (0.70, 0.05, 0.25),
    (0.65, 0.10, 0.25),
    (0.75, 0.25, 0.00),
]


def open_source(cfg, offline: bool):
    if offline or bool(cfg["source.offline"]):
        from cfbrank.sources.fixtures import FixtureSource

        return FixtureSource(cfg["source.fixtures_dir"], cfg["season.year"])

    from cfbrank.sources.cfbd import CFBDClient
    from cfbrank.sources.http_cache import HttpCache

    key = os.environ.get("CFBD_API_KEY", "").strip() or str(cfg.get("source.api_key", "")).strip()
    if not key:
        raise CfbRankError("no API key; use --offline")
    return CFBDClient(
        api_key=key,
        base_url=cfg["source.base_url"],
        cache=HttpCache(cfg["source.cache_dir"], int(cfg["source.cache_ttl_minutes"])),
    )


def latest_ap(rows) -> dict[str, int]:
    """The most recent AP top 25 in the payload."""
    weeks = [r for r in rows if any("AP" in p.get("poll", "") for p in r.get("polls") or [])]
    if not weeks:
        return {}
    newest = max(weeks, key=lambda r: (r.get("seasonType") != "regular", r.get("week") or 0))
    for poll in newest.get("polls") or []:
        if "AP" in poll.get("poll", ""):
            return {r["school"]: r["rank"] for r in poll.get("ranks") or []}
    return {}


def score(mine: dict[str, int], ap: dict[str, int]) -> dict:
    both = [(mine[t], ap[t]) for t in mine if t in ap]
    if not both:
        return {"overlap": 0, "gap": float("nan"), "tau": 0.0, "top10": 0}
    gap = sum(abs(a - b) for a, b in both) / len(both)
    conc = disc = 0
    for (x1, y1), (x2, y2) in itertools.combinations(both, 2):
        s = (x1 - x2) * (y1 - y2)
        conc += s > 0
        disc += s < 0
    tau = (conc - disc) / (conc + disc) if conc + disc else 0.0
    top10 = sum(1 for t, r in ap.items() if r <= 10 and mine.get(t, 99) <= 10)
    return {"overlap": len(both), "gap": gap, "tau": tau, "top10": top10}


def run_once(year, overrides, dataset, config_path):
    cfg = load(config_path, [f"season.year={year}", *overrides])
    result = rank(dataset, cfg)
    return cfg, result, {t: i + 1 for i, t in enumerate(result.order[:25])}


def main() -> int:
    ap_ = argparse.ArgumentParser()
    ap_.add_argument("--year", type=int, required=True)
    ap_.add_argument("--config", default="config/ranking.toml")
    ap_.add_argument("--offline", action="store_true")
    ap_.add_argument("--grid", action="store_true", help="sweep w_sor/w_sos/w_fpi")
    args = ap_.parse_args()

    cfg0 = load(args.config, [f"season.year={args.year}"])
    source = open_source(cfg0, args.offline)
    dataset = build_dataset(source, args.year, str(cfg0["season.season_type"]))

    season_type = "postseason" if args.year < 2026 else "regular"
    ap = latest_ap(source.poll_rankings(args.year, season_type))
    if not ap:
        print(f"no AP poll available for {args.year}", file=sys.stderr)
        return 3

    if args.grid:
        print(f"=== {args.year}: base = w_sor*SoR + w_sos*SoS + w_fpi*FPI ===")
        print(f"{'w_sor':>6}{'w_sos':>6}{'w_fpi':>6} |{'overlap':>8}{'gap':>7}{'tau':>7}{'top10':>7}")
        shipped = (
            float(cfg0["stage1.w_sor"]),
            float(cfg0["stage1.w_sos"]),
            float(cfg0["stage1.w_fpi"]),
        )
        for w_sor, w_sos, w_fpi in DEFAULT_GRID:
            _, _, mine = run_once(
                args.year,
                [f"stage1.w_sor={w_sor}", f"stage1.w_sos={w_sos}", f"stage1.w_fpi={w_fpi}"],
                dataset,
                args.config,
            )
            m = score(mine, ap)
            flag = "  <-- shipped" if (w_sor, w_sos, w_fpi) == shipped else ""
            print(
                f"{w_sor:6.2f}{w_sos:6.2f}{w_fpi:6.2f} |{m['overlap']:8}{m['gap']:7.1f}"
                f"{m['tau']:+7.2f}{m['top10']:7}{flag}"
            )
        return 0

    cfg, result, mine = run_once(args.year, [], dataset, args.config)
    m = score(mine, ap)
    print(f"\n=== {result.snapshot_id} vs the AP top 25 ===")
    print(
        f"  base = {cfg['stage1.w_sor']:g} x SoR"
        + (f" + {cfg['stage1.w_fpi']:g} x FPI" if cfg["stage1.w_fpi"] else "")
        + (f" + {cfg['stage1.w_sos']:g} x SoS" if cfg["stage1.w_sos"] else "")
    )
    print(
        f"  overlap {m['overlap']}/25   mean rank gap {m['gap']:.1f}"
        f"   Kendall tau {m['tau']:+.2f}   AP top-10 matched {m['top10']}/10\n"
    )

    print(f"  {'#':>3}  {'team':22}{'AP':>4}{'diff':>6}")
    for team, pos in mine.items():
        a = ap.get(team)
        diff = f"{a - pos:+d}" if a else ""
        print(f"  {pos:3}. {team:22}{(a if a else '-'):>4}{diff:>6}")

    only_mine = sorted(set(mine) - set(ap), key=lambda t: mine[t])
    only_ap = sorted(set(ap) - set(mine), key=lambda t: ap[t])
    print(f"\n  we rank, AP does not : {', '.join(f'{t} (#{mine[t]})' for t in only_mine) or 'none'}")
    print(f"  AP ranks, we do not  : {', '.join(f'{t} (AP {ap[t]})' for t in only_ap) or 'none'}")

    worst = sorted(((abs(ap[t] - mine[t]), t) for t in mine if t in ap), reverse=True)[:5]
    if worst:
        print("\n  biggest disagreements:")
        for d, t in worst:
            print(f"    {t:22} ours #{mine[t]:<3} AP #{ap[t]:<3} ({d} apart)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
