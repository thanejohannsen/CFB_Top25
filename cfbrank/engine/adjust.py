"""Opponent adjustment: turning per-game numbers into a rating per team.

Both quality inputs -- what the betting market implies and what the
play-by-play says -- arrive as one number per team per game, and both need the
same correction: a +20 margin against the best team in the country is not the
same achievement as +20 against the worst. The classic fix is to model every
observation as a difference of two team strengths,

    value(team, opponent)  ~=  rating[team] - rating[opponent] + edge * site

and solve for the ratings. `site` is +1 at home, -1 away, 0 on neutral ground,
so `edge` is the home-field advantage *measured from the data* rather than
assumed.

The solve alternates two cheap steps until neither moves:

    1. with `edge` fixed, set each rating to the (weighted) mean of
       (own value - edge * site + opponent rating) over that team's games, then
       recentre the whole board on zero;
    2. with the ratings fixed, set `edge` to the mean residual on games that had
       a host.

This is the Simple Rating System, and it is a Gauss-Seidel sweep over a sparse
linear system: step 1 is one pass of Jacobi iteration on the normal equations.
Pure Python is fine here -- a season is ~1,700 observations over ~135 teams and
the whole thing converges in well under a hundred passes.

Two properties the rest of the engine depends on:

  * **Determinism.** Teams are visited in `sort_key` order and every mean sums a
    pre-sorted list, so the result does not depend on dict insertion order or on
    the order rows arrived from the API.
  * **Honesty about what it cannot know.** Ratings are only comparable inside a
    connected component of the schedule graph -- recentring pins one degree of
    freedom per component, not one overall. In week 1 the graph is a scatter of
    islands and a "rating" across them is meaningless, so `solve()` reports the
    components and the caller decides.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from typing import Iterable, Mapping, Sequence

from cfbrank.normalize import sort_key

# Enough passes to converge on a season of football; the tolerance normally
# stops it first. A cap matters because a pathological component (two teams who
# played only each other, in opposite venues) can oscillate.
MAX_PASSES = 200
TOLERANCE = 1e-10


@dataclass(frozen=True, slots=True)
class Observation:
    """One team's number from one game, before any opponent adjustment."""

    team: str
    opponent: str
    value: float
    site: int = 0  # +1 the team hosted, -1 it travelled, 0 neutral ground
    weight: float = 1.0  # how much this game counts; equal weighting by default

    def __post_init__(self) -> None:
        if self.site not in (-1, 0, 1):
            raise ValueError(f"site must be -1, 0 or +1, got {self.site!r}")
        if self.weight <= 0:
            raise ValueError(f"weight must be > 0, got {self.weight!r}")


@dataclass(slots=True)
class Solution:
    ratings: dict[str, float] = field(default_factory=dict)
    home_edge: float = 0.0
    games: dict[str, int] = field(default_factory=dict)
    components: list[list[str]] = field(default_factory=list)
    passes: int = 0
    converged: bool = False

    @property
    def largest_component(self) -> int:
        return max((len(c) for c in self.components), default=0)

    def residual(self, obs: Observation) -> float:
        """How far one observation sits from what the ratings predict."""
        return (
            obs.value
            - self.home_edge * obs.site
            - (self.ratings.get(obs.team, 0.0) - self.ratings.get(obs.opponent, 0.0))
        )


def components(observations: Iterable[Observation]) -> list[list[str]]:
    """Undirected connected components of "played each other", largest first.

    Union-find with the parent map walked in sorted order, so the component
    lists -- and therefore any warning built from them -- are stable.
    """
    parent: dict[str, str] = {}

    def find(x: str) -> str:
        parent.setdefault(x, x)
        root = x
        while parent[root] != root:
            root = parent[root]
        while parent[x] != root:  # path compression
            parent[x], x = root, parent[x]
        return root

    for obs in observations:
        a, b = find(obs.team), find(obs.opponent)
        if a != b:
            # Union by name so the representative does not depend on input order.
            lo, hi = sorted((a, b), key=sort_key)
            parent[hi] = lo

    groups: dict[str, list[str]] = defaultdict(list)
    for name in sorted(parent, key=sort_key):
        groups[find(name)].append(name)
    return sorted(groups.values(), key=lambda g: (-len(g), sort_key(g[0])))


