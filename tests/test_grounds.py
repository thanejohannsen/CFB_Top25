"""The four rules that decide whether a result may be ranked against.

    Team A beat Team B. A HAS to be ranked above B unless
      1. A has more losses than B                            (any gap)
      2. A has lost since the game and B has not             (any gap)
      3. A has lost more times since than B has              (any gap)
      4. B has impressive wins and A has mediocre wins, and
         at least min_weeks have passed                   (BANDED gap)
"""

from __future__ import annotations

import math
import random
import unittest

from cfbrank.engine import grounds
from tests.helpers import result

CFG = {
    "enabled": True,
    "impressive_bar": 6.0,
    "min_weeks": 2,
    "band_start": 0.5,
    "band_step": 0.25,
    "band_max": 1.5,
}

# These sum to ZERO, so the board mean is 0 and quality past the bar is just the
# rating. GREAT and GOOD clear the impressive bar of 6.0 comfortably, FINE clears
# it by a hair, OKAY misses by a hair, MEH is dead average.
QUALITY = {
    "GREAT": 20.0, "GOOD": 10.0, "FINE": 6.1, "OKAY": 5.9,
    "MEH": 0.0, "BAD": -20.0, "W": -11.0, "L": -11.0,
}
assert sum(QUALITY.values()) == 0.0, "the tests read the bar as zero"

# The result being judged: W beat L in week 2. Everything else happens after it.
GAME = result("W", "L", week=2)


def one(after, cfg=None, quality=None, edges=(GAME,)):
    out = grounds.compute(
        list(edges), list(edges) + list(after), quality or QUALITY, cfg or CFG
    )
    return out[(edges[0].winner, edges[0].loser)]


class TestEnforcedByDefault(unittest.TestCase):
    """With none of the four rules met, the result simply stands."""

    def test_nothing_since_enforces_the_result(self):
        """The published bug: Missouri beat Florida 45-17 and Florida ranked above.

        Nothing had happened since, because there was no since -- the game was the
        week being ranked. Under these rules that cannot be overridden at all.
        """
        g = one([])
        self.assertEqual(g.rules, ())
        self.assertTrue(g.enforced)
        self.assertFalse(g.banded)
        self.assertEqual(g.max_lead, 0.0)

    def test_a_disabled_stage_imposes_nothing(self):
        self.assertEqual(grounds.compute([GAME], [GAME], QUALITY, dict(CFG, enabled=False)), {})

    def test_a_winner_still_playing_well_is_still_enforced(self):
        """Neither has lost, and the winner's wins since are the better ones."""
        g = one([result("W", "GREAT", week=3), result("L", "MEH", week=3)])
        self.assertEqual(g.rules, ())
        self.assertEqual(g.max_lead, 0.0)

    def test_age_alone_is_not_an_exception(self):
        """Time passing is not one of the four rules, however much of it passes."""
        g = one([result("MEH", "BAD", week=15)])
        self.assertEqual(g.rules, ())
        self.assertEqual(g.max_lead, 0.0)
        self.assertGreater(g.weeks_since, 10)


class TestRuleOneMoreLosses(unittest.TestCase):
    """A has more losses than B. Total losses, not losses since."""

    # The loser's defeat to the WINNER counts in its own total, so the winner
    # needs to be two losses up before rule 1 fires on a one-loss opponent.
    def test_more_total_losses_unlocks_any_gap(self):
        g = one([result("GREAT", "W", week=1), result("GOOD", "W", week=1)])
        self.assertEqual(g.rules, (1,), "both losses came BEFORE the game")
        self.assertEqual(g.max_lead, math.inf)
        self.assertEqual((g.winner_losses, g.loser_losses), (2, 1))

    def test_equal_total_losses_is_not_an_exception(self):
        g = one([result("GREAT", "W", week=1)])
        self.assertNotIn(1, g.rules)
        self.assertEqual((g.winner_losses, g.loser_losses), (1, 1))
        self.assertEqual(g.max_lead, 0.0)

    def test_fewer_total_losses_is_not_an_exception(self):
        g = one([result("GREAT", "L", week=1), result("GOOD", "L", week=3)])
        self.assertEqual(g.rules, ())
        self.assertEqual(g.max_lead, 0.0)


