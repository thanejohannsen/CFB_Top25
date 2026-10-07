"""Turning games into head-to-head results, and results into a graph.

Filtering rules that matter:
  * a game needs BOTH `completed` and two non-null scores (during live games
    CFBD serves completed=false with points present, and during data lag the
    reverse);
  * a tie produces no result in either direction;
  * any chronological comparison leads with season type, because postseason
    weeks restart at 1 -- without that, a national championship sorts before
    a regular-season week 2 game.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from typing import AbstractSet, Iterable, Mapping, Sequence

from cfbrank.engine.graph import Digraph
from cfbrank.models import CalendarWeek, Game, GameResult, SEASON_TYPE_ORDER
from cfbrank.normalize import sort_key

Cutoff = tuple[int, int, str]


@dataclass(frozen=True, slots=True)
class SkippedGame:
    home: str
    away: str
    week: int
    season_type: str
    reason: str


@dataclass(frozen=True, slots=True)
class SeriesNote:
    teams: tuple[str, str]
    meetings: int
    policy: str
    detail: str
    used: GameResult | None = None


# A week becomes "the current week" only once this share of its games are final.
# ONE means all of them: the owner wants a ranking a week, after that week has
# actually been played, and anything less publishes a week that is still running.
# Measured before shipping it: every week of the finished 2025 season reached
# 100% -- all sixteen, including week 15 (9 games) and week 16 (1 game) -- so
# this is not a bar real data fails to clear.
WEEK_SETTLED_FRACTION = 1.0


def _settled_weeks(
    games: Sequence[Game],
    played_keys: Mapping[tuple[str, int], int],
    season_type: str,
    min_fraction: float,
    material_teams: AbstractSet[str],
) -> list[tuple[str, int]]:
    """Which (season_type, week) pairs count as finished.

    A week qualifies when every scheduled game is final, or -- the straggler
    escape -- when each one that is not final is BOTH inconsequential and
    overtaken:

      * inconsequential: neither team is in `material_teams`, the teams the
        board actually ranks, so nobody is waiting on the result;
      * overtaken: some game scheduled LATER in the same week is already final.

    The second condition is what stops a week settling at Saturday lunchtime
    just because its late kickoffs are late. It is also deliberately clock-free
    -- "the week has moved past this game" is a statement about the data, not
    about now -- because the engine reads no clock.

    Both are required, so with `material_teams` empty this degrades to strict
    "all games final", which is the right default for a season's first run.
    """
    scheduled: dict[tuple[str, int], list[Game]] = defaultdict(list)
    for g in games:
        if season_type in ("regular", "postseason") and g.season_type != season_type:
            continue
        scheduled[(g.season_type, g.week)].append(g)

    settled: list[tuple[str, int]] = []
    for key, week_games in scheduled.items():
        if not week_games:
            continue
        done = played_keys.get(key, 0)
        if done / len(week_games) >= min_fraction:
            settled.append(key)
            continue
        if not material_teams or not done:
            continue

        def is_final(g: Game) -> bool:
            return g.completed and g.home_points is not None and g.away_points is not None

        latest_final = max(
            (g.start_date for g in week_games if is_final(g) and g.start_date), default=None
        )
        if latest_final is None:
            continue
        stragglers = [g for g in week_games if not is_final(g)]
        if all(
            g.home_team not in material_teams
            and g.away_team not in material_teams
            and g.start_date is not None
            and g.start_date < latest_final
            for g in stragglers
        ):
            settled.append(key)

    return settled


def resolve_week(
    games: Sequence[Game],
    calendar: Sequence[CalendarWeek],
    want: int | str,
    season_type: str,
    min_fraction: float = WEEK_SETTLED_FRACTION,
    material_teams: AbstractSet[str] = frozenset(),
) -> tuple[int, str, Cutoff]:
    """Pick the week to rank through, and the chronological cutoff for it.

    "auto" means the latest week that has actually been PLAYED -- not merely
    started, and not merely half done. Dates are not used to pick the week:
    calendar windows overlap, and a week can close before its last game is
    logged.

    The "played, not started" part is load-bearing. It used to be the latest week
    with *any* completed game, and on 2026-10-07 a single Wednesday fixture
    (Southern Miss at Troy) promoted the whole board to week 6 while 57 of that
    week's 58 games were still unplayed. That is not just a cosmetic label: the
    cutoff drives the market term, and advancing it dropped 57 unplayed week-6
    betting lines and swapped in week-7 ones, churning about 50 of the 329 inputs
    on the strength of one game nobody had asked about.

    A half-finished week was the first fix and was not enough, because half a
    week is still a week in progress. The bar is now every game -- see
    `_settled_weeks` for the one escape, and `WEEK_SETTLED_FRACTION` for the
    evidence that real seasons clear it.

    A week that never completes does not strand the board: this returns the
    LATEST settled week, so an abandoned fixture costs that week its snapshot
    while every later week still publishes on time. If nothing has settled yet --
    the opening Thursday of a season -- it falls back to the latest week with any
    result, which is the best available answer at that point.
    """
    played = [g for g in games if g.completed and g.home_points is not None and g.away_points is not None]
    if season_type in ("regular", "postseason"):
        played = [g for g in played if g.season_type == season_type]

    if want == "auto":
        if not played:
            weeks = [c for c in calendar if c.season_type == "regular"]
            week, st = (weeks[0].week if weeks else 1), "regular"
        else:
            finished: dict[tuple[str, int], int] = defaultdict(int)
            for g in played:
                finished[(g.season_type, g.week)] += 1

            settled = _settled_weeks(games, finished, season_type, min_fraction, material_teams)
            if settled:
                st, week = max(settled, key=lambda k: (SEASON_TYPE_ORDER.get(k[0], 9), k[1]))
            else:
                latest = max(played, key=lambda g: g.order_key)
                week, st = latest.week, latest.season_type
    else:
        week = int(want)
        st = "regular" if season_type == "both" else season_type
        candidates = [g for g in played if g.season_type == st and g.week <= week]
        if candidates:
            week = max(candidates, key=lambda g: g.order_key).week

    cutoff = (SEASON_TYPE_ORDER.get(st, 9), week, "￿")
    return week, st, cutoff


def to_results(
    games: Iterable[Game], cutoff: Cutoff | None = None
) -> tuple[list[GameResult], list[SkippedGame]]:
    """Every usable completed result, oriented winner -> loser."""
    results: list[GameResult] = []
    skipped: list[SkippedGame] = []

    for g in games:
        if cutoff is not None and g.order_key > cutoff:
            continue
        has_points = g.home_points is not None and g.away_points is not None
        if not g.completed:
            if has_points:
                skipped.append(SkippedGame(g.home_team, g.away_team, g.week, g.season_type, "in_progress"))
            continue
        if not has_points:
            skipped.append(SkippedGame(g.home_team, g.away_team, g.week, g.season_type, "no_score"))
            continue
        if g.home_points == g.away_points:
            skipped.append(SkippedGame(g.home_team, g.away_team, g.week, g.season_type, "tie_no_result"))
            continue

        home_won = g.home_points > g.away_points
        results.append(
            GameResult(
                winner=g.home_team if home_won else g.away_team,
                loser=g.away_team if home_won else g.home_team,
                winner_points=max(g.home_points, g.away_points),
                loser_points=min(g.home_points, g.away_points),
                week=g.week,
                season_type=g.season_type,
                neutral_site=g.neutral_site,
                winner_was_home=home_won,
                game_id=g.game_id,
                start_date=g.start_date,
            )
        )

    results.sort(key=lambda r: (r.order_key, sort_key(r.winner), sort_key(r.loser)))
    skipped.sort(key=lambda s: (s.season_type, s.week, sort_key(s.home), sort_key(s.away)))
    return results, skipped


def restrict(results: Iterable[GameResult], teams: AbstractSet[str]) -> list[GameResult]:
    return [r for r in results if r.winner in teams and r.loser in teams]


def collapse_series(
    results: Sequence[GameResult], policy: str = "most_recent"
) -> tuple[list[GameResult], list[SeriesNote]]:
    """Reduce each pair to a single result.

    A conference championship rematch that flips the regular-season outcome is
    the main source of December contradictions, so split series are always
    reported even when a policy silently picks a winner.
    """
    by_pair: dict[frozenset[str], list[GameResult]] = defaultdict(list)
    for r in results:
        by_pair[frozenset((r.winner, r.loser))].append(r)

    kept: list[GameResult] = []
    notes: list[SeriesNote] = []

    for pair in sorted(by_pair, key=lambda p: tuple(sorted(p, key=sort_key))):
        meetings = sorted(by_pair[pair], key=lambda r: r.order_key)
        pair_names = tuple(sorted(pair, key=sort_key))  # type: ignore[assignment]
        winners = {r.winner for r in meetings}

        if len(winners) == 1:
            chosen = meetings[-1]
            if len(meetings) > 1:
                notes.append(
                    SeriesNote(
                        teams=pair_names,
                        meetings=len(meetings),
                        policy=policy,
                        detail=f"{chosen.winner} swept the series {len(meetings)}-0",
                        used=chosen,
                    )
                )
            kept.append(chosen)
            continue

        scores = "; ".join(
            f"{r.winner} {r.winner_points}-{r.loser_points} (wk {r.week} {r.season_type})" for r in meetings
        )
        if policy == "drop":
            notes.append(
                SeriesNote(pair_names, len(meetings), policy, f"split series ignored -- {scores}", None)
            )
            continue
        if policy == "aggregate_margin":
            totals: dict[str, int] = defaultdict(int)
            for r in meetings:
                totals[r.winner] += r.margin
                totals[r.loser] -= r.margin
            best = max(sorted(totals, key=sort_key), key=lambda t: totals[t])
            if len([t for t in totals if totals[t] == totals[best]]) > 1:
                chosen = meetings[-1]
                detail = f"split series tied on aggregate margin, using the later meeting -- {scores}"
            else:
                chosen = next(r for r in reversed(meetings) if r.winner == best)
                detail = f"split series decided on aggregate margin ({best} +{totals[best]}) -- {scores}"
        else:  # most_recent
            chosen = meetings[-1]
            detail = f"split series decided by the later meeting -- {scores}"

        notes.append(SeriesNote(pair_names, len(meetings), policy, detail, chosen))
        kept.append(chosen)

    kept.sort(key=lambda r: (r.order_key, sort_key(r.winner), sort_key(r.loser)))
    return kept, notes


def build_graph(results: Iterable[GameResult], teams: Iterable[str]) -> Digraph:
    g = Digraph(sorted(set(teams), key=sort_key))
    nodes = set(g.nodes)
    for r in results:
        if r.winner in nodes and r.loser in nodes:
            g.add_edge(r.winner, r.loser)
    return g


def opponents(results: Iterable[GameResult]) -> dict[str, dict[str, GameResult]]:
    """team -> opponent -> the result of their (most recent) meeting."""
    out: dict[str, dict[str, GameResult]] = defaultdict(dict)
    for r in sorted(results, key=lambda r: r.order_key):
        out[r.winner][r.loser] = r
        out[r.loser][r.winner] = r
    return out


def wins_losses(
    team: str, results: Iterable[GameResult], within: AbstractSet[str] | None = None
) -> tuple[list[GameResult], list[GameResult]]:
    wins, losses = [], []
    for r in results:
        if r.winner == team and (within is None or r.loser in within):
            wins.append(r)
        elif r.loser == team and (within is None or r.winner in within):
            losses.append(r)
    key = lambda r: (r.order_key,)
    return sorted(wins, key=key), sorted(losses, key=key)
