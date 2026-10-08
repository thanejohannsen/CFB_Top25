"""When may a head-to-head result be ranked against? The owner's four rules.

Stage 4 asks two separate questions about a result. `evidence.py` answers the
first: how convincing was the game? Margin, venue, whether the power ratings
agree, how the two fared against shared opponents. That is a fact about one
afternoon and it never changes again.

This module answers the second, and it is not a matter of degree:

    Team A beat Team B. **A HAS to be ranked above B** unless

      1. A has more losses than B                                   (any gap)
      2. A has lost since the game and B has not                    (any gap)
      3. A has lost more times since the game than B has            (any gap)
      4. B has impressive wins and A has mediocre wins, AND at
         least `min_weeks` have passed since the game            (BANDED gap)

Rules 1-3 are about slipping; rule 4 is about being overtaken. Nothing else
counts. If none of them applies the result stands, full stop.

## Why this is a cap and not a price

Everything in stage 4 used to be a price, and so once a game became an edge the
optimiser was free to rank against it whenever the arithmetic was cheaper.
`weight_floor > 0` only guaranteed the price was not zero. On 2026 week 5 that
published **Missouri's 45-17 win over Florida with Florida five places higher**,
on the Saturday it happened, with nothing in between.

A priced "licence" on the distance was built and measured first and it did narrow
that. It was still the wrong shape: a price can always be outbid, and the rule
above is a requirement. So each result now carries

    max_lead -- the most places the LOSER may finish above the WINNER

which stage 4 treats as a hard constraint rather than a term:

| case | `max_lead` |
| --- | --- |
| no exception applies | `0` -- the winner must finish above the loser |
| rule 1, 2 or 3 | `inf` -- stages 1-3 decide, which is what the owner asked for |
| rule 4 alone | `floor(band_rate(weeks_since) x quality_diff)` |

`max_lead` is always an integer or infinity. `engine/order.py`'s feasibility
shortcuts rely on that, so do not hand it a fraction.

## Rule 4's band grows with time, because the evidence does

`band_rate` is places of licence per rating point of win-quality difference, and
it ramps: 0.5 at `min_weeks`, +`band_step` per further week, to `band_max`. One
good win the week after a game proves little whatever it was worth; six weeks of
them is a different team. Measured consequence worth knowing: on a completed
season nearly every rule-4 edge is 6+ weeks old and sits at the ceiling, so the
ramp is an early-season instrument rather than something that shapes a finished
board.

## Win quality is the MEAN of the wins since, not the sum

Summing conflates quality with volume, and volume grows every week: on the
completed 2025 season the sum reached 73 rating points for a week-8 result, which
would have maxed any band on its own and pinned 15 of 111 results at the ceiling.
Every old result then looked equally undermined and the measure stopped
discriminating exactly where the contradictions are. A per-win mean is also the
plainer reading of "impressive wins while the other has mediocre wins": it is
about what the wins were worth, not how many there were.

"Impressive" and "mediocre" are one bar, not two dials -- B's mean must clear
`impressive_bar` and A's must not. That is the owner's sentence read literally,
and it means two teams who have BOTH been beating good sides since do not unlock
rule 4 at all.

## Everything is counted over EVERY game, not just pool games

A loss to a team nobody ranks is still a loss, and the credit for beating a good
unranked team is real. The edges being judged come from the pool; the evidence
about them does not. Market ratings only -- FPI has no week dimension, so folding
it in here would read a season-end snapshot into a question about what has
happened since one particular Saturday.
"""

from __future__ import annotations

import math
from collections import defaultdict
from dataclasses import dataclass
from typing import Mapping, Sequence

from cfbrank.engine.stats import mean
from cfbrank.models import GameResult, week_index

Edge = tuple[str, str]

UNBOUNDED = math.inf


@dataclass(frozen=True, slots=True)
class Grounds:
    """Which exceptions let this result be ranked against, and how far."""

    rules: tuple[int, ...]       # the exceptions that apply; () means ENFORCED
    max_lead: float              # places the loser may finish above the winner
    weeks_since: float
    winner_losses: int           # total losses, for rule 1
    loser_losses: int
    winner_losses_since: int     # losses after this game, for rules 2 and 3
    loser_losses_since: int
    winner_quality: float        # mean rating points past the board mean, wins SINCE
    loser_quality: float
    quality_diff: float          # how far the loser's subsequent wins lead, >= 0
    band_rate: float             # places per rating point; 0.0 unless rule 4 decides

    @property
    def enforced(self) -> bool:
        """The result must be honoured: nothing since the game excuses it."""
        return not self.rules

    @property
    def banded(self) -> bool:
        """An exception applies, but it only buys a bounded number of places."""
        return bool(self.rules) and self.max_lead != UNBOUNDED


