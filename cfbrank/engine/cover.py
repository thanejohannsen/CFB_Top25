"""Did they play as well as they were supposed to?

The market says, before kickoff, how much a team should win by. The difference
between that and what actually happened is the cleanest statement of "did they
look good" the data offers:

    cover margin = actual margin - the posted line

Beating a bad team by 17 when you were favoured by 28 is a miss. Beating a good
team by 1 when you were favoured by 3 is roughly par. This is the asymmetry that
makes the measure worth having, and it falls out of the arithmetic rather than
needing a rule: the easier the game was supposed to be, the more a middling
performance costs you.

**This can only ever be a modifier.** Ranked on its own, cover margin puts
Georgia State, James Madison, New Mexico and Sam Houston at the top of the
country, because it measures *exceeding expectations*, not *being good* — a bad
team losing by 10 as a 20-point underdog covers. It belongs in stage 2, adjusting
a resume that already knows who you beat, and nowhere else.

**It is also close to noise, and the site says so.** Markets are efficient: cover
rates sit near 50% for almost everyone and past cover performance barely predicts
future cover performance. Carrying it is a judgement that a ranking should
describe how teams have looked, not only what they predict — the same kind of
choice as the weight on the resume itself.

The line used is the one posted for that game, not a margin implied by our own
solved season rating. The posted line is the market's actual pre-game view of
*that* matchup; our season rating is a smoothed summary that already contains
the result being judged.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Mapping, Sequence

from cfbrank.models import SEASON_TYPE_ORDER, Game, GameLine
from cfbrank.normalize import sort_key

# Games below this count get their cover margin pulled toward zero, so two
# flukes in September cannot define a season. See `shrunk_mean`.
SHRINKAGE_GAMES = 4.0

# How far one game may move a team's average, in points past the line.
# SHIPPED OFF (0 = no cap), and it was built and measured before being switched
# off, so do not rebuild it without reading this.
#
# The idea was sound -- Ole Miss lost at Florida by 20 against the number on 2026
# week 5, and that one game took their mean from -1.73 to -6.31. But a cap does
# not move them, or anyone, because THIS TERM IS Z-SCORED: clipping compresses
# the whole board, so every team's absolute number improves and their relative
# standing survives. Ole Miss came out 15th at every cap from 10 to none.
#
# What it does cost is real. The per-game cover margin has an sd of 14.97, so:
#
#    cap    clips     board spread of team means
#     10    48.7%     5.83
#     14    34.9%     7.39
#     21    14.4%     9.13
#   none     0.0%    10.36
#
# A cap at 14 throws away 29% of the spread of a term built to discriminate, and
# buys nothing. The one real effect is incidental: it clips Northwestern's
# outsized CREDIT (+30.5 vs Colorado, +23.75 vs Penn State) and drops them from
# 12th to 14th, which is the small-sample flattery problem from another angle --
# if that is ever worth chasing, this is the knob, but go in knowing the price.
GAME_CAP = 0.0

# Below this many lined games the venue bias is not fitted at all. It is a mean
# over the whole board, and on a handful of games it stops being a league-wide
# tendency and starts absorbing the very results it is meant to adjust: fitted on
# ONE game it equals that game's cover margin exactly, and subtracting it zeroes
# the result. The suite caught that. In practice a real week has 384-870 games.
VENUE_MIN_GAMES = 50


def venue_bias(lines: Sequence[GameLine], games: Sequence[Game]) -> float:
    """How many points the market under-prices home field by, measured.

    THE POSTED LINE ALREADY CONTAINS HOME FIELD, so it is natural to assume this
    measure is venue-neutral. It is not. Measured on completed non-neutral games
    with a line, home teams beat the number by **+1.045** in 2025 (n=870, SE
    0.511) and **+1.640** in 2026 (n=384, SE 0.762) -- the same direction in both
    seasons at about two standard errors each, so the line under-prices home
    field by roughly a point. Home teams covered 54.7% of the time in 2026
    against road teams' 44.0%.

    Left uncorrected that is a standing penalty on a road-heavy schedule which
    says nothing about the team, so each game is centred on its venue's mean.
    Measured from the season's own lines rather than assumed, for the same reason
    `market.fit_home_field` is: see CLAUDE.md on the +4.32 that turned out to be
    +2.37 once somebody actually measured it.

    Positive means home teams beat the number. Returns 0.0 when there is too
    little to fit -- see `VENUE_MIN_GAMES`.
    """
    seen: list[tuple[int, float]] = []
    line_of = {ln.game_id: ln for ln in lines if ln.game_id is not None}
    for g in games:
        if g.neutral_site or g.game_id is None:
            continue
        if not g.completed or g.home_points is None or g.away_points is None:
            continue
        ln = line_of.get(g.game_id)
        if ln is None:
            continue
        seen.append((g.game_id, float(g.home_points - g.away_points) - ln.home_margin))
    if len(seen) < VENUE_MIN_GAMES:
        return 0.0
    # Sorted by game id so the float summation order cannot depend on the order
    # the API happened to return games in.
    ordered = [v for _, v in sorted(seen)]
    return sum(ordered) / len(ordered)


@dataclass(frozen=True, slots=True)
class CoverGame:
    opponent: str
    week: int
    season_type: str
    expected: float      # points the market said this team would win by
    actual: float        # points it actually won by (negative = lost by)
    site: int = 0        # +1 hosted, -1 travelled, 0 neutral
    bias: float = 0.0    # measured points the market under-prices home field by
    cap: float = GAME_CAP

    @property
    def sort_key(self) -> tuple[int, int, tuple[str, str]]:
        """Chronological, then by name, so `best`/`worst` cannot depend on
        the order games happened to arrive in."""
        return (SEASON_TYPE_ORDER.get(self.season_type, 9), self.week, sort_key(self.opponent))

    @property
    def raw_margin(self) -> float:
        """Points better than the market expected. What actually happened."""
        return self.actual - self.expected

    @property
    def margin(self) -> float:
        """What gets scored: venue-corrected, then capped if a cap is set.

        A home team gives the bias back and a road team is credited it, so the
        measure asks "did you beat the number by more than teams in your
        situation usually do" rather than "did you beat the number".

        `cap <= 0` means no cap, which is how it ships -- see GAME_CAP.
        """
        adjusted = self.raw_margin - self.site * self.bias
        if self.cap <= 0:
            return adjusted
        return max(-self.cap, min(self.cap, adjusted))

    @property
    def clipped(self) -> bool:
        """Did the cap bite? Published so a reader can see when it did."""
        if self.cap <= 0:
            return False
        return abs(self.raw_margin - self.site * self.bias) > self.cap + 1e-9

    @property
    def covered(self) -> bool:
        return self.margin > 0


@dataclass(slots=True)
class CoverRecord:
    games: tuple[CoverGame, ...] = ()

    @property
    def played(self) -> int:
        return len(self.games)

    @property
    def covers(self) -> int:
        return sum(1 for g in self.games if g.covered)

    @property
    def raw_mean_margin(self) -> float:
        """What actually happened against the number, uncorrected.

        This is the human-legible one and it is what the site shows. Keep it
        distinct from `mean_margin`: the panel has to be able to explain its own
        adjustment, and publishing only one of the two is what made it
        irreconcilable last time.
        """
        return sum(g.raw_margin for g in self.games) / len(self.games) if self.games else 0.0

    @property
    def mean_margin(self) -> float:
        """Venue-corrected (and capped, if a cap is set). What gets scored."""
        return sum(g.margin for g in self.games) / len(self.games) if self.games else 0.0

    @property
    def shrunk_margin(self) -> float:
        """Mean cover margin pulled toward zero when there is little to go on.

        With three lined games a single 30-point overperformance would otherwise
        read as a season-defining trait rather than one loud afternoon.
        """
        if not self.games:
            return 0.0
        n = len(self.games)
        return self.mean_margin * (n / (n + SHRINKAGE_GAMES))

    @property
    def best(self) -> CoverGame | None:
        return max(self.games, key=lambda g: g.margin) if self.games else None

    @property
    def worst(self) -> CoverGame | None:
        """The game furthest short of the posted line, on the RAW scale.

        Raw rather than venue-corrected because this is the callout the site
        prints beside `raw_mean_margin`, and a reader can only check a number
        that is "actual margin minus the posted line". It shipped on `.margin`
        beside a raw mean, which put two scales in one sentence with nothing
        saying so: Texas beat UTSA by 24 against a 29.75 line, which is 5.75
        short, and the panel printed `-7` -- the same game after the +1.64 home
        correction. The owner read it as the spread, which is exactly what it
        looks like.
        """
        return min(self.games, key=lambda g: g.raw_margin) if self.games else None


def cover_margins(
    lines: Sequence[GameLine],
    games: Sequence[Game],
    cutoff: tuple[int, int, str] | None = None,
    cap: float = GAME_CAP,
    fit_venue: bool = True,
) -> dict[str, CoverRecord]:
    """Every team's performance against the posted line, through the cutoff.

    `cap` limits how far one game may move a team's average; `fit_venue` measures
    the market's home-field bias from these same games and centres each game on
    it. See `venue_bias` and `CoverGame.margin`.
    """
    line_of: dict[int, GameLine] = {
        ln.game_id: ln for ln in lines if ln.game_id is not None
    }
    # Fitted on the same window the records are built from, so the correction
    # describes this board and not a different one.
    in_window = [g for g in games if cutoff is None or g.order_key <= cutoff]
    bias = venue_bias(lines, in_window) if fit_venue else 0.0
    collected: dict[str, list[CoverGame]] = {}

    for g in in_window:
        if not g.completed or g.home_points is None or g.away_points is None:
            continue
        if g.game_id is None:
            continue
        ln = line_of.get(g.game_id)
        if ln is None:
            continue  # no line posted: no expectation to measure against

        home_margin = float(g.home_points - g.away_points)
        for team, sign in ((g.home_team, 1.0), (g.away_team, -1.0)):
            collected.setdefault(team, []).append(
                CoverGame(
                    opponent=g.away_team if sign > 0 else g.home_team,
                    week=g.week,
                    season_type=g.season_type,
                    expected=sign * ln.home_margin,
                    actual=sign * home_margin,
                    site=0 if g.neutral_site else int(sign),
                    bias=bias,
                    cap=cap,
                )
            )

    return {
        team: CoverRecord(tuple(sorted(gs, key=lambda c: c.sort_key)))
        for team, gs in sorted(collected.items(), key=lambda kv: sort_key(kv[0]))
    }
