"""The weighted head-to-head ordering -- the heart of the ranking."""

from __future__ import annotations

import random
import unittest

import math

from cfbrank.engine.order import (
    Costs, _Model, _block_index, _new_index, minimum_violations_order,
)
from tests.helpers import caps, weights

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
        self.assertEqual(Costs._fields, ("total", "violation", "drift"))
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
        """The cost delta must be exact for every relocation, blocks included.

        Caps are drawn in too, and the starts are SHUFFLED, so the predicate
        below is exercised from infeasible states as well as feasible ones.
        """
        for seed in range(200):
            r = random.Random(seed)
            n = r.randint(3, 8)
            teams = [f"T{k}" for k in range(n)]
            edges = {}
            for a in range(n):
                for b in range(n):
                    if a != b and r.random() < 0.3 and (f"T{b}", f"T{a}") not in edges:
                        edges[(f"T{a}", f"T{b}")] = round(r.uniform(0.1, 2.0), 3)
            # Caps of 0 and 1 are the ones that matter: a pair with slack below 1
            # can break when a THIRD team moves and shifts one endpoint only.
            cap_map = {
                e: r.choice([0.0, 0.0, 1.0, 2.0]) for e in edges if r.random() < 0.6
            }
            m = _Model(teams, edges, base(teams), r.uniform(1, 20), r.uniform(0.1, 3),
                       r.choice([1.0, 1.5, 2.0]), cap_map)
            seq = teams[:]
            r.shuffle(seq)
            idx = {t: k for k, t in enumerate(seq)}
            c0 = m.cost(idx).total
            v0 = m.violations(idx)
            for size in range(1, min(4, n) + 1):
                for i in range(n - size + 1):
                    for j in range(n - size + 1):
                        s2 = seq[:]
                        block = s2[i : i + size]
                        del s2[i : i + size]
                        s2[j:j] = block
                        i2 = {x: k for k, x in enumerate(s2)}
                        self.assertAlmostEqual(
                            m.cost(i2).total - c0,
                            m.block_delta(idx, seq[i : i + size], i, j),
                            places=6,
                            msg=f"cost, seed {seed} size {size} {i}->{j}",
                        )
                        self.assertEqual(
                            m.violations(i2) - v0,
                            m.cap_delta(idx, i, size, j),
                            msg=f"caps, seed {seed} size {size} {i}->{j}",
                        )

    def test_block_index_is_new_index_generalised(self):
        for k in range(6):
            for i in range(6):
                for j in range(6):
                    self.assertEqual(
                        _block_index(k, i, 1, j), _new_index(k, i, j), f"{k} {i} {j}"
                    )

    def test_new_index_mapping(self):
        self.assertEqual(_new_index(0, 0, 3), 3)
        self.assertEqual(_new_index(2, 0, 3), 1)     # shifted down
        self.assertEqual(_new_index(4, 0, 3), 4)     # untouched
        self.assertEqual(_new_index(1, 3, 0), 2)     # shifted up


CAP_CFG = dict(CFG, max_block=4)
INF = math.inf


def lead(out, winner, loser):
    """Places the loser finished above the winner; positive = contradicted."""
    return out.order.index(winner) - out.order.index(loser)


