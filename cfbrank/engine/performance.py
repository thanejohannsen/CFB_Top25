"""The eye test, made countable: points added per play, adjusted for opponent.

Predicted Points Added asks, for every snap, how much the expected points of the
drive changed. Summing it per play gives a team's efficiency on offence and the
efficiency it allowed on defence, and the difference between them is the closest
single number to "how good did they look out there" -- independent of whether the
ball bounced their way at the end.

    net = offence PPA per play - defence PPA per play

Garbage time is already stripped upstream (`excludeGarbageTime=true` on the
fetch), because a fourth-quarter drive against the backups is exactly the kind of
football that flatters a team that was never in control.

The numbers then go through the same opponent adjustment the market ratings use,
for the same reason: +0.25 per play against the best defence in the country is
not +0.25 against the worst.

Two things deliberately absent:

  * **No recency weighting.** Tested, measured, and it made predictions worse
    every time. It is tempting because it lifts a team that started badly and has
    since looked excellent -- which is a real pattern and also the single easiest
    way to talk yourself into a ranking the results do not support. If it is ever
    revisited, it needs a backtest, not an anecdote.
  * **No FCS games.** A 0.6-per-play afternoon against an FCS defence says
    nothing, and leaving it in rewards scheduling down.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import AbstractSet, Mapping, Sequence

from cfbrank.engine.adjust import Observation, Solution, solve
from cfbrank.engine.stats import rank_desc
from cfbrank.models import TeamGamePPA, Warning_
from cfbrank.normalize import sort_key


@dataclass(slots=True)
class PerformanceRatings:
    """Opponent-adjusted net PPA per team, in points per play, plus ranks."""

    ratings: dict[str, float] = field(default_factory=dict)
    ranks: dict[str, int] = field(default_factory=dict)
    games: dict[str, int] = field(default_factory=dict)
    raw: dict[str, float] = field(default_factory=dict)
    home_edge: float = 0.0
    games_used: int = 0
    components: int = 0
    converged: bool = True
    warnings: list[Warning_] = field(default_factory=list)

    def __bool__(self) -> bool:
        return bool(self.ratings)

    def rating(self, team: str) -> float | None:
        return self.ratings.get(team)

    def rank(self, team: str) -> int | None:
        return self.ranks.get(team)


def usable_rows(
    ppa: Sequence[TeamGamePPA],
    fbs: AbstractSet[str],
    cutoff: tuple[int, int, str] | None,
    played: AbstractSet[int] | None = None,
) -> list[TeamGamePPA]:
    """FBS-vs-FBS rows for games finished on or before the cutoff."""
    out = []
    for row in ppa:
        if cutoff is not None and row.order_key > cutoff:
            continue
        if played is not None and (row.game_id is None or row.game_id not in played):
            continue
        if fbs and (row.team not in fbs or row.opponent not in fbs):
            continue
        out.append(row)
    out.sort(key=lambda r: (r.order_key, sort_key(r.team)))
    return out


def observations(rows: Sequence[TeamGamePPA]) -> list[Observation]:
    obs: list[Observation] = []
    for row in rows:
        if row.neutral_site or row.was_home is None:
            site = 0
        else:
            site = 1 if row.was_home else -1
        obs.append(
            Observation(team=row.team, opponent=row.opponent, value=row.net, site=site)
        )
    return obs


def compute(
    ppa: Sequence[TeamGamePPA],
    fbs: AbstractSet[str],
    cutoff: tuple[int, int, str] | None,
    cfg: Mapping[str, object],
    played: AbstractSet[int] | None = None,
) -> PerformanceRatings:
    """Solve opponent-adjusted net PPA for every team with enough FBS games."""
    min_games = int(cfg.get("min_games", 3))  # type: ignore[arg-type]
    fit_home_edge = bool(cfg.get("fit_home_edge", True))

    rows = usable_rows(ppa, fbs, cutoff, played)
    if not rows:
        return PerformanceRatings(
            warnings=[Warning_("performance_unavailable", "no usable per-game PPA rows")]
        )

    obs = observations(rows)
    sol: Solution = solve(obs, estimate_home_edge=fit_home_edge)

    warnings: list[Warning_] = []
    if len(sol.components) > 1:
        sizes = ", ".join(str(len(c)) for c in sol.components[:5])
        warnings.append(
            Warning_(
                "performance_disconnected",
                f"the schedule splits into {len(sol.components)} groups (sizes {sizes}...); "
                "PPA ratings compare teams within a group, not across groups",
            )
        )
    if not sol.converged:
        warnings.append(
            Warning_("performance_not_converged", f"ratings still moving after {sol.passes} passes")
        )

    kept = {t: r for t, r in sol.ratings.items() if sol.games.get(t, 0) >= max(1, min_games)}
    for team in sorted(set(sol.ratings) - set(kept), key=sort_key):
        warnings.append(
            Warning_(
                "performance_too_few_games",
                f"only {sol.games.get(team, 0)} FBS game(s) with PPA; needs {min_games}",
                team,
            )
        )

    # The unadjusted mean is kept for the site: showing both makes the
    # adjustment legible ("looked like +0.18, worth +0.24 for who they played").
    totals: dict[str, list[float]] = {}
    for row in rows:
        totals.setdefault(row.team, []).append(row.net)

    return PerformanceRatings(
        ratings=kept,
        ranks=rank_desc(kept),
        games={t: sol.games.get(t, 0) for t in sorted(sol.ratings, key=sort_key)},
        raw={t: sum(v) / len(v) for t, v in sorted(totals.items(), key=lambda kv: sort_key(kv[0]))},
        home_edge=sol.home_edge,
        games_used=len(rows),
        components=len(sol.components),
        converged=sol.converged,
        warnings=warnings,
    )
