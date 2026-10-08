"""Shared builders for the test suite."""

from __future__ import annotations

from typing import Iterable, Sequence

from cfbrank.engine.evidence import EdgeFact
from cfbrank.engine.grounds import Grounds
from cfbrank.engine.graph import Digraph
from cfbrank.models import Game, GameLine, GameResult, Record, TeamGamePPA, TeamRating

FIXTURES = "tests/fixtures/cfbd"


def graph(nodes: Iterable[str], edges: Iterable[tuple[str, str]]) -> Digraph:
    g = Digraph(list(nodes))
    for s, d in edges:
        g.add_edge(s, d)
    return g


def result(
    winner: str,
    loser: str,
    wp: int = 24,
    lp: int = 17,
    week: int = 5,
    season_type: str = "regular",
    site: str = "home",
) -> GameResult:
    """`site` is the WINNER's venue, matching GameResult's own convention."""
    return GameResult(
        winner=winner,
        loser=loser,
        winner_points=wp,
        loser_points=lp,
        week=week,
        season_type=season_type,
        neutral_site=(site == "neutral"),
        winner_was_home=(site == "home"),
        start_date=f"2025-09-{week:02d}T00:00:00.000Z",
    )


def game(
    home: str,
    away: str,
    hp: int | None,
    ap: int | None,
    week: int = 5,
    season_type: str = "regular",
    completed: bool = True,
    neutral: bool = False,
    home_cls: str = "fbs",
    away_cls: str = "fbs",
    start_date: str | None = None,
) -> Game:
    return Game(
        game_id=None,
        year=2025,
        week=week,
        season_type=season_type,
        # Derived from the week by default, so games in a week sort together.
        # Pass it explicitly to order games WITHIN a week, which the
        # settled-week rule needs (a straggler has to be shown as overtaken).
        start_date=start_date or f"2025-09-{week:02d}T00:00:00.000Z",
        completed=completed,
        neutral_site=neutral,
        conference_game=False,
        home_team=home,
        home_points=hp,
        home_conference="X",
        home_classification=home_cls,
        away_team=away,
        away_points=ap,
        away_conference="Y",
        away_classification=away_cls,
    )


def rating(team: str, sor: int, sos: int, fpi: float, gc: int | None = None) -> TeamRating:
    return TeamRating(
        team=team,
        conference="X",
        fpi=fpi,
        sor_rank=sor,
        sos_rank=sos,
        fpi_resume_rank=None,
        game_control_rank=gc,
    )


def record(team: str, w: int, l: int) -> Record:
    return Record(team=team, wins=w, losses=l)


def line(
    home: str,
    away: str,
    spread: float,
    week: int = 5,
    season_type: str = "regular",
    neutral: bool = False,
    game_id: int | None = None,
    books: int = 2,
) -> GameLine:
    """`spread` keeps CFBD's convention: negative means the HOME team is favoured."""
    return GameLine(
        game_id=game_id,
        week=week,
        season_type=season_type,
        start_date=f"2025-09-{week:02d}T00:00:00.000Z",
        home_team=home,
        away_team=away,
        home_classification="fbs",
        away_classification="fbs",
        spread=spread,
        books=books,
        neutral_site=neutral,
    )


def ppa(
    team: str,
    opponent: str,
    offense: float,
    defense: float,
    week: int = 5,
    season_type: str = "regular",
    game_id: int | None = None,
    was_home: bool | None = True,
    neutral: bool = False,
) -> TeamGamePPA:
    return TeamGamePPA(
        game_id=game_id,
        week=week,
        season_type=season_type,
        start_date=f"2025-09-{week:02d}T00:00:00.000Z",
        team=team,
        opponent=opponent,
        offense=offense,
        defense=defense,
        neutral_site=neutral,
        was_home=was_home,
    )


def grounds(allowance: float, **kw) -> Grounds:
    """A minimal Grounds when only the licensed gap matters."""
    fields = dict(
        category="form",
        severity="clear",
        weeks_since=4.0,
        winner_losses=1,
        loser_losses=0,
        slide=1.0,
        winner_credit=0.0,
        loser_credit=0.0,
        ascent=0.0,
        ramp=1.0,
        allowance=float(allowance),
        relief=0.0,
    )
    fields.update(kw)
    return Grounds(**fields)


def fact(
    winner: str, loser: str, weight: float, grounds: Grounds | None = None
) -> EdgeFact:
    """A minimal EdgeFact when only the conviction weight matters."""
    return EdgeFact(
        winner=winner,
        loser=loser,
        week=5,
        season_type="regular",
        winner_points=24,
        loser_points=17,
        margin=7,
        adj_margin=7.0,
        site="home",
        neutral_site=False,
        rating_gap=0.0,
        common_opponents=(),
        common_diff=0.0,
        recency=1.0,
        conviction=weight,
        weight=weight,
        components={},
        grounds=grounds,
    )


def weights(pairs: Sequence[tuple[str, str, float]]) -> dict[tuple[str, str], EdgeFact]:
    """(winner, loser, weight) triples, or (winner, loser, weight, licence)."""
    return {
        (p[0], p[1]): fact(p[0], p[1], p[2], grounds(p[3]) if len(p) > 3 else None)
        for p in pairs
    }
