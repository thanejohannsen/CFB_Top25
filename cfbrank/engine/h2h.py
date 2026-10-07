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
# Half is deliberately forgiving: it clears on Saturday evening rather than
# waiting for a Monday make-up game, while ignoring a lone midweek fixture.
WEEK_SETTLED_FRACTION = 0.5


def resolve_week(
    games: Sequence[Game],
    calendar: Sequence[CalendarWeek],
    want: int | str,
    season_type: str,
    min_fraction: float = WEEK_SETTLED_FRACTION,
) -> tuple[int, str, Cutoff]:
    """Pick the week to rank through, and the chronological cutoff for it.

    "auto" means the latest week that has actually been PLAYED -- not merely
    started. Dates are not used: calendar windows overlap, and a week can close
    before its last game is logged.

    The "played, not started" part is load-bearing. It used to be the latest week
    with *any* completed game, and on 2026-10-07 a single Wednesday fixture
    (Southern Miss at Troy) promoted the whole board to week 6 while 57 of that
    week's 58 games were still unplayed. That is not just a cosmetic label: the
    cutoff drives the market term, and advancing it dropped 57 unplayed week-6
    betting lines and swapped in week-7 ones, churning about 50 of the 329 inputs
    on the strength of one game nobody had asked about.

    So a week only becomes current once `min_fraction` of its scheduled games are
    final. If no week clears that bar yet -- the opening Thursday of a season --
    it falls back to the latest week with any result, which is the old behaviour
    and the best available answer at that point.
    """
    played = [g for g in games if g.completed and g.home_points is not None and g.away_points is not None]
    if season_type in ("regular", "postseason"):
        played = [g for g in played if g.season_type == season_type]

    if want == "auto":
        if not played:
            weeks = [c for c in calendar if c.season_type == "regular"]
            week, st = (weeks[0].week if weeks else 1), "regular"
        else:
            scheduled: dict[tuple[str, int], int] = defaultdict(int)
            finished: dict[tuple[str, int], int] = defaultdict(int)
            for g in games:
                if season_type in ("regular", "postseason") and g.season_type != season_type:
                    continue
                scheduled[(g.season_type, g.week)] += 1
            for g in played:
                finished[(g.season_type, g.week)] += 1

            settled = [
                key
                for key, total in scheduled.items()
                if total and finished[key] / total >= min_fraction
            ]
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
