#!/usr/bin/env python3
"""Grade a configuration by what it predicts, not by who it agrees with.

Take the ranking as it stood after week N, then predict the winner of every game
played after week N: the higher-ranked team wins. Count how often that is right.
That is the only test of a ranking that cannot be gamed by copying somebody.

    python3 scripts/evaluate.py --year 2025
    python3 scripts/evaluate.py --year 2025 --grid
    python3 scripts/evaluate.py --year 2025 --set market.horizon_weeks=0

The AP poll is printed as one reference row, on exactly the same games, and that
is all it is. It is not a target. In 2025 AP had Miami #18 in week 11 and they
finished #2; it got 58.2% of its own ranked-vs-ranked games right, which is the
worst figure this script produces. Tuning toward poll agreement is how you build
a ranking that is wrong in the same places as everyone else's.

LOOK-AHEAD -- read this before trusting any number here
--------------------------------------------------------
`/ratings/fpi` has no week parameter. It serves ONE snapshot, taken whenever the
request is made, and on a completed season that snapshot is the final answer:
Strength of Schedule and FPI describe the whole year. So ranking 2025 "through
week 4" with any weight on those terms reads the answer key. An earlier version
of this project quoted 75.5% accuracy from exactly that mistake, back when
Strength of Record came from the same snapshot.

Betting lines and per-game PPA are different: both are stamped per game, so a
cutoff is a real cutoff. The resume is now computed from those lines rather than
taken from ESPN, which is what finally lets the WHOLE formula be scored instead
of half of it. Only ESPN's own undated metrics are still zeroed by default;
`--allow-lookahead` puts them back and labels every number as contaminated.
"""

from __future__ import annotations

import argparse
import os
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable, Mapping, Sequence

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from cfbrank.config import load  # noqa: E402
from cfbrank.engine import h2h  # noqa: E402
from cfbrank.engine.pipeline import rank  # noqa: E402
from cfbrank.errors import CfbRankError  # noqa: E402
from cfbrank.models import Dataset, SEASON_TYPE_ORDER  # noqa: E402
from cfbrank.sources.loader import build_dataset  # noqa: E402

# Terms that come from a single season-long snapshot and therefore cannot be
# backtested. Zeroed unless --allow-lookahead.
# The resume is computed here now (engine/resume_strength), from per-game
# betting lines, so it HAS a real cutoff and can be scored. Only ESPN's own
# undated metrics stay on this list.
SNAPSHOT_TERMS = ("stage1.w_sos", "stage1.w_fpi")

# Weeks to rank through. Early weeks have too little signal to mean much and
# late ones leave too few games to predict; this spans the useful middle.
DEFAULT_WEEKS = (5, 7, 9, 11, 13)

# (w_market, w_perf) pairs. The ratio between these two is the one thing in the
# base formula that measurement can settle, so the sweep varies it alone.
DEFAULT_GRID = (
    (1.00, 0.00),
    (0.75, 0.25),
    (0.60, 0.40),
    (0.50, 0.50),
    (0.40, 0.60),
    (0.25, 0.75),
    (0.00, 1.00),
)


@dataclass(slots=True)
class Tally:
    right: int = 0
    total: int = 0

    def add(self, correct: bool) -> None:
        self.total += 1
        self.right += 1 if correct else 0

    @property
    def pct(self) -> float:
        return 100.0 * self.right / self.total if self.total else float("nan")

    def __iadd__(self, other: "Tally") -> "Tally":
        self.right += other.right
        self.total += other.total
        return self

    def __str__(self) -> str:
        return "n/a" if not self.total else f"{self.pct:5.1f}% ({self.right}/{self.total})"


@dataclass(slots=True)
class Report:
    overall: Tally = field(default_factory=Tally)
    ranked: Tally = field(default_factory=Tally)
    ap_shared: Tally = field(default_factory=Tally)
    ap_own: Tally = field(default_factory=Tally)
    violations: int = 0
    honored: int = 0
    weeks: int = 0

    @property
    def h2h_kept(self) -> float:
        total = self.honored + self.violations
        return 100.0 * self.honored / total if total else float("nan")


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


