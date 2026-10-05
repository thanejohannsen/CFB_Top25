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
    bucket = next((s for s in seasons if s.get("year") == year), None)
    if bucket is None:
        bucket = {"year": year, "snapshots": []}
        seasons.append(bucket)
    snaps = [s for s in bucket.get("snapshots") or [] if s.get("id") != snapshot_id]
    snaps.append(entry)
    snaps.sort(key=lambda s: str(s.get("id")))
    bucket["snapshots"] = snaps
    seasons.sort(key=lambda s: int(s.get("year") or 0), reverse=True)

    index["schema_version"] = SCHEMA_VERSION
    index["seasons"] = seasons
    index["current"] = snapshot_id
    index["updated_at"] = meta.get("generated_at")
    return index
