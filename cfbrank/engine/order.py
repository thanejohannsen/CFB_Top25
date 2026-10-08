"""Stage 4 -- head-to-head ordering, under hard caps.

We minimize

    cost(order) = strength     * sum(weight of each overridden result)
                + drift_weight * sum(|position - base position| ^ drift_exponent)

subject to, for every result,

    lead = position(winner) - position(loser)  <=  max_lead

`max_lead` comes from `engine/grounds.py` and is 0 when no exception applies (so
the winner must finish above the loser), infinite when the winner has slipped,
and a bounded number of places when the loser has merely been overtaking the
winner. Among the results an exception has unlocked, the ones that end up
overridden are by construction the least convincing: the "drop the weakest win"
rule, chosen globally.

**There was briefly a third cost term and it was the wrong shape.** Pricing the
distance -- `gap_weight * (places past a licence) ^ gap_exponent` -- was built and
measured, and it did narrow the published contradiction it was built for. But a
price can always be outbid, and the rule is a requirement rather than a
preference. So the caps are a FEASIBLE REGION, not a term. Do not reinstate it.

## This is not "enforce everything and topologically sort"

That really is as bad as this module used to say: it holds a team behind every
team its conqueror is behind, and because schedule density tracks schedule
strength it systematically sinks teams who play tough schedules -- measured on a
completed season it put three sub-.500-schedule teams in the top 12 while
dropping the two best resumes to #17 and #20. Real graphs are not tidy triangles
either; one season produced a single tangled 22-team component.

What makes the caps safe is that an exception is cheap to earn, so only a subset
of edges is hard and the chains stay short. Measured: 21 of 28 pool results are
enforced on 2026 week 5 and 59 of 111 on the completed 2025 season, with NO
cycles among them and longest chains of 4 and 6 teams. A team nobody in the pool
played still simply stays where its resume put it. If a config change ever pushes
those chain lengths up, this paragraph stops being true.

## Two facts about the caps, because neither is obvious

**1. Any topological order of the capped arcs is feasible.** Every cap is
non-negative, so `lead <= max_lead` is satisfied outright by putting the winner
above the loser. Therefore **infeasibility implies a cycle**, and relaxing a cap
that is not on a cycle is never necessary.

**2. A cycle is only infeasible when it is tight.** A cycle forces its members
into a window of width `sum(max_lead)`, and distinct positions need
`sum(max_lead) >= len(cycle) - 1`. So an all-zero cycle is *always* infeasible,
while a cycle of caps `(5, 5, 5)` is perfectly satisfiable and must be left
alone. That is why the pre-pass below only relaxes cycles among the ZERO caps,
where infeasibility is provable, and leaves every numeric cap to the search.

## Why block moves exist

A hard cap has no gradient, so single-team relocation gets trapped. With the
loser at base #9, the winner at base #19 and the winner required above it,
lifting the winner to just above the loser costs `10^1.5 + 1^1.5 = 32.6` of
drift, while meeting at #13/#14 costs `5^1.5 + 5^1.5 = 25.9` -- but no single
team can move from the first arrangement to the second, because moving the winner
down puts it back below the loser and moving the loser down alone strands the
winner. Relocating the pair as a block reaches it in one step, and the midpoint
falls out of `drift_exponent` rather than needing a dial of its own.

A contiguous block only helps when the two teams are close enough to sit in one.
A BANDED cap puts them `max_lead` apart on purpose, with innocent teams in
between, and dragging those along is worse than the problem: on 2026 week 5 that
left Oklahoma State eight places above its resume position rather than meeting
Oregon halfway. So there is a second, targeted move -- shift both ends of a
binding cap by the same amount and let everything between them close up. There
are only ever a couple of binding caps, so these candidates are evaluated by full
recompute rather than an incremental formula.

Both extra move classes are only considered when there are caps to navigate. With
none, the search is exactly the single-relocation one it has always been, which is
what keeps `grounds.enabled = false` an exact escape hatch.
"""

from __future__ import annotations

