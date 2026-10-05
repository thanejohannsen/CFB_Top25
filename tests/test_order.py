"""The weighted head-to-head ordering -- the heart of the ranking."""

from __future__ import annotations

import random
import unittest

from cfbrank.engine.order import _Model, _new_index, minimum_violations_order
from tests.helpers import weights

CFG = {"strength": 14.0, "drift_weight": 1.0, "drift_exponent": 1.5, "max_passes": 400}


def base(teams):
    return {t: i + 1 for i, t in enumerate(teams)}


class TestCostModel(unittest.TestCase):
    def test_cost_counts_only_backward_edges(self):
        teams = ["A", "B"]
        m = _Model(teams, {("B", "A"): 2.0}, base(teams), 10.0, 1.0, 1.0)
        # A first: B's win over A runs backward, so it is violated.
        total, viol, drift = m.cost({"A": 0, "B": 1})
        self.assertAlmostEqual(viol, 20.0)
        self.assertAlmostEqual(drift, 0.0)
        # B first: the result is honoured but both teams are one off their base.
        total, viol, drift = m.cost({"B": 0, "A": 1})
        self.assertAlmostEqual(viol, 0.0)
        self.assertAlmostEqual(drift, 2.0)

    def test_drift_exponent_makes_long_moves_dearer(self):
        teams = [f"T{i}" for i in range(10)]
        linear = _Model(teams, {}, base(teams), 1.0, 1.0, 1.0)
        superlinear = _Model(teams, {}, base(teams), 1.0, 1.0, 2.0)
        idx = {t: i for i, t in enumerate(teams)}
        far = dict(idx)
        far["T0"], far["T9"] = 9, 0
        self.assertAlmostEqual(linear.cost(far)[2], 18.0)
        self.assertAlmostEqual(superlinear.cost(far)[2], 162.0)

    def test_move_delta_matches_full_recompute(self):
        for seed in range(200):
            r = random.Random(seed)
            n = r.randint(3, 8)
            teams = [f"T{k}" for k in range(n)]
            edges = {}
            for a in range(n):
                for b in range(n):
                    if a != b and r.random() < 0.3 and (f"T{b}", f"T{a}") not in edges:
                        edges[(f"T{a}", f"T{b}")] = round(r.uniform(0.1, 2.0), 3)
            m = _Model(teams, edges, base(teams), r.uniform(1, 20), r.uniform(0.1, 3),
                       r.choice([1.0, 1.5, 2.0]))
            seq = teams[:]
            r.shuffle(seq)
            idx = {t: k for k, t in enumerate(seq)}
            c0 = m.cost(idx)[0]
            for i in range(n):
                for j in range(n):
                    s2 = seq[:]
                    s2.pop(i)
                    s2.insert(j, seq[i])
                    i2 = {x: k for k, x in enumerate(s2)}
                    self.assertAlmostEqual(
                        m.cost(i2)[0] - c0, m.move_delta(idx, seq[i], i, j), places=6,
                        msg=f"seed {seed} move {i}->{j}",
                    )

    def test_new_index_mapping(self):
        self.assertEqual(_new_index(0, 0, 3), 3)
        self.assertEqual(_new_index(2, 0, 3), 1)     # shifted down
        self.assertEqual(_new_index(4, 0, 3), 4)     # untouched
        self.assertEqual(_new_index(1, 3, 0), 2)     # shifted up


