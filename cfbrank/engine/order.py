"""Stage 4 -- weighted head-to-head ordering.

Head-to-head is a strong preference, not a hard constraint. We minimize

    cost(order) = strength     * sum(price of each overridden result)
                + drift_weight * sum(|position - base position| ^ drift_exponent)
                + gap_weight   * sum(places past the licence) ^ gap_exponent

and the results that end up overridden are, by construction, the least
convincing ones. That is the "drop the weakest win" rule, chosen globally.

**The third term is what stops an override being binary.** The first two alone
price the DECISION to rank against a result and then leave the DISTANCE free: pay
`strength x price` once and the two teams can finish anywhere the base order
likes. On 2026 week 5 that published Missouri's 45-17 win over Florida with
Florida eleven places higher, on the Saturday it happened, with nothing in
between to justify it. Each result now carries a licence from
`engine/grounds.py` -- how far apart the two may sit, in places, given what the
two teams have done SINCE the game -- and every place past it is charged.
`gap_exponent` matches `drift_exponent` by default so the two terms are
commensurate: the ordering settles where pulling the pair together stops being
cheaper than holding them apart, which is usually both teams moving part of the
way rather than one being dragged across the board.

Why not simply enforce every result and topologically sort? Because that holds
a team behind every team its conqueror is behind. Schedule density correlates
with schedule strength, so hard enforcement systematically sinks teams that
play tough schedules and floats teams that play nobody -- measured on a real
completed season it put three sub-.500-schedule teams in the top 12 while
dropping the two best resumes to #17 and #20. Real head-to-head graphs also
are not tidy triangles: one completed season produced a single tangled
22-team strongly connected component, where per-cycle resolution is not even
computable. A weighted objective has neither problem: an unconstrained team
simply stays where its resume put it.

`strength` reads as "how many positions of resume drift one unit of
head-to-head conviction is worth", and `drift_exponent` makes long moves
disproportionately expensive, so how far a result can move a team scales with
how convincing it was. With the shipped values a marginal win (weight ~1) buys
about six places and an emphatic one (weight ~3.7) about fourteen. A linear
drift term instead lets any honoured result, however weak, buy an unbounded
move: a four-point home win by a team rated three points worse was enough to
carry an 8-5 team from #36 to #21 on real data.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from typing import Mapping, NamedTuple, Sequence

from cfbrank.engine.evidence import EdgeFact
from cfbrank.normalize import sort_key

Edge = tuple[str, str]


class Costs(NamedTuple):
    total: float
    violation: float
    drift: float
    gap: float


@dataclass(slots=True)
class OrderResult:
    order: list[str]
    cost: float
    violation_cost: float
    drift_cost: float
    violated: list[Edge]
    honored: list[Edge]
    drift: dict[str, int]
    passes: int
    moves: list[str] = field(default_factory=list)
    gap_cost: float = 0.0
    # Per-edge, for publishing: how many places the loser finished above the
    # winner, and how many of those were past what the grounds licensed.
    gaps: dict[Edge, int] = field(default_factory=dict)
    excess: dict[Edge, float] = field(default_factory=dict)


def _new_index(k: int, i: int, j: int) -> int:
    """Where index k lands after the team at i is reinserted at j."""
    if k == i:
        return j
    if j > i:
        return k - 1 if i < k <= j else k
    if j < i:
        return k + 1 if j <= k < i else k
    return k


class _Model:
    """Cost model over a mutable ordering. Indices are 0-based internally."""

    def __init__(
        self,
        teams: Sequence[str],
        edges: Mapping[Edge, float],
        base_rank: Mapping[str, int],
        strength: float,
        drift_weight: float,
        drift_exponent: float = 1.5,
        allowance: Mapping[Edge, float] | None = None,
        gap_weight: float = 0.0,
        gap_exponent: float = 1.5,
    ) -> None:
        self.teams = list(teams)
        self.base = {t: int(base_rank[t]) for t in self.teams}
        self.strength = float(strength)
        self.drift_weight = float(drift_weight)
        self.drift_exponent = float(drift_exponent)
        self.gap_weight = float(gap_weight)
        self.gap_exponent = float(gap_exponent)
        present = set(self.teams)
        self.edges = {
            (w, l): float(wt) for (w, l), wt in edges.items() if w in present and l in present
        }
        # An edge with no licence is unconstrained, which is what the stage did
        # before grounds existed, so leaving `allowance` out reproduces it exactly.
        self.allowance = {
            key: float((allowance or {}).get(key, float("inf"))) for key in self.edges
        }
        self.licensed = (
            {key: a for key, a in self.allowance.items() if a != float("inf")}
            if self.gap_weight > 0
            else {}
        )
        # Only pairs involving the moved team can change relative order, so an
        # incremental evaluation needs just the incident edges.
        self.incident: dict[str, list[tuple[str, str, float]]] = defaultdict(list)
        for (w, l), wt in self.edges.items():
            self.incident[w].append((w, l, wt))
            self.incident[l].append((w, l, wt))

    def _drift(self, position: int, team: str) -> float:
        """Cost of sitting `position` places from where the resume put you."""
        return abs(position - self.base[team]) ** self.drift_exponent

    def _gap(self, iw: int, il: int, allowed: float) -> float:
        """Cost of the loser finishing further above the winner than licensed.

        `iw - il` is positive only when the result is being overridden, so an
        honoured result never pays here however far apart the two finish: the
        licence is a limit on contradicting a result, not on agreeing with it.
        """
        excess = (iw - il) - allowed
        return self.gap_weight * excess ** self.gap_exponent if excess > 0 else 0.0

    def gap_cost(self, index: Mapping[str, int]) -> float:
        return sum(
            self._gap(index[w], index[l], allowed)
            for (w, l), allowed in self.licensed.items()
        )

    def cost(self, index: Mapping[str, int]) -> Costs:
        drift = self.drift_weight * sum(self._drift(index[t] + 1, t) for t in self.teams)
        violations = self.strength * sum(
            wt for (w, l), wt in self.edges.items() if index[w] > index[l]
        )
        gap = self.gap_cost(index)
        return Costs(violations + drift + gap, violations, drift, gap)

    def move_delta(self, index: Mapping[str, int], team: str, i: int, j: int) -> float:
        """Exact cost change of moving `team` from index i to index j."""
        if i == j:
            return 0.0

        delta = self.drift_weight * (
            self._drift(j + 1, team) - self._drift(i + 1, team)
        )
        lo, hi, shift = (i + 1, j, -1) if j > i else (j, i - 1, +1)
        for other in self.teams:
            k = index[other]
            if other != team and lo <= k <= hi:
                delta += self.drift_weight * (
                    self._drift(k + shift + 1, other) - self._drift(k + 1, other)
                )

        for (w, l, wt) in self.incident[team]:
            before = index[w] > index[l]
            after = _new_index(index[w], i, j) > _new_index(index[l], i, j)
            if before != after:
                delta += self.strength * (wt if after else -wt)

        # The gap term is NOT incident-local: relocating a third team can shift
        # one endpoint of a pair without the other, which changes that pair's gap
        # by one. So every licensed edge is checked. There are a few dozen of them
        # against a forty-team pool, so this stays cheap.
        for (w, l), allowed in self.licensed.items():
            iw, il = index[w], index[l]
            nw, nl = _new_index(iw, i, j), _new_index(il, i, j)
            if nw - nl != iw - il:
                delta += self._gap(nw, nl, allowed) - self._gap(iw, il, allowed)
        return delta


def minimum_violations_order(
    teams: Sequence[str],
    edge_facts: Mapping[Edge, EdgeFact],
    base_rank: Mapping[str, int],
    cfg: Mapping[str, object],
) -> OrderResult:
    """Best-improvement local search over single-team relocations.

    Starts from the base order, so with no head-to-head results the output is
    the base order exactly. Every tie breaks on (delta, team name, target
    index), making the result independent of input ordering.
    """
    strength = float(cfg.get("strength", 14.0))  # type: ignore[arg-type]
    drift_weight = float(cfg.get("drift_weight", 1.0))  # type: ignore[arg-type]
    drift_exponent = float(cfg.get("drift_exponent", 1.5))  # type: ignore[arg-type]
    max_passes = int(cfg.get("max_passes", 400))  # type: ignore[arg-type]
    gcfg = cfg.get("grounds", {})
    gcfg = gcfg if isinstance(gcfg, Mapping) else {}
    gap_weight = float(gcfg.get("gap_weight", 0.0))  # type: ignore[arg-type]
    gap_exponent = float(gcfg.get("gap_exponent", drift_exponent))  # type: ignore[arg-type]

    ordered = sorted(teams, key=lambda t: (int(base_rank[t]), sort_key(t)))
    # The PRICE, not the raw conviction weight: grounds for setting a result
    # aside forgive a share of it. See evidence.EdgeFact.price.
    weights = {k: f.price for k, f in edge_facts.items()}
    allowance = {
        k: f.grounds.allowance for k, f in edge_facts.items() if f.grounds is not None
    }
    model = _Model(
        ordered,
        weights,
        base_rank,
        strength,
        drift_weight,
        drift_exponent,
        allowance=allowance,
        gap_weight=gap_weight,
        gap_exponent=gap_exponent,
    )

    seq = list(ordered)
    index = {t: i for i, t in enumerate(seq)}
    moves: list[str] = []
    passes = 0

    for passes in range(1, max_passes + 1):
        best: tuple[float, tuple[str, str], str, int] | None = None
        for team in seq:
            i = index[team]
            for j in range(len(seq)):
                if j == i:
                    continue
                delta = model.move_delta(index, team, i, j)
                if delta < -1e-9:
                    candidate = (round(delta, 9), sort_key(team), team, j)
                    if best is None or candidate < best:
                        best = candidate
        if best is None:
            passes -= 1
            break

        _, _, team, j = best
        i = index[team]
        seq.pop(i)
        seq.insert(j, team)
        index = {t: k for k, t in enumerate(seq)}
        moves.append(f"{team}: {i + 1} -> {j + 1}")
    else:
        passes = max_passes

    costs = model.cost(index)
    violated = sorted(
        (e for e in model.edges if index[e[0]] > index[e[1]]),
        key=lambda e: (model.edges[e], sort_key(e[0]), sort_key(e[1])),
    )
    honored = sorted(
        (e for e in model.edges if index[e[0]] < index[e[1]]),
        key=lambda e: (sort_key(e[0]), sort_key(e[1])),
    )
    drift = {t: (index[t] + 1) - int(base_rank[t]) for t in seq}
    gaps = {(w, l): index[w] - index[l] for (w, l) in model.edges}
    # Reported for every edge that HAS a licence, not just the ones being charged
    # for it, so `gap_weight = 0` still publishes how far past its licence a
    # contradiction sits -- the measurement is useful even when it costs nothing.
    excess = {
        key: max(0.0, gaps[key] - allowed)
        for key, allowed in sorted(model.allowance.items(), key=lambda kv: kv[0])
        if allowed != float("inf")
    }

    return OrderResult(
        order=seq,
        cost=costs.total,
        violation_cost=costs.violation,
        drift_cost=costs.drift,
        violated=violated,
        honored=honored,
        drift=drift,
        passes=passes,
        moves=moves,
        gap_cost=costs.gap,
        gaps=gaps,
        excess=excess,
    )