import heapq
import math
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Iterable, Mapping, NamedTuple, Sequence

from cfbrank.engine.evidence import EdgeFact
from cfbrank.engine.graph import Digraph, nontrivial_sccs
from cfbrank.normalize import sort_key

Edge = tuple[str, str]

UNBOUNDED = math.inf


class Costs(NamedTuple):
    total: float
    violation: float
    drift: float


@dataclass(frozen=True, slots=True)
class Relaxation:
    """A cap given up because it could not be satisfied.

    Relaxing means surrendering the CAP, not dropping the result: it keeps its
    weight in the violation term, so a relaxed result may still come out
    honoured. The pre-pass changes the feasible region, not the decision.
    """

    edge: Edge
    max_lead: float          # the cap that was given up
    reason: str              # "zero_cycle" | "unsatisfiable"
    members: tuple[str, ...] = ()   # the cycle it came out of, where known


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
    # Per-edge, for publishing. `leads` is how many places the loser finished
    # above the winner, positive meaning the result was contradicted; it is kept
    # for every edge because the measurement is useful even where no cap applies.
    leads: dict[Edge, int] = field(default_factory=dict)
    max_lead: dict[Edge, float] = field(default_factory=dict)  # the cap in force
    enforced: list[Edge] = field(default_factory=list)   # max_lead == 0
    capped: list[Edge] = field(default_factory=list)      # 0 < max_lead < inf
    binding: list[Edge] = field(default_factory=list)     # the cap set the distance
    relaxed: list[Relaxation] = field(default_factory=list)
    repairs: list[str] = field(default_factory=list)      # moves the start needed
    start: str = "base"      # "base" | "zero_topo"


def _new_index(k: int, i: int, j: int) -> int:
    """Where index k lands after the team at i is reinserted at j.

    Superseded in the engine by `_block_index`, and kept as the reference the
    generalisation is checked against: `test_block_index_is_new_index_generalised`
    asserts the two agree for every single-team move. This version is simple
    enough to read and was verified long before blocks existed, so it is a better
    oracle than a second derivation of the same arithmetic.
    """
    if k == i:
        return j
    if j > i:
        return k - 1 if i < k <= j else k
    if j < i:
        return k + 1 if j <= k < i else k
    return k


def _block_index(k: int, i: int, size: int, j: int) -> int:
    """Where index k lands after the block at [i, i+size) is reinserted at j.

    `j` is the insertion point in the sequence with the block already removed,
    which is what `del seq[i:i+size]; seq[j:j] = block` does. Generalizes
    `_new_index`, which is the size == 1 case.
    """
    if i <= k < i + size:
        return j + (k - i)
    shifted = k - size if k >= i + size else k
    return shifted + size if shifted >= j else shifted


def _topo_by_key(
    teams: Sequence[str], arcs: Iterable[Edge], key: Mapping[str, int]
) -> list[str] | None:
    """Lexicographically smallest topological order of `arcs` under `key`.

    Kahn's algorithm with a min-heap on `(key[t], sort_key(t))`. An arc `(a, b)`
    means a must come before b. Returns None when `arcs` has a cycle.

    The heap is what makes this the feasible order CLOSEST to the key order: when
    the key order already satisfies every arc, the heap's minimum is always the
    next team in key order, so this returns that order unchanged.
    """
    succ: dict[str, list[str]] = defaultdict(list)
    indeg: dict[str, int] = {t: 0 for t in teams}
    for a, b in sorted(arcs, key=lambda e: (sort_key(e[0]), sort_key(e[1]))):
        succ[a].append(b)
        indeg[b] += 1

    heap = [(key[t], sort_key(t), t) for t in teams if indeg[t] == 0]
    heapq.heapify(heap)
    out: list[str] = []
    while heap:
        _, _, team = heapq.heappop(heap)
        out.append(team)
        for nxt in succ[team]:
            indeg[nxt] -= 1
            if indeg[nxt] == 0:
                heapq.heappush(heap, (key[nxt], sort_key(nxt), nxt))
    return out if len(out) == len(teams) else None