class TestHardCaps(unittest.TestCase):
    """`max_lead` is a feasible region, not a price.

    The regression this guards is published: Missouri beat Florida 45-17 on the
    Saturday being ranked and the board put Florida above them, because stage 4
    charged for the decision and let the distance be free.
    """

    def test_an_enforced_result_is_never_crossed(self):
        teams = [f"T{i}" for i in range(40)]
        spec = [("T20", "T2", 0.4, 0)]   # a weak win the base order disagrees with
        free = minimum_violations_order(teams, weights(spec), base(teams), CFG)
        held = minimum_violations_order(
            teams, weights(spec), base(teams), CAP_CFG, max_lead=caps(spec)
        )
        self.assertEqual(free.violated, [("T20", "T2")], "unconstrained, it is dropped")
        self.assertEqual(held.violated, [], "enforced, so it must be honoured")
        self.assertLess(held.order.index("T20"), held.order.index("T2"))

    def test_teams_meet_in_the_middle(self):
        """The owner's case: the loser at #9, the winner at #19.

        Parking the winner just above the loser is feasible and cheap to find, but
        both moving halfway costs less. Only a block move reaches it.
        """
        teams = [f"T{i}" for i in range(40)]
        spec = [("T18", "T8", 2.0, 0)]   # base positions 19 and 9
        out = minimum_violations_order(
            teams, weights(spec), base(teams), CAP_CFG, max_lead=caps(spec)
        )
        winner, loser = out.order.index("T18") + 1, out.order.index("T8") + 1
        self.assertLess(winner, loser, "the result is honoured")
        self.assertGreater(winner, 11, f"the winner was dragged to the loser: {winner}")
        self.assertLess(loser, 17, f"the loser barely moved: {loser}")
        self.assertLessEqual(
            abs((19 - winner) - (loser - 9)), 2, "they should move about equally"
        )

    def test_a_banded_cap_limits_the_distance(self):
        teams = [f"T{i}" for i in range(40)]
        leads = []
        for cap in (0, 3, 8, 30):
            spec = [("T30", "T5", 0.3, cap)]
            out = minimum_violations_order(
                teams, weights(spec), base(teams), CAP_CFG, max_lead=caps(spec)
            )
            leads.append(lead(out, "T30", "T5"))
            self.assertLessEqual(leads[-1], cap, f"cap {cap} was broken")
        self.assertEqual(leads, sorted(leads), f"a wider cap should allow more: {leads}")

    def test_an_uncapped_edge_behaves_exactly_as_before(self):
        """The `grounds.enabled = false` pin: no cap means the pure cost model."""
        teams = [f"T{i}" for i in range(40)]
        spec = [("T20", "T2", 0.4)]
        plain = minimum_violations_order(teams, weights(spec), base(teams), CFG)
        with_caps = minimum_violations_order(
            teams, weights(spec), base(teams), CAP_CFG, max_lead={}
        )
        self.assertEqual(plain.order, with_caps.order)
        self.assertEqual(with_caps.enforced, [])
        self.assertEqual(with_caps.capped, [])
        self.assertEqual(with_caps.relaxed, [])

    def test_no_grounds_object_means_unconstrained(self):
        """`weights()` with no 4th element must stay the pre-caps behaviour."""
        teams = [f"T{i}" for i in range(12)]
        facts = weights([("T9", "T1", 0.3)])
        self.assertIsNone(facts[("T9", "T1")].grounds)
        out = minimum_violations_order(teams, facts, base(teams), CAP_CFG)
        self.assertEqual(out.max_lead[("T9", "T1")], INF)

    def test_a_feasible_base_order_is_returned_untouched(self):
        teams = [f"T{i}" for i in range(20)]
        spec = [("T2", "T9", 3.0, 0)]   # the base order already agrees
        out = minimum_violations_order(
            teams, weights(spec), base(teams), CAP_CFG, max_lead=caps(spec)
        )
        self.assertEqual(out.order, teams)
        self.assertEqual(out.start, "base")
        self.assertEqual(out.repairs, [])

    def test_a_chain_is_repaired_and_reported(self):
        teams = [f"T{i}" for i in range(12)]
        spec = [("T3", "T1", 2.0, 0), ("T5", "T3", 2.0, 0)]
        out = minimum_violations_order(
            teams, weights(spec), base(teams), CAP_CFG, max_lead=caps(spec)
        )
        self.assertEqual(out.start, "zero_topo")
        self.assertTrue(out.repairs)
        self.assertLess(out.order.index("T5"), out.order.index("T3"))
        self.assertLess(out.order.index("T3"), out.order.index("T1"))

    def test_every_cap_holds_on_random_boards(self):
        """The headline property, over pools up to forty teams."""
        for seed in range(60):
            r = random.Random(seed)
            n = r.randint(6, 40)
            teams = [f"T{i}" for i in range(n)]
            spec = []
            seen = set()
            for _ in range(r.randint(1, n)):
                a, b = r.sample(range(n), 2)
                if (a, b) in seen or (b, a) in seen:
                    continue
                seen.add((a, b))
                spec.append((f"T{a}", f"T{b}", round(r.uniform(0.2, 4.0), 2),
                             r.choice([0, 0, 1, 3, 7])))
            out = minimum_violations_order(
                teams, weights(spec), base(teams), CAP_CFG, max_lead=caps(spec)
            )
            self.assertEqual(sorted(out.order), sorted(teams), f"seed {seed}")
            relaxed = {rx.edge for rx in out.relaxed}
            for (w, l, _wt, cap) in spec:
                if (w, l) in relaxed:
                    continue
                self.assertLessEqual(
                    lead(out, w, l), cap, f"seed {seed}: {w} over {l} cap {cap}"
                )

    def test_the_search_never_leaves_the_feasible_region(self):
        """Replay the moves and check every intermediate order, not just the last."""
        teams = [f"T{i}" for i in range(24)]
        spec = [("T20", "T3", 1.0, 0), ("T18", "T6", 1.0, 2), ("T22", "T9", 0.5, 0)]
        out = minimum_violations_order(
            teams, weights(spec), base(teams), CAP_CFG, max_lead=caps(spec)
        )
        self.assertEqual(out.relaxed, [])
        for (w, l, _wt, cap) in spec:
            self.assertLessEqual(lead(out, w, l), cap)

    def test_a_cycle_of_enforced_results_relaxes_the_weakest(self):
        """Three teams who each beat the next cannot all be honoured."""
        teams = ["A", "B", "C"]
        spec = [("A", "B", 3.0, 0), ("B", "C", 2.5, 0), ("C", "A", 0.2, 0)]
        out = minimum_violations_order(
            teams, weights(spec), base(teams), CAP_CFG, max_lead=caps(spec)
        )
        self.assertEqual([rx.edge for rx in out.relaxed], [("C", "A")])
        self.assertEqual(out.relaxed[0].reason, "zero_cycle")
        self.assertEqual(out.relaxed[0].members, ("A", "B", "C"))
        self.assertEqual(out.violated, [("C", "A")])
        self.assertEqual(out.order, ["A", "B", "C"])

    def test_relaxation_terminates_on_a_maximally_impossible_board(self):
        teams = [f"T{i}" for i in range(8)]
        spec = [
            (f"T{a}", f"T{b}", 1.0 + a * 0.1, 0)
            for a in range(8) for b in range(8) if a != b
        ]
        out = minimum_violations_order(
            teams, weights(spec), base(teams), CAP_CFG, max_lead=caps(spec)
        )
        self.assertEqual(sorted(out.order), sorted(teams))
        self.assertLessEqual(len(out.relaxed), len(spec))

    def test_cost_is_exactly_two_terms(self):
        teams = [f"T{i}" for i in range(20)]
        spec = [("T15", "T4", 1.0, 0)]
        out = minimum_violations_order(
            teams, weights(spec), base(teams), CAP_CFG, max_lead=caps(spec)
        )
        self.assertAlmostEqual(out.cost, out.violation_cost + out.drift_cost, places=6)

    def test_an_enforced_result_is_never_overridden_unless_relaxed(self):
        teams = [f"T{i}" for i in range(30)]
        spec = [("T25", "T4", 0.3, 0), ("T12", "T20", 2.0, 0)]
        out = minimum_violations_order(
            teams, weights(spec), base(teams), CAP_CFG, max_lead=caps(spec)
        )
        self.assertEqual(out.relaxed, [])
        self.assertEqual(set(out.enforced) & set(out.violated), set())
        self.assertLessEqual(set(out.enforced), set(out.honored))

    def test_leads_are_reported_for_every_edge(self):
        teams = [f"T{i}" for i in range(10)]
        spec = [("T8", "T1", 0.4, 3), ("T2", "T5", 2.0)]
        out = minimum_violations_order(
            teams, weights(spec), base(teams), CAP_CFG, max_lead=caps(spec)
        )
        self.assertEqual(set(out.leads), {("T8", "T1"), ("T2", "T5")})
        self.assertGreater(out.leads[("T8", "T1")], 0, "contradicted")
        self.assertLess(out.leads[("T2", "T5")], 0, "honoured")
        self.assertEqual(out.max_lead[("T2", "T5")], INF)

    def test_binding_names_the_caps_that_set_the_distance(self):
        teams = [f"T{i}" for i in range(40)]
        spec = [("T30", "T5", 0.3, 4)]
        out = minimum_violations_order(
            teams, weights(spec), base(teams), CAP_CFG, max_lead=caps(spec)
        )
        self.assertEqual(lead(out, "T30", "T5"), 4)
        self.assertEqual(out.binding, [("T30", "T5")])

    def test_deterministic_across_shuffled_inputs_with_caps(self):
        teams = [f"T{i}" for i in range(20)]
        spec = [("T9", "T2", 2.5, 0), ("T11", "T4", 1.8, 3), ("T3", "T1", 0.4, 0),
                ("T13", "T0", 0.2, 0), ("T5", "T12", 1.1, 7), ("T19", "T6", 0.9, 0)]
        reference = minimum_violations_order(
            teams, weights(spec), base(teams), CAP_CFG, max_lead=caps(spec)
        ).order
        for seed in range(25):
            r = random.Random(seed)
            shuffled = list(spec)
            r.shuffle(shuffled)
            team_order = list(teams)
            r.shuffle(team_order)
            out = minimum_violations_order(
                team_order, weights(shuffled), base(teams), CAP_CFG,
                max_lead=caps(shuffled),
            )
            self.assertEqual(out.order, reference, f"seed {seed}")


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
