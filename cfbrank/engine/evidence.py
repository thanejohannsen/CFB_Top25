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

**This file is only half the question.** Conviction is about the game itself and
never changes once it is played. What has happened to the two teams SINCE is a
separate matter, and it lives in `engine/grounds.py`: it forgives a share of this
price and -- the part that used to be missing entirely -- sets how far apart the
two may sit once the result is set aside.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from typing import Mapping, Sequence

from cfbrank.engine.grounds import Grounds
from cfbrank.engine.stats import clamp, half_life_weight, zscorer
from cfbrank.models import GameResult, week_index
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
    # What has happened SINCE the game -- see engine/grounds.py. None when the
    # grounds stage is switched off, in which case stage 4 reverts to pricing the
    # override once and letting the distance be free.
    grounds: Grounds | None = None

    @property
    def pair(self) -> tuple[str, str]:
        return (self.winner, self.loser)

    @property
    def price(self) -> float:
        """What going against this result actually costs stage 4.

        `weight` is the price the game itself earned; the grounds for setting it
        aside forgive a share of that, because a win the winner has since
        undercut is a weaker thing to rank against than the same win from a team
        still playing well.
        """
        return self.weight * (1.0 - (self.grounds.relief if self.grounds else 0.0))

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


def recency_weight(
    age_weeks: float, half_life: float, floor: float
) -> float:
    """How much a result `age_weeks` old still counts.

    A true half-life in weeks, not a position in the list of game dates. The old
    shape scaled by index into the sorted distinct dates, so the same September
    game decayed differently depending on how many distinct dates happened to be
    in the pool -- which is not a property of football.
    """
    return half_life_weight(age_weeks, half_life, floor)


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
    grounds: Mapping[tuple[str, str], Grounds] | None = None,
) -> dict[tuple[str, str], EdgeFact]:
    """Score every result. z-scores are taken over the whole result set, so
    conviction is comparable across cycles and stable week to week.

    `quality` is one rating per team -- the market's neutral-field points where
    they exist, FPI where they do not. Only the *gap* between two teams is read,
    so the units do not matter as long as higher is better.

    `home_field_points` overrides the configured fallback with a value measured
    from this season's lines. `grounds` carries what has happened since each game
    (engine/grounds.py); it is attached here so every consumer -- stage 4, the
    cycle reports, the published payload -- reads one object per result.
    """
    if not results:
        return {}

    cfg_hfp = float(cfg.get("home_field_points", 2.5))
    hfp = cfg_hfp if home_field_points is None or not cfg.get("fit_home_field", True) else float(home_field_points)
    cap = float(cfg.get("margin_cap", 28))
    floor = float(cfg.get("recency_floor", 0.15))
    hl_good = float(cfg.get("recency_half_life_good", 8.0))
    hl_bad = float(cfg.get("recency_half_life_bad", 6.0))
    w_margin = float(cfg.get("w_margin", 0.50))
    w_gap = float(cfg.get("w_rating_gap", 0.30))
    w_rec = float(cfg.get("w_recency", 0.20))
    w_common = float(cfg.get("w_common_opponents", 0.15))

    latest = max(week_index(r.season_type, r.week) for r in results)

    raw = []
    for r in results:
        adj = location_adjusted_margin(r, hfp, cap)
        gap = float(quality.get(r.winner, 0.0)) - float(quality.get(r.loser, 0.0))
        shared, diff = common_opponent_diff(r.winner, r.loser, opponent_results, hfp, cap)
        raw.append((r, adj, gap, shared, diff, latest - week_index(r.season_type, r.week)))

    z_margin = zscorer([x[1] for x in raw])
    z_gap = zscorer([x[2] for x in raw])
    z_common = zscorer([x[4] for x in raw])

    # Quality WITHOUT the recency term, so the half-life a result gets is not a
    # function of the age it is about to be judged on. A good win keeps its
    # relevance longer than a bad one; the threshold is the board's own median,
    # so "good" means good for this week's set of results.
    def quality_of(adj, gap, shared, diff):
        return (
            w_margin * z_margin(adj)
            + w_gap * z_gap(gap)
            + (w_common * z_common(diff) if shared else 0.0)
        )

    qualities = sorted(quality_of(x[1], x[2], x[3], x[4]) for x in raw)
    mid = len(qualities) // 2
    median_quality = (
        qualities[mid] if len(qualities) % 2 else (qualities[mid - 1] + qualities[mid]) / 2.0
    )

    facts: dict[tuple[str, str], EdgeFact] = {}
    for r, adj, gap, shared, diff, age in raw:
        good = quality_of(adj, gap, shared, diff) >= median_quality
        rec = recency_weight(age, hl_good if good else hl_bad, floor)
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
            grounds=(grounds or {}).get((r.winner, r.loser)),
        )

    # Turn conviction into a price. `conviction_floor` is an absolute z level,
    # not the weakest result on this board, so the cheapest result to override is
    # whichever one is genuinely unconvincing -- and in a week where every result
    # was emphatic, nothing is cheap.
    w_floor = float(cfg.get("weight_floor", 1.0))
    z_floor = float(cfg.get("conviction_floor", -2.0))
    return {
        key: replace(f, weight=w_floor + max(0.0, f.conviction - z_floor))
        for key, f in facts.items()
    }