def _with_positions(seq: Sequence[str], targets: Mapping[str, int]) -> list[str]:
    """`seq` with each team in `targets` placed at its given index.

    Everybody else keeps their relative order and closes up around them, which is
    what makes a paired shift leave the teams BETWEEN the pair alone instead of
    dragging them. Targets must be distinct and inside the sequence.
    """
    out: list[str | None] = [None] * len(seq)
    for team, k in targets.items():
        out[k] = team
    rest = (t for t in seq if t not in targets)
    for k in range(len(out)):
        if out[k] is None:
            out[k] = next(rest)
    return [t for t in out if t is not None]


def _pair_shifts(
    model: "_Model", seq: Sequence[str], index: Mapping[str, int], reach: int
) -> list[tuple[str, list[str]]]:
    """Orders in which both ends of a binding or broken cap move together.

    The trap this escapes: the winner cannot drift back toward its resume position
    without the loser moving too, and moving the loser first is uphill, so
    best-improvement never takes the first step. Only caps that actually bind are
    worth trying, which keeps this to a few dozen candidates a pass.
    """
    n = len(seq)
    out: list[tuple[str, list[str]]] = []
    for (w, l, cap) in model.caps:
        if index[w] - index[l] < cap:
            continue
        for step in range(1, reach + 1):
            for sign in (-1, 1):
                shift = sign * step
                tw, tl = index[w] + shift, index[l] + shift
                if not (0 <= tw < n and 0 <= tl < n):
                    continue
                out.append(
                    (f"{w}+{l}: {shift:+d}", _with_positions(seq, {w: tw, l: tl}))
                )
    return out


def _weakest(arcs: Sequence[Edge], weights: Mapping[Edge, float]) -> Edge:
    """The least convincing of these results -- the one to give up on.

    Same rule the violation term already follows, so the relaxation story and the
    override story agree. `sort_key` is the terminal key because two results can
    share a weight.
    """
    return min(arcs, key=lambda e: (weights[e], sort_key(e[0]), sort_key(e[1])))


def _relax_zero_cycles(
    caps: Mapping[Edge, float], weights: Mapping[Edge, float]
) -> tuple[dict[Edge, float], list[Relaxation]]:
    """Make the zero-cap arcs acyclic, which is what the start order needs.

    A cycle among caps of zero is *provably* unsatisfiable (Fact 2 with a window
    of width 0), so no judgement is involved here -- unlike a numeric cycle,
    which may well be satisfiable and is left to the search. The weakest arc in
    each offending component is given up, and the components are recomputed each
    round because one component can contain two edge-disjoint cycles.
    """
    out = dict(caps)
    relaxations: list[Relaxation] = []
    for _ in range(len(out) + 1):
        zero = sorted(
            (e for e, cap in out.items() if cap == 0),
            key=lambda e: (sort_key(e[0]), sort_key(e[1])),
        )
        if not zero:
            break
        graph = Digraph(sorted({t for e in zero for t in e}, key=sort_key))
        for winner, loser in zero:
            graph.add_edge(winner, loser)
        components = nontrivial_sccs(graph)
        if not components:
            break
        members = set(components[0])
        inside = [e for e in zero if e[0] in members and e[1] in members]
        edge = _weakest(inside, weights)
        relaxations.append(
            Relaxation(edge, out[edge], "zero_cycle", tuple(components[0]))
        )
        out[edge] = UNBOUNDED
    return out, relaxations


