"""How convincing was this win?

Each head-to-head result gets a single *conviction* score. In stage 4 that score
is the price of ranking against the result, so the results that end up overridden
are by construction the least convincing ones -- the "drop the weakest win" rule,
applied across the whole board rather than per cycle.

Four signals, all configurable:
  margin           location-adjusted and capped
  rating_gap       does the result agree with the power ratings?
  recency          later games say more about current strength
  common_opponents how did the two fare against the teams they both played?

**The floor is load-bearing.** Conviction is a blend of z-scores, so it is
centred near zero and runs roughly -2.5..+2.5; turning it into a price means
adding a floor, and the floor decides how cheap the weakest result on the board
is to ignore. This used to be `0.10 + (conviction - lowest_on_the_board)`, which
had two bugs with the same root: the price was *relative*, so whichever game
happened to be least convincing was always pinned at 0.10 -- about one
seventeenth of a typical result -- however real that game was. A 2025 Oklahoma
State win over Oregon was overridden at exactly that floor. Now the floor is an
absolute conviction level (`conviction_floor`), so a weak result costs
`weight_floor` because it is genuinely weak, not because something had to be last,
and a convincing result costs maybe three times that rather than seventeen.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Mapping, Sequence

from cfbrank.engine.stats import clamp, zscorer
from cfbrank.models import GameResult
from cfbrank.normalize import sort_key


@dataclass(frozen=True, slots=True)
class EdgeFact:
    winner: str
    loser: str
    week: int
    season_type: str
    winner_points: int
    loser_points: int
    margin: int
    adj_margin: float
    site: str
    neutral_site: bool
    rating_gap: float
    common_opponents: tuple[str, ...]
    common_diff: float
    recency: float
    conviction: float
    weight: float
    components: Mapping[str, float] = field(default_factory=dict)

    @property
    def pair(self) -> tuple[str, str]:
        return (self.winner, self.loser)

    @property
    def score(self) -> str:
        return f"{self.winner_points}-{self.loser_points}"


def location_adjusted_margin(r: GameResult, home_field_points: float, margin_cap: float) -> float:
    """Margin, capped, then shifted to what it implies on neutral ground.

    Winning on the road is worth more than the same margin at home. With a
    2.5-point adjustment a one-point home win scores negative: home field alone
    is worth more than the margin, so the result is weak evidence that the winner
    is actually the better team.

    The 2.5 is only a fallback. The market prices home field every week and
    `engine.market` solves for it, so the pipeline passes that measured value
    through -- which on both seasons checked lands at about +2.35, close enough
    to the old guess to be reassuring rather than embarrassing.
    """
    capped = clamp(float(r.margin), -margin_cap, margin_cap)
    if r.neutral_site:
        return capped
    return capped + (home_field_points if not r.winner_was_home else -home_field_points)


def recency_weight(order_index: int, max_index: int, floor: float) -> float:
    """Scale a result by how late in the season it happened, never below `floor`."""
    if max_index <= 0:
        return 1.0
    frac = clamp(order_index / max_index, 0.0, 1.0)
    return floor + (1.0 - floor) * frac


def common_opponent_diff(
    winner: str,
    loser: str,
    opponent_results: Mapping[str, Mapping[str, GameResult]],
    home_field_points: float,
    margin_cap: float,
) -> tuple[tuple[str, ...], float]:
    """Mean edge the winner holds over the loser against shared opponents.

    Positive means the winner also fared better against the teams they both
    played, which corroborates the head-to-head result.
    """
    w_opps = opponent_results.get(winner, {})
    l_opps = opponent_results.get(loser, {})
    shared = sorted((set(w_opps) & set(l_opps)) - {winner, loser}, key=sort_key)
    if not shared:
        return (), 0.0

    diffs = []
    for opp in shared:
        diffs.append(
            _signed_margin(w_opps[opp], winner, home_field_points, margin_cap)
            - _signed_margin(l_opps[opp], loser, home_field_points, margin_cap)
        )
    return tuple(shared), sum(diffs) / len(diffs)


def _signed_margin(r: GameResult, team: str, home_field_points: float, margin_cap: float) -> float:
    adj = location_adjusted_margin(r, home_field_points, margin_cap)
    return adj if r.winner == team else -adj


def score_edges(
    results: Sequence[GameResult],
    quality: Mapping[str, float],
    opponent_results: Mapping[str, Mapping[str, GameResult]],
    cfg: Mapping[str, float],
    home_field_points: float | None = None,
) -> dict[tuple[str, str], EdgeFact]:
    """Score every result. z-scores are taken over the whole result set, so
    conviction is comparable across cycles and stable week to week.

    `quality` is one rating per team -- the market's neutral-field points where
    they exist, FPI where they do not. Only the *gap* between two teams is read,
    so the units do not matter as long as higher is better.

    `home_field_points` overrides the configured fallback with a value measured
    from this season's lines.
    """
    if not results:
        return {}

    cfg_hfp = float(cfg.get("home_field_points", 2.5))
    hfp = cfg_hfp if home_field_points is None or not cfg.get("fit_home_field", True) else float(home_field_points)
    cap = float(cfg.get("margin_cap", 28))
    floor = float(cfg.get("recency_floor", 0.25))
    w_margin = float(cfg.get("w_margin", 0.50))
    w_gap = float(cfg.get("w_rating_gap", 0.30))
    w_rec = float(cfg.get("w_recency", 0.20))
    w_common = float(cfg.get("w_common_opponents", 0.15))

    ordered_keys = sorted({r.order_key for r in results})
    index_of = {k: i for i, k in enumerate(ordered_keys)}
    max_index = max(len(ordered_keys) - 1, 1)

    raw = []
    for r in results:
        adj = location_adjusted_margin(r, hfp, cap)
        gap = float(quality.get(r.winner, 0.0)) - float(quality.get(r.loser, 0.0))
        shared, diff = common_opponent_diff(r.winner, r.loser, opponent_results, hfp, cap)
        raw.append((r, adj, gap, shared, diff, recency_weight(index_of[r.order_key], max_index, floor)))

    z_margin = zscorer([x[1] for x in raw])
    z_gap = zscorer([x[2] for x in raw])
    z_common = zscorer([x[4] for x in raw])

    facts: dict[tuple[str, str], EdgeFact] = {}
    for r, adj, gap, shared, diff, rec in raw:
        components = {
            "margin": w_margin * z_margin(adj),
            "rating_gap": w_gap * z_gap(gap),
            "recency": w_rec * rec,
            "common_opponents": w_common * z_common(diff) if shared else 0.0,
        }
        facts[(r.winner, r.loser)] = EdgeFact(
            winner=r.winner,
            loser=r.loser,
            week=r.week,
            season_type=r.season_type,
            winner_points=r.winner_points,
            loser_points=r.loser_points,
            margin=r.margin,
            adj_margin=adj,
            site=r.site,
            neutral_site=r.neutral_site,
            rating_gap=gap,
            common_opponents=shared,
            common_diff=diff,
            recency=rec,
            conviction=sum(components.values()),
            weight=0.0,  # filled in below, once the range is known
            components=components,
        )

    # Turn conviction into a price. `conviction_floor` is an absolute z level,
    # not the weakest result on this board, so the cheapest result to override is
    # whichever one is genuinely unconvincing -- and in a week where every result
    # was emphatic, nothing is cheap.
    w_floor = float(cfg.get("weight_floor", 1.0))
    z_floor = float(cfg.get("conviction_floor", -2.0))
    return {
        key: EdgeFact(
            **{**_as_dict(f), "weight": w_floor + max(0.0, f.conviction - z_floor)}
        )
        for key, f in facts.items()
    }


def _as_dict(f: EdgeFact) -> dict:
    return {
        "winner": f.winner, "loser": f.loser, "week": f.week, "season_type": f.season_type,
        "winner_points": f.winner_points, "loser_points": f.loser_points, "margin": f.margin,
        "adj_margin": f.adj_margin, "site": f.site, "neutral_site": f.neutral_site,
        "rating_gap": f.rating_gap, "common_opponents": f.common_opponents, "common_diff": f.common_diff,
        "recency": f.recency, "conviction": f.conviction, "weight": f.weight,
        "components": f.components,
    }