def ap_poll_at(rows: Iterable[Mapping], week: int) -> dict[str, int]:
    """The AP top 25 as it stood after `week`, which is what AP knew then too."""
    best: tuple[tuple[int, int], dict[str, int]] | None = None
    for row in rows or []:
        st = str(row.get("seasonType") or "regular")
        wk = int(row.get("week") or 0)
        if (SEASON_TYPE_ORDER.get(st, 9), wk) > (SEASON_TYPE_ORDER.get("regular", 0), week):
            continue
        for poll in row.get("polls") or []:
            if "AP" not in str(poll.get("poll") or ""):
                continue
            ranks = {
                str(r["school"]): int(r["rank"])
                for r in poll.get("ranks") or []
                if r.get("school") and r.get("rank")
            }
            key = (SEASON_TYPE_ORDER.get(st, 9), wk)
            if ranks and (best is None or key > best[0]):
                best = (key, ranks)
    return best[1] if best else {}


def later_games(dataset: Dataset, cutoff) -> list[tuple[str, str]]:
    """(winner, loser) for every decided game after the cutoff."""
    results, _ = h2h.to_results(dataset.games)
    return [(r.winner, r.loser) for r in results if r.order_key > cutoff]


def predict(ranks: Mapping[str, int], games: Sequence[tuple[str, str]], universe=None) -> Tally:
    """Score one ranking: the better-ranked team is predicted to win.

    A game is only counted when BOTH teams are ranked -- a ranking cannot be
    held to a game it has no opinion about. `universe` narrows that further so
    two systems can be compared on identical games, which is the comparison an
    earlier version of this script got wrong: it graded AP on ranked-vs-ranked
    games while grading the engine on everything, then called the engine better.
    """
    tally = Tally()
    for winner, loser in games:
        if winner not in ranks or loser not in ranks:
            continue
        if universe is not None and (winner not in universe or loser not in universe):
            continue
        tally.add(ranks[winner] < ranks[loser])
    return tally


def full_order(result) -> dict[str, int]:
    """Every rated team, in order: the published pool first, then the rest.

    The site shows 25 and reorders 40, but a prediction test wants an opinion on
    every game, so teams outside the pool are appended by base score.
    """
    ranks = {t: i + 1 for i, t in enumerate(result.order)}
    rest = sorted(
        (tb for tb in result.teams.values() if tb.team not in ranks),
        key=lambda tb: (tb.base_score, tb.sor_rank, tb.team),
    )
    for i, tb in enumerate(rest, len(ranks) + 1):
        ranks[tb.team] = i
    return ranks