class _Model:
    """Cost model and feasible region over a mutable ordering. Indices are 0-based."""

    def __init__(
        self,
        teams: Sequence[str],
        edges: Mapping[Edge, float],
        base_rank: Mapping[str, int],
        strength: float,
        drift_weight: float,
        drift_exponent: float = 1.5,
        caps: Mapping[Edge, float] | None = None,
    ) -> None:
        self.teams = list(teams)
        self.base = {t: int(base_rank[t]) for t in self.teams}
        self.strength = float(strength)
        self.drift_weight = float(drift_weight)
        self.drift_exponent = float(drift_exponent)
        present = set(self.teams)
        self.edges = {
            (w, l): float(wt) for (w, l), wt in edges.items() if w in present and l in present
        }
        # SORTED, and every per-edge float sum below runs over this list rather
        # than the dict. The dict iterates in the CALLER's insertion order, and
        # float addition is not associative, so summing over it let a shuffled
        # `edge_facts` move the last bits of `violation_cost` -- and therefore of
        # the published `meta.cost.total`. Same discipline as `at_least()` and
        # `grounds._win_quality`, for the same reason.
        self.edge_list = sorted(
            ((w, l, wt) for (w, l), wt in self.edges.items()),
            key=lambda e: (sort_key(e[0]), sort_key(e[1])),
        )
        # Only the FINITE caps constrain anything, so an edge left out of `caps`
        # is unconstrained -- which is exactly how the stage behaved before any of
        # this existed, and is what `grounds.enabled = false` buys.
        self.caps = sorted(
            (
                (w, l, float((caps or {}).get((w, l), UNBOUNDED)))
                for (w, l, _wt) in self.edge_list
            ),
            key=lambda e: (sort_key(e[0]), sort_key(e[1])),
        )
        self.caps = [(w, l, c) for (w, l, c) in self.caps if c != UNBOUNDED]

    def _drift(self, position: int, team: str) -> float:
        """Cost of sitting `position` places from where the resume put you."""
        return abs(position - self.base[team]) ** self.drift_exponent

    def cost(self, index: Mapping[str, int]) -> Costs:
        drift = self.drift_weight * sum(self._drift(index[t] + 1, t) for t in self.teams)
        violations = self.strength * sum(
            wt for (w, l, wt) in self.edge_list if index[w] > index[l]
        )
        return Costs(violations + drift, violations, drift)

    # -- the feasible region ------------------------------------------------
    def violations(self, index: Mapping[str, int]) -> int:
        """How many caps this ordering breaks."""
        return sum(1 for (w, l, cap) in self.caps if index[w] - index[l] > cap)

    def broken(self, index: Mapping[str, int]) -> list[Edge]:
        return [(w, l) for (w, l, cap) in self.caps if index[w] - index[l] > cap]

    def cap_delta(self, index: Mapping[str, int], i: int, size: int, j: int) -> int:
        """Change in the number of broken caps if the block at i moves to j.

        **This is not incident-local, and that is the whole subtlety.** Relocating
        a block shifts a run of other teams by `size` while leaving the rest
        alone, so a pair with exactly one endpoint inside that run has its lead
        change even though neither endpoint moved. Every capped edge is therefore
        checked. There are a few dozen against a forty-team pool, and the drift
        loop below is already O(n) per candidate, so this is not the bottleneck.
        """
        delta = 0
        for (w, l, cap) in self.caps:
            iw, il = index[w], index[l]
            before = (iw - il) > cap
            after = (
                _block_index(iw, i, size, j) - _block_index(il, i, size, j)
            ) > cap
            if before != after:
                delta += 1 if after else -1
        return delta

    # -- the cost gradient --------------------------------------------------
    def block_delta(
        self, index: Mapping[str, int], block: Sequence[str], i: int, j: int
    ) -> float:
        """Exact cost change of moving `block`, which sits at [i, i+len(block)), to j."""
        size = len(block)
        if i == j:
            return 0.0

        delta = 0.0
        for offset, team in enumerate(block):
            delta += self.drift_weight * (
                self._drift(j + offset + 1, team) - self._drift(i + offset + 1, team)
            )

        if j > i:
            lo, hi, shift = i + size, j + size - 1, -size
        else:
            lo, hi, shift = j, i - 1, size
        for other in self.teams:
            k = index[other]
            if lo <= k <= hi and not (i <= k < i + size):
                delta += self.drift_weight * (
                    self._drift(k + shift + 1, other) - self._drift(k + 1, other)
                )

        # Teams outside the block never change their order relative to each other
        # -- the shifted run moves as a unit and cannot cross the teams that stay
        # -- so only a pair involving a block member can flip. Scanning every edge
        # and testing both endpoints is the simplest correct way to say that.
        for (w, l, wt) in self.edge_list:
            iw, il = index[w], index[l]
            before = iw > il
            after = _block_index(iw, i, size, j) > _block_index(il, i, size, j)
            if before != after:
                delta += self.strength * (wt if after else -wt)
        return delta

    def move_delta(self, index: Mapping[str, int], team: str, i: int, j: int) -> float:
        """Exact cost change of moving `team` from index i to index j."""
        return self.block_delta(index, [team], i, j)


