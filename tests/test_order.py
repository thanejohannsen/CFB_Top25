"""The weighted head-to-head ordering -- the heart of the ranking."""

from __future__ import annotations

import random
import unittest

from dataclasses import replace

from cfbrank.engine.order import _Model, _new_index, minimum_violations_order
from tests.helpers import grounds, weights

CFG = {"strength": 14.0, "drift_weight": 1.0, "drift_exponent": 1.5, "max_passes": 400}


def base(teams):
    return {t: i + 1 for i, t in enumerate(teams)}


class TestCostModel(unittest.TestCase):
    def test_cost_counts_only_backward_edges(self):
        teams = ["A", "B"]
        m = _Model(teams, {("B", "A"): 2.0}, base(teams), 10.0, 1.0, 1.0)
        # A first: B's win over A runs backward, so it is violated.
        costs = m.cost({"A": 0, "B": 1})
        self.assertAlmostEqual(costs.violation, 20.0)
        self.assertAlmostEqual(costs.drift, 0.0)
        self.assertAlmostEqual(costs.gap, 0.0)
        # B first: the result is honoured but both teams are one off their base.
        costs = m.cost({"B": 0, "A": 1})
        self.assertAlmostEqual(costs.violation, 0.0)
        self.assertAlmostEqual(costs.drift, 2.0)

    def test_drift_exponent_makes_long_moves_dearer(self):
        teams = [f"T{i}" for i in range(10)]
        linear = _Model(teams, {}, base(teams), 1.0, 1.0, 1.0)
        superlinear = _Model(teams, {}, base(teams), 1.0, 1.0, 2.0)
        idx = {t: i for i, t in enumerate(teams)}
        far = dict(idx)
        far["T0"], far["T9"] = 9, 0
        self.assertAlmostEqual(linear.cost(far).drift, 18.0)
        self.assertAlmostEqual(superlinear.cost(far).drift, 162.0)

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
            # Licence a random subset, so the gap term is exercised too. It is
            # NOT incident-local -- moving a third team can shift one endpoint of
            # a pair and not the other -- so this is the test that catches a
            # delta that forgot to look past the moved team's own edges.
            allowance = {
                key: float(r.randint(0, n)) for key in edges if r.random() < 0.6
            }
            m = _Model(teams, edges, base(teams), r.uniform(1, 20), r.uniform(0.1, 3),
                       r.choice([1.0, 1.5, 2.0]), allowance=allowance,
                       gap_weight=r.choice([0.0, 0.5, 2.0]),
                       gap_exponent=r.choice([1.0, 1.5]))
            seq = teams[:]
            r.shuffle(seq)
            idx = {t: k for k, t in enumerate(seq)}
            c0 = m.cost(idx).total
            for i in range(n):
                for j in range(n):
                    s2 = seq[:]
                    s2.pop(i)
                    s2.insert(j, seq[i])
                    i2 = {x: k for k, x in enumerate(s2)}
                    self.assertAlmostEqual(
                        m.cost(i2).total - c0, m.move_delta(idx, seq[i], i, j), places=6,
                        msg=f"seed {seed} move {i}->{j}",
                    )

    def test_new_index_mapping(self):
        self.assertEqual(_new_index(0, 0, 3), 3)
        self.assertEqual(_new_index(2, 0, 3), 1)     # shifted down
        self.assertEqual(_new_index(4, 0, 3), 4)     # untouched
        self.assertEqual(_new_index(1, 3, 0), 2)     # shifted up


GAP_CFG = dict(CFG, grounds={"gap_weight": 1.5, "gap_exponent": 1.5})