def solve(
    observations: Sequence[Observation],
    *,
    estimate_home_edge: bool = True,
    home_edge: float = 0.0,
    max_passes: int = MAX_PASSES,
    tolerance: float = TOLERANCE,
) -> Solution:
    """Solve for one rating per team, in the units of `value`.

    `home_edge` seeds the alternating fit and is the fixed value used when
    `estimate_home_edge` is false. Ratings are centred on zero within each
    connected component of the schedule.
    """
    if not observations:
        return Solution(home_edge=home_edge, converged=True)

    by_team: dict[str, list[Observation]] = defaultdict(list)
    for obs in observations:
        by_team[obs.team].append(obs)

    teams = sorted(by_team, key=sort_key)
    # An opponent with no observations of its own (an FCS team in a one-sided
    # schedule) still needs a rating, or the mean below silently treats it as
    # exactly average.
    for obs in observations:
        if obs.opponent not in by_team:
            by_team[obs.opponent] = []
    all_teams = sorted(by_team, key=sort_key)

    ratings = {t: 0.0 for t in all_teams}
    edge = float(home_edge)
    comps = components(observations)
    member_of = {t: i for i, comp in enumerate(comps) for t in comp}

    passes = 0
    converged = False
    for passes in range(1, max_passes + 1):
        updated = dict(ratings)
        delta = 0.0
        for team in all_teams:
            games = by_team[team]
            if not games:
                continue
            # Gauss-Seidel, NOT Jacobi: each team reads the ratings updated
            # earlier in this same sweep. Computing the whole pass against the
            # previous one instead makes two teams who have only played each
            # other oscillate forever -- A pulls to +21, B answers -21, A
            # answers 0 -- so the answer came out as whatever the parity of the
            # pass limit happened to land on rather than the +10.5/-10.5 the
            # single game actually implies.
            total = sum(
                obs.weight * (obs.value - edge * obs.site + updated.get(obs.opponent, 0.0))
                for obs in games
            )
            new = total / sum(obs.weight for obs in games)
            delta = max(delta, abs(new - updated[team]))
            updated[team] = new

        # Recentre per component. One component's mean says nothing about
        # another's, so a single global shift would quietly make unrelated teams
        # look comparable.
        for comp in comps:
            rated = [updated[t] for t in comp if t in updated]
            if not rated:
                continue
            shift = sum(rated) / len(rated)
            for t in comp:
                updated[t] -= shift

        if estimate_home_edge:
            # Only games inside one component: a residual that spans two
            # separately-centred islands is an artefact of the centring.
            hosted = [
                (
                    obs.weight,
                    obs.site
                    * (obs.value - (updated.get(obs.team, 0.0) - updated.get(obs.opponent, 0.0))),
                )
                for obs in observations
                if obs.site and member_of.get(obs.team) == member_of.get(obs.opponent)
            ]
            if hosted:
                new_edge = sum(w * r for w, r in hosted) / sum(w for w, _r in hosted)
                delta = max(delta, abs(new_edge - edge))
                edge = new_edge

        ratings = updated
        if delta < tolerance:
            converged = True
            break

    return Solution(
        ratings={t: ratings[t] for t in all_teams},
        home_edge=edge,
        games={t: len(by_team[t]) for t in all_teams},
        components=comps,
        passes=passes,
        converged=converged,
    )


def restrict(sol: Solution, teams: Iterable[str], min_games: int) -> dict[str, float]:
    """Ratings for the teams that have enough games to deserve one."""
    wanted = set(teams)
    return {
        t: r
        for t, r in sol.ratings.items()
        if t in wanted and sol.games.get(t, 0) >= max(1, min_games)
    }


def spread(ratings: Mapping[str, float]) -> float:
    """Range of a rating set -- used only for reporting."""
    if not ratings:
        return 0.0
    return max(ratings.values()) - min(ratings.values())
