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


@dataclass(frozen=True, slots=True)
class CoverGame:
    opponent: str
    week: int
    season_type: str
    expected: float      # points the market said this team would win by
    actual: float        # points it actually won by (negative = lost by)

    @property
    def sort_key(self) -> tuple[int, int, tuple[str, str]]:
        """Chronological, then by name, so `best`/`worst` cannot depend on
        the order games happened to arrive in."""
        return (SEASON_TYPE_ORDER.get(self.season_type, 9), self.week, sort_key(self.opponent))

    @property
    def margin(self) -> float:
        """Points better than the market expected. Positive = covered."""
        return self.actual - self.expected

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
    def mean_margin(self) -> float:
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
        return min(self.games, key=lambda g: g.margin) if self.games else None


def cover_margins(
    lines: Sequence[GameLine],
    games: Sequence[Game],
    cutoff: tuple[int, int, str] | None = None,
) -> dict[str, CoverRecord]:
    """Every team's performance against the posted line, through the cutoff."""
    line_of: dict[int, GameLine] = {
        ln.game_id: ln for ln in lines if ln.game_id is not None
    }
    collected: dict[str, list[CoverGame]] = {}

    for g in games:
        if cutoff is not None and g.order_key > cutoff:
            continue
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
                )
            )

    return {
        team: CoverRecord(tuple(sorted(gs, key=lambda c: c.sort_key)))
        for team, gs in sorted(collected.items(), key=lambda kv: sort_key(kv[0]))
    }
