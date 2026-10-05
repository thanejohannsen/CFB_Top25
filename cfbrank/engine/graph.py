"""Directed graph primitives used to detect and report head-to-head cycles.

Cycles are not resolved here. Ordering is done by engine.order, which treats
head-to-head results as weighted preferences; this module exists so the site
can *explain* a contradiction ("A beat B, B beat C, C beat A") by naming the
strongly connected component it belongs to.

Everything is iterative (no recursion limits) and every traversal visits nodes
in sort_key order, so output never depends on insertion order.
"""

from __future__ import annotations

from typing import Iterable, Sequence

from cfbrank.normalize import sort_key

Node = str


class Digraph:
    """A tiny directed graph. All accessors return sorted sequences."""

    __slots__ = ("_succ", "_pred")

    def __init__(self, nodes: Iterable[Node] = ()) -> None:
        self._succ: dict[Node, set[Node]] = {}
        self._pred: dict[Node, set[Node]] = {}
        for n in nodes:
            self.add_node(n)

    def add_node(self, n: Node) -> None:
        self._succ.setdefault(n, set())
        self._pred.setdefault(n, set())

    def add_edge(self, src: Node, dst: Node) -> None:
        if src not in self._succ or dst not in self._succ:
            raise KeyError(f"both endpoints must be added first: {src!r} -> {dst!r}")
        self._succ[src].add(dst)
        self._pred[dst].add(src)

    def remove_edge(self, src: Node, dst: Node) -> None:
        self._succ.get(src, set()).discard(dst)
        self._pred.get(dst, set()).discard(src)

    def has_edge(self, src: Node, dst: Node) -> bool:
        return dst in self._succ.get(src, ())

    @property
    def nodes(self) -> list[Node]:
        return sorted(self._succ, key=sort_key)

    def successors(self, n: Node) -> list[Node]:
        return sorted(self._succ.get(n, ()), key=sort_key)

    def predecessors(self, n: Node) -> list[Node]:
        return sorted(self._pred.get(n, ()), key=sort_key)

    def edges(self) -> list[tuple[Node, Node]]:
        return sorted(
            ((s, d) for s, ds in self._succ.items() for d in ds),
            key=lambda e: (sort_key(e[0]), sort_key(e[1])),
        )

    def subgraph(self, nodes: Iterable[Node]) -> "Digraph":
        keep = set(nodes)
        g = Digraph(sorted(keep, key=sort_key))
        for s, d in self.edges():
            if s in keep and d in keep:
                g.add_edge(s, d)
        return g

    def copy(self) -> "Digraph":
        g = Digraph(self.nodes)
        for s, d in self.edges():
            g.add_edge(s, d)
        return g

    def __len__(self) -> int:
        return len(self._succ)


def strongly_connected_components(g: Digraph) -> list[list[Node]]:
    """Tarjan's algorithm, iterative.

    Returns every SCC (including singletons), each member list sorted, in
    reverse topological order of the condensation. Roots are visited in
    g.nodes order and neighbours in successors() order, so the emission order
    is fully determined by the node names.
    """
    index: dict[Node, int] = {}
    low: dict[Node, int] = {}
    on_stack: dict[Node, bool] = {}
    stack: list[Node] = []
    out: list[list[Node]] = []
    counter = 0

    for root in g.nodes:
        if root in index:
            continue
        index[root] = low[root] = counter
        counter += 1
        stack.append(root)
        on_stack[root] = True
        work: list[tuple[Node, list[Node], int]] = [(root, g.successors(root), 0)]

        while work:
            v, succs, i = work[-1]
            if i < len(succs):
                work[-1] = (v, succs, i + 1)
                w = succs[i]
                if w not in index:
                    index[w] = low[w] = counter
                    counter += 1
                    stack.append(w)
                    on_stack[w] = True
                    work.append((w, g.successors(w), 0))
                elif on_stack.get(w):
                    low[v] = min(low[v], index[w])
                continue

            work.pop()
            if work:
                parent = work[-1][0]
                low[parent] = min(low[parent], low[v])
            if low[v] == index[v]:
                comp: list[Node] = []
                while True:
                    x = stack.pop()
                    on_stack[x] = False
                    comp.append(x)
                    if x == v:
                        break
                out.append(sorted(comp, key=sort_key))

    return out


def nontrivial_sccs(g: Digraph) -> list[list[Node]]:
    """SCCs that represent an actual contradiction: 2+ members, or a self-loop."""
    return [
        c
        for c in strongly_connected_components(g)
        if len(c) > 1 or (len(c) == 1 and g.has_edge(c[0], c[0]))
    ]


def find_cycle(g: Digraph) -> list[Node] | None:
    """Return one cycle as a node list, or None when the graph is acyclic."""
    WHITE, GREY, BLACK = 0, 1, 2
    color: dict[Node, int] = {n: WHITE for n in g.nodes}
    parent: dict[Node, Node] = {}

    for root in g.nodes:
        if color[root] != WHITE:
            continue
        color[root] = GREY
        work: list[tuple[Node, list[Node], int]] = [(root, g.successors(root), 0)]
        while work:
            v, succs, i = work[-1]
            if i < len(succs):
                work[-1] = (v, succs, i + 1)
                w = succs[i]
                if color.get(w) == GREY:
                    cycle = [w]
                    x = v
                    while x != w:
                        cycle.append(x)
                        x = parent[x]
                    cycle.reverse()
                    return cycle
                if color.get(w, WHITE) == WHITE:
                    color[w] = GREY
                    parent[w] = v
                    work.append((w, g.successors(w), 0))
                continue
            color[v] = BLACK
            work.pop()
    return None


def is_acyclic(g: Digraph) -> bool:
    return find_cycle(g) is None


def condensation(
    g: Digraph, components: Sequence[Sequence[Node]]
) -> tuple[Digraph, dict[Node, Node], dict[Node, list[Node]]]:
    """Collapse each component to a single node named after its best sort_key."""
    group_of: dict[Node, Node] = {}
    members: dict[Node, list[Node]] = {}
    for comp in components:
        ordered = sorted(comp, key=sort_key)
        gid = ordered[0]
        members[gid] = ordered
        for n in ordered:
            group_of[n] = gid

    cg = Digraph(sorted(members, key=sort_key))
    for s, d in g.edges():
        gs, gd = group_of[s], group_of[d]
        if gs != gd:
            cg.add_edge(gs, gd)
    return cg, group_of, members
