"""How hard was this record to earn?

The question a resume metric answers is not "how many did you win" but "how
unlikely is this record". Going 5-0 against nobody is not the same achievement
as going 4-0 having beaten the #1 team in the country, and a 3-1 that includes a
win over a top-ten side can be harder to assemble than most unbeaten runs.

So: take a fixed **reference team**, walk it through the schedule this team
actually played, and ask how often it would come out with at least this many
wins. A low probability means a record few teams could have matched.

    P(reference team wins >= this many of these games)      lower = better

The reference is the 25th-best team on the board, so the scale reads as "could a
top-25 team have done this?". Texas opening 4-0 with Ohio State and Tennessee on
the card comes out at 2.6% — about one time in forty.

Why this replaces ESPN's `resumeRanks.strengthOfRecord`, which it tracks closely
at the top: **ESPN serves one undated snapshot.** `/ratings/fpi` has no week
parameter, so on a finished season that number describes the finished season, and
any backtest that reads it is reading the answer key. Betting lines are stamped
per game. Computing the resume ourselves is what lets the whole ranking finally
be scored honestly — and it can be shown game by game on the site, which is the
real answer to anyone who does not believe the number.

## The win-probability curve

Margins land roughly normally around the spread with a standard deviation of
about **13.5 points**, so that is the curve: `P(win) = Phi(points / 13.5)`.

This is worth stating because getting it wrong is easy and quiet. An earlier
draft used a *logistic* at the same scale, which is far too flat — it priced a
30-point favourite at 0.90 when the real figure is about 0.99, and a 14-point
favourite at 0.74 against a real 0.85. Every resume came out looking harder than
it was.

    favoured by      logistic(13.5)    normal(13.5)    reality
             7            0.627           0.698        ~0.70
            14            0.738           0.850        ~0.85
            30            0.902           0.987        ~0.99
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import AbstractSet, Mapping, Sequence

from cfbrank.engine.stats import rank_desc
from cfbrank.models import SEASON_TYPE_ORDER, Game, Warning_
from cfbrank.normalize import sort_key

# Standard deviation of actual margin around the closing spread, in points.
MARGIN_SIGMA = 13.5
# The reference team's position on the board. 25th makes the published number
# read as "could a top-25 team have done this?".
REFERENCE_PLACE = 25
# How much worse than the worst rated FBS team an unrated opponent is assumed to
# be. Non-FBS opponents have no market rating of their own.
UNRATED_GAP = 14.0


def win_probability(points: float, sigma: float = MARGIN_SIGMA) -> float:
    """Chance of winning when favoured by `points` (negative = underdog)."""
    if sigma <= 0:
        return 1.0 if points > 0 else (0.0 if points < 0 else 0.5)
    return 0.5 * (1.0 + math.erf(points / (sigma * math.sqrt(2.0))))


def at_least(probabilities: Sequence[float], wins: int) -> float:
    """P(at least `wins` successes) over independent games of differing odds.

    The Poisson-binomial distribution, built by a DP over games: `dp[k]` is the
    chance of exactly k wins so far, and each game shifts mass up or down.
    O(n^2) on a dozen games is nothing.
    """
    if wins <= 0:
        return 1.0
    if wins > len(probabilities):
        return 0.0
    dp = [1.0]
    for p in probabilities:
        nxt = [0.0] * (len(dp) + 1)
        for k, mass in enumerate(dp):
            nxt[k] += mass * (1.0 - p)
            nxt[k + 1] += mass * p
        dp = nxt
    return sum(dp[wins:])


@dataclass(frozen=True, slots=True)
class ResumeGame:
    """One game as the resume metric sees it."""

    opponent: str
    opponent_rating: float
    site: int           # +1 hosted, -1 travelled, 0 neutral
    won: bool
    win_probability: float
    week: int = 0
    season_type: str = "regular"
    rated_opponent: bool = True

    @property
    def sort_key(self) -> tuple[int, int, tuple[str, str]]:
        """Chronological, then by name. The DP below sums floats in this order,
        so without a total order the output is not byte-stable."""
        return (SEASON_TYPE_ORDER.get(self.season_type, 9), self.week, sort_key(self.opponent))


@dataclass(slots=True)
class ResumeRatings:
    probability: dict[str, float] = field(default_factory=dict)
    ranks: dict[str, int] = field(default_factory=dict)
    games: dict[str, tuple[ResumeGame, ...]] = field(default_factory=dict)
    reference_rating: float = 0.0
    warnings: list[Warning_] = field(default_factory=list)

    def __bool__(self) -> bool:
        return bool(self.probability)

    def rank(self, team: str) -> int | None:
        return self.ranks.get(team)

    def expected_wins(self, team: str) -> float | None:
        gs = self.games.get(team)
        return sum(g.win_probability for g in gs) if gs else None

    def actual_wins(self, team: str) -> int | None:
        gs = self.games.get(team)
        return sum(1 for g in gs if g.won) if gs else None


def reference_rating(ratings: Mapping[str, float], place: int = REFERENCE_PLACE) -> float:
    """The rating of the `place`-th best team, or the worst if there are fewer."""
    ordered = sorted(ratings.values(), reverse=True)
    if not ordered:
        return 0.0
    return ordered[min(place, len(ordered)) - 1]


def compute(
    games: Sequence[Game],
    market_ratings: Mapping[str, float],
    home_field_points: float,
    fbs: AbstractSet[str],
    cutoff: tuple[int, int, str] | None,
    cfg: Mapping[str, object],
) -> ResumeRatings:
    """Score every team's resume from the games it has actually played."""
    min_games = int(cfg.get("min_games", 3))  # type: ignore[arg-type]
    sigma = float(cfg.get("margin_sigma", MARGIN_SIGMA))  # type: ignore[arg-type]
    place = int(cfg.get("reference_place", REFERENCE_PLACE))  # type: ignore[arg-type]

    if not market_ratings:
        return ResumeRatings(
            warnings=[Warning_("resume_unavailable", "no market ratings to measure a schedule with")]
        )

    ref = reference_rating(market_ratings, place)
    # A non-FBS opponent has no market rating; it is worse than the worst team
    # that has one, and saying so beats silently treating it as average.
    unrated = min(market_ratings.values()) - UNRATED_GAP

    played: dict[str, list[ResumeGame]] = {}
    for g in games:
        if cutoff is not None and g.order_key > cutoff:
            continue
        if not g.completed or g.home_points is None or g.away_points is None:
            continue
        if g.home_points == g.away_points:
            continue  # a tie is not a result in either direction
        for team, opponent, site, points, opp_points in (
            (g.home_team, g.away_team, 0 if g.neutral_site else 1, g.home_points, g.away_points),
            (g.away_team, g.home_team, 0 if g.neutral_site else -1, g.away_points, g.home_points),
        ):
            if fbs and team not in fbs:
                continue
            rated = opponent in market_ratings
            opp_rating = market_ratings.get(opponent, unrated)
            played.setdefault(team, []).append(
                ResumeGame(
                    opponent=opponent,
                    opponent_rating=opp_rating,
                    site=site,
                    won=points > opp_points,
                    # What the REFERENCE team's chances would have been, not
                    # this team's. Using the team's own rating would measure it
                    # against itself, which is always about even by definition.
                    win_probability=win_probability(
                        ref - opp_rating + home_field_points * site, sigma
                    ),
                    week=g.week,
                    season_type=g.season_type,
                    rated_opponent=rated,
                )
            )

    warnings: list[Warning_] = []
    probability: dict[str, float] = {}
    schedules: dict[str, tuple[ResumeGame, ...]] = {}
    for team in sorted(played, key=sort_key):
        # Sorting is load-bearing, not tidiness: `at_least` accumulates floats
        # game by game, so a different order changes the last bits of the
        # probability and can flip a rank.
        gs = sorted(played[team], key=lambda g: g.sort_key)
        schedules[team] = tuple(gs)
        if len(gs) < max(1, min_games):
            warnings.append(
                Warning_(
                    "resume_too_few_games",
                    f"only {len(gs)} completed game(s); needs {min_games}",
                    team,
                )
            )
            continue
        probability[team] = at_least(
            [g.win_probability for g in gs], sum(1 for g in gs if g.won)
        )

    return ResumeRatings(
        probability=probability,
        # Lower probability is a better resume, so rank ascending. rank_desc
        # ranks high-is-best, so the sign is flipped going in.
        ranks=rank_desc({t: -p for t, p in probability.items()}),
        games=schedules,
        reference_rating=ref,
        warnings=warnings,
    )