class TestRulesTwoAndThreeLostSince(unittest.TestCase):
    """Rule 2 is the N=1 case of rule 3, so both are reported when both hold."""

    def test_losing_since_while_the_other_has_not(self):
        g = one([result("GREAT", "W", week=4)])
        self.assertEqual(g.rules, (2, 3))
        self.assertEqual(g.max_lead, math.inf)
        self.assertEqual((g.winner_losses_since, g.loser_losses_since), (1, 0))

    def test_strictly_more_losses_since_counts_even_when_both_have_lost(self):
        g = one([
            result("GREAT", "W", week=4), result("GOOD", "W", week=5),
            result("GREAT", "L", week=4),
        ])
        self.assertIn(3, g.rules)
        self.assertNotIn(2, g.rules, "rule 2 needs the loser to be unbeaten since")
        self.assertEqual(g.max_lead, math.inf)

    def test_equal_losses_since_is_not_rule_three(self):
        g = one([result("GREAT", "W", week=4), result("GREAT", "L", week=4)])
        self.assertNotIn(2, g.rules)
        self.assertNotIn(3, g.rules)

    def test_the_loser_slipping_more_is_no_exception_at_all(self):
        """The result gets STRONGER when the team that lost it keeps losing."""
        g = one([result("GREAT", "L", week=4), result("GOOD", "L", week=5)])
        self.assertEqual(g.rules, ())
        self.assertEqual(g.max_lead, 0.0)
        self.assertEqual(g.loser_losses_since, 2)

    def test_losses_before_the_game_are_not_losses_since(self):
        g = one([result("GREAT", "W", week=1)])
        self.assertEqual(g.winner_losses_since, 0)
        self.assertNotIn(2, g.rules)
        self.assertNotIn(3, g.rules)


