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
        "w_sor": 0.60,
        "w_market": 0.25,
        "w_perf": 0.15,
        "w_adjust": 0.5,
        "w_fpi": 0.0,
        "w_sos": 0.0,
        "pool_size": 40,
        "output_size": 25,
        "require_fpi": True,
        "fbs_only": True,
    },
    "market": {
        "min_games": 3,
        "horizon_weeks": 1,
        "recency_half_life": 0.0,
        "fit_home_field": True,
        "home_field_points": 2.5,
    },
    "performance": {"min_games": 3, "fit_home_edge": True},
    "stage2": {
        "w_loss_quality": 3.0,
        "w_best_win": 1.0,
        "w_game_control": 0.5,
        "w_cover": 4.0,
        "cover_game_cap": 0.0,
        "best_win_place": 25,
        "fit_cover_venue": True,
    },
    "resume": {"min_games": 3, "margin_sigma": 13.5, "reference_place": 25},
    "stage3": {"enabled": True, "gap": 15, "strength": 0.5},
    "stage4": {
        "strength": 14.0,
        "drift_weight": 1.0,
        "drift_exponent": 1.5,
        "split_series": "most_recent",
        "max_passes": 400,
        "max_block": 4,
        "evidence": {
            "w_margin": 0.50,
            "w_rating_gap": 0.30,
            "w_recency": 1.20,
            "w_common_opponents": 0.15,
            "home_field_points": 2.5,
            "fit_home_field": True,
            "margin_cap": 28,
            "recency_floor": 0.15,
            "recency_half_life_good": 8.0,
            "recency_half_life_bad": 6.0,
            "weight_floor": 1.0,
            "conviction_floor": -2.0,
        },
        "grounds": {
            "enabled": True,
            "impressive_bar": 6.0,
            "min_weeks": 2,
            "band_start": 0.5,
            "band_step": 0.25,
            "band_max": 1.5,
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
    # w_adjust lives in [stage1] for discoverability but scales stage 2, so it is
    # deliberately absent from this list -- a board of all-zero base weights is
    # still invalid however large the adjustment dial is.
    base_weights = ("stage1.w_sor", "stage1.w_market", "stage1.w_perf", "stage1.w_sos", "stage1.w_fpi")
    for key in base_weights:
        if cfg[key] < 0:
            raise ConfigError(f"{key} must be >= 0")
    if sum(cfg[key] for key in base_weights) <= 0:
        raise ConfigError("at least one stage1 weight must be > 0")

    if cfg["stage1.w_adjust"] < 0:
        raise ConfigError("stage1.w_adjust must be >= 0 (0 turns the resume adjustment off)")
    # Every stage-2 weight scales a quantity whose sign already carries the
    # meaning, so a negative weight inverts the term rather than softening it.
    # It matters most for w_loss_quality, which is one-sided: negative would turn
    # the loss penalty into an unbounded reward for losing badly.
    for key in ("w_cover", "w_loss_quality", "w_best_win", "w_game_control"):
        if cfg[f"stage2.{key}"] < 0:
            raise ConfigError(f"stage2.{key} must be >= 0 (0 turns that term off)")
    if cfg["resume.margin_sigma"] <= 0:
        raise ConfigError("resume.margin_sigma must be > 0")
    place = cfg["resume.reference_place"]
    if not isinstance(place, int) or place < 1:
        raise ConfigError("resume.reference_place must be an integer >= 1")

    for section in ("market", "performance", "resume"):
        if not isinstance(cfg[f"{section}.min_games"], int) or cfg[f"{section}.min_games"] < 1:
            raise ConfigError(f"{section}.min_games must be an integer >= 1")
    if cfg["market.home_field_points"] < 0:
        raise ConfigError("market.home_field_points must be >= 0")
    if cfg["market.recency_half_life"] < 0:
        raise ConfigError("market.recency_half_life must be >= 0 (0 turns it off)")
    horizon = cfg["market.horizon_weeks"]
    if not isinstance(horizon, int) or not 0 <= horizon <= 1:
        raise ConfigError(
            "market.horizon_weeks must be 0 or 1 -- a line two weeks out has already "
            "priced in results this ranking is not allowed to see"
        )

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
    for key in ("recency_half_life_good", "recency_half_life_bad"):
        if ev[key] <= 0:
            raise ConfigError(f"stage4.evidence.{key} must be > 0")
    if ev["recency_half_life_good"] < ev["recency_half_life_bad"]:
        raise ConfigError(
            "stage4.evidence.recency_half_life_good must be >= recency_half_life_bad "
            "-- a convincing result should not fade faster than a weak one"
        )
    if ev["weight_floor"] <= 0:
        raise ConfigError(
            "stage4.evidence.weight_floor must be > 0 -- overriding a result has to cost something"
        )

    block = cfg["stage4.max_block"]
    if not isinstance(block, int) or not 2 <= block <= pool:
        raise ConfigError(
            "stage4.max_block must be an integer in 2..pool_size -- blocks of one are "
            "the ordinary move, and a block cannot be larger than the board"
        )

    gr = cfg.section("stage4.grounds")
    if not isinstance(gr.get("enabled"), bool):
        raise ConfigError("stage4.grounds.enabled must be true or false")
    for key in ("impressive_bar", "band_start", "band_step"):
        if gr[key] < 0:
            raise ConfigError(f"stage4.grounds.{key} must be >= 0")
    weeks = gr["min_weeks"]
    if not isinstance(weeks, int) or weeks < 0:
        raise ConfigError("stage4.grounds.min_weeks must be an integer >= 0")
    if gr["band_max"] < gr["band_start"]:
        raise ConfigError(
            "stage4.grounds.band_max must be >= band_start -- the band widens with time, "
            "so its ceiling cannot sit below where it starts"
        )

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
