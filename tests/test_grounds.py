"""The grounds for setting a head-to-head result aside -- engine/grounds.py.

Two grounds, both of which answer "my team beat them and is ranked below them":
the winner has slipped since, or the loser has beaten better teams since. What
they buy is a licence measured in places, which is what stage 4 charges against.
"""

from __future__ import annotations

import random
import unittest

from cfbrank.engine import grounds
from tests.helpers import result

CFG = {
    "enabled": True,
    "base_places": 2.0,
    "per_net_loss": 4.0,
    "loss_offset": 0.5,
    "per_rating_point": 0.30,
    "ramp_weeks": 6.0,
    "per_week": 0.40,
    "max_places": 20.0,
    "relief": 0.40,
}

# A board whose mean rating is exactly 0, so credit past the bar is just the
# rating itself. GREAT is a top-ten side, MEH is average, BAD is a doormat.
QUALITY = {"GREAT": 20.0, "GOOD": 10.0, "MEH": 0.0, "BAD": -20.0, "W": 0.0, "L": 0.0}


def compute(edges, after, cfg=None, quality=None):
    return grounds.compute(edges, list(edges) + list(after), quality or QUALITY, cfg or CFG)


def one(edges, after, cfg=None, quality=None):
    out = compute(edges, after, cfg, quality)
    return next(iter(out.values()))


# The result being judged: W beat L in week 2. Everything else happens after it.
GAME = result("W", "L", week=2)


class TestNoGrounds(unittest.TestCase):
    def test_nothing_since_licenses_exactly_the_base(self):
        """The published bug: a 28-point win and the loser eleven places higher.

        Missouri beat Florida 45-17 on the Saturday being ranked. Nothing had
        happened since, because there was no since -- so the two may swap, but
        they stay neighbours.
        """
        g = one([GAME], [])
        self.assertEqual(g.category, "none")
        self.assertEqual(g.severity, "none")
        self.assertAlmostEqual(g.allowance, CFG["base_places"])
        self.assertAlmostEqual(g.relief, 0.0)

    def test_a_disabled_stage_grants_no_licence_at_all(self):
        self.assertEqual(compute([GAME], [], dict(CFG, enabled=False)), {})

    def test_the_winner_still_playing_well_earns_nothing(self):
        """Both unbeaten since, winner's wins the better ones: no grounds."""
        g = one([GAME], [result("W", "GREAT", week=3), result("L", "MEH", week=3)])
        self.assertEqual(g.category, "age")
        self.assertAlmostEqual(g.ascent, 0.0)
        self.assertAlmostEqual(g.slide, 0.0)


class TestFormGround(unittest.TestCase):
    """The winner has lost since -- and lost more often than the team it beat."""

    def test_a_subsequent_loss_widens_the_licence(self):
        g = one([GAME], [result("GREAT", "W", week=4)])
        self.assertEqual(g.category, "form")
        self.assertEqual(g.winner_losses, 1)
        self.assertEqual(g.loser_losses, 0)
        self.assertAlmostEqual(g.slide, 1.0)
        self.assertGreater(g.allowance, CFG["base_places"])

    def test_losing_more_is_stronger_grounds_than_losing_once(self):
        one_loss = one([GAME], [result("GREAT", "W", week=4)])
        two_losses = one([GAME], [result("GREAT", "W", week=4), result("GOOD", "W", week=5)])
        self.assertGreater(two_losses.slide, one_loss.slide)
        self.assertGreater(two_losses.allowance, one_loss.allowance)

    def test_the_loser_slipping_too_forgives_part_of_it(self):
        alone = one([GAME], [result("GREAT", "W", week=4)])
        both = one([GAME], [result("GREAT", "W", week=4), result("GREAT", "L", week=4)])
        self.assertLess(both.slide, alone.slide)
        self.assertGreater(both.slide, 0.0, "having lost at all is still grounds")

    def test_loss_offset_one_leaves_only_the_net(self):
        cfg = dict(CFG, loss_offset=1.0)
        both = one([GAME], [result("GREAT", "W", week=4), result("GREAT", "L", week=4)], cfg)
        self.assertAlmostEqual(both.slide, 0.0)
        self.assertEqual(both.category, "age")

    def test_the_losers_losses_alone_are_no_grounds(self):
        """The result gets STRONGER when the team that lost it keeps losing."""
        g = one([GAME], [result("GREAT", "L", week=4), result("GOOD", "L", week=5)])
        self.assertAlmostEqual(g.slide, 0.0)
        self.assertEqual(g.loser_losses, 2)

    def test_losses_before_the_game_do_not_count(self):
        g = one([GAME], [result("GREAT", "W", week=1)])
        self.assertEqual(g.winner_losses, 0)
        self.assertAlmostEqual(g.slide, 0.0)


