"""Stable JSON serialization and write-if-changed."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Mapping

from cfbrank.engine.stats import round_floats


def dumps_stable(payload: Mapping[str, Any], float_precision: int = 4) -> str:
    """Deterministic bytes for identical content, so git diffs stay meaningful.

    allow_nan=False is deliberate: a NaN would serialize to invalid JSON that
    the browser refuses to parse, and failing here is far easier to diagnose.
    """
    rounded = round_floats(dict(payload), float_precision)
    return (
        json.dumps(rounded, sort_keys=True, indent=2, ensure_ascii=False, allow_nan=False) + "\n"
    )


def write_if_changed(path: str | Path, text: str, force: bool = False) -> bool:
    """Write only when the content actually differs. Returns True if written."""
    p = Path(path)
    if not force and p.exists():
        try:
            if p.read_text(encoding="utf-8") == text:
                return False
        except OSError:
            pass
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(p.suffix + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    tmp.replace(p)
    return True