def evaluate(
    year: int,
    dataset: Dataset,
    config_path: str,
    overrides: Sequence[str],
    weeks: Sequence[int],
    polls: Sequence[Mapping],
    top_n: int = 25,
) -> Report:
    report = Report()
    for week in weeks:
        cfg = load(config_path, [f"season.year={year}", f"season.week={week}", *overrides])
        result = rank(dataset, cfg)
        if result.week != week:
            continue  # the season does not reach this week
        _, _, cutoff = h2h.resolve_week(
            dataset.games, dataset.calendar, week, str(cfg["season.season_type"])
        )
        games = later_games(dataset, cutoff)
        if not games:
            continue

        ranks = full_order(result)
        top = {t for t, r in ranks.items() if r <= top_n}
        ap = ap_poll_at(polls, week)

        report.overall += predict(ranks, games)
        report.ranked += predict(ranks, games, universe=top)
        if ap:
            # Same games for both systems: those between two AP-ranked teams.
            report.ap_own += predict(ap, games, universe=set(ap))
            report.ap_shared += predict(ranks, games, universe=set(ap))
        report.violations += result.counts["h2h_overridden"]
        report.honored += result.counts["h2h_honored"]
        report.weeks += 1
    return report


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--year", type=int, required=True)
    p.add_argument("--config", default="config/ranking.toml")
    p.add_argument("--offline", action="store_true")
    p.add_argument("--grid", action="store_true", help="sweep the market:PPA ratio")
    p.add_argument("--set", dest="overrides", action="append", default=[], metavar="path=value")
    p.add_argument(
        "--weeks",
        default=",".join(str(w) for w in DEFAULT_WEEKS),
        help="comma-separated weeks to rank through",
    )
    p.add_argument("--top", type=int, default=25, help="size of the ranked-vs-ranked subset")
    p.add_argument(
        "--allow-lookahead",
        action="store_true",
        help="keep the season-snapshot terms (SoR/SoS/FPI). Every result is then contaminated.",
    )
    args = p.parse_args()

    weeks = [int(w) for w in args.weeks.split(",") if w.strip()]
    cfg0 = load(args.config, [f"season.year={args.year}"])
    source = open_source(cfg0, args.offline)
    dataset = build_dataset(source, args.year, str(cfg0["season.season_type"]))
    polls = list(source.poll_rankings(args.year, "regular") or [])

    guard: list[str] = []
    snapshot_weight = sum(float(cfg0[k]) for k in SNAPSHOT_TERMS)
    if args.allow_lookahead:
        print("!" * 72)
        print("!! --allow-lookahead: SoR/SoS/FPI come from ONE end-of-season snapshot.")
        print("!! Every accuracy below is reading the answer key. Do not quote these.")
        print("!" * 72)
    elif snapshot_weight > 0:
        guard = [f"{k}=0.0" for k in SNAPSHOT_TERMS]
        print(
            f"note: zeroing {', '.join(k.split('.')[-1] for k in SNAPSHOT_TERMS)}"
            f" (combined weight {snapshot_weight:g}) -- ESPN serves those from one"
            " undated snapshot, so they cannot be scored honestly.\n"
        )

    base_overrides = list(args.overrides) + guard

    if args.grid:
        print(f"=== {args.year}: predicting every game after weeks {weeks} ===")
        print(
            "  read the 'all FBS' column: it is the same games in every row.\n"
            "  'ranked v ranked' uses each row's OWN top 25, so its denominator moves\n"
            "  and small differences there are composition, not skill.\n"
        )
        print(f"{'w_mkt':>6}{'w_ppa':>6} |{'all FBS':>18}{'ranked v ranked':>22}{'H2H kept':>11}")
        # The grid rows sum to 1 while the shipped weights share the base with
        # w_sor, so compare the RATIO rather than the raw pair.
        mkt, perf = float(cfg0["stage1.w_market"]), float(cfg0["stage1.w_perf"])
        shipped_ratio = mkt / (mkt + perf) if (mkt + perf) else 0.0
        for w_market, w_perf in DEFAULT_GRID:
            rep = evaluate(
                args.year,
                dataset,
                args.config,
                [*base_overrides, f"stage1.w_market={w_market}", f"stage1.w_perf={w_perf}"],
                weeks,
                polls,
                args.top,
            )
            flag = "  <-- shipped ratio" if abs(w_market - shipped_ratio) < 1e-9 else ""
            print(
                f"{w_market:6.2f}{w_perf:6.2f} |{str(rep.overall):>18}{str(rep.ranked):>22}"
                f"{rep.h2h_kept:10.1f}%{flag}"
            )
        return 0

    rep = evaluate(args.year, dataset, args.config, base_overrides, weeks, polls, args.top)
    if not rep.weeks:
        print(f"no week in {weeks} has games to predict for {args.year}", file=sys.stderr)
        return 3

    print(f"=== {args.year}: ranked through {rep.weeks} week(s), predicting every later game ===\n")
    print(f"  all FBS games          {rep.overall}")
    print(f"  both in the top {args.top:<2}      {rep.ranked}")
    if rep.ap_own.total:
        print(f"\n  on AP-vs-AP games only:")
        print(f"    this ranking          {rep.ap_shared}")
        print(f"    the AP poll           {rep.ap_own}   (reference, not a target)")
    print(f"\n  head-to-head results kept {rep.h2h_kept:.1f}%"
          f"  ({rep.honored} honoured, {rep.violations} overridden)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