class TestResumeGround(unittest.TestCase):
    """The loser has since been beating better teams than the winner has."""

    def test_the_loser_beating_better_teams_widens_the_licence(self):
        g = one([GAME], [result("L", "GREAT", week=4), result("W", "BAD", week=4)])
        self.assertEqual(g.category, "resume")
        self.assertGreater(g.ascent, 0.0)
        self.assertGreater(g.allowance, CFG["base_places"])

    def test_it_strengthens_with_time(self):
        """The owner's rule: a few weeks of good wins means more than one week.

        Same evidence, read at increasing distance from the game. The ramp is the
        dial that does it, so this is the test that would fail if it were dropped.
        """
        seen = []
        for week in (3, 5, 8):
            after = [result("L", "GREAT", week=3), result("W", "BAD", week=3)]
            after.append(result("MEH", "BAD", week=week))  # moves the latest week on
            g = one([GAME], after)
            seen.append((g.ramp, g.allowance))
        ramps = [r for r, _ in seen]
        allowances = [a for _, a in seen]
        self.assertEqual(ramps, sorted(ramps))
        self.assertLess(ramps[0], ramps[-1])
        self.assertLess(allowances[0], allowances[-1])

    def test_a_ramp_of_zero_lands_the_ground_at_once(self):
        after = [result("L", "GREAT", week=3), result("W", "BAD", week=3)]
        g = one([GAME], after, dict(CFG, ramp_weeks=0.0))
        self.assertAlmostEqual(g.ramp, 1.0)

    def test_quality_is_the_MEAN_of_the_wins_not_the_sum(self):
        """Volume must not stand in for quality.

        Summing let a long run of ordinary wins out-score a genuine scalp, and on
        the completed 2025 season it pinned 15 of 111 results at max_places.
        """
        one_good = one([GAME], [result("L", "GREAT", week=4)])
        padded = one(
            [GAME],
            [
                result("L", "GREAT", week=4),
                result("L", "MEH", week=5),
                result("L", "MEH", week=6),
            ],
        )
        self.assertLess(padded.loser_credit, one_good.loser_credit)

    def test_beating_an_average_team_scores_the_same_as_beating_nobody(self):
        mediocre = one([GAME], [result("L", "MEH", week=4)])
        self.assertAlmostEqual(mediocre.loser_credit, 0.0)
        self.assertAlmostEqual(mediocre.ascent, 0.0)

    def test_an_unrated_opponent_earns_no_credit(self):
        g = one([GAME], [result("L", "Some FCS School", week=4)])
        self.assertAlmostEqual(g.loser_credit, 0.0)

    def test_wins_before_the_game_do_not_count(self):
        g = one([GAME], [result("L", "GREAT", week=1)])
        self.assertAlmostEqual(g.loser_credit, 0.0)

    def test_the_winner_beating_better_teams_cancels_it(self):
        g = one([GAME], [result("L", "GOOD", week=4), result("W", "GREAT", week=4)])
        self.assertAlmostEqual(g.ascent, 0.0, msg="the ground is one-sided")
        self.assertGreater(g.winner_credit, g.loser_credit)


