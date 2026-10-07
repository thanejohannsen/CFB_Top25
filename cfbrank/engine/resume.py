"""Stage 2 -- the resume adjustment: "how do your losses actually look?"

Strength of Record already accounts for who you played, but it is blind to the
margin and the venue of a loss. A three-point road loss to the eventual #2 and
a 24-point home loss to a middling team can produce the same SoR, and they are
not the same result.

Four adjustments, all in base-score rank points, positive meaning worse:

  loss_quality  per loss: half the location-adjusted losing margin, half the
                weakness of who beat you. ONE-SIDED -- see below
  best_win      how good the best team you beat was
  game_control  the feed's average in-game win probability rank
  cover         how the team played against the number the market set

`loss_quality` is the only one on a one-sided scale, and it has to be. The other
three are two-sided because every team has a value for them: beat nobody and you
take the floor on `best_win`, so being below average is a genuine credit. A team
with no losses has no value at all, which is a different thing, and scoring the
rest against the mean of the teams that DID lose made that mean the yardstick --
so a tidy loss beat no loss. A 16-0 Indiana finished last in the 2025 top 25 on
this component. `penalty_scaler` fixes it: zero badness is the best score
available, and no loss scores zero.

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
from cfbrank.engine.resume_strength import reference_rating
from cfbrank.engine.stats import mean, penalty_scaler, zscorer
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

    # Opponent QUALITY in points, for the best-win credit. A rank cannot carry
    # that credit: see the comment on `best_raw` below.
    market = {tb.team: tb.market_rating for tb in teams if tb.market_rating is not None}
    place = int(cfg.get("best_win_place", 25))  # type: ignore[arg-type]
    # "A top-25 team" means the project's existing definition of one -- the
    # place-th best market rating, the same yardstick the resume walks its
    # reference team through.
    top_rating = reference_rating(market, place) if market else 0.0

    losses: dict[str, list[tuple[GameResult, float]]] = {tb.team: [] for tb in teams}
    best_win: dict[str, int] = {}
    best_quality: dict[str, tuple[float, str]] = {}

    for r in results:
        if r.loser in by_team:
            opp_rank = rank_of.get(r.winner, worst)
            losses[r.loser].append((r, _loss_badness(r, opp_rank, n, hfp, cap)))
        if r.winner in by_team:
            opp_rank = rank_of.get(r.loser, worst)
            if opp_rank < best_win.get(r.winner, worst + 1):
                best_win[r.winner] = opp_rank
            q = market.get(r.loser)
            if q is not None and q > best_quality.get(r.winner, (float("-inf"), ""))[0]:
                best_quality[r.winner] = (q, r.loser)

    loss_raw = {t: mean([b for _, b in v]) for t, v in losses.items() if v}
    # NOT z-scored: this is a penalty, and badness is already anchored at zero.
    # Zero badness is the best loss available -- a one-point road defeat to the
    # best team in the country -- so the scale needs a divisor, not a centre.
    # `sorted` is load-bearing for byte-stability: it pins the float summation
    # order inside `pstdev`.
    scale_loss = penalty_scaler(sorted(loss_raw.values()))
    # A ONE-SIDED CREDIT, measured in rating points past the top-25 bar.
    #
    # It used to be the opponent's RANK, z-scored over all 138 teams, and that
    # could not tell a good win from a great one. Rank is not linear in quality --
    # #1 to #10 is a chasm, #100 to #110 is nothing -- and the population is
    # dominated by the 20 teams pinned at the no-good-win sentinel, giving mean
    # 88 and sd 44. So beating #6 and beating #16 differed by 0.25 sd: Texas
    # beating Ohio State outscored Notre Dame beating Wisconsin by 0.28 rank
    # points, half of ONE place of SoR rank. By rating the same pair differ by
    # 1.58. The opponent's base score is no better (0.33), because base_raw is
    # itself a weighted sum of ranks and inherits the same non-linearity.
    #
    # Anchored at the top-25 bar rather than centred, for the reason
    # `loss_quality` is: no qualifying win scores 0, and 0 must be the FLOOR of a
    # credit, not its middle. Centring would hand a positive (a penalty) to
    # whichever team's best scalp happened to be the weakest of the qualifying
    # ones -- worse than beating nobody good at all. Beating exactly the 25th
    # team also scores ~0, so there is no cliff at the bar.
    best_raw = {
        tb.team: max(0.0, best_quality.get(tb.team, (float("-inf"), ""))[0] - top_rating)
        for tb in teams
    }
    # A FIXED scale, not the board's spread. `penalty_scaler` is wrong here: only
    # 18 of 138 teams have a qualifying win, so the spread is set by the 120
    # zeros and collapses to 2.31, which inflated Texas's one win over Ohio State
    # to -9.27 rank points -- larger than the whole rest of the adjustment, while
    # the #1 team got nothing.
    #
    # The natural scale is the bar itself: from the top-25 rating up to the best
    # rating on the board. The credit then runs 0 (beat exactly the 25th team) to
    # 1 (beat the best team in the country), so the most this term can ever be
    # worth is w_best_win, and it does not move with how many teams happen to
    # qualify this week.
    best_span = (max(market.values()) - top_rating) if market else 0.0
    scale_best = (
        (lambda x: x / best_span) if best_span > 1e-9 else (lambda _x: 0.0)
    )
    gc_raw = {tb.team: float(tb.game_control_rank or worst) for tb in teams}
    z_gc = zscorer([gc_raw[tb.team] for tb in teams])

    # z-scored across the whole board, so "beat the number" means beat it by
    # more than the rest of the country did, not merely by something positive.
    cover_raw = {tb.team: covers[tb.team].shrunk_margin for tb in teams if tb.team in covers}
    z_cover = zscorer(sorted(cover_raw.values()))

    for tb in teams:
        components = {
            # Zero for an undefeated team, and on this scale zero is the BEST
            # score available rather than a middling one. A good loss costs
            # nearly nothing; it can never pay.
            "loss_quality": w_loss * scale_loss(loss_raw[tb.team]) if tb.team in loss_raw else 0.0,
            # Negated: beating a good team is a credit, and 0 means no
            # qualifying win, which is the worst outcome rather than an average
            # one.
            "best_win": -w_best * scale_best(best_raw[tb.team]),
            "game_control": w_gc * z_gc(gc_raw[tb.team]),
            # Negative is a credit: beating the number lowers the base score,
            # which is better. Two-sided on purpose -- covering is meant to pay.
            # A team with no lined games gets 0.0, which on a centred scale means
            # "treated as board-average", not "no adjustment"; dead code today
            # because every rateable team has a line, but do not mistake it for
            # the `loss_quality` fix.
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
            # What the credit is actually computed from: the strongest top-25
            # team beaten, and how far past the bar they were.
            "best_win_opponent": best_quality.get(tb.team, (None, None))[1],
            "best_win_opponent_rating": best_quality.get(tb.team, (None, None))[0],
            "best_win_over_bar": best_raw.get(tb.team),
            "game_control_rank": tb.game_control_rank,
            "cover_games": rec.played if rec else 0,
            "covers": rec.covers if rec else None,
            # What happened, uncorrected -- the legible one the site shows.
            "mean_cover_margin": rec.raw_mean_margin if rec else None,
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
