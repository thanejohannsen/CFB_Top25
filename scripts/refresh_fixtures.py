#!/usr/bin/env python3
"""Fetch one season from CFBD and write trimmed fixtures into tests/fixtures/cfbd/.

Requires CFBD_API_KEY. Makes exactly six calls and projects each record down
to the fields cfbrank actually reads, which keeps a full season near 600 KB
instead of 3 MB. A completed season makes an ideal fixture: it never changes.

    python3 scripts/refresh_fixtures.py --year 2025
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from cfbrank.sources.cfbd import CFBDClient  # noqa: E402
from cfbrank.sources.http_cache import HttpCache  # noqa: E402

GAME_FIELDS = (
    "id", "season", "week", "seasonType", "startDate", "completed",
    "neutralSite", "conferenceGame", "venue",
    "homeTeam", "homePoints", "homeConference", "homeClassification",
    "awayTeam", "awayPoints", "awayConference", "awayClassification",
)
RECORD_FIELDS = ("year", "team", "classification", "conference")
CALENDAR_FIELDS = ("season", "week", "seasonType", "startDate", "endDate")

# Books whose closing spread we average. Keeping the list short and explicit
# makes a fixture reproducible: adding a provider later changes every rating.
LINE_PROVIDERS = ("DraftKings", "Bovada", "ESPN Bet")


def trim(row: dict, fields: tuple[str, ...]) -> dict:
    return {k: row.get(k) for k in fields if k in row}


def trim_rating(row: dict) -> dict:
    rr = row.get("resumeRanks") or {}
    eff = row.get("efficiencies") or {}
    return {
        "year": row.get("year"),
        "team": row.get("team"),
        "conference": row.get("conference"),
        "fpi": row.get("fpi"),
        "resumeRanks": {
            k: rr.get(k)
            for k in (
                "strengthOfRecord", "fpi", "averageWinProbability",
                "strengthOfSchedule", "remainingStrengthOfSchedule", "gameControl",
            )
        },
        "efficiencies": {k: eff.get(k) for k in ("overall", "offense", "defense", "specialTeams")},
    }


def trim_record(row: dict) -> dict:
    out = trim(row, RECORD_FIELDS)
    for block in ("total", "conferenceGames"):
        src = row.get(block) or {}
        out[block] = {k: src.get(k) for k in ("games", "wins", "losses", "ties")}
    return out


def trim_line(row: dict) -> dict:
    """Keep the closing spread from each provider we use, and nothing else.

    Moneylines and over/unders are dropped: the ranking only needs the spread,
    and carrying the rest triples the fixture.
    """
    books = [
        {"provider": ln.get("provider"), "spread": ln.get("spread")}
        for ln in (row.get("lines") or [])
        if ln.get("provider") in LINE_PROVIDERS and ln.get("spread") is not None
    ]
    books.sort(key=lambda b: str(b["provider"]))
    return {
        "id": row.get("id"),
        "season": row.get("season"),
        "week": row.get("week"),
        "seasonType": row.get("seasonType"),
        "startDate": row.get("startDate"),
        "homeTeam": row.get("homeTeam"),
        "homeClassification": row.get("homeClassification"),
        "awayTeam": row.get("awayTeam"),
        "awayClassification": row.get("awayClassification"),
        "lines": books,
    }


def trim_ppa(row: dict) -> dict:
    """Only the `overall` figures. The per-down splits are not read anywhere."""
    off = row.get("offense") or {}
    dfn = row.get("defense") or {}
    return {
        "gameId": row.get("gameId"),
        "season": row.get("season"),
        "week": row.get("week"),
        "seasonType": row.get("seasonType"),
        "team": row.get("team"),
        "opponent": row.get("opponent"),
        "offense": {"overall": off.get("overall")},
        "defense": {"overall": dfn.get("overall")},
    }


def trim_poll(row: dict) -> dict:
    """Keep only the two FBS human polls.

    They exist in the fixtures for ONE purpose: scripts/evaluate.py prints them
    as a reference row. Nothing in the ranking pipeline may read them.
    """
    polls = [
        {
            "poll": poll.get("poll"),
            "ranks": [
                {"rank": r.get("rank"), "school": r.get("school")}
                for r in (poll.get("ranks") or [])
                if r.get("school") and r.get("rank")
            ],
        }
        for poll in (row.get("polls") or [])
        if str(poll.get("poll") or "") in ("AP Top 25", "Coaches Poll")
    ]
    return {
        "season": row.get("season"),
        "week": row.get("week"),
        "seasonType": row.get("seasonType"),
        "polls": polls,
    }


def write(path: Path, payload: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as fh:
        json.dump(payload, fh, indent=1, sort_keys=True, ensure_ascii=False)
        fh.write("\n")
    print(f"  {path}  ({len(payload)} records, {path.stat().st_size // 1024} KB)")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--year", type=int, required=True)
    ap.add_argument("--out", default="tests/fixtures/cfbd")
    ap.add_argument("--label", default=None, help="subdirectory name (default: the year)")
    args = ap.parse_args()

    key = os.environ.get("CFBD_API_KEY", "")
    if not key:
        print("CFBD_API_KEY is not set", file=sys.stderr)
        return 4

    client = CFBDClient(key, "https://api.collegefootballdata.com", HttpCache(".cache/cfbd", 360))
    out = Path(args.out) / (args.label or str(args.year))
    print(f"fetching {args.year} -> {out}")

    write(out / "ratings_fpi.json", [trim_rating(r) for r in client.fpi_ratings(args.year)])
    write(out / "games.json", [trim(g, GAME_FIELDS) for g in client.games(args.year, "both")])
    write(out / "records.json", [trim_record(r) for r in client.records(args.year)])
    write(out / "calendar.json", [trim(c, CALENDAR_FIELDS) for c in client.calendar(args.year)])
    write(out / "lines.json", [trim_line(r) for r in client.lines(args.year, "both")])
    write(out / "ppa_games.json", [trim_ppa(r) for r in client.ppa_games(args.year, "both")])
    write(
        out / "rankings_polls.json",
        [trim_poll(r) for r in client.poll_rankings(args.year, "both")],
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
