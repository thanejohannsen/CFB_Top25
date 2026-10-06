"""What the betting market thinks each team is worth on a neutral field.

A point spread is the sharpest public estimate of a matchup there is: real money
corrects it, and it prices things a results-only system cannot see -- who is
injured, who is suspended, who has quietly been playing above their record. The
spread is about a *pair* of teams, though, so it has to be decomposed before it
can rank anybody:

    expected home margin  =  rating[home] - rating[away] + home_field

`engine.adjust` solves that system, estimating `home_field` from the lines rather
than assuming a value. The output is in points and reads directly: a team at
+19.5 would be favoured by four over a team at +15.5 on neutral ground.

Three deliberate restrictions:

  * **Only games already played.** A line posted for next Saturday is public
    knowledge today, but a *completed season's* file holds closing lines for
    games played weeks after the point a backtest claims to stand at. Using them
    is exactly the look-ahead that made this project's earlier numbers worthless,
    so the cutoff is enforced here rather than trusted to callers.
  * **FBS against FBS only.** A line on an FBS-vs-FCS game is real, but it is the
    only line that FCS team has, so its rating would be whatever that one game
    says and it would then contaminate its opponent. Coverage of FBS-vs-FBS games
    is complete in both seasons checked, so nothing is lost.
  * **A minimum number of games.** One lined game is not a rating. Teams below
    the floor get none and the base formula renormalises around them.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import AbstractSet, Mapping, Sequence

from cfbrank.engine.adjust import Observation, Solution, solve
from cfbrank.engine.stats import rank_desc
from cfbrank.models import GameLine, Warning_
from cfbrank.normalize import sort_key

# CFBD's own default home-field value, used to seed the fit and as the fallback
# when there are too few hosted games to measure one.
DEFAULT_HOME_FIELD = 2.5


@dataclass(slots=True)
class MarketRatings:
    """Neutral-field points ratings implied by the market, plus their ranks."""

    ratings: dict[str, float] = field(default_factory=dict)
    ranks: dict[str, int] = field(default_factory=dict)
    games: dict[str, int] = field(default_factory=dict)
    home_field_points: float = DEFAULT_HOME_FIELD
    lines_used: int = 0
    components: int = 0
    converged: bool = True
    warnings: list[Warning_] = field(default_factory=list)

    def __bool__(self) -> bool:
        return bool(self.ratings)

    def rating(self, team: str) -> float | None:
        return self.ratings.get(team)

    def rank(self, team: str) -> int | None:
        return self.ranks.get(team)

    def edge(self, team: str, opponent: str) -> float | None:
        """Neutral-field points `team` is favoured by over `opponent`."""
        a, b = self.ratings.get(team), self.ratings.get(opponent)
        return None if a is None or b is None else a - b


def horizon_cutoff(cutoff: tuple[int, int, str] | None, weeks: int) -> tuple[int, int, str] | None:
    """Push a cutoff `weeks` forward inside the same season type."""
    if cutoff is None or weeks <= 0:
        return cutoff
    season_rank, week, tail = cutoff
    return (season_rank, week + int(weeks), tail)


def usable_lines(
    lines: Sequence[GameLine],
    fbs: AbstractSet[str],
    cutoff: tuple[int, int, str] | None,
    played: AbstractSet[int] | None = None,
    horizon_weeks: int = 1,
) -> list[GameLine]:
    """Lines this ranking is allowed to see: FBS vs FBS, and not from the future.

    Two kinds of line qualify, and the second is the one that makes this a
    *current* rating rather than a historical average:

      * **Games already played**, up to the cutoff. `played` is the set of game
        ids with a final score, so a postponed fixture inside the window is not
        counted as evidence.
      * **Games in the next `horizon_weeks`.** A closing line for next Saturday
        is the market's opinion *right now* -- it already contains every result so
        far, which is exactly what a power rating should contain. Without it the
        market term is a blend of stale pre-game priors: Oklahoma State's 2026 win
        over Oregon as a 24-point home underdog would never touch their rating,
        because the only lines read would be the ones that had them as an
        underdog in the first place.

    One week is the limit, and the limit is the honesty constraint. A line for a
    week N+1 game closes before that game, so it knows everything through week N
    and nothing after -- which is precisely the cutoff. A line for week N+2 closes
    after week N+1 has been played, and reading it is look-ahead.
    """
    far = horizon_cutoff(cutoff, horizon_weeks)
    out = []
    for ln in lines:
        if cutoff is not None and ln.order_key > cutoff:
            if far is None or ln.order_key > far:
                continue  # beyond the horizon: genuinely the future
        elif played is not None and (ln.game_id is None or ln.game_id not in played):
            continue
        if fbs and (ln.home_team not in fbs or ln.away_team not in fbs):
            continue
        out.append(ln)
    out.sort(key=lambda l: (l.order_key, sort_key(l.home_team), sort_key(l.away_team)))
    return out


def recency_weights(lines: Sequence[GameLine], half_life: float) -> dict[int, float]:
    """Weight per line position: 1.0 for the newest, halving every `half_life` weeks.

    Keyed by index into `lines` so two lines from the same week always agree.
    `half_life <= 0` turns it off and every line counts the same.
    """
    if half_life <= 0 or not lines:
        return {}
    latest = max(_week_index(ln) for ln in lines)
    return {i: 0.5 ** ((latest - _week_index(ln)) / half_life) for i, ln in enumerate(lines)}


def _week_index(ln: GameLine) -> float:
    """A week number that keeps increasing into the postseason."""
    return ln.week + (20 if ln.season_type != "regular" else 0)


def observations(lines: Sequence[GameLine], half_life: float = 0.0) -> list[Observation]:
    """Two observations per game: the margin each side was expected to win by.

    They are exact negations, which is the point -- the solver sees the same
    game from both ends and neither team's schedule is weighted differently for
    having hosted.
    """
    weights = recency_weights(lines, half_life)
    obs: list[Observation] = []
    for i, ln in enumerate(lines):
        margin = ln.home_margin
        site = 0 if ln.neutral_site else 1
        weight = weights.get(i, 1.0)
        obs.append(
            Observation(ln.home_team, ln.away_team, margin, site, weight)
        )
        obs.append(
            Observation(ln.away_team, ln.home_team, -margin, -site, weight)
        )
    return obs


def compute(
    lines: Sequence[GameLine],
    fbs: AbstractSet[str],
    cutoff: tuple[int, int, str] | None,
    cfg: Mapping[str, object],
    played: AbstractSet[int] | None = None,
) -> MarketRatings:
    """Solve the market's neutral-field ratings for every team with enough lines."""
    min_games = int(cfg.get("min_games", 3))  # type: ignore[arg-type]
    seed = float(cfg.get("home_field_points", DEFAULT_HOME_FIELD))  # type: ignore[arg-type]
    fit_home_field = bool(cfg.get("fit_home_field", True))
    horizon = int(cfg.get("horizon_weeks", 1))  # type: ignore[arg-type]
    half_life = float(cfg.get("recency_half_life", 0.0))  # type: ignore[arg-type]

    usable = usable_lines(lines, fbs, cutoff, played, horizon)
    if not usable:
        return MarketRatings(
            home_field_points=seed,
            warnings=[Warning_("market_unavailable", "no usable betting lines")],
        )

    sol: Solution = solve(
        observations(usable, half_life), estimate_home_edge=fit_home_field, home_edge=seed
    )

    warnings: list[Warning_] = []
    # The solver centres each component separately, so ratings only compare
    # inside one. Rank anyway -- dropping half the board in week 2 is worse --
    # but say so, because a rating from a 6-team island is not a national one.
    if len(sol.components) > 1:
        sizes = ", ".join(str(len(c)) for c in sol.components[:5])
        warnings.append(
            Warning_(
                "market_disconnected",
                f"the schedule splits into {len(sol.components)} groups (sizes {sizes}...); "
                "market ratings compare teams within a group, not across groups",
            )
        )
    if not sol.converged:
        warnings.append(
            Warning_("market_not_converged", f"ratings still moving after {sol.passes} passes")
        )

    kept = {
        t: r for t, r in sol.ratings.items() if sol.games.get(t, 0) >= max(1, min_games)
    }
    for team in sorted(set(sol.ratings) - set(kept), key=sort_key):
        warnings.append(
            Warning_(
                "market_too_few_games",
                f"only {sol.games.get(team, 0)} lined FBS game(s); needs {min_games}",
                team,
            )
        )

    return MarketRatings(
        ratings=kept,
        ranks=rank_desc(kept),
        games={t: sol.games.get(t, 0) for t in sorted(sol.ratings, key=sort_key)},
        home_field_points=sol.home_edge if fit_home_field else seed,
        lines_used=len(usable),
        components=len(sol.components),
        converged=sol.converged,
        warnings=warnings,
    )