class TestLicensedGap(unittest.TestCase):
    """Overriding a result is no longer binary: the gap it buys is licensed.

    The regression this guards is the published one -- Missouri beat Florida
    45-17 on the Saturday being ranked and the board put Florida eleven places
    higher, because the old cost model charged for the decision and gave the
    distance away.
    """

    def test_an_unlicensed_override_is_pulled_together(self):
        teams = [f"T{i}" for i in range(40)]
        # T20 beat T2 and nothing has happened since: 2 places of licence.
        spec = [("T20", "T2", 0.4, 2.0)]
        loose = minimum_violations_order(teams, weights(spec), base(teams), CFG)
        tight = minimum_violations_order(teams, weights(spec), base(teams), GAP_CFG)
        self.assertEqual(loose.violated, [("T20", "T2")])
        loose_gap = loose.order.index("T20") - loose.order.index("T2")
        tight_gap = tight.order.index("T20") - tight.order.index("T2")
        self.assertLess(tight_gap, loose_gap)

    def test_a_wide_licence_leaves_the_gap_alone(self):
        teams = [f"T{i}" for i in range(40)]
        spec = [("T20", "T2", 0.4, 30.0)]
        loose = minimum_violations_order(teams, weights(spec), base(teams), CFG)
        tight = minimum_violations_order(teams, weights(spec), base(teams), GAP_CFG)
        self.assertEqual(loose.order, tight.order)
        self.assertEqual(tight.excess[("T20", "T2")], 0.0)

    def test_a_wider_licence_permits_a_wider_gap(self):
        teams = [f"T{i}" for i in range(40)]
        gaps = []
        for licence in (2.0, 6.0, 12.0):
            out = minimum_violations_order(
                teams, weights([("T20", "T2", 0.4, licence)]), base(teams), GAP_CFG
            )
            gaps.append(out.order.index("T20") - out.order.index("T2"))
        self.assertEqual(gaps, sorted(gaps), f"licence should widen the gap: {gaps}")
        self.assertLess(gaps[0], gaps[-1])

    def test_an_honoured_result_never_pays_the_gap_term(self):
        """The licence limits contradicting a result, not agreeing with it."""
        teams = [f"T{i}" for i in range(20)]
        out = minimum_violations_order(
            teams, weights([("T2", "T19", 3.0, 1.0)]), base(teams), GAP_CFG
        )
        self.assertEqual(out.violated, [])
        self.assertEqual(out.gap_cost, 0.0)
        self.assertEqual(out.excess[("T2", "T19")], 0.0)
        self.assertEqual(out.order, teams, "a result the base order agrees with moves nobody")

    def test_an_unlicensed_edge_is_unconstrained(self):
        """No grounds object at all means the stage behaves as it did before."""
        teams = [f"T{i}" for i in range(40)]
        spec = [("T20", "T2", 0.4)]
        with_gap = minimum_violations_order(teams, weights(spec), base(teams), GAP_CFG)
        without = minimum_violations_order(teams, weights(spec), base(teams), CFG)
        self.assertEqual(with_gap.order, without.order)
        self.assertEqual(with_gap.gap_cost, 0.0)
        self.assertEqual(with_gap.excess, {})

    def test_relief_makes_a_result_cheaper_to_override(self):
        teams = ["A", "B"]
        full = weights([("B", "A", 2.0)])
        relieved = {
            ("B", "A"): replace(
                full[("B", "A")], grounds=grounds(2.0, relief=0.5)
            )
        }
        self.assertAlmostEqual(full[("B", "A")].price, 2.0)
        self.assertAlmostEqual(relieved[("B", "A")].price, 1.0)

    def test_gap_cost_is_reported_separately_from_the_other_terms(self):
        teams = [f"T{i}" for i in range(40)]
        out = minimum_violations_order(
            teams, weights([("T20", "T2", 0.4, 2.0)]), base(teams), GAP_CFG
        )
        self.assertGreater(out.gap_cost, 0.0)
        self.assertAlmostEqual(
            out.cost, out.violation_cost + out.drift_cost + out.gap_cost, places=6
        )

    def test_a_zero_charge_still_reports_the_overspill(self):
        """`gap_weight = 0` is the "publish it, do not act on it" setting."""
        teams = [f"T{i}" for i in range(40)]
        spec = [("T20", "T2", 0.4, 2.0)]
        cfg = dict(CFG, grounds={"gap_weight": 0.0, "gap_exponent": 1.5})
        free = minimum_violations_order(teams, weights(spec), base(teams), cfg)
        charged = minimum_violations_order(teams, weights(spec), base(teams), GAP_CFG)
        self.assertEqual(free.gap_cost, 0.0)
        self.assertGreater(free.excess[("T20", "T2")], 0.0, "measured even when free")
        self.assertLess(charged.excess[("T20", "T2")], free.excess[("T20", "T2")])

    def test_gaps_are_reported_for_every_edge(self):
        teams = [f"T{i}" for i in range(10)]
        out = minimum_violations_order(
            teams, weights([("T8", "T1", 0.4, 2.0), ("T2", "T5", 2.0)]), base(teams), GAP_CFG
        )
        self.assertEqual(set(out.gaps), {("T8", "T1"), ("T2", "T5")})
        self.assertGreater(out.gaps[("T8", "T1")], 0, "an overridden result has a positive gap")
        self.assertLess(out.gaps[("T2", "T5")], 0, "an honoured one has a negative gap")


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
        begin = start.cost({t: i for i, t in enumerate(teams)}).total
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
