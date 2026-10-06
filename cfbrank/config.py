"""Configuration loading, validation, and dotted-path overrides.

The shipped defaults below are the single source of truth for the shape of
config/ranking.toml. Any key in the file that is absent here is rejected, so a
typo (w_sos -> w_sosu) fails loudly instead of silently using a default.
"""

from __future__ import annotations

import copy
import tomllib
from pathlib import Path
from typing import Any, Mapping

from cfbrank.errors import ConfigError

DEFAULTS: dict[str, Any] = {
    "schema_version": 1,
    "season": {"year": 2026, "week": "auto", "season_type": "both"},
    "source": {
        "api_key": "",
        "base_url": "https://api.collegefootballdata.com",
        "cache_dir": ".cache/cfbd",
        "cache_ttl_minutes": 360,
        "timeout_seconds": 20.0,
        "max_retries": 4,
        "retry_backoff_seconds": 1.5,
        "serve_stale_on_failure": True,
        "offline": False,
        "fixtures_dir": "tests/fixtures/cfbd",
        "fixture_year": "2025",
    },
    "stage1": {
        "w_sor": 0.75,
        "w_fpi": 0.25,
        "w_sos": 0.0,
        "pool_size": 40,
        "output_size": 25,
        "require_fpi": True,
        "fbs_only": True,
    },
    "stage2": {"w_loss_quality": 3.0, "w_best_win": 1.0, "w_game_control": 0.5},
    "stage3": {"enabled": True, "gap": 15, "strength": 0.5},
    "stage4": {
        "strength": 14.0,
        "drift_weight": 1.0,
        "drift_exponent": 1.5,
        "split_series": "most_recent",
        "max_passes": 400,
        "evidence": {
            "w_margin": 0.50,
            "w_fpi_gap": 0.30,
            "w_recency": 0.20,
            "w_common_opponents": 0.15,
            "home_field_points": 2.5,
            "margin_cap": 28,
            "recency_floor": 0.25,
        },
    },
    "output": {
        "rankings_path": "docs/data/rankings.json",
        "history_dir": "docs/data/weeks",
        "index_path": "docs/data/index.json",
        "float_precision": 4,
        "previous_snapshot": "auto",
        "include_pool_tail": True,
    },
}

SPLIT_SERIES_POLICIES = ("most_recent", "drop", "aggregate_margin")
SEASON_TYPES = ("regular", "postseason", "both")


class Config:
    """A validated configuration tree with dotted-path access."""

    def __init__(self, data: Mapping[str, Any]) -> None:
        self._data = copy.deepcopy(dict(data))

    # -- access ------------------------------------------------------------
    def get(self, path: str, default: Any = None) -> Any:
        node: Any = self._data
        for part in path.split("."):
            if not isinstance(node, Mapping) or part not in node:
                return default
            node = node[part]
        return node

    def section(self, path: str) -> dict[str, Any]:
        node = self.get(path, {})
        return dict(node) if isinstance(node, Mapping) else {}

    def as_dict(self) -> dict[str, Any]:
        return copy.deepcopy(self._data)

    def __getitem__(self, path: str) -> Any:
        sentinel = object()
        val = self.get(path, sentinel)
        if val is sentinel:
            raise ConfigError(f"missing config key: {path}")
        return val


def _merge_strict(defaults: Mapping[str, Any], override: Mapping[str, Any], prefix: str = "") -> dict[str, Any]:
    out = dict(defaults)
    for key, val in override.items():
        dotted = f"{prefix}{key}"
        if key not in defaults:
            raise ConfigError(f"unknown config key: {dotted}")
        if isinstance(defaults[key], Mapping):
            if not isinstance(val, Mapping):
                raise ConfigError(f"config key {dotted} must be a table")
            out[key] = _merge_strict(defaults[key], val, prefix=f"{dotted}.")
        else:
            out[key] = val
    return out


def _coerce(text: str) -> Any:
    low = text.strip().lower()
    if low in ("true", "false"):
        return low == "true"
    for caster in (int, float):
        try:
            return caster(text)
        except ValueError:
            pass
    return text


