"""Raw CFBD dicts -> Dataset. The only module that touches API field names.

Every access is .get()-guarded: /ratings/fpi has null resumeRanks entries early
in a season and for newly promoted programs, and a KeyError here would take the
whole site down.
"""

from __future__ import annotations

from typing import Any, Iterable, Mapping

from cfbrank.models import (
    CalendarWeek,
    Dataset,
    Game,
    GameLine,
    Record,
    TeamGamePPA,
    TeamRating,
    Warning_,
)
from cfbrank.normalize import canonical, join_report, sort_key
from cfbrank.sources.base import DataSource


def _int(v: Any) -> int | None:
    if v is None or isinstance(v, bool):
        return None
    try:
        return int(v)
    except (TypeError, ValueError):
        return None


def _float(v: Any) -> float | None:
    if v is None or isinstance(v, bool):
        return None
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return f if f == f and abs(f) != float("inf") else None


def _str(v: Any) -> str | None:
    return None if v is None else str(v)


def _optional(src: DataSource, method: str, year: int, season_type: str) -> list[dict]:
    """Call an endpoint a source may not implement, and treat absence as empty.

    Added so an older fixture directory, or a stub source in a test, keeps
    working after /lines and /ppa/games joined the contract.
    """
    fn = getattr(src, method, None)
    if fn is None:
        return []
    try:
        return list(fn(year, season_type) or [])
    except TypeError:
        return list(fn(year) or [])


def parse_ratings(rows: Iterable[Mapping[str, Any]]) -> list[TeamRating]:
    out: list[TeamRating] = []
    for row in rows or []:
        team = canonical(row.get("team") or "")
        if not team:
            continue
        rr = row.get("resumeRanks") or {}
        eff = row.get("efficiencies") or {}
        out.append(
            TeamRating(
                team=team,
                conference=_str(row.get("conference")),
                fpi=_float(row.get("fpi")),
                eff_overall=_float(eff.get("overall")),
                eff_offense=_float(eff.get("offense")),
                eff_defense=_float(eff.get("defense")),
                eff_special=_float(eff.get("specialTeams")),
                sor_rank=_int(rr.get("strengthOfRecord")),
                fpi_resume_rank=_int(rr.get("fpi")),
                awp_rank=_int(rr.get("averageWinProbability")),
                sos_rank=_int(rr.get("strengthOfSchedule")),
                rsos_rank=_int(rr.get("remainingStrengthOfSchedule")),
                game_control_rank=_int(rr.get("gameControl")),
            )
        )
    out.sort(key=lambda r: sort_key(r.team))
    return out


def parse_games(rows: Iterable[Mapping[str, Any]], year: int) -> list[Game]:
    out: list[Game] = []
    for row in rows or []:
        home = canonical(row.get("homeTeam") or "")
        away = canonical(row.get("awayTeam") or "")
        if not home or not away:
            continue
        week = _int(row.get("week"))
        if week is None:
            continue
        out.append(
            Game(
                game_id=_int(row.get("id")),
                year=_int(row.get("season")) or year,
                week=week,
                season_type=str(row.get("seasonType") or "regular"),
                start_date=_str(row.get("startDate")),
                completed=bool(row.get("completed")),
                neutral_site=bool(row.get("neutralSite")),
                conference_game=bool(row.get("conferenceGame")),
                home_team=home,
                home_points=_int(row.get("homePoints")),
                home_conference=_str(row.get("homeConference")),
                home_classification=_str(row.get("homeClassification")),
                away_team=away,
                away_points=_int(row.get("awayPoints")),
                away_conference=_str(row.get("awayConference")),
                away_classification=_str(row.get("awayClassification")),
                venue=_str(row.get("venue")),
            )
        )
    out.sort(key=lambda g: (g.order_key, sort_key(g.home_team), sort_key(g.away_team)))
    return out


def _median(xs: list[float]) -> float:
    ordered = sorted(xs)
    mid = len(ordered) // 2
    if len(ordered) % 2:
        return ordered[mid]
    return (ordered[mid - 1] + ordered[mid]) / 2.0


