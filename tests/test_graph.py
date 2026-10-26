"""Tarjan, cycle finding, and the determinism the whole pipeline rests on."""

from __future__ import annotations

import random
import unittest

from cfbrank.engine.graph import (
    Digraph,
    condensation,
    find_cycle,
    is_acyclic,
    shortest_path,
    nontrivial_sccs,
    strongly_connected_components,
)
from tests.helpers import graph


class TestSCC(unittest.TestCase):
    def test_empty_and_single(self):
        self.assertEqual(strongly_connected_components(Digraph()), [])
        self.assertEqual(strongly_connected_components(graph("A", [])), [["A"]])
        self.assertEqual(nontrivial_sccs(graph("A", [])), [])

    def test_self_loop_counts_as_nontrivial(self):
        g = graph("A", [("A", "A")])
        self.assertEqual(nontrivial_sccs(g), [["A"]])

    def test_pure_dag_has_only_singletons(self):
        g = graph("ABCD", [("A", "B"), ("B", "C"), ("A", "D")])
        self.assertEqual(len(strongly_connected_components(g)), 4)
        self.assertEqual(nontrivial_sccs(g), [])
        self.assertTrue(is_acyclic(g))

    def test_canonical_three_cycle(self):
        g = graph("ABC", [("A", "B"), ("B", "C"), ("C", "A")])
        self.assertEqual(nontrivial_sccs(g), [["A", "B", "C"]])
        self.assertFalse(is_acyclic(g))

    def test_two_cycle(self):
        g = graph("AB", [("A", "B"), ("B", "A")])
        self.assertEqual(nontrivial_sccs(g), [["A", "B"]])

    def test_two_disjoint_cycles(self):
        g = graph("ABCDEF", [("A", "B"), ("B", "C"), ("C", "A"), ("D", "E"), ("E", "F"), ("F", "D")])
        self.assertEqual(sorted(nontrivial_sccs(g)), [["A", "B", "C"], ["D", "E", "F"]])

    def test_triangles_sharing_a_node_are_one_component(self):
        # The classic bug is reporting two 3-cycles here instead of one 5-SCC.
        g = graph("ABCDE", [("A", "B"), ("B", "C"), ("C", "A"), ("C", "D"), ("D", "E"), ("E", "C")])
        self.assertEqual(nontrivial_sccs(g), [["A", "B", "C", "D", "E"]])

    def test_long_chain_with_one_back_edge(self):
        nodes = [f"T{i}" for i in range(12)]
        edges = [(nodes[i], nodes[i + 1]) for i in range(11)] + [(nodes[11], nodes[0])]
        self.assertEqual(nontrivial_sccs(graph(nodes, edges)), [sorted(nodes)])

    def test_reverse_topological_emission_order(self):
        g = graph("ABCD", [("A", "B"), ("B", "C"), ("C", "D")])
        self.assertEqual(strongly_connected_components(g), [["D"], ["C"], ["B"], ["A"]])

    def test_determinism_across_shuffled_construction(self):
        nodes = list("ABCDEFGH")
        edges = [("A", "B"), ("B", "C"), ("C", "A"), ("D", "E"), ("E", "F"),
                 ("F", "D"), ("C", "D"), ("G", "H")]
        reference = strongly_connected_components(graph(nodes, edges))
        for seed in range(25):
            r = random.Random(seed)
            n, e = list(nodes), list(edges)
            r.shuffle(n)
            r.shuffle(e)
            self.assertEqual(strongly_connected_components(graph(n, e)), reference, f"seed {seed}")

    def test_deep_chain_does_not_hit_the_recursion_limit(self):
        nodes = [f"N{i:05d}" for i in range(4000)]
        edges = [(nodes[i], nodes[i + 1]) for i in range(3999)]
        self.assertEqual(len(strongly_connected_components(graph(nodes, edges))), 4000)


class TestCycleFinding(unittest.TestCase):
    def test_none_on_a_dag(self):
        self.assertIsNone(find_cycle(graph("ABC", [("A", "B"), ("B", "C")])))

    def test_returns_a_real_cycle(self):
        g = graph("ABCD", [("A", "B"), ("B", "C"), ("C", "A"), ("C", "D")])
        cycle = find_cycle(g)
        self.assertIsNotNone(cycle)
        for i, node in enumerate(cycle):
            self.assertTrue(g.has_edge(node, cycle[(i + 1) % len(cycle)]))


class TestCondensation(unittest.TestCase):
    def test_condensation_is_acyclic(self):
        g = graph("ABCDE", [("A", "B"), ("B", "C"), ("C", "A"), ("C", "D"), ("D", "E")])
        comps = strongly_connected_components(g)
        cg, group_of, members = condensation(g, comps)
        self.assertTrue(is_acyclic(cg))
        self.assertEqual(group_of["B"], group_of["C"])
        self.assertEqual(members[group_of["A"]], ["A", "B", "C"])


class TestShortestPath(unittest.TestCase):
    """Used to name the chain that holds a set-aside result's loser on top."""

    TRIANGLE = {"Missouri": {"Florida"}, "Florida": {"Ole Miss"}, "Ole Miss": {"Missouri"}}

    def test_finds_the_chain(self):
        self.assertEqual(
            shortest_path(self.TRIANGLE, "Missouri", "Ole Miss"),
            ("Missouri", "Florida", "Ole Miss"),
        )

    def test_prefers_the_shorter_chain(self):
        succ = {"A": {"B", "Z"}, "B": {"C"}, "C": {"D"}, "Z": {"D"}}
        self.assertEqual(shortest_path(succ, "A", "D"), ("A", "Z", "D"))

    def test_no_route_is_empty_not_an_error(self):
        self.assertEqual(shortest_path({"A": {"B"}}, "B", "A"), ())
        self.assertEqual(shortest_path({}, "A", "B"), ())

    def test_a_node_does_not_reach_itself(self):
        """The caller asks "does the loser reach the winner"; A -> A is no chain."""
        self.assertEqual(shortest_path(self.TRIANGLE, "Missouri", "Missouri"), ())

    def test_ties_break_on_sort_key_not_insertion(self):
        """This feeds a published sentence, so an unstable path would churn
        content_hash with no change in the ranking."""
        succ = {"A": {"zeta", "Alpha"}, "zeta": {"D"}, "Alpha": {"D"}}
        reference = shortest_path(succ, "A", "D")
        self.assertEqual(reference, ("A", "Alpha", "D"))
        for seed in range(10):
            shuffled = {k: set(random.sample(sorted(v), len(v))) for k, v in succ.items()}
            self.assertEqual(shortest_path(shuffled, "A", "D"), reference)


class TestDigraph(unittest.TestCase):
    def test_accessors_are_sorted(self):
        g = graph(["b", "a", "c"], [("a", "c"), ("a", "b")])
        self.assertEqual(g.nodes, ["a", "b", "c"])
        self.assertEqual(g.successors("a"), ["b", "c"])
        self.assertEqual(g.predecessors("b"), ["a"])

    def test_edge_requires_known_endpoints(self):
        g = Digraph(["a"])
        with self.assertRaises(KeyError):
            g.add_edge("a", "zzz")

    def test_subgraph_keeps_only_internal_edges(self):
        g = graph("ABC", [("A", "B"), ("B", "C")])
        self.assertEqual(g.subgraph(["A", "B"]).edges(), [("A", "B")])


if __name__ == "__main__":
    unittest.main()
