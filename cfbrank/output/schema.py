"""Builds the published JSON payload, and validates it.

The payload is the contract between the engine, the site, and the workflow's
no-op check. It carries enough provenance that every placement on the page can
be justified without the browser knowing any ranking rules.
"""

from __future__ import annotations

import hashlib
import json
from typing import Any, Mapping, Sequence

from cfbrank.config import Config
from cfbrank.engine.stats import round_floats
from cfbrank.engine.base_score import TeamBase
from cfbrank.engine.evidence import EdgeFact
from cfbrank.engine.order import OrderResult
from cfbrank.engine.pipeline import RankingResult
from cfbrank.models import Dataset

SCHEMA_VERSION = 1
ATTRIBUTION = (
    "Data: CollegeFootballData.com -- betting lines, play-by-play PPA, and "
    "Strength of Record, Strength of Schedule and FPI via ESPN"
)


def _grounds(fact: EdgeFact) -> dict[str, Any] | None:
    """Which of the four exceptions let this result be ranked against, and how far.

    Published per result so the page can say why a contradiction is permitted at
    all, and how wide it is allowed to be, without knowing any ranking rules.
    None when the stage is switched off.
    """
    g = fact.grounds
    if g is None:
        return None
    return {
        # Which of the four exceptions apply. Empty means the result is ENFORCED:
        # nothing since the game lets the ranking go against it.
        "rules": list(g.rules),
        # Places the loser may finish above the winner. **null, never Infinity** --
        # validate_payload's NaN/Infinity scan would reject the payload, and
        # round_floats passes a float infinity straight through.
        "max_lead": None if g.max_lead == float("inf") else g.max_lead,
        "enforced": g.enforced,
        "weeks_since": g.weeks_since,
        "winner_losses": g.winner_losses,
        "loser_losses": g.loser_losses,
        "winner_losses_since": g.winner_losses_since,
        "loser_losses_since": g.loser_losses_since,
        "winner_win_quality": g.winner_quality,
        "loser_win_quality": g.loser_quality,
        "quality_diff": g.quality_diff,
        "band_rate": g.band_rate,
    }


def _edge(
    fact: EdgeFact,
    rank_of: Mapping[str, int],
    overridden: bool,
    ordering: OrderResult | None = None,
) -> dict[str, Any]:
    lead = None if ordering is None else ordering.leads.get((fact.winner, fact.loser))
    return {
        "winner": fact.winner,
        "loser": fact.loser,
        "winner_rank": rank_of.get(fact.winner),
        "loser_rank": rank_of.get(fact.loser),
        "week": fact.week,
        "season_type": fact.season_type,
        "score": fact.score,
        "margin": fact.margin,
        "site": fact.site,
        "adj_margin": fact.adj_margin,
        "rating_gap": fact.rating_gap,
        "common_opponents": list(fact.common_opponents),
        "common_diff": fact.common_diff,
        "conviction": fact.conviction,
        "weight": fact.weight,
        "components": dict(fact.components),
        "status": "overridden" if overridden else "honored",
        "grounds": _grounds(fact),
        # Places the loser finished above the winner; negative means the result
        # was honoured.
        "lead": lead,
    }