def minimum_violations_order(
    teams: Sequence[str],
    edge_facts: Mapping[Edge, EdgeFact],
    base_rank: Mapping[str, int],
    cfg: Mapping[str, object],
    max_lead: Mapping[Edge, float] | None = None,
) -> OrderResult:
    """Best-improvement local search over relocations, inside the feasible region.

    Starts from the base order -- or, when the base order breaks a zero cap, from
    the closest order that does not -- so with no head-to-head results the output
    is the base order exactly. Candidates are ranked on
    `(broken caps, cost, block size, team name, from, to)` and a move that would
    break MORE caps is never taken, so feasibility once won is never lost and the
    remaining numeric caps are satisfied before cost is polished.
    """
    strength = float(cfg.get("strength", 14.0))  # type: ignore[arg-type]
    drift_weight = float(cfg.get("drift_weight", 1.0))  # type: ignore[arg-type]
    drift_exponent = float(cfg.get("drift_exponent", 1.5))  # type: ignore[arg-type]
    max_passes = int(cfg.get("max_passes", 400))  # type: ignore[arg-type]
    max_block = int(cfg.get("max_block", 4))  # type: ignore[arg-type]

    ordered = sorted(teams, key=lambda t: (int(base_rank[t]), sort_key(t)))
    weights = {k: f.weight for k, f in edge_facts.items()}
    present = set(ordered)
    caps = {
        e: float(cap)
        for e, cap in (max_lead or {}).items()
        if cap != UNBOUNDED and e[0] in present and e[1] in present
    }
    caps, relaxations = _relax_zero_cycles(caps, weights)
    caps = {e: c for e, c in caps.items() if c != UNBOUNDED}

    # -- a feasible start ---------------------------------------------------
    start_name, repairs = "base", []
    seq = list(ordered)
    zero_arcs = [e for e, cap in caps.items() if cap == 0]
    if zero_arcs:
        rank_of = {t: i for i, t in enumerate(ordered)}
        repaired = _topo_by_key(ordered, zero_arcs, rank_of)
        # `_relax_zero_cycles` made these acyclic, so this cannot be None -- but
        # falling back to the base order is better than raising if it ever is.
        if repaired is not None and repaired != ordered:
            before = {t: i for i, t in enumerate(ordered)}
            seq, start_name = repaired, "zero_topo"
            repairs = [
                f"{t}: {before[t] + 1} -> {k + 1}"
                for k, t in enumerate(repaired)
                if before[t] != k
            ]

    # -- search, relaxing a cap only if it proves unsatisfiable -------------
    model = _Model(
        ordered, weights, base_rank, strength, drift_weight, drift_exponent, caps
    )
    moves: list[str] = []
    passes = 0
    for _attempt in range(len(caps) + 1):
        seq, moves, passes, index = _search(model, seq, max_passes, max_block)
        broken = model.broken(index)
        if not broken:
            break
        # Fact 1 says this means a cycle. The zero caps are already acyclic, so
        # what is left is a tight numeric cycle: give up its weakest cap and go
        # again. Bounded by the number of caps, and with every cap gone the search
        # is unconstrained, so this always terminates satisfied.
        edge = _weakest(broken, weights)
        relaxations.append(Relaxation(edge, caps[edge], "unsatisfiable"))
        caps = {e: c for e, c in caps.items() if e != edge}
        model = _Model(
            ordered, weights, base_rank, strength, drift_weight, drift_exponent, caps
        )

    index = {t: i for i, t in enumerate(seq)}
    costs = model.cost(index)
    violated = sorted(
        ((w, l) for (w, l, _wt) in model.edge_list if index[w] > index[l]),
        key=lambda e: (model.edges[e], sort_key(e[0]), sort_key(e[1])),
    )
    honored = sorted(
        ((w, l) for (w, l, _wt) in model.edge_list if index[w] < index[l]),
        key=lambda e: (sort_key(e[0]), sort_key(e[1])),
    )
    drift = {t: (index[t] + 1) - int(base_rank[t]) for t in seq}
    leads = {(w, l): index[w] - index[l] for (w, l, _wt) in model.edge_list}

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
        leads=leads,
        max_lead={(w, l): caps.get((w, l), UNBOUNDED) for (w, l, _wt) in model.edge_list},
        enforced=[(w, l) for (w, l, cap) in model.caps if cap == 0],
        capped=[(w, l) for (w, l, cap) in model.caps if cap > 0],
        binding=[
            (w, l)
            for (w, l, cap) in model.caps
            if cap > 0 and leads[(w, l)] == int(cap)
        ],
        relaxed=relaxations,
        repairs=repairs,
        start=start_name,
    )