class TestAgeAndCategories(unittest.TestCase):
    def test_age_alone_widens_the_licence(self):
        near = one([GAME], [result("MEH", "BAD", week=3)])
        far = one([GAME], [result("MEH", "BAD", week=12)])
        self.assertEqual(near.category, "age")
        self.assertEqual(far.category, "age")
        self.assertGreater(far.allowance, near.allowance)

    def test_both_grounds_together_report_as_both(self):
        g = one(
            [GAME],
            [result("GREAT", "W", week=4), result("L", "GREAT", week=5)],
        )
        self.assertEqual(g.category, "both")
        self.assertGreater(g.slide, 0.0)
        self.assertGreater(g.ascent, 0.0)
        self.assertTrue(g.licensed)

    def test_none_and_age_do_not_count_as_licensed(self):
        self.assertFalse(one([GAME], []).licensed)
        self.assertFalse(one([GAME], [result("MEH", "BAD", week=9)]).licensed)

    def test_severity_rises_with_the_licence(self):
        seen = []
        for extra in ([], [result("GREAT", "W", week=4)],
                      [result("GREAT", "W", week=4), result("GOOD", "W", week=5),
                       result("MEH", "W", week=6)]):
            seen.append(one([GAME], extra).severity)
        self.assertEqual(seen[0], "none")
        self.assertIn(seen[1], ("slight", "clear"))
        self.assertIn(seen[2], ("clear", "decisive"))
        self.assertNotEqual(seen[1], seen[2])


class TestCapAndRelief(unittest.TestCase):
    def test_the_licence_is_capped(self):
        after = [result("GREAT", "W", week=w) for w in range(3, 14)]
        after.append(result("L", "GREAT", week=13))
        g = one([GAME], after)
        self.assertAlmostEqual(g.allowance, CFG["max_places"])
        self.assertEqual(g.severity, "decisive")

    def test_relief_is_zero_without_grounds_and_capped_with_them(self):
        self.assertAlmostEqual(one([GAME], []).relief, 0.0)
        after = [result("GREAT", "W", week=w) for w in range(3, 14)]
        self.assertAlmostEqual(one([GAME], after).relief, CFG["relief"])

    def test_relief_scales_with_the_licence_earned(self):
        small = one([GAME], [result("GREAT", "W", week=3)])
        big = one([GAME], [result("GREAT", "W", week=3), result("GOOD", "W", week=4),
                           result("MEH", "W", week=5)])
        self.assertLess(small.relief, big.relief)
        self.assertLessEqual(big.relief, CFG["relief"])

    def test_a_degenerate_cap_grants_no_relief_rather_than_dividing_by_zero(self):
        cfg = dict(CFG, max_places=CFG["base_places"])
        g = one([GAME], [result("GREAT", "W", week=4)])
        g2 = one([GAME], [result("GREAT", "W", week=4)], cfg)
        self.assertGreater(g.relief, 0.0)
        self.assertAlmostEqual(g2.relief, 0.0)
        self.assertAlmostEqual(g2.allowance, CFG["base_places"])


class TestDeterminism(unittest.TestCase):
    def test_shuffling_the_results_changes_nothing(self):
        """Credit is a mean over floats, so the accumulation order is sorted."""
        after = [
            result("L", "GREAT", week=4), result("L", "GOOD", week=5),
            result("L", "MEH", week=6), result("GREAT", "W", week=4),
            result("W", "BAD", week=5), result("W", "MEH", week=7),
        ]
        reference = one([GAME], after)
        for seed in range(20):
            shuffled = list(after)
            random.Random(seed).shuffle(shuffled)
            self.assertEqual(one([GAME], shuffled), reference, f"seed {seed}")


if __name__ == "__main__":
    unittest.main()
