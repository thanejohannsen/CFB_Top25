"""Stage 3 -- upset regression.

When a result spans a large gap in the base order, it is evidence that *both*
teams were mis-rated: the winner is better than its resume says and the loser
worse. Rather than enforce the result outright (which would drag a mediocre
team into the top 10) or ignore it (which lets a bad loss slide), pull the two
teams toward the midpoint of their base scores and let stage 4 sort them out
from there.

`strength` is the fraction of the way to the midpoint. Keep it at or below 0.5:
at 1.0 the stage fights stage 4, which simply pulls the winner back, producing
large week-to-week swings for no gain.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from typing import Mapping, Sequence

from cfbrank.engine.base_score import TeamBase, rerank
from cfbrank.models import GameResult
from cfbrank.normalize import sort_key


@dataclass(frozen=True, slots=True)
class RegressionEvent:
    winner: str
    loser: str
    winner_base_rank: int
    loser_base_rank: int
    gap: int
    midpoint: float
    winner_delta: float
    loser_delta: float
    week: int
    season_type: str
    score: str

    def describe(self) -> str:
        return (
            f"{self.winner} beat {self.loser} {self.score} from {self.gap} places lower "
            f"on the resume board (#{self.winner_base_rank} over #{self.loser_base_rank}), "
            f"so both were pulled toward the midpoint"
        )


def apply_upset_regression(
    teams: Sequence[TeamBase],
    results: Sequence[GameResult],
    cfg: Mapping[str, object],
) -> list[RegressionEvent]:
    """Mutates regression_adj for teams in qualifying results, then re-ranks."""
    if not bool(cfg.get("enabled", True)):
        return []

    gap_threshold = int(cfg.get("gap", 15))  # type: ignore[arg-type]
    strength = float(cfg.get("strength", 0.5))  # type: ignore[arg-type]
    if strength <= 0.0:
        return []

    by_team = {tb.team: tb for tb in teams}
    deltas: dict[str, float] = defaultdict(float)
    events: list[RegressionEvent] = []

    # Snapshot the scores first: every qualifying result is measured against the
    # same pre-regression board, so the outcome cannot depend on result order.
    score_before = {tb.team: tb.base_score for tb in teams}
    rank_before = {tb.team: tb.base_rank for tb in teams}

    for r in sorted(results, key=lambda r: (r.order_key, sort_key(r.winner))):
        w, l = by_team.get(r.winner), by_team.get(r.loser)
        if w is None or l is None:
            continue
        gap = rank_before[w.team] - rank_before[l.team]
        if gap <= gap_threshold:
            continue
        midpoint = (score_before[w.team] + score_before[l.team]) / 2.0
        dw = (midpoint - score_before[w.team]) * strength
        dl = (midpoint - score_before[l.team]) * strength
        deltas[w.team] += dw
        deltas[l.team] += dl
        events.append(
            RegressionEvent(
                winner=w.team,
                loser=l.team,
                winner_base_rank=rank_before[w.team],
                loser_base_rank=rank_before[l.team],
                gap=gap,
                midpoint=midpoint,
                winner_delta=dw,
                loser_delta=dl,
                week=r.week,
                season_type=r.season_type,
                score=f"{r.winner_points}-{r.loser_points}",
            )
        )

    for team, delta in deltas.items():
        tb = by_team[team]
        tb.regression_adj += delta
    for ev in events:
        by_team[ev.winner].regression_notes.append(ev.describe())
        by_team[ev.loser].regression_notes.append(ev.describe())

    rerank(teams)
    events.sort(key=lambda e: (-e.gap, sort_key(e.winner)))
    return events
