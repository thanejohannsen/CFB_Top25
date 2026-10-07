"""Weekly snapshots and the snapshot catalogue the week selector reads."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Mapping

SCHEMA_VERSION = 1


def snapshot_path(history_dir: str | Path, snapshot_id: str) -> Path:
    return Path(history_dir) / f"{snapshot_id}.json"


def load_json(path: str | Path) -> dict[str, Any] | None:
    p = Path(path)
    if not p.exists():
        return None
    try:
        with p.open("r", encoding="utf-8") as fh:
            data = json.load(fh)
    except (OSError, json.JSONDecodeError):
        return None
    return data if isinstance(data, dict) else None


def load_previous(
    index_path: str | Path,
    history_dir: str | Path,
    mode: str,
    current_id: str,
    current_year: int | None = None,
) -> dict[str, Any] | None:
    """The snapshot to measure movement against.

    "auto" picks the newest catalogued snapshot of the SAME season whose id
    differs from the one being generated. Two details matter:

      * skipping the current id -- re-running a week mid-week must still
        compare against the previous week, or every delta collapses to zero;
      * staying inside the season -- a week 1 ranking has no meaningful
        predecessor, and comparing it to last season's final list would
        manufacture 25 bogus arrows.
    """
    if mode == "none":
        return None
    if mode != "auto":
        return load_json(Path(history_dir).parent / mode) or load_json(mode)

    index = load_json(index_path) or {}
    entries = [
        e
        for season in index.get("seasons") or []
        for e in (season.get("snapshots") or [])
        if isinstance(e, Mapping) and e.get("id") and e.get("id") != current_id
    ]
    if current_year is not None:
        entries = [e for e in entries if int(e.get("year") or 0) == int(current_year)]
    if not entries:
        return None
    entries.sort(key=lambda e: (str(e.get("year", "")), str(e.get("id", ""))))
    return load_json(snapshot_path(history_dir, str(entries[-1]["id"])))


def published_board(
    index_path: str | Path, history_dir: str | Path, year: int | None = None
) -> frozenset[str]:
    """Every team named by the newest published snapshot of this season.

    That is the top 25 plus `pool_tail`, i.e. the `stage1.pool_size` teams the
    ranking actually considers -- so it answers "is anyone waiting on this
    game?" without the engine having to rank anything first, which would be
    circular. Empty before a season's first snapshot exists, and the week
    resolver reads empty as "wait for every game".
    """
    index = load_json(index_path) or {}
    entries = [
        e
        for season in index.get("seasons") or []
        for e in (season.get("snapshots") or [])
        if isinstance(e, Mapping) and e.get("id")
    ]
    if year is not None:
        entries = [e for e in entries if int(e.get("year") or 0) == int(year)]
    if not entries:
        return frozenset()
    entries.sort(key=lambda e: (int(e.get("year") or 0), str(e.get("id", ""))))
    snap = load_json(snapshot_path(history_dir, str(entries[-1]["id"]))) or {}
    rows = list(snap.get("rankings") or []) + list(snap.get("pool_tail") or [])
    return frozenset(
        str(r["team"]) for r in rows if isinstance(r, Mapping) and r.get("team")
    )


def update_index(
    index_path: str | Path, payload: Mapping[str, Any], history_dir: str | Path
) -> dict[str, Any]:
    """Insert or refresh this snapshot's catalogue entry."""
    meta = payload.get("meta") or {}
    season = meta.get("season") or {}
    snapshot_id = str(meta.get("snapshot_id"))
    year = int(season.get("year") or 0)

    index = load_json(index_path) or {"schema_version": SCHEMA_VERSION, "seasons": []}
    entry = {
        "id": snapshot_id,
        "year": year,
        "week": season.get("week"),
        "season_type": season.get("season_type"),
        "label": season.get("label"),
        "path": f"{Path(history_dir).name}/{snapshot_id}.json",
        "generated_at": meta.get("generated_at"),
        "content_hash": meta.get("content_hash"),
        "top1": (payload.get("rankings") or [{}])[0].get("team"),
    }

    seasons = [s for s in index.get("seasons") or [] if isinstance(s, dict)]
    # A season whose every snapshot file is gone should leave no empty husk.
    for other in seasons:
        if other.get("year") != year:
            other["snapshots"] = [
                s
                for s in other.get("snapshots") or []
                if snapshot_path(history_dir, str(s.get("id"))).exists()
            ]
    seasons = [s for s in seasons if s.get("snapshots") or s.get("year") == year]
    bucket = next((s for s in seasons if s.get("year") == year), None)
    if bucket is None:
        bucket = {"year": year, "snapshots": []}
        seasons.append(bucket)
    snaps = [
        s
        for s in bucket.get("snapshots") or []
        # Drop the entry being rewritten, and any whose file has gone. The
        # catalogue is what the week selector reads, so a stale entry is a 404
        # waiting to happen -- and snapshots do legitimately get deleted: a
        # week published off one midweek game, a season regenerated under a new
        # id. Rebuilding from the directory each time keeps the two in step.
        if s.get("id") != snapshot_id
        and snapshot_path(history_dir, str(s.get("id"))).exists()
    ]
    snaps.append(entry)
    snaps.sort(key=lambda s: str(s.get("id")))
    bucket["snapshots"] = snaps
    seasons.sort(key=lambda s: int(s.get("year") or 0), reverse=True)

    index["schema_version"] = SCHEMA_VERSION
    index["seasons"] = seasons
    # "current" is the newest ranking in the catalogue, not whichever file was
    # written last -- regenerating an archived season must not make it current.
    index["current"] = _newest(seasons) or snapshot_id
    index["updated_at"] = meta.get("generated_at")
    return index


def _newest(seasons) -> str | None:
    """The latest snapshot id across every season, by (year, type, week)."""
    best = None
    for season in seasons:
        for snap in season.get("snapshots") or []:
            key = (
                int(snap.get("year") or 0),
                0 if snap.get("season_type") == "regular" else 1,
                int(snap.get("week") or 0),
            )
            if best is None or key > best[0]:
                best = (key, str(snap.get("id")))
    return best[1] if best else None