def _team_entry(
    tb: TeamBase,
    rank: int,
    result: RankingResult,
    rank_of: Mapping[str, int],
    overridden: set[tuple[str, str]],
    prev_ranks: Mapping[str, int],
    prev_id: str | None,
) -> dict[str, Any]:
    wins = [f for k, f in result.edge_facts.items() if k[0] == tb.team]
    losses = [f for k, f in result.edge_facts.items() if k[1] == tb.team]

    previous = prev_ranks.get(tb.team)
    delta = (previous - rank) if previous is not None else None
    movement = {
        "previous_rank": previous,
        "delta": delta,
        "status": (
            "new"
            if previous is None
            else ("up" if (delta or 0) > 0 else "down" if (delta or 0) < 0 else "same")
        ),
        "has_previous": prev_id is not None,
    }

    regression = None
    if abs(tb.regression_adj) > 1e-9:
        regression = {
            "adjustment": tb.regression_adj,
            "notes": list(tb.regression_notes),
        }

    return {
        "rank": rank,
        "team": tb.team,
        "conference": tb.conference,
        "record": {
            "overall": tb.record.overall if tb.record else None,
            "conference": tb.record.conference if tb.record else None,
            "wins": tb.record.wins if tb.record else None,
            "losses": tb.record.losses if tb.record else None,
        },
        "base": {
            "rank": tb.base_rank,
            "resume_rank": tb.raw_rank,
            "score": tb.base_score,
            "raw_score": tb.base_raw,
            # The rank the SoR term actually used is OURS; ESPN's is published
            # beside it for comparison and is no longer an input.
            "sor_rank": tb.resume_rank if tb.resume_rank is not None else tb.sor_rank,
            "espn_sor_rank": tb.sor_rank,
            "sos_rank": tb.sos_rank,
            "market_rank": tb.market_rank,
            "ppa_rank": tb.ppa_rank,
            # The weights actually applied to THIS team, which differ from the
            # configured ones when an input is missing and the rest absorb it.
            "weights": {label: weight for label, weight, _rank in tb.base_terms},
            "missing": list(tb.missing_terms),
            "formula": tb.formula(),
        },
        "resume": {
            "rank": tb.resume_rank,
            "probability": tb.resume_prob,
            "expected_wins": tb.resume_expected_wins,
            "actual_wins": tb.resume_actual_wins,
            "espn_rank": tb.sor_rank,
            "schedule": [
                {
                    "opponent": g.opponent,
                    "won": g.won,
                    "site": "home" if g.site > 0 else ("away" if g.site < 0 else "neutral"),
                    "week": g.week,
                    "reference_win_probability": g.win_probability,
                }
                for g in result.resume.games.get(tb.team, ())
            ],
        },
        "cover": {
            "games": tb.resume_detail.get("cover_games"),
            "covers": tb.resume_detail.get("covers"),
            "mean_margin": tb.resume_detail.get("mean_cover_margin"),
            # `scored_margin` is what the adjustment is computed from: the mean
            # pulled toward zero by games/(games+4), so four loud afternoons
            # cannot read as a season-defining trait. Both are published because
            # the raw mean is the human-legible one and the shrunk one is the
            # one the arithmetic uses.
            "scored_margin": tb.resume_detail.get("shrunk_cover_margin"),
            "worst": tb.resume_detail.get("worst_cover"),
            "adjustment": tb.resume_components.get("cover"),
        },
        "market": {
            "rating": tb.market_rating,
            "rank": tb.market_rank,
            "games": tb.market_games,
        },
        "performance": {
            "rating": tb.ppa_rating,
            "rank": tb.ppa_rank,
            "unadjusted": tb.ppa_raw,
            "games": tb.ppa_games,
        },
        "resume_adjustment": {
            "total": tb.resume_adj,
            "components": dict(tb.resume_components),
            "detail": dict(tb.resume_detail),
        },
        "regression": regression,
        "fpi": {
            "rating": tb.fpi,
            "rank": tb.fpi_rank,
            "efficiencies": {
                "overall": tb.eff_overall,
                "offense": tb.eff_offense,
                "defense": tb.eff_defense,
                "special_teams": tb.eff_special,
            },
        },
        "resume_ranks": {
            "strength_of_record": tb.sor_rank,
            "strength_of_schedule": tb.sos_rank,
            "fpi": tb.fpi_resume_rank,
            "average_win_probability": tb.awp_rank,
            "remaining_strength_of_schedule": tb.rsos_rank,
            "game_control": tb.game_control_rank,
        },
        "h2h": {
            "wins_vs_pool": [
                _edge(f, rank_of, (f.winner, f.loser) in overridden, result.ordering)
                for f in sorted(wins, key=lambda f: rank_of.get(f.loser, 10**6))
            ],
            "losses_vs_pool": [
                _edge(f, rank_of, (f.winner, f.loser) in overridden, result.ordering)
                for f in sorted(losses, key=lambda f: rank_of.get(f.winner, 10**6))
            ],
        },
        "movement": movement,
        "cycle": result.team_cycle.get(tb.team),
        "placement": {
            "base_rank": tb.base_rank,
            "final_rank": rank,
            "drift": tb.base_rank - rank,
            "reasons": list(result.reasons.get(tb.team, [])),
        },
        "flags": sorted(
            f
            for f in (
                "cycle" if tb.team in result.team_cycle else "",
                "lifted" if tb.base_rank - rank > 0 else "",
                "dropped" if tb.base_rank - rank < 0 else "",
                "regressed" if regression else "",
            )
            if f
        ),
    }