class TestOrdering(unittest.TestCase):
    def test_no_results_leaves_the_base_order_untouched(self):
        teams = [f"T{i}" for i in range(12)]
        out = minimum_violations_order(teams, {}, base(teams), CFG)
        self.assertEqual(out.order, teams)
        self.assertEqual(out.violated, [])
        self.assertEqual(max(abs(d) for d in out.drift.values()), 0)

    def test_a_team_nobody_played_never_moves(self):
        """The regression test for the topological-sort pathology.

        Under hard enforcement, teams with no results float to the top because
        nothing blocks them. Here an unconstrained team must simply stay put.
        """
        teams = [f"T{i}" for i in range(8)]
        edges = weights([("T5", "T1", 3.0), ("T6", "T2", 3.0)])
        out = minimum_violations_order(teams, edges, base(teams), CFG)
        self.assertEqual(out.drift["T7"], 0, "a team with no results should not move")
        self.assertEqual(out.drift["T0"], 0)

    def test_a_convincing_win_is_honoured(self):
        teams = ["A", "B"]
        out = minimum_violations_order(teams, weights([("B", "A", 3.0)]), base(teams), CFG)
        self.assertEqual(out.order, ["B", "A"])
        self.assertEqual(out.violated, [])

    def test_a_weak_win_across_a_wide_gap_is_overridden(self):
        teams = [f"T{i}" for i in range(40)]
        out = minimum_violations_order(teams, weights([("T35", "T2", 0.15)]), base(teams), CFG)
        self.assertEqual(out.violated, [("T35", "T2")])
        self.assertLess(abs(out.drift["T2"]), 3)

    def test_conviction_scales_how_far_a_team_can_move(self):
        teams = [f"T{i}" for i in range(40)]
        weak = minimum_violations_order(teams, weights([("T20", "T10", 0.5)]), base(teams), CFG)
        strong = minimum_violations_order(teams, weights([("T20", "T10", 4.0)]), base(teams), CFG)
        self.assertGreater(-strong.drift["T20"], -weak.drift["T20"],
                           "a more convincing win should move a team further")

    def test_local_search_never_increases_cost(self):
        teams = [f"T{i}" for i in range(14)]
        edges = weights([("T9", "T2", 2.5), ("T11", "T4", 1.8), ("T3", "T1", 0.4), ("T13", "T0", 0.2)])
        start = _Model(teams, {k: f.weight for k, f in edges.items()}, base(teams), 14.0, 1.0, 1.5)
        begin = start.cost({t: i for i, t in enumerate(teams)})[0]
        out = minimum_violations_order(teams, edges, base(teams), CFG)
        self.assertLessEqual(out.cost, begin + 1e-9)

    def test_three_cycle_overrides_exactly_the_weakest_result(self):
        teams = ["A", "B", "C"]
        edges = weights([("A", "B", 3.0), ("B", "C", 2.5), ("C", "A", 0.2)])
        out = minimum_violations_order(teams, edges, base(teams), CFG)
        self.assertEqual(out.violated, [("C", "A")])
        self.assertEqual(out.order, ["A", "B", "C"])

    def test_rotating_the_weakest_result_rotates_the_answer(self):
        """Proves the choice is driven by conviction, not by input order."""
        teams = ["A", "B", "C"]
        edges = weights([("A", "B", 0.2), ("B", "C", 3.0), ("C", "A", 2.5)])
        out = minimum_violations_order(teams, edges, base(teams), CFG)
        self.assertEqual(out.violated, [("A", "B")])

    def test_deterministic_across_shuffled_inputs(self):
        teams = [f"T{i}" for i in range(20)]
        spec = [("T9", "T2", 2.5), ("T11", "T4", 1.8), ("T3", "T1", 0.4),
                ("T13", "T0", 0.2), ("T5", "T12", 1.1), ("T19", "T6", 0.9)]
        reference = minimum_violations_order(teams, weights(spec), base(teams), CFG).order
        for seed in range(25):
            r = random.Random(seed)
            shuffled = list(spec)
            r.shuffle(shuffled)
            team_order = list(teams)
            r.shuffle(team_order)
            out = minimum_violations_order(team_order, weights(shuffled), base(teams), CFG)
            self.assertEqual(out.order, reference, f"seed {seed}")

    def test_every_team_appears_exactly_once(self):
        teams = [f"T{i}" for i in range(30)]
        spec = [(f"T{i+3}", f"T{i}", 1.0 + (i % 5) * 0.4) for i in range(0, 25, 2)]
        out = minimum_violations_order(teams, weights(spec), base(teams), CFG)
        self.assertEqual(sorted(out.order), sorted(teams))
        self.assertEqual(len(out.order), len(teams))

    def test_honored_and_violated_partition_the_results(self):
        teams = [f"T{i}" for i in range(16)]
        spec = [("T7", "T1", 2.0), ("T9", "T3", 0.3), ("T2", "T11", 1.5)]
        out = minimum_violations_order(teams, weights(spec), base(teams), CFG)
        self.assertEqual(len(out.honored) + len(out.violated), len(spec))
        self.assertEqual(set(out.honored) & set(out.violated), set())

    def test_zero_strength_ignores_results_entirely(self):
        teams = [f"T{i}" for i in range(10)]
        cfg = dict(CFG, strength=0.0)
        out = minimum_violations_order(teams, weights([("T9", "T0", 5.0)]), base(teams), cfg)
        self.assertEqual(out.order, teams)


if __name__ == "__main__":
    unittest.main()