def _search(
    model: _Model, start: Sequence[str], max_passes: int, max_block: int
) -> tuple[list[str], list[str], int, dict[str, int]]:
    """Lexicographic best-improvement: fewer broken caps first, then lower cost."""
    seq = list(start)
    index = {t: i for i, t in enumerate(seq)}
    moves: list[str] = []
    n = len(seq)
    # The extra move classes exist only to navigate a constrained region. With no
    # caps the search is exactly the single-relocation one it has always been,
    # which is what keeps `grounds.enabled = false` reproduce-the-old-board exact.
    sizes = range(1, max(1, min(max_block, n)) + 1) if model.caps else (1,)
    passes = 0

    for passes in range(1, max_passes + 1):
        best: tuple | None = None
        for size in sizes:
            for i in range(n - size + 1):
                for j in range(n - size + 1):
                    if j == i:
                        continue
                    broke = model.cap_delta(index, i, size, j)
                    if broke > 0:
                        continue
                    delta = model.block_delta(index, seq[i : i + size], i, j)
                    if broke == 0 and delta >= -1e-9:
                        continue
                    candidate = (
                        broke, round(delta, 9), size, sort_key(seq[i]), seq[i], i, j,
                        None,
                    )
                    if best is None or candidate[:7] < best[:7]:
                        best = candidate

        if model.caps:
            here = model.cost(index).total
            broken_here = model.violations(index)
            for label, candidate_seq in _pair_shifts(model, seq, index, n):
                nxt = {t: k for k, t in enumerate(candidate_seq)}
                broke = model.violations(nxt) - broken_here
                if broke > 0:
                    continue
                delta = model.cost(nxt).total - here
                if broke == 0 and delta >= -1e-9:
                    continue
                # Sorts after a block move of equal value: `size = n + 1` keeps
                # the cheaper, more local move preferred on a tie.
                candidate = (
                    broke, round(delta, 9), n + 1, sort_key(label), label, 0, 0,
                    (label, candidate_seq),
                )
                if best is None or candidate[:7] < best[:7]:
                    best = candidate

        if best is None:
            passes -= 1
            break

        _, _, size, _, _, i, j, paired = best
        if paired is not None:
            label, seq = paired[0], list(paired[1])
        else:
            block = seq[i : i + size]
            del seq[i : i + size]
            seq[j:j] = block
            label = (
                f"{block[0]}: {i + 1} -> {j + 1}"
                if size == 1
                else f"{block[0]}..{block[-1]}: {i + 1} -> {j + 1}"
            )
        index = {t: k for k, t in enumerate(seq)}
        moves.append(label)
    else:
        passes = max_passes

    return seq, moves, passes, index
