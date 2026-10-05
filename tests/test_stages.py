"""Stage 1 base order, stage 2 resume adjustment, stage 3 upset regression."""

from __future__ import annotations

import unittest

from cfbrank.engine.base_score import compute_base, pool, priority, rerank
from cfbrank.engine.regression import apply_upset_regression
from cfbrank.engine.resume import _loss_badness, apply_resume_adjustment
from dataclasses import replace
from tests.helpers import rating, record, result

S1 = {"w_sor": 0.75, "w_sos": 0.25, "require_fpi": True, "fbs_only": True}
S2 = {"w_loss_quality": 3.0, "w_best_win": 1.0, "w_game_control": 0.5}
EV = {"home_field_points": 2.5, "margin_cap": 28}
FBS = {"A", "B", "C", "D", "E"}


def teams_for(specs):
    ratings = [rating(t, sor, sos, fpi) for (t, sor, sos, fpi) in specs]
    records = {t: record(t, 8, 2) for (t, _, _, _) in specs}
    return compute_base(ratings, records, {t for (t, _, _, _) in specs}, S1)


class TestBaseScore(unittest.TestCase):
    def test_formula(self):
        teams, _ = teams_for([("A", 4, 8, 10.0)])
        self.assertAlmostEqual(teams[0].base_raw, 0.75 * 4 + 0.25 * 8)
        self.assertIn("0.75 x SoR #4", teams[0].formula(0.75, 0.25))

    def test_lower_score_ranks_first(self):
        teams, _ = teams_for([("A", 1, 1, 5.0), ("B", 50, 50, 5.0)])
        self.assertEqual([t.team for t in pool(teams, 2)], ["A", "B"])

    def test_schedule_weight_actually_matters(self):
        specs = [("A", 5, 90, 10.0), ("B", 8, 5, 10.0)]
        teams, _ = teams_for(specs)
        self.assertEqual(pool(teams, 1)[0].team, "B", "a brutal schedule outweighs 3 SoR places")

    def test_missing_ratings_are_excluded_with_a_warning(self):
        ratings = [rating("A", 1, 1, 5.0), rating("B", None, 4, 5.0)]
        teams, warnings = compute_base(ratings, {}, {"A", "B"}, S1)
        self.assertEqual([t.team for t in teams], ["A"])
        self.assertEqual([w.code for w in warnings], ["excluded_missing_rating"])

    def test_non_fbs_teams_are_excluded(self):
        ratings = [rating("A", 1, 1, 5.0), rating("Z", 2, 2, 4.0)]
        teams, warnings = compute_base(ratings, {}, {"A"}, S1)
        self.assertEqual([t.team for t in teams], ["A"])
        self.assertIn("excluded_not_fbs", [w.code for w in warnings])

    def test_fpi_rank_is_computed_from_the_rating_not_the_feed_rank(self):
        # `fpi` is a rating (higher is better); resumeRanks.fpi is a rank.
        teams, _ = teams_for([("A", 9, 9, 2.0), ("B", 9, 9, 30.0)])
        by = {t.team: t for t in teams}
        self.assertEqual(by["B"].fpi_rank, 1)
        self.assertEqual(by["A"].fpi_rank, 2)

    def test_fpi_rank_mismatch_is_warned(self):
        # Guards against a feed change that swaps the rating and rank fields.
        ratings = [
            replace(rating("A", 1, 1, 30.0), fpi_resume_rank=99),
            rating("B", 2, 2, 2.0),
        ]
        _, warnings = compute_base(ratings, {}, {"A", "B"}, S1)
        self.assertIn("fpi_rank_mismatch", [w.code for w in warnings])

    def test_priority_is_a_total_order(self):
        teams, _ = teams_for([("A", 4, 4, 5.0), ("B", 4, 4, 5.0)])
        keys = sorted(priority(t) for t in teams)
        self.assertEqual(len(set(keys)), 2, "identical teams must still order deterministically")