#: Config paths blanked before the config is echoed into published JSON.
REDACTED_CONFIG_PATHS = (("source", "api_key"),)


def redacted_config(cfg: Config) -> dict[str, Any]:
    """The resolved config, with credentials blanked.

    `meta.config` is echoed into every published snapshot so the methodology
    page can never describe weights the ranking was not built with. The API key
    lives in that same config, so it has to be stripped here or it would be
    copied into rankings.json, every weekly snapshot, and the history files.
    """
    data = cfg.as_dict()
    for *parents, leaf in REDACTED_CONFIG_PATHS:
        node = data
        for part in parents:
            node = node.get(part) if isinstance(node, dict) else None
            if node is None:
                break
        if isinstance(node, dict) and node.get(leaf):
            node[leaf] = ""
    return data


def build_payload(
    result: RankingResult,
    dataset: Dataset,
    cfg: Config,
    generated_at: str,
    previous: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    weights = {
        "SoR": float(cfg["stage1.w_sor"]),
        "Mkt": float(cfg["stage1.w_market"]),
        "PPA": float(cfg["stage1.w_perf"]),
        "FPI": float(cfg["stage1.w_fpi"]),
        "SoS": float(cfg["stage1.w_sos"]),
    }
    live = {label: w for label, w in weights.items() if w}
    base_summary = " + ".join(f"{w:g} x {label}" for label, w in live.items()) or "no weighted terms"
    output_size = int(cfg["stage1.output_size"])

    rank_of = {t: i + 1 for i, t in enumerate(result.order)}
    overridden = set(result.ordering.violated)

    prev_id = None
    prev_ranks: dict[str, int] = {}
    if previous:
        prev_id = (previous.get("meta") or {}).get("snapshot_id")
        for row in previous.get("rankings") or []:
            if isinstance(row, Mapping) and row.get("team"):
                prev_ranks[str(row["team"])] = int(row.get("rank") or 0)

    rankings = [
        _team_entry(result.teams[t], i + 1, result, rank_of, overridden, prev_ranks, prev_id)
        for i, t in enumerate(result.order[:output_size])
    ]

    pool_tail = []
    if bool(cfg["output.include_pool_tail"]):
        for i, t in enumerate(result.order[output_size:], output_size + 1):
            tb = result.teams[t]
            pool_tail.append(
                {
                    "rank": i,
                    "team": tb.team,
                    "conference": tb.conference,
                    "record": tb.record.overall if tb.record else None,
                    "base_rank": tb.base_rank,
                    "sor_rank": tb.sor_rank,
                    "sos_rank": tb.sos_rank,
                    "market_rank": tb.market_rank,
                    "market_rating": tb.market_rating,
                    "ppa_rank": tb.ppa_rank,
                    "ppa_rating": tb.ppa_rating,
                    "fpi": tb.fpi,
                }
            )

    cycles = [
        {
            "cycle_id": c.cycle_id,
            "members": list(c.members),
            "member_ranks": [rank_of.get(m) for m in c.members],
            "size": c.size,
            "explanation": c.explanation,
            "edges": [
                _edge(f, rank_of, (f.winner, f.loser) in overridden, result.ordering) for f in c.edges
            ],
            "overridden": [f"{f.winner} over {f.loser}" for f in c.overridden],
        }
        for c in result.cycles
    ]

    overridden_results = [
        dict(
            _edge(result.edge_facts[k], rank_of, True, result.ordering),
            cycle=result.team_cycle.get(k[0]),
            reason=_override_reason(result.edge_facts[k]),
        )
        for k in result.ordering.violated
    ]

    payload: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "meta": {
            "generated_at": generated_at,
            "snapshot_id": result.snapshot_id,
            "previous_snapshot_id": prev_id,
            "season": {
                "year": result.year,
                "week": result.week,
                "season_type": result.season_type,
                "label": _week_label(result.week, result.season_type),
            },
            "source": {
                "synthetic": dataset.synthetic,
                "attribution": ATTRIBUTION,
                "endpoints": [
                    {
                        "path": p.path,
                        "params": dict(p.params),
                        "fetched_at": p.fetched_at,
                        "cache": p.cache,
                        "sha256": p.sha256,
                        "records": p.records,
                    }
                    for p in dataset.provenance
                ],
                "stale": any(p.cache == "stale" for p in dataset.provenance),
            },
            "config": redacted_config(cfg),
            "counts": dict(result.counts),
            "ratings": {
                "market": {
                    "teams_rated": len(result.market.ratings),
                    "lines_used": result.market.lines_used,
                    "home_field_points": result.market.home_field_points,
                    "schedule_groups": result.market.components,
                    "converged": result.market.converged,
                },
                "resume": {
                    "teams_rated": len(result.resume.probability),
                    "reference_rating": result.resume.reference_rating,
                },
                "performance": {
                    "teams_rated": len(result.performance.ratings),
                    "games_used": result.performance.games_used,
                    "home_edge": result.performance.home_edge,
                    "schedule_groups": result.performance.components,
                    "converged": result.performance.converged,
                },
            },
            "cost": {
                "total": result.ordering.cost,
                "overrides": result.ordering.violation_cost,
                "drift": result.ordering.drift_cost,
                "passes": result.ordering.passes,
            },
            "warnings": [
                {"code": w.code, "team": w.team, "detail": w.detail} for w in result.warnings
            ],
            "content_hash": "",
        },
        "rankings": rankings,
        "pool_tail": pool_tail,
        "cycles": cycles,
        "overridden_results": overridden_results,
        "regressions": [
            {
                "winner": e.winner,
                "loser": e.loser,
                "winner_base_rank": e.winner_base_rank,
                "loser_base_rank": e.loser_base_rank,
                "gap": e.gap,
                "winner_delta": e.winner_delta,
                "loser_delta": e.loser_delta,
                "week": e.week,
                "season_type": e.season_type,
                "score": e.score,
                "description": e.describe(),
            }
            for e in result.regressions
        ],
        "series_notes": [
            {
                "teams": list(n.teams),
                "meetings": n.meetings,
                "policy": n.policy,
                "detail": n.detail,
            }
            for n in result.series_notes
            if n.meetings > 1
        ],
        "skipped_games": [
            {
                "home": s.home,
                "away": s.away,
                "week": s.week,
                "season_type": s.season_type,
                "reason": s.reason,
            }
            for s in result.skipped
        ],
        "methodology": {
            "summary": (
                f"base = {base_summary}; "
                f"pool {cfg['stage1.pool_size']}; "
                f"H2H strength {cfg['stage4.strength']:g}, drift exponent {cfg['stage4.drift_exponent']:g}; "
                f"upset regression {'on' if cfg['stage3.enabled'] else 'off'}"
                f" (gap {cfg['stage3.gap']}, {cfg['stage3.strength']:g})"
            ),
            "stages": [
                "Stage 1 - rank every team by what it has achieved and how good it is: "
                + " + ".join(
                    f"{w:g} x {name}"
                    for label, name in (
                        ("SoR", "Strength of Record"),
                        ("Mkt", "market neutral-field rank"),
                        ("PPA", "opponent-adjusted points added per play"),
                        ("FPI", "FPI rank"),
                        ("SoS", "Strength of Schedule"),
                    )
                    for w in [live.get(label)]
                    if w
                )
                + " (all national ranks, lower is better).",
                "Stage 2 - adjust for how your losses actually look: the venue and margin of each defeat, the quality of your best win, and how comfortably you win.",
                f"Stage 3 - when a result spans more than {cfg['stage3.gap']} places, pull both teams {cfg['stage3.strength']:g} of the way to their midpoint; the upset says both were mis-rated.",
                "Stage 4 - reorder so as few head-to-head results as possible are contradicted, weighing each result by how convincing it was against how far a team would have to move.",
                "Stage 5 - report every contradiction loop and every overridden result, so nothing is hidden.",
            ],
            "link": "methodology.html",
        },
    }

    # Round BEFORE hashing. Floating-point summation order shifts the last
    # bits of a few values when the input arrives in a different order; the
    # published file is rounded anyway, so hashing the unrounded payload would
    # make the hash non-reproducible from the file it describes -- and would
    # make the workflow's no-op check fire on noise.
    payload = round_floats(payload, int(cfg["output.float_precision"]))
    payload["meta"]["content_hash"] = content_hash(payload)
    return payload


