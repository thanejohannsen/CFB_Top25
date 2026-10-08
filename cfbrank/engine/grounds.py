"""What has happened SINCE the game -- the grounds for setting a result aside.

Stage 4 asks two separate questions about a head-to-head result. `evidence.py`
answers the first: how convincing was the game itself? Margin, venue, whether
the power ratings agree, how the two fared against shared opponents. That is a
fact about one afternoon and it never changes again.

This module answers the second: what have the two teams DONE since? A win is a
claim about who was better on that day, and weeks of football afterwards can
undermine the claim without touching the box score. Two ways:

  * **Form** -- the winner has lost since, and lost more often than the team it
    beat. "I beat you in September" is a thinner argument from a team that has
    dropped two games since than from one that has not lost at all.
  * **Resume** -- the loser has since beaten better teams than the winner has.
    This ground STRENGTHENS WITH TIME on purpose. One good win the week after
    proves little; six weeks of them is a different team.

## What the grounds buy is a LICENCE, measured in places

This is the part that was missing, and it was missing in a way that showed. The
old stage 4 priced *whether* to override and then let the distance be free: pay
`strength x weight` once and the two teams could end up anywhere. On 2026 week 5
that published **Missouri 45-17 over Florida on the Saturday being ranked, with
Florida at #9 and Missouri at #20** -- eleven places apart, over a 28-point
result, with literally nothing having happened in between, because the game was
that week. Oklahoma State's week-2 win over Oregon came out fifteen places apart
on the same board.

So the licence: `allowance` is how many places the loser may sit above the
winner, and stage 4 charges `gap_weight x (places past the licence) ^
gap_exponent` on top of the override price. With no grounds at all the licence is
`base_places` -- the two may swap, because the base order is allowed to disagree
about near-neighbours, but they stay near-neighbours. Grounds widen it.

Three terms widen it, and the units are places so they can be read directly:

    allowance = base_places
              + per_net_loss     x slide          # form
              + per_rating_point x ascent x ramp  # resume, ramped by age
              + per_week         x weeks_since    # age alone
                                                  (capped at max_places)

`per_week` is the owner's "this gap can increase with time" taken on its own: a
week-1 result constrains a week-12 board less tightly whatever else happened.
It overlaps with `evidence.recency`, which already makes an old result cheaper to
override -- and the overlap is deliberate rather than overlooked. Recency sets
the PRICE of going against a result; this sets the DISTANCE allowed once you
have. A ranking can reasonably make an old result cheap to set aside while still
refusing to put the two teams on opposite ends of the board.

## Why the numbers are continuous and the categories are only labels

The owner asked for categories of severity. `category` and `severity` are
published for exactly that, and the site renders them -- but they are derived
FROM the allowance, never the other way round. Bands that set the allowance
would make it a step function of the evidence, and stage 4 is a local search over
a cost surface: a step is a cliff two teams can straddle, where one more rating
point of subsequent resume jumps the licence four places and the whole board
rearranges. Continuous in, labels out.

## Both grounds are counted over EVERY game, not just pool games

A loss to a team nobody ranks is still a loss, and the resume credit for beating
a good non-pool team is real. The edges being judged come from the pool, the
evidence about them does not.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from typing import Mapping, Sequence

from cfbrank.engine.stats import clamp, mean
from cfbrank.models import GameResult, week_index

Edge = tuple[str, str]

# Where the severity labels cut, as fractions of the licence span -- the places
# between `base_places` (no grounds) and `max_places` (everything). Fractions
# rather than absolute places so the labels follow the config instead of
# disagreeing with it when a dial moves. Labels only: see the module docstring.
SEVERITY_BANDS = ((1.0 / 3.0, "slight"), (2.0 / 3.0, "clear"))
SEVERITY_TOP = "decisive"
SEVERITY_NONE = "none"


@dataclass(frozen=True, slots=True)
class Grounds:
    """Why this result may be set aside, and how far apart that lets the two sit."""

    category: str          # "none" | "age" | "form" | "resume" | "both"
    severity: str          # "none" | "slight" | "clear" | "decisive"
    weeks_since: float     # weeks between the game and the week being ranked
    winner_losses: int     # the winner's losses AFTER this game
    loser_losses: int      # the loser's losses after it
    slide: float           # net subsequent losses the winner has to answer for
    winner_credit: float   # rating points of subsequent win quality, winner
    loser_credit: float    # ... and loser
    ascent: float          # how far the loser's subsequent resume leads, in rating points
    ramp: float            # how much of the resume ground has come into force yet
    allowance: float       # places the loser may sit above the winner
    relief: float          # share of the override price these grounds forgive

    @property
    def licensed(self) -> bool:
        return self.category not in ("none", "age")


def _rating(quality: Mapping[str, float], team: str, bar: float) -> float:
    """A team's rating, or the board's average where the market has no opinion."""
    value = quality.get(team)
    return bar if value is None else float(value)


def _credit(
    wins: Sequence[GameResult], after: float, quality: Mapping[str, float], bar: float
) -> float:
    """Average rating points past `bar` of the wins picked up after week `after`.

    Anchored at zero rather than centred, for the reason `best_win` and
    `loss_quality` are: a team with no good wins since has *nothing* here, and
    nothing must be the bottom of the scale rather than the middle of it. Beating
    an average team is worth 0, the same as beating nobody -- which is right,
    because "mediocre wins" and "no wins" are the same argument.

    **The MEAN, not the sum, and that was measured.** Summing conflates quality
    with volume, and volume grows every week: on the completed 2025 season the
    sum reached 73 rating points for a week-8 result, which multiplied past
    `max_places` on its own and pinned 15 of 111 results at the cap. Every old
    result then looked equally well undermined and the term stopped
    discriminating exactly where the contradictions are. A per-win mean is also
    the plainer reading of the owner's "big wins while Team A has mediocre wins":
    it is about what the wins were worth, not how many there were. Growth with
    time is left to `ramp`, where it is explicit and dialable.

    Losses are not in the denominator. They belong to the form ground and
    counting them here would price the same slide twice.

    An opponent the market has no rating for counts as exactly average, so an FCS
    cupcake earns 0. That is conservative for a lightly-lined FBS team and the
    conservative direction is the right one: an unknown opponent should not widen
    the licence.
    """
    scores = sorted(
        max(0.0, _rating(quality, r.loser, bar) - bar)
        for r in wins
        if week_index(r.season_type, r.week) > after
    )
    # SORTED before the mean, for the reason `at_least()` and `engine/cover.py`
    # sort: summing floats in input order makes the last bits a function of how
    # the games happened to arrive, which can flip a rank and break
    # byte-stability. tests/test_pipeline.py::TestDeterminism shuffles
    # `dataset.games` directly and would catch it.
    return mean(scores)


def _severity(allowance: float, base: float, cap: float) -> str:
    if allowance <= base + 1e-9:
        return SEVERITY_NONE
    span = cap - base
    if span <= 0:
        return SEVERITY_TOP
    share = (allowance - base) / span
    for edge, label in SEVERITY_BANDS:
        if share < edge:
            return label
    return SEVERITY_TOP


def compute(
    edges: Sequence[GameResult],
    all_results: Sequence[GameResult],
    quality: Mapping[str, float],
    cfg: Mapping[str, object],
) -> dict[Edge, Grounds]:
    """Grounds for every result in `edges`, judged on everything in `all_results`.

    `edges` are the pool results stage 4 will order (already collapsed, so one
    per pair). `quality` is one rating per team, higher is better -- the market's
    neutral-field points, over the whole board rather than just the pool, because
    a win over a good unranked team still counts here.
    """
    if not edges or not bool(cfg.get("enabled", True)):
        return {}

    base = float(cfg.get("base_places", 2.0))             # type: ignore[arg-type]
    per_loss = float(cfg.get("per_net_loss", 4.0))        # type: ignore[arg-type]
    offset = float(cfg.get("loss_offset", 0.5))           # type: ignore[arg-type]
    per_point = float(cfg.get("per_rating_point", 0.3))   # type: ignore[arg-type]
    ramp_weeks = float(cfg.get("ramp_weeks", 6.0))        # type: ignore[arg-type]
    per_week = float(cfg.get("per_week", 0.4))            # type: ignore[arg-type]
    cap = float(cfg.get("max_places", 20.0))              # type: ignore[arg-type]
    relief_share = float(cfg.get("relief", 0.4))          # type: ignore[arg-type]

    latest = max(week_index(r.season_type, r.week) for r in all_results)
    # SORTED before the mean, for the same reason `_credit` sorts: this is the bar
    # every credit is measured against, so a last-bit wobble in it moves every
    # licence on the board at once.
    bar = mean(sorted(float(v) for v in quality.values() if v is not None))

    wins_by: dict[str, list[GameResult]] = defaultdict(list)
    losses_by: dict[str, list[GameResult]] = defaultdict(list)
    for r in all_results:
        wins_by[r.winner].append(r)
        losses_by[r.loser].append(r)

    def losses_after(team: str, after: float) -> int:
        return sum(
            1 for r in losses_by[team] if week_index(r.season_type, r.week) > after
        )

    out: dict[Edge, Grounds] = {}
    for r in edges:
        played = week_index(r.season_type, r.week)
        age = max(0.0, latest - played)

        w_losses = losses_after(r.winner, played)
        l_losses = losses_after(r.loser, played)
        # "has lost since" OR "lost more times since than the other" in one
        # quantity. `loss_offset` is how much of the loser's own slide forgives
        # the winner's: at 0.5 one loss each still leaves half a loss of grounds,
        # at 1.0 only the net counts, at 0.0 the loser's losses are irrelevant.
        slide = max(0.0, w_losses - offset * l_losses)

        w_credit = _credit(wins_by[r.winner], played, quality, bar)
        l_credit = _credit(wins_by[r.loser], played, quality, bar)
        ascent = max(0.0, l_credit - w_credit)
        ramp = 1.0 if ramp_weeks <= 0 else clamp(age / ramp_weeks, 0.0, 1.0)

        allowance = min(
            cap,
            base + per_loss * slide + per_point * ascent * ramp + per_week * age,
        )
        earned = max(0.0, allowance - base)
        # Relief scales with how much of the available licence the grounds bought,
        # so grounds that earn the full licence forgive the full `relief` share of
        # the override price and grounds that earn half of it forgive half.
        relief = (
            relief_share * clamp(earned / (cap - base), 0.0, 1.0) if cap > base else 0.0
        )

        if slide > 0 and ascent > 0:
            category = "both"
        elif slide > 0:
            category = "form"
        elif ascent > 0:
            category = "resume"
        else:
            category = "age" if age > 0 else "none"

        out[(r.winner, r.loser)] = Grounds(
            category=category,
            severity=_severity(allowance, base, cap),
            weeks_since=age,
            winner_losses=w_losses,
            loser_losses=l_losses,
            slide=slide,
            winner_credit=w_credit,
            loser_credit=l_credit,
            ascent=ascent,
            ramp=ramp,
            allowance=allowance,
            relief=relief,
        )
    return out