class TestResumeAdjustment(unittest.TestCase):
    def test_loss_badness_ordering(self):
        road = _loss_badness(result("W", "L", 24, 21, site="home"), 10, 130, 2.5, 28)
        home = _loss_badness(result("W", "L", 24, 21, site="away"), 10, 130, 2.5, 28)
        blowout = _loss_badness(result("W", "L", 45, 21, site="away"), 10, 130, 2.5, 28)
        bad_opp = _loss_badness(result("W", "L", 24, 21, site="away"), 120, 130, 2.5, 28)
        self.assertLess(road, home, "losing on the road is more forgivable")
        self.assertLess(home, blowout)
        self.assertLess(home, bad_opp, "losing to a weak team is worse")

    def test_undefeated_team_takes_no_loss_penalty(self):
        teams, _ = teams_for([("A", 1, 1, 20.0), ("B", 2, 2, 10.0), ("C", 3, 3, 5.0)])
        # B loses badly at home, C loses narrowly on the road, A is unbeaten.
        apply_resume_adjustment(
            teams,
            [result("A", "B", 45, 7, site="away"), result("A", "C", 24, 21, site="home")],
            S2, EV,
        )
        by = {t.team: t for t in teams}
        self.assertAlmostEqual(by["A"].resume_components["loss_quality"], 0.0,
                               msg="an unbeaten team has no losses to judge")
        self.assertGreater(by["B"].resume_components["loss_quality"],
                           by["C"].resume_components["loss_quality"],
                           "a 38-point home loss should cost more than a 3-point road loss")

    def test_a_single_team_with_losses_has_no_relative_penalty(self):
        # One sample has no variance, so the z-score is 0 by construction
        # rather than a division by zero.
        teams, _ = teams_for([("A", 1, 1, 20.0), ("B", 2, 2, 10.0)])
        apply_resume_adjustment(teams, [result("A", "B")], S2, EV)
        by = {t.team: t for t in teams}
        self.assertAlmostEqual(by["B"].resume_components["loss_quality"], 0.0)

    def test_components_are_recorded_for_the_site(self):
        teams, _ = teams_for([("A", 1, 1, 20.0), ("B", 2, 2, 10.0)])
        apply_resume_adjustment(teams, [result("A", "B")], S2, EV)
        self.assertEqual(
            sorted(teams[0].resume_components), ["best_win", "game_control", "loss_quality"]
        )

    def test_adjustment_changes_the_order(self):
        teams, _ = teams_for([("A", 1, 1, 20.0), ("B", 2, 2, 10.0), ("C", 3, 3, 5.0)])
        before = [t.team for t in pool(teams, 3)]
        apply_resume_adjustment(teams, [result("C", "A", 50, 0, site="away")], S2, EV)
        after = [t.team for t in pool(teams, 3)]
        self.assertNotEqual(before, after, "a 50-0 home loss should cost something")


class TestUpsetRegression(unittest.TestCase):
    def setUp(self):
        self.teams, _ = teams_for([(chr(65 + i), i + 1, i + 1, 30.0 - i) for i in range(5)])
        rerank(self.teams)

    def test_disabled_does_nothing(self):
        events = apply_upset_regression(self.teams, [result("E", "A")], {"enabled": False})
        self.assertEqual(events, [])
        self.assertTrue(all(t.regression_adj == 0 for t in self.teams))

    def test_small_gap_does_not_trigger(self):
        events = apply_upset_regression(self.teams, [result("B", "A")], {"enabled": True, "gap": 2, "strength": 0.5})
        self.assertEqual(events, [])

    def test_wide_gap_pulls_both_toward_the_midpoint(self):
        by = {t.team: t for t in self.teams}
        before_a, before_e = by["A"].base_score, by["E"].base_score
        events = apply_upset_regression(self.teams, [result("E", "A")], {"enabled": True, "gap": 2, "strength": 0.5})
        self.assertEqual(len(events), 1)
        self.assertLess(by["E"].base_score, before_e, "the winner should improve")
        self.assertGreater(by["A"].base_score, before_a, "the loser should worsen")

    def test_full_strength_reaches_the_midpoint_exactly(self):
        by = {t.team: t for t in self.teams}
        mid = (by["A"].base_score + by["E"].base_score) / 2
        apply_upset_regression(self.teams, [result("E", "A")], {"enabled": True, "gap": 2, "strength": 1.0})
        self.assertAlmostEqual(by["A"].base_score, mid)
        self.assertAlmostEqual(by["E"].base_score, mid)

    def test_movement_is_symmetric(self):
        by = {t.team: t for t in self.teams}
        apply_upset_regression(self.teams, [result("E", "A")], {"enabled": True, "gap": 2, "strength": 0.5})
        self.assertAlmostEqual(by["A"].regression_adj, -by["E"].regression_adj)

    def test_result_order_does_not_change_the_outcome(self):
        res = [result("E", "A", week=2), result("D", "A", week=9)]
        a = apply_upset_regression(self.teams, res, {"enabled": True, "gap": 1, "strength": 0.5})
        adj_a = {t.team: t.regression_adj for t in self.teams}

        teams2, _ = teams_for([(chr(65 + i), i + 1, i + 1, 30.0 - i) for i in range(5)])
        rerank(teams2)
        b = apply_upset_regression(teams2, list(reversed(res)), {"enabled": True, "gap": 1, "strength": 0.5})
        adj_b = {t.team: t.regression_adj for t in teams2}
        self.assertEqual(len(a), len(b))
        for team in adj_a:
            self.assertAlmostEqual(adj_a[team], adj_b[team], msg=team)


if __name__ == "__main__":
    unittest.main()