def apply_override(data: dict[str, Any], assignment: str) -> None:
    """Apply one `dotted.path=value` override in place, rejecting unknown paths."""
    if "=" not in assignment:
        raise ConfigError(f"--set expects dotted.path=value, got: {assignment!r}")
    path, raw = assignment.split("=", 1)
    parts = [p for p in path.strip().split(".") if p]
    if not parts:
        raise ConfigError(f"--set expects dotted.path=value, got: {assignment!r}")
    node: Any = data
    for part in parts[:-1]:
        if not isinstance(node, dict) or part not in node:
            raise ConfigError(f"unknown config path in --set: {path}")
        node = node[part]
    leaf = parts[-1]
    if not isinstance(node, dict) or leaf not in node:
        raise ConfigError(f"unknown config path in --set: {path}")
    if isinstance(node[leaf], Mapping):
        raise ConfigError(f"--set cannot replace the table {path}")
    node[leaf] = _coerce(raw)


def validate(data: Mapping[str, Any]) -> None:
    cfg = Config(data)

    if cfg["schema_version"] != 1:
        raise ConfigError(f"unsupported schema_version: {cfg['schema_version']}")

    week = cfg["season.week"]
    if week != "auto" and not (isinstance(week, int) and 1 <= week <= 25):
        raise ConfigError("season.week must be \"auto\" or an integer in 1..25")
    if cfg["season.season_type"] not in SEASON_TYPES:
        raise ConfigError(f"season.season_type must be one of {SEASON_TYPES}")

    pool, out = cfg["stage1.pool_size"], cfg["stage1.output_size"]
    if not isinstance(pool, int) or pool < 1:
        raise ConfigError("stage1.pool_size must be a positive integer")
    if not isinstance(out, int) or not 0 < out <= pool:
        raise ConfigError("stage1.output_size must satisfy 0 < output_size <= pool_size")
    for key in ("stage1.w_sor", "stage1.w_sos", "stage1.w_fpi"):
        if cfg[key] < 0:
            raise ConfigError(f"{key} must be >= 0")
    if cfg["stage1.w_sor"] + cfg["stage1.w_sos"] + cfg["stage1.w_fpi"] <= 0:
        raise ConfigError("at least one of stage1.w_sor/w_sos/w_fpi must be > 0")

    if cfg["stage3.gap"] < 0:
        raise ConfigError("stage3.gap must be >= 0")
    if not 0.0 <= cfg["stage3.strength"] <= 1.0:
        raise ConfigError("stage3.strength must be in 0.0..1.0")

    if cfg["stage4.strength"] < 0:
        raise ConfigError("stage4.strength must be >= 0")
    if cfg["stage4.drift_weight"] < 0:
        raise ConfigError("stage4.drift_weight must be >= 0")
    if not 1.0 <= cfg["stage4.drift_exponent"] <= 3.0:
        raise ConfigError("stage4.drift_exponent must be in 1.0..3.0")
    if cfg["stage4.split_series"] not in SPLIT_SERIES_POLICIES:
        raise ConfigError(f"stage4.split_series must be one of {SPLIT_SERIES_POLICIES}")
    if cfg["stage4.max_passes"] < 1:
        raise ConfigError("stage4.max_passes must be >= 1")

    ev = cfg.section("stage4.evidence")
    if ev["margin_cap"] <= 0:
        raise ConfigError("stage4.evidence.margin_cap must be > 0")
    if not 0.0 <= ev["recency_floor"] <= 1.0:
        raise ConfigError("stage4.evidence.recency_floor must be in 0.0..1.0")
    if ev["home_field_points"] < 0:
        raise ConfigError("stage4.evidence.home_field_points must be >= 0")

    prec = cfg["output.float_precision"]
    if not isinstance(prec, int) or not 0 <= prec <= 12:
        raise ConfigError("output.float_precision must be an integer in 0..12")


def load(path: str | Path | None = None, overrides: list[str] | None = None) -> Config:
    """Load config from TOML, merge over defaults, apply --set, validate."""
    data = copy.deepcopy(DEFAULTS)
    if path is not None:
        p = Path(path)
        if not p.exists():
            raise ConfigError(f"config file not found: {p}")
        try:
            with p.open("rb") as fh:
                parsed = tomllib.load(fh)
        except tomllib.TOMLDecodeError as exc:
            raise ConfigError(f"could not parse {p}: {exc}") from exc
        data = _merge_strict(data, parsed)
    for assignment in overrides or []:
        apply_override(data, assignment)
    validate(data)
    return Config(data)