def parse_lines(
    rows: Iterable[Mapping[str, Any]], by_id: Mapping[int, Game]
) -> list[GameLine]:
    """One consensus line per game: the median spread across the books quoted.

    The median rather than the mean on purpose -- a single book posting a stale
    or mistyped number would drag a mean several points and the spread is the
    whole input to the market rating.

    Site and date come from the matching /games row when there is one, because
    /lines does not say whether a game was played on neutral ground and a bowl
    priced at -3 means something different from a home team priced at -3.
    """
    out: list[GameLine] = []
    for row in rows or []:
        home = canonical(row.get("homeTeam") or "")
        away = canonical(row.get("awayTeam") or "")
        week = _int(row.get("week"))
        if not home or not away or week is None:
            continue
        spreads = [
            f
            for f in (_float(ln.get("spread")) for ln in (row.get("lines") or []))
            if f is not None
        ]
        if not spreads:
            continue
        gid = _int(row.get("id"))
        game = by_id.get(gid) if gid is not None else None
        out.append(
            GameLine(
                game_id=gid,
                week=week,
                season_type=str(row.get("seasonType") or "regular"),
                start_date=_str(row.get("startDate")) or (game.start_date if game else None),
                home_team=home,
                away_team=away,
                home_classification=_str(row.get("homeClassification"))
                or (game.home_classification if game else None),
                away_classification=_str(row.get("awayClassification"))
                or (game.away_classification if game else None),
                spread=_median(spreads),
                books=len(spreads),
                neutral_site=bool(game.neutral_site) if game else False,
            )
        )
    out.sort(key=lambda l: (l.order_key, sort_key(l.home_team), sort_key(l.away_team)))
    return out


def parse_ppa(
    rows: Iterable[Mapping[str, Any]], by_id: Mapping[int, Game]
) -> list[TeamGamePPA]:
    """Per-team, per-game PPA. Rows missing either side of the ball are dropped."""
    out: list[TeamGamePPA] = []
    for row in rows or []:
        team = canonical(row.get("team") or "")
        opponent = canonical(row.get("opponent") or "")
        week = _int(row.get("week"))
        off = _float((row.get("offense") or {}).get("overall"))
        dfn = _float((row.get("defense") or {}).get("overall"))
        if not team or not opponent or week is None or off is None or dfn is None:
            continue
        gid = _int(row.get("gameId"))
        game = by_id.get(gid) if gid is not None else None
        out.append(
            TeamGamePPA(
                game_id=gid,
                week=week,
                season_type=str(row.get("seasonType") or "regular"),
                start_date=_str(row.get("startDate")) or (game.start_date if game else None),
                team=team,
                opponent=opponent,
                offense=off,
                defense=dfn,
                neutral_site=bool(game.neutral_site) if game else False,
                was_home=(game.home_team == team) if game else None,
            )
        )
    out.sort(key=lambda r: (r.order_key, sort_key(r.team)))
    return out


def parse_records(rows: Iterable[Mapping[str, Any]]) -> tuple[list[Record], set[str]]:
    """Returns (records, fbs_team_names) -- classification is authoritative here."""
    out: list[Record] = []
    fbs: set[str] = set()
    for row in rows or []:
        team = canonical(row.get("team") or "")
        if not team:
            continue
        if str(row.get("classification") or "").lower() == "fbs":
            fbs.add(team)
        total = row.get("total") or {}
        conf = row.get("conferenceGames") or {}
        out.append(
            Record(
                team=team,
                wins=_int(total.get("wins")) or 0,
                losses=_int(total.get("losses")) or 0,
                ties=_int(total.get("ties")) or 0,
                conference_wins=_int(conf.get("wins")) or 0,
                conference_losses=_int(conf.get("losses")) or 0,
            )
        )
    out.sort(key=lambda r: sort_key(r.team))
    return out, fbs


