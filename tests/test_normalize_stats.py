"""Name handling and the small statistics helpers."""

from __future__ import annotations

import unittest

from cfbrank.engine.stats import (
    clamp,
    mean,
    penalty_scaler,
    pstdev,
    rank_desc,
    round_floats,
    zscorer,
)
from cfbrank.normalize import ALIASES, canonical, is_probably_fcs, join_report, sort_key


class TestCanonical(unittest.TestCase):
    def test_trims_and_collapses_whitespace(self):
        self.assertEqual(canonical("  Ohio   State  "), "Ohio State")

    def test_applies_aliases(self):
        self.assertEqual(canonical("Miami (FL)"), "Miami")

    def test_handles_none_and_empty(self):
        self.assertEqual(canonical(None), "")
        self.assertEqual(canonical(""), "")

    def test_never_folds_distinct_schools_together(self):
        # The dangerous case: a related FCS program must stay separate.
        for a, b in [("Texas A&M", "Texas A&M-Commerce"), ("Miami", "Miami (OH)")]:
            self.assertNotEqual(canonical(a), canonical(b))

    def test_aliases_do_not_chain(self):
        for value in ALIASES.values():
            self.assertEqual(canonical(value), value, f"{value} must be a fixed point")


class TestSortKey(unittest.TestCase):
    def test_folds_accents_and_case(self):
        self.assertEqual(sort_key("San José State")[0], "san jose state")

    def test_orders_ampersands_and_apostrophes_stably(self):
        names = ["Texas A&M", "Hawaii", "Ole Miss", "San José State", "texas a&m"]
        self.assertEqual(sorted(names, key=sort_key), sorted(names, key=sort_key))

    def test_distinguishes_names_that_fold_together(self):
        self.assertNotEqual(sort_key("Texas"), sort_key("texas"))


class TestJoinReport(unittest.TestCase):
    def test_reports_both_directions(self):
        report = join_report(["A", "B"], ["B", "C"])
        self.assertEqual(report["ratings_without_games"], ["A"])
        self.assertEqual(report["games_without_ratings"], ["C"])

    def test_fcs_detection(self):
        self.assertTrue(is_probably_fcs("fcs"))
        self.assertFalse(is_probably_fcs("fbs"))
        self.assertFalse(is_probably_fcs(None))


class TestStats(unittest.TestCase):
    def test_mean_and_stdev_on_empty_and_single(self):
        self.assertEqual(mean([]), 0.0)
        self.assertEqual(pstdev([]), 0.0)
        self.assertEqual(pstdev([5.0]), 0.0)

    def test_zscorer_on_a_degenerate_sample(self):
        z = zscorer([3.0, 3.0, 3.0])
        self.assertEqual(z(3.0), 0.0)
        self.assertEqual(z(99.0), 0.0)

    def test_zscorer_is_centred(self):
        z = zscorer([1.0, 2.0, 3.0])
        self.assertAlmostEqual(z(2.0), 0.0)
        self.assertGreater(z(3.0), 0)

    def test_penalty_scaler_is_not_centred_and_never_negative(self):
        """The whole point: a below-average value is not a credit.

        `zscorer` would hand 1.0 a negative score here because it is under the
        mean. For a penalty that is wrong -- it pays a team for a small amount of
        the bad thing rather than none of it.
        """
        xs = [1.0, 2.0, 3.0]
        p, z = penalty_scaler(xs), zscorer(xs)
        self.assertLess(z(1.0), 0.0)
        self.assertGreater(p(1.0), 0.0)
        self.assertAlmostEqual(p(0.0), 0.0, msg="zero is the floor, and the best score")
        self.assertAlmostEqual(p(-5.0), 0.0, msg="and it clamps below zero")
        # Same divisor as the z-score, so a weight still reads as rank points
        # per standard deviation.
        self.assertAlmostEqual(p(3.0), 3.0 / pstdev(xs))
        self.assertGreater(p(3.0), p(2.0))

    def test_penalty_scaler_on_a_degenerate_sample(self):
        p = penalty_scaler([3.0, 3.0, 3.0])
        self.assertEqual(p(3.0), 0.0)
        self.assertEqual(p(99.0), 0.0)

    def test_rank_desc_ties_share_the_minimum_rank(self):
        self.assertEqual(rank_desc({"a": 3.0, "b": 5.0, "c": 3.0}), {"b": 1, "a": 2, "c": 2})

    def test_rank_desc_is_order_independent(self):
        a = rank_desc({"x": 1.0, "y": 2.0, "z": 3.0})
        b = rank_desc({"z": 3.0, "x": 1.0, "y": 2.0})
        self.assertEqual(a, b)

    def test_clamp(self):
        self.assertEqual(clamp(5, 0, 3), 3)
        self.assertEqual(clamp(-5, 0, 3), 0)
        self.assertEqual(clamp(2, 0, 3), 2)

    def test_round_floats_is_recursive_and_normalises_negative_zero(self):
        out = round_floats({"a": [1.23456, {"b": -0.00001}], "c": "text", "d": 7}, 3)
        self.assertEqual(out["a"][0], 1.235)
        self.assertEqual(out["a"][1]["b"], 0.0)
        self.assertEqual(str(out["a"][1]["b"]), "0.0", "must not serialize as -0.0")
        self.assertEqual(out["c"], "text")
        self.assertEqual(out["d"], 7)


if __name__ == "__main__":
    unittest.main()