class TestRuleFourWinQuality(unittest.TestCase):
    """B has impressive wins since, A has mediocre ones, and time has passed."""

    def fires(self, after, cfg=None):
        return one(after, cfg)

    def test_the_loser_beating_better_teams_unlocks_a_banded_gap(self):
        g = self.fires([result("L", "GREAT", week=4), result("W", "MEH", week=4)])
        self.assertEqual(g.rules, (4,))
        self.assertTrue(g.banded)
        self.assertFalse(g.enforced)
        self.assertLess(g.max_lead, math.inf)
        self.assertGreater(g.max_lead, 0)

    def test_it_needs_min_weeks_to_have_passed(self):
        """One good win the week after proves nothing, however good it was."""
        g = self.fires([result("L", "GREAT", week=3)])
        self.assertEqual(g.weeks_since, 1.0)
        self.assertEqual(g.rules, ())
        self.assertEqual(g.max_lead, 0.0)

    def test_both_teams_beating_good_sides_is_not_an_exception(self):
        """"B impressive AND A mediocre" is one bar, so this fails the A half."""
        g = self.fires([result("L", "GREAT", week=4), result("W", "GREAT", week=4)])
        self.assertNotIn(4, g.rules)
        self.assertGreaterEqual(g.winner_quality, CFG["impressive_bar"])

    def test_the_band_widens_with_time(self):
        rates, leads = [], []
        for week in (4, 5, 6, 7, 8):
            after = [result("L", "GREAT", week=3), result("W", "MEH", week=3),
                     result("MEH", "BAD", week=week)]
            g = self.fires(after)
            rates.append(g.band_rate)
            leads.append(g.max_lead)
        self.assertEqual(rates, [0.5, 0.75, 1.0, 1.25, 1.5], "the owner's ramp")
        self.assertEqual(leads, sorted(leads))
        self.assertLess(leads[0], leads[-1])

    def test_the_band_stops_at_its_ceiling(self):
        after = [result("L", "GREAT", week=3), result("W", "MEH", week=3),
                 result("MEH", "BAD", week=20)]
        self.assertEqual(self.fires(after).band_rate, CFG["band_max"])

    def test_the_cap_is_the_floor_of_rate_times_quality_difference(self):
        after = [result("L", "GREAT", week=3), result("W", "MEH", week=3),
                 result("MEH", "BAD", week=5)]
        g = self.fires(after)
        self.assertEqual(g.band_rate, 0.75)
        self.assertAlmostEqual(g.quality_diff, 20.0)
        self.assertEqual(g.max_lead, 15.0)
        self.assertEqual(g.max_lead, float(math.floor(g.band_rate * g.quality_diff)))

    def test_a_tiny_quality_edge_buys_no_places(self):
        """The exception applies and is worth nothing, so the result still stands."""
        after = [result("L", "FINE", week=3), result("W", "OKAY", week=3),
                 result("MEH", "BAD", week=4)]
        g = one(after)
        self.assertEqual(g.rules, (4,))
        self.assertAlmostEqual(g.quality_diff, 0.2)
        self.assertEqual(g.band_rate, 0.5)
        self.assertEqual(g.max_lead, 0.0, "floor(0.5 x 0.2) is zero places")

    def test_quality_is_the_MEAN_of_the_wins_since_not_the_sum(self):
        """Volume must not stand in for quality.

        Summing let a run of ordinary wins out-score a genuine scalp, and on the
        completed 2025 season it pinned 15 of 111 results at the ceiling.
        """
        after = [result("MEH", "BAD", week=6)]
        lone = one([result("L", "GREAT", week=4), *after])
        padded = one([
            result("L", "GREAT", week=4), result("L", "MEH", week=5),
            result("L", "MEH", week=5), *after,
        ])
        self.assertLess(padded.loser_quality, lone.loser_quality)

    def test_beating_an_average_team_scores_the_same_as_beating_nobody(self):
        g = one([result("L", "MEH", week=4), result("MEH", "BAD", week=6)])
        self.assertAlmostEqual(g.loser_quality, 0.0)
        self.assertNotIn(4, g.rules)

    def test_an_unrated_opponent_earns_no_credit(self):
        g = one([result("L", "Some FCS School", week=4), result("MEH", "BAD", week=6)])
        self.assertAlmostEqual(g.loser_quality, 0.0)

    def test_wins_before_the_game_do_not_count(self):
        g = one([result("L", "GREAT", week=1), result("MEH", "BAD", week=6)])
        self.assertAlmostEqual(g.loser_quality, 0.0)


class TestRulePrecedence(unittest.TestCase):
    def test_slipping_outranks_the_band(self):
        """Any of rules 1-3 means "any gap", even when rule 4 also applies."""
        g = one([
            result("GREAT", "W", week=4),       # rules 1, 2, 3
            result("L", "GREAT", week=5),       # rule 4
            result("MEH", "BAD", week=6),
        ])
        self.assertEqual(set(g.rules), {2, 3, 4})
        self.assertEqual(g.max_lead, math.inf)
        self.assertFalse(g.banded, "unbounded, so the band does not apply")

    def test_band_rate_is_still_reported_when_outranked(self):
        g = one([
            result("GREAT", "W", week=4), result("L", "GREAT", week=5),
            result("MEH", "BAD", week=6),
        ])
        self.assertGreater(g.band_rate, 0.0)
        self.assertGreater(g.quality_diff, 0.0)


class TestDeterminism(unittest.TestCase):
    def test_shuffling_the_results_changes_nothing(self):
        """Win quality is a mean over floats, so the accumulation order is sorted."""
        after = [
            result("L", "GREAT", week=4), result("L", "GOOD", week=5),
            result("L", "MEH", week=6), result("GREAT", "W", week=4),
            result("W", "BAD", week=5), result("W", "MEH", week=7),
        ]
        reference = one(after)
        for seed in range(20):
            shuffled = list(after)
            random.Random(seed).shuffle(shuffled)
            self.assertEqual(one(shuffled), reference, f"seed {seed}")


if __name__ == "__main__":
    unittest.main()