def parse_calendar(rows: Iterable[Mapping[str, Any]]) -> list[CalendarWeek]:
    out: list[CalendarWeek] = []
    for row in rows or []:
        week = _int(row.get("week"))
        if week is None:
            continue
        out.append(
            CalendarWeek(
                week=week,
                season_type=str(row.get("seasonType") or "regular"),
                start_date=str(row.get("startDate") or ""),
                end_date=str(row.get("endDate") or ""),
            )
        )
    out.sort(key=lambda c: (c.season_type != "regular", c.week))
    return out


def fbs_from_games(games: Iterable[Game]) -> set[str]:
    """FBS membership inferred from per-participant game classification."""
    fbs: set[str] = set()
    for g in games:
        if (g.home_classification or "").lower() == "fbs":
            fbs.add(g.home_team)
        if (g.away_classification or "").lower() == "fbs":
            fbs.add(g.away_team)
    return fbs


def build_dataset(src: DataSource, year: int, season_type: str = "both") -> Dataset:
    ratings = parse_ratings(src.fpi_ratings(year))
    games = parse_games(src.games(year, season_type), year)
    records, fbs_records = parse_records(src.records(year))
    calendar = parse_calendar(src.calendar(year))

    # Lines and PPA are joined back to /games by id for site and date, so games
    # must be parsed first. Neither is required: a source that cannot serve them
    # yields an empty list and the corresponding base term drops out.
    by_id = {g.game_id: g for g in games if g.game_id is not None}
    lines = parse_lines(_optional(src, "lines", year, season_type), by_id)
    ppa = parse_ppa(_optional(src, "ppa_games", year, season_type), by_id)

    fbs = fbs_records or fbs_from_games(games)
    warnings: list[Warning_] = []

    # /ratings/fpi carries a handful of non-FBS programs; flag them so the
    # stage-1 filter is visible rather than silent.
    for r in ratings:
        if fbs and r.team not in fbs:
            warnings.append(
                Warning_(code="not_fbs", team=r.team, detail="has an FPI entry but is not an FBS team")
            )
        if r.sor_rank is None or r.sos_rank is None or r.fpi is None:
            missing = [
                name
                for name, val in (
                    ("strengthOfRecord", r.sor_rank),
                    ("strengthOfSchedule", r.sos_rank),
                    ("fpi", r.fpi),
                )
                if val is None
            ]
            warnings.append(
                Warning_(code="missing_rating", team=r.team, detail=f"missing {', '.join(missing)}")
            )

    # A completed game with no score, or a score on an incomplete game, is a
    # data-quality signal rather than a routine skip.
    for g in games:
        has_points = g.home_points is not None and g.away_points is not None
        if g.completed and not has_points:
            warnings.append(
                Warning_(
                    code="completed_without_score",
                    detail=f"{g.away_team} at {g.home_team}, week {g.week} ({g.season_type})",
                )
            )

    fbs_played = [
        name
        for g in games
        for name, cls in ((g.home_team, g.home_classification), (g.away_team, g.away_classification))
        if (cls or "").lower() == "fbs"
    ]
    report = join_report((r.team for r in ratings), fbs_played)
    for name in report["games_without_ratings"]:
        warnings.append(
            Warning_(code="no_fpi_entry", team=name, detail="played as FBS but has no /ratings/fpi row")
        )

    if not lines:
        warnings.append(
            Warning_(code="no_lines", detail="no betting lines available; the market term is off")
        )
    if not ppa:
        warnings.append(
            Warning_(code="no_ppa", detail="no per-game PPA available; the play-by-play term is off")
        )

    return Dataset(
        year=year,
        ratings=tuple(ratings),
        games=tuple(games),
        records=tuple(records),
        calendar=tuple(calendar),
        lines=tuple(lines),
        ppa=tuple(ppa),
        provenance=tuple(src.provenance()),
        warnings=tuple(warnings),
        synthetic=bool(getattr(src, "synthetic", False)),
    )
