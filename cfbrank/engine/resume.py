"""Stage 2 -- the resume adjustment: "how do your losses actually look?"

Strength of Record already accounts for who you played, but it is blind to the
margin and the venue of a loss. A three-point road loss to the eventual #2 and
a 24-point home loss to a middling team can produce the same SoR, and they are
not the same result.

Four adjustments, all in base-score rank points, positive meaning worse:

  loss_quality  per loss: half the location-adjusted losing margin, half the
                weakness of who beat you
  best_win      how good the best team you beat was
  game_control  the feed's average in-game win probability rank
  cover         how the team played against the number the market set

`cover` is the one that reads a team's performance rather than its results. A
resume says who you beat; it cannot say whether you looked like you meant it.
Beating a bad team by 17 when you were favoured by 28 is a miss, and beating a
good team by 1 when you were favoured by 3 is par — see `engine.cover`, which
also explains why this belongs here, modifying a resume, and never in the base.
"""

from __future__ import annotations

from typing import Mapping, Sequence

from cfbrank.engine.base_score import TeamBase, rerank
from cfbrank.engine.cover import CoverRecord
from cfbrank.engine.evidence import location_adjusted_margin
from cfbrank.engine.stats import mean, zscorer
from cfbrank.models import GameResult

# An unrated or FCS opponent is treated as worse than every ranked team, by
# this many positions past the bottom of the board.
UNRATED_PENALTY = 20
# Scales an opponent's rank (1..N) into roughly the same units as a point margin.
OPPONENT_SCALE = 40.0


def _loss_badness(
    r: GameResult, opponent_rank: int, team_count: int, hfp: float, cap: float
) -> float:
    """How bad one loss looks, in rank points. Higher is worse.

    The adjusted margin is the winner's, so it already carries the venue in the
    right direction for the loser: losing at home scores worse than losing the
    same game on the road.
    """
    opponent_weakness = opponent_rank / max(team_count, 1) * OPPONENT_SCALE
    return 0.5 * location_adjusted_margin(r, hfp, cap) + 0.5 * opponent_weakness


def apply_resume_adjustment(
    teams: Sequence[TeamBase],
    results: Sequence[GameResult],
    cfg: Mapping[str, object],
    evidence_cfg: Mapping[str, object],
    covers: Mapping[str, CoverRecord] | None = None,
    scale: float = 1.0,
) -> None:
    """Mutates each TeamBase's resume_adj, then re-ranks. Idempotent per call.

    `scale` is `stage1.w_adjust`: one dial for how loudly this whole stage speaks,
    rather than four weights that have to be kept in proportion by hand. It is
    applied to each COMPONENT, not just the total, because the components are
    published and rendered in the site's panel -- halving the total while leaving
    the parts alone would make that panel stop adding up.
    """
    w_loss = float(cfg.get("w_loss_quality", 3.0))  # type: ignore[arg-type]
    w_best = float(cfg.get("w_best_win", 1.0))  # type: ignore[arg-type]
    w_gc = float(cfg.get("w_game_control", 0.5))  # type: ignore[arg-type]
    w_cover = float(cfg.get("w_cover", 4.0))  # type: ignore[arg-type]
    covers = covers or {}
    hfp = float(evidence_cfg.get("home_field_points", 2.5))  # type: ignore[arg-type]
    cap = float(evidence_cfg.get("margin_cap", 28))  # type: ignore[arg-type]

    by_team = {tb.team: tb for tb in teams}
    # Stage-1 order is the yardstick for opponent strength; using the evolving
    # score here would make the stage depend on its own output.
    rank_of = {tb.team: tb.raw_rank for tb in teams}
    n = len(teams)
    worst = n + UNRATED_PENALTY

    losses: dict[str, list[tuple[GameResult, float]]] = {tb.team: [] for tb in teams}
    best_win: dict[str, int] = {}

    for r in results:
        if r.loser in by_team:
            opp_rank = rank_of.get(r.winner, worst)
            losses[r.loser].append((r, _loss_badness(r, opp_rank, n, hfp, cap)))
        if r.winner in by_team:
            opp_rank = rank_of.get(r.loser, worst)
            if opp_rank < best_win.get(r.winner, worst + 1):
                best_win[r.winner] = opp_rank

    loss_raw = {t: mean([b for _, b in v]) for t, v in losses.items() if v}
    z_loss = zscorer(sorted(loss_raw.values()))
    best_raw = {tb.team: float(best_win.get(tb.team, worst)) for tb in teams}
    z_best = zscorer([best_raw[tb.team] for tb in teams])
    gc_raw = {tb.team: float(tb.game_control_rank or worst) for tb in teams}
    z_gc = zscorer([gc_raw[tb.team] for tb in teams])

    # z-scored across the whole board, so "beat the number" means beat it by
    # more than the rest of the country did, not merely by something positive.
    cover_raw = {tb.team: covers[tb.team].shrunk_margin for tb in teams if tb.team in covers}
    z_cover = zscorer(sorted(cover_raw.values()))

    for tb in teams:
        components = {
            # An undefeated team has no losses to judge, so it takes no penalty.
            "loss_quality": w_loss * z_loss(loss_raw[tb.team]) if tb.team in loss_raw else 0.0,
            "best_win": w_best * z_best(best_raw[tb.team]),
            "game_control": w_gc * z_gc(gc_raw[tb.team]),
            # Negative is a credit: beating the number lowers the base score,
            # which is better. A team with no lined games takes no adjustment
            # either way rather than being treated as average.
            "cover": -w_cover * z_cover(cover_raw[tb.team]) if tb.team in cover_raw else 0.0,
        }
        components = {k: scale * v for k, v in components.items()}
        tb.resume_components = components
        tb.resume_adj = sum(components.values())
        rec = covers.get(tb.team)
        tb.resume_detail = {
            "losses_considered": len(losses[tb.team]),
            "mean_loss_badness": loss_raw.get(tb.team),
            "best_win_opponent_base_rank": best_win.get(tb.team),
            "game_control_rank": tb.game_control_rank,
            "cover_games": rec.played if rec else 0,
            "covers": rec.covers if rec else None,
            "mean_cover_margin": rec.mean_margin if rec else None,
            # The number the adjustment is actually computed from. Publishing
            # only the raw mean made the panel irreconcilable: Northwestern
            # showed +17.81 beside an adjustment scored off +8.91.
            "shrunk_cover_margin": rec.shrunk_margin if rec else None,
            "worst_cover": (
                f"{rec.worst.margin:+.0f} vs {rec.worst.opponent}"
                if rec and rec.worst
                else None
            ),
        }

    rerank(teams)
