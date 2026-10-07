"""Immutable value types. Raw API parsing happens only in sources/loader.py."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Mapping, Sequence

# Season types, in chronological order. Postseason weeks restart at 1, so any
# chronological comparison must lead with this rank rather than the week number.
SEASON_TYPE_ORDER = {"regular": 0, "postseason": 1}


def order_key(season_type: str, week: int, start_date: str | None) -> tuple[int, int, str]:
    """A total chronological key across season types."""
    return (SEASON_TYPE_ORDER.get(season_type, 9), int(week), start_date or "")


# Postseason weeks restart at 1, so a bowl would otherwise look older than a
# week 15 game. Offsetting past the longest regular season keeps "weeks ago"
# arithmetic honest across the boundary.
POSTSEASON_WEEK_OFFSET = 20


def week_index(season_type: str, week: int) -> float:
    """A week number that keeps increasing into the postseason."""
    return float(week) + (0.0 if season_type == "regular" else POSTSEASON_WEEK_OFFSET)


@dataclass(frozen=True, slots=True)
class TeamRating:
    """One row of /ratings/fpi. All resumeRanks are national ranks, 1 = best."""

    team: str
    conference: str | None = None
    fpi: float | None = None
    eff_overall: float | None = None
    eff_offense: float | None = None
    eff_defense: float | None = None
    eff_special: float | None = None
    sor_rank: int | None = None
    fpi_resume_rank: int | None = None
    awp_rank: int | None = None
    sos_rank: int | None = None
    rsos_rank: int | None = None
    game_control_rank: int | None = None


@dataclass(frozen=True, slots=True)
class Game:
    """One row of /games."""

    game_id: int | None
    year: int
    week: int
    season_type: str
    start_date: str | None
    completed: bool
    neutral_site: bool
    conference_game: bool
    home_team: str
    home_points: int | None
    home_conference: str | None
    home_classification: str | None
    away_team: str
    away_points: int | None
    away_conference: str | None
    away_classification: str | None
    venue: str | None = None

    @property
    def order_key(self) -> tuple[int, int, str]:
        return order_key(self.season_type, self.week, self.start_date)


@dataclass(frozen=True, slots=True)
class GameResult:
    """A completed, non-tied game, oriented winner -> loser."""

    winner: str
    loser: str
    winner_points: int
    loser_points: int
    week: int
    season_type: str
    neutral_site: bool
    winner_was_home: bool
    game_id: int | None = None
    start_date: str | None = None

    @property
    def order_key(self) -> tuple[int, int, str]:
        return order_key(self.season_type, self.week, self.start_date)

    @property
    def margin(self) -> int:
        return self.winner_points - self.loser_points

    @property
    def site(self) -> str:
        if self.neutral_site:
            return "neutral"
        return "home" if self.winner_was_home else "away"


@dataclass(frozen=True, slots=True)
class GameLine:
    """One game's consensus betting line.

    `spread` keeps CFBD's convention: it is quoted from the HOME team's side and
    negative means the home team is favoured. `home_margin` flips it into the
    expected home points margin, which is what every caller actually wants.
    """

    game_id: int | None
    week: int
    season_type: str
    start_date: str | None
    home_team: str
    away_team: str
    home_classification: str | None
    away_classification: str | None
    spread: float
    books: int
    neutral_site: bool = False

    @property
    def order_key(self) -> tuple[int, int, str]:
        return order_key(self.season_type, self.week, self.start_date)

    @property
    def home_margin(self) -> float:
        """Points the market expects the home team to win by (negative = dog)."""
        return -self.spread


@dataclass(frozen=True, slots=True)
class TeamGamePPA:
    """One team's per-play efficiency in one game, garbage time already excluded.

    `net` is the figure the ranking uses: points added per play on offence less
    points allowed per play on defence.
    """

    game_id: int | None
    week: int
    season_type: str
    start_date: str | None
    team: str
    opponent: str
    offense: float
    defense: float
    neutral_site: bool = False
    was_home: bool | None = None

    @property
    def order_key(self) -> tuple[int, int, str]:
        return order_key(self.season_type, self.week, self.start_date)

    @property
    def net(self) -> float:
        return self.offense - self.defense


@dataclass(frozen=True, slots=True)
class Record:
    team: str
    wins: int = 0
    losses: int = 0
    ties: int = 0
    conference_wins: int = 0
    conference_losses: int = 0

    @property
    def overall(self) -> str:
        base = f"{self.wins}-{self.losses}"
        return f"{base}-{self.ties}" if self.ties else base

    @property
    def conference(self) -> str:
        return f"{self.conference_wins}-{self.conference_losses}"


@dataclass(frozen=True, slots=True)
class CalendarWeek:
    week: int
    season_type: str
    start_date: str
    end_date: str


@dataclass(frozen=True, slots=True)
class EndpointProvenance:
    path: str
    params: Mapping[str, str]
    fetched_at: str
    cache: str  # "hit" | "miss" | "stale" | "fixture"
    sha256: str
    records: int


@dataclass(frozen=True, slots=True)
class Warning_:
    code: str
    detail: str
    team: str | None = None


@dataclass(frozen=True, slots=True)
class Dataset:
    year: int
    ratings: tuple[TeamRating, ...] = ()
    games: tuple[Game, ...] = ()
    records: tuple[Record, ...] = ()
    calendar: tuple[CalendarWeek, ...] = ()
    lines: tuple[GameLine, ...] = ()
    ppa: tuple[TeamGamePPA, ...] = ()
    provenance: tuple[EndpointProvenance, ...] = ()
    warnings: tuple[Warning_, ...] = ()
    synthetic: bool = False
    # Teams the board actually ranks, read from the last published snapshot.
    # `h2h.resolve_week` uses it to tell a straggler worth waiting for from one
    # that is not; empty means wait for every game. It is input data rather than
    # something the engine looks up, so `rank()` stays pure.
    material_teams: frozenset[str] = frozenset()

    def records_by_team(self) -> dict[str, Record]:
        return {r.team: r for r in self.records}
