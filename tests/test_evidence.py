"""Conviction scoring: venue, margin, recency, common opponents."""

from __future__ import annotations

import unittest

from cfbrank.engine.evidence import (
    common_opponent_diff,
    location_adjusted_margin,
    recency_weight,
    score_edges,
)
from cfbrank.engine.h2h import opponents
from tests.helpers import result

EV = {
    "w_margin": 0.50, "w_rating_gap": 0.30, "w_recency": 0.20, "w_common_opponents": 0.15,
    "home_field_points": 2.5, "margin_cap": 28, "recency_floor": 0.25,
}


class TestLocationAdjustedMargin(unittest.TestCase):
    def test_road_beats_neutral_beats_home(self):
        away = location_adjusted_margin(result("W", "L", 26, 20, site="away"), 2.5, 28)
        neutral = location_adjusted_margin(result("W", "L", 26, 20, site="neutral"), 2.5, 28)
        home = location_adjusted_margin(result("W", "L", 26, 20, site="home"), 2.5, 28)
        self.assertGreater(away, neutral)
        self.assertGreater(neutral, home)
        self.assertAlmostEqual(away, 8.5)
        self.assertAlmostEqual(neutral, 6.0)
        self.assertAlmostEqual(home, 3.5)

    def test_one_point_home_win_is_negative(self):
        # Home field alone is worth more than the margin, so the result is not
        # evidence that the winner is the better team.
        self.assertLess(location_adjusted_margin(result("W", "L", 21, 20, site="home"), 2.5, 28), 0)

    def test_margin_cap_saturates(self):
        big = location_adjusted_margin(result("W", "L", 70, 0, site="neutral"), 2.5, 28)
        capped = location_adjusted_margin(result("W", "L", 28, 0, site="neutral"), 2.5, 28)
        self.assertAlmostEqual(big, capped)
        self.assertAlmostEqual(big, 28.0)

    def test_zero_home_field_makes_venue_irrelevant(self):
        a = location_adjusted_margin(result("W", "L", 24, 17, site="home"), 0.0, 28)
        b = location_adjusted_margin(result("W", "L", 24, 17, site="away"), 0.0, 28)
        self.assertAlmostEqual(a, b)


class TestRecency(unittest.TestCase):
    """A result fades with a half-life in weeks, toward a floor rather than zero."""

    def test_a_fresh_result_counts_in_full(self):
        self.assertAlmostEqual(recency_weight(0, 6.0, 0.15), 1.0)

    def test_one_half_life_is_halfway_to_the_floor(self):
        # 0.15 + 0.85 * 0.5
        self.assertAlmostEqual(recency_weight(6.0, 6.0, 0.15), 0.575)

    def test_it_decays_toward_the_floor_and_never_below(self):
        self.assertGreater(recency_weight(100.0, 6.0, 0.15), 0.15)
        self.assertLess(recency_weight(100.0, 6.0, 0.15), 0.16)

    def test_a_longer_half_life_fades_slower(self):
        good = recency_weight(10.0, 8.0, 0.15)
        bad = recency_weight(10.0, 6.0, 0.15)
        self.assertGreater(good, bad, "a convincing result should outlast a weak one")

    def test_age_is_monotonic(self):
        weights = [recency_weight(w, 6.0, 0.15) for w in range(0, 15)]
        self.assertEqual(weights, sorted(weights, reverse=True))

class TestCommonOpponents(unittest.TestCase):
    def test_no_shared_opponents(self):
        res = [result("A", "X"), result("B", "Y")]
        shared, diff = common_opponent_diff("A", "B", opponents(res), 2.5, 28)
        self.assertEqual(shared, ())
        self.assertAlmostEqual(diff, 0.0)

    def test_winner_who_fared_better_scores_positive(self):
        res = [
            result("A", "Z", 35, 7, site="neutral"),   # A won by 28
            result("Z", "B", 21, 20, site="neutral"),  # B lost by 1
        ]
        shared, diff = common_opponent_diff("A", "B", opponents(res), 2.5, 28)
        self.assertEqual(shared, ("Z",))
        self.assertGreater(diff, 0)

    def test_sign_flips_when_the_loser_fared_better(self):
        res = [
            result("Z", "A", 35, 7, site="neutral"),
            result("B", "Z", 30, 3, site="neutral"),
        ]
        _, diff = common_opponent_diff("A", "B", opponents(res), 2.5, 28)
        self.assertLess(diff, 0)


class TestScoreEdges(unittest.TestCase):
    def test_empty_input(self):
        self.assertEqual(score_edges([], {}, {}, EV), {})

    def test_weights_are_strictly_positive(self):
        res = [result("A", "B", 21, 20, site="home"), result("C", "D", 49, 0, site="away")]
        facts = score_edges(res, {"A": 1, "B": 20, "C": 5, "D": 4}, opponents(res), EV)
        for f in facts.values():
            self.assertGreater(f.weight, 0.0, "overriding a result must always cost something")

    def test_an_upset_scores_below_a_chalk_win_of_equal_margin(self):
        res = [result("A", "B", 24, 17, site="home"), result("C", "D", 24, 17, site="home")]
        # A is far worse than B; C is far better than D.
        facts = score_edges(res, {"A": -10.0, "B": 20.0, "C": 20.0, "D": -10.0}, opponents(res), EV)
        self.assertLess(facts[("A", "B")].weight, facts[("C", "D")].weight)

    def test_identical_margins_do_not_divide_by_zero(self):
        # Every margin equal is the normal week-1 case: the z-scorer must
        # return 0 rather than raising.
        res = [result("A", "B", 24, 17, site="neutral"), result("C", "D", 24, 17, site="neutral")]
        facts = score_edges(res, {"A": 1.0, "B": 1.0, "C": 1.0, "D": 1.0}, opponents(res), EV)
        self.assertEqual(len(facts), 2)
        self.assertAlmostEqual(facts[("A", "B")].weight, facts[("C", "D")].weight)

    def test_components_are_reported_for_the_site(self):
        res = [result("A", "B"), result("C", "D")]
        facts = score_edges(res, {"A": 1, "B": 0, "C": 1, "D": 0}, opponents(res), EV)
        self.assertEqual(
            sorted(facts[("A", "B")].components),
            ["common_opponents", "margin", "rating_gap", "recency"],
        )

    def test_later_results_carry_more_recency(self):
        res = [result("A", "B", 24, 17, week=1), result("C", "D", 24, 17, week=14)]
        facts = score_edges(res, {"A": 1, "B": 1, "C": 1, "D": 1}, opponents(res), EV)
        self.assertGreater(facts[("C", "D")].recency, facts[("A", "B")].recency)


if __name__ == "__main__":
    unittest.main()