def _override_reason(fact: EdgeFact) -> str:
    from cfbrank.engine.provenance import override_reason

    return override_reason(fact)


def _week_label(week: int, season_type: str) -> str:
    return f"Week {week}" if season_type == "regular" else f"Postseason week {week}"


VOLATILE_META = ("generated_at", "content_hash")


def content_hash(payload: Mapping[str, Any]) -> str:
    """Hash of everything that is not a timestamp.

    Drives the commit no-op check and doubles as the site's cache-buster, so it
    must ignore when a run happened and whether a response came from cache.
    Computed on the rounded payload, so re-hashing a published file reproduces
    the value stored inside it.
    """
    import copy

    clone = copy.deepcopy(dict(payload))
    meta = clone.get("meta")
    if isinstance(meta, dict):
        for key in VOLATILE_META:
            meta.pop(key, None)
        source = meta.get("source")
        if isinstance(source, dict):
            for ep in source.get("endpoints") or []:
                if isinstance(ep, dict):
                    ep.pop("fetched_at", None)
                    ep.pop("cache", None)
            source.pop("stale", None)
    blob = json.dumps(clone, sort_keys=True, separators=(",", ":"), ensure_ascii=False, default=str)
    return "sha256:" + hashlib.sha256(blob.encode("utf-8")).hexdigest()