def _rating(quality: Mapping[str, float], team: str, bar: float) -> float:
    """A team's rating, or the board's average where the market has no opinion.

    Conservative on purpose: an unknown opponent should not unlock rule 4.
    """
    value = quality.get(team)
    return bar if value is None else float(value)


def _win_quality(
    wins: Sequence[GameResult], after: float, quality: Mapping[str, float], bar: float
) -> float:
    """Mean rating points past `bar` of the wins picked up after week `after`.

    Anchored at zero rather than centred, for the reason `best_win` and
    `loss_quality` are: a team with no good wins since has *nothing* here, and
    nothing must be the bottom of the scale rather than the middle of it. Beating
    an average team is worth 0, the same as beating nobody -- which is right,
    because "mediocre wins" and "no wins" are the same argument.
    """
    scores = sorted(
        max(0.0, _rating(quality, r.loser, bar) - bar)
        for r in wins
        if week_index(r.season_type, r.week) > after
    )
    # SORTED before the mean, for the reason `at_least()` and `engine/cover.py`
    # sort: summing floats in input order makes the last bits a function of how
    # the games happened to arrive, which can flip a rank and break
    # byte-stability. tests/test_grounds.py::TestDeterminism shuffles the result
    # list directly and would catch it.
    return mean(scores)


def band_rate(
    age: float, min_weeks: float, start: float, step: float, cap: float
) -> float:
    """Places of licence per rating point, for a result `age` weeks old.

    0.5 at two weeks, then +0.25 a week to a ceiling of 1.5 with the shipped
    dials. Below `min_weeks` rule 4 cannot apply at all, so this is never asked.
    """
    return min(cap, start + step * max(0.0, age - min_weeks))


def compute(
    edges: Sequence[GameResult],
    all_results: Sequence[GameResult],
    quality: Mapping[str, float],
    cfg: Mapping[str, object],
) -> dict[Edge, Grounds]:
    """Judge every result in `edges` against everything in `all_results`.

    `edges` are the pool results stage 4 will order (already collapsed, so one
    per pair). `quality` is one rating per team, higher is better -- the market's
    neutral-field points over the whole board rather than just the pool.

    An empty result means "no constraints at all", which is exactly the engine as
    it behaved before any of this existed. That is what `enabled = false` buys.
    """
    if not edges or not bool(cfg.get("enabled", True)):
        return {}

    bar_q = float(cfg.get("impressive_bar", 6.0))      # type: ignore[arg-type]
    min_weeks = float(cfg.get("min_weeks", 2))         # type: ignore[arg-type]
    start = float(cfg.get("band_start", 0.5))          # type: ignore[arg-type]
    step = float(cfg.get("band_step", 0.25))           # type: ignore[arg-type]
    cap = float(cfg.get("band_max", 1.5))              # type: ignore[arg-type]

    latest = max(week_index(r.season_type, r.week) for r in all_results)
    # SORTED before the mean, for the same reason `_win_quality` sorts: this is
    # the bar every credit is measured against, so a last-bit wobble in it would
    # move every licence on the board at once.
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

        w_total, l_total = len(losses_by[r.winner]), len(losses_by[r.loser])
        w_since = losses_after(r.winner, played)
        l_since = losses_after(r.loser, played)
        w_quality = _win_quality(wins_by[r.winner], played, quality, bar)
        l_quality = _win_quality(wins_by[r.loser], played, quality, bar)
        diff = max(0.0, l_quality - w_quality)

        rules: list[int] = []
        if w_total > l_total:
            rules.append(1)
        # Rule 2 is the N=1 case of rule 3, so both are reported when both hold:
        # "has lost since while the other has not" is the owner's own first
        # clause and reads differently from "has lost more since".
        if w_since >= 1 and l_since == 0:
            rules.append(2)
        if w_since > l_since:
            rules.append(3)
        rate = 0.0
        if l_quality >= bar_q and w_quality < bar_q and age >= min_weeks:
            rules.append(4)
            rate = band_rate(age, min_weeks, start, step, cap)

        if {1, 2, 3} & set(rules):
            # Slipping is unbounded: the owner's "any gap". Stages 1-3 decide,
            # and whichever of rules 1-3 fired outranks rule 4's band even when
            # rule 4 also applies.
            max_lead = UNBOUNDED
        elif rate:
            # Rule 4 alone. A small quality edge can floor to zero places, which
            # is the right answer rather than a bug: the exception applies and
            # buys nothing, so the result still stands.
            max_lead = float(math.floor(rate * diff))
        else:
            max_lead = 0.0

        out[(r.winner, r.loser)] = Grounds(
            rules=tuple(rules),
            max_lead=max_lead,
            weeks_since=age,
            winner_losses=w_total,
            loser_losses=l_total,
            winner_losses_since=w_since,
            loser_losses_since=l_since,
            winner_quality=w_quality,
            loser_quality=l_quality,
            quality_diff=diff,
            band_rate=rate,
        )
    return out