def validate_payload(payload: Mapping[str, Any]) -> list[str]:
    """Structural checks, hand-rolled so the package needs no jsonschema."""
    problems: list[str] = []

    if payload.get("schema_version") != SCHEMA_VERSION:
        problems.append(f"schema_version must be {SCHEMA_VERSION}")
    for key in ("meta", "rankings", "cycles", "overridden_results", "methodology"):
        if key not in payload:
            problems.append(f"missing top-level key: {key}")
    meta = payload.get("meta") or {}
    for key in ("snapshot_id", "season", "counts", "content_hash"):
        if key not in meta:
            problems.append(f"missing meta.{key}")

    rankings = payload.get("rankings") or []
    if not rankings:
        problems.append("rankings is empty")
    ranks = [r.get("rank") for r in rankings]
    if ranks != list(range(1, len(rankings) + 1)):
        problems.append(f"ranks must be contiguous 1..{len(rankings)}, got {ranks[:5]}...")
    teams = [r.get("team") for r in rankings]
    if len(set(teams)) != len(teams):
        problems.append("duplicate team in rankings")

    cycle_ids = {c.get("cycle_id") for c in payload.get("cycles") or []}
    for r in rankings:
        cid = r.get("cycle")
        if cid is not None and cid not in cycle_ids:
            problems.append(f"{r.get('team')} references unknown cycle {cid}")
        for field in ("base", "fpi", "placement", "h2h", "movement"):
            if field not in r:
                problems.append(f"{r.get('team')} missing {field}")

    blob = json.dumps(payload, default=str)
    for bad in ("NaN", "Infinity", "-Infinity"):
        if bad in blob:
            problems.append(f"payload contains {bad}")

    return problems
