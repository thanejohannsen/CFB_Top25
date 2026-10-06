"""The two quality inputs and the solver they share.

These are the terms that replaced FPI in the base, so the properties worth
pinning are the ones that would be invisible if they broke: that the solver
actually recovers the strengths it is given, that a spread's sign is read the
way the book meant it, that nothing from the future is allowed in, and that the
whole thing is reproducible.
"""

from __future__ import annotations

import random
import unittest

from cfbrank.engine import market, performance
from cfbrank.engine.adjust import Observation, components, solve
from tests.helpers import line, ppa

FBS = {"A", "B", "C", "D", "E", "F"}


def round_robin(strength: dict[str, float], home_edge: float = 0.0) -> list[Observation]:
    """Every pair plays twice, once at each venue."""
    obs = []
    teams = sorted(strength)
    for home in teams:
        for away in teams:
            if home == away:
                continue
            value = strength[home] - strength[away] + home_edge
            obs.append(Observation(home, away, value, 1))
            obs.append(Observation(away, home, -value, -1))
    return obs


class TestSolver(unittest.TestCase):
    def test_recovers_the_strengths_it_was_given(self):
        truth = {"A": 20.0, "B": 7.0, "C": 0.0, "D": -9.0, "E": -18.0}
        centred = sum(truth.values()) / len(truth)
        sol = solve(round_robin(truth), estimate_home_edge=False)
        for team, want in truth.items():
            self.assertAlmostEqual(sol.ratings[team], want - centred, places=6, msg=team)
        self.assertTrue(sol.converged)

    def test_measures_home_field_rather_than_assuming_it(self):
        truth = {"A": 14.0, "B": 3.0, "C": -5.0, "D": -12.0}
        sol = solve(round_robin(truth, home_edge=4.5), estimate_home_edge=True, home_edge=2.5)
        self.assertAlmostEqual(sol.home_edge, 4.5, places=4)

    def test_a_fixed_home_edge_is_respected(self):
        truth = {"A": 10.0, "B": 0.0, "C": -10.0}
        sol = solve(round_robin(truth, home_edge=7.0), estimate_home_edge=False, home_edge=7.0)
        centred = sum(truth.values()) / len(truth)
        for team, want in truth.items():
            self.assertAlmostEqual(sol.ratings[team], want - centred, places=6, msg=team)

    def test_opponent_strength_is_what_separates_two_equal_margins(self):
        # Both teams won by 10. One did it against the best team on the board.
        obs = [
            Observation("Strong", "Best", 10.0),
            Observation("Best", "Strong", -10.0),
            Observation("Best", "Worst", 40.0),
            Observation("Worst", "Best", -40.0),
            Observation("Weak", "Worst", 10.0),
            Observation("Worst", "Weak", -10.0),
        ]
        sol = solve(obs, estimate_home_edge=False)
        self.assertGreater(sol.ratings["Strong"], sol.ratings["Weak"])

    def test_a_heavier_game_pulls_harder(self):
        """Two meetings, one emphatic and one even: the weights decide the gap.

        Equal weights average to 15. Weighting the emphatic meeting nine times
        heavier drags it to 27. This is the mechanism `market.recency_half_life`
        drives -- which measured worse and ships off, but the knob has to work.
        """
        def gap(w_blowout: float, w_even: float) -> float:
            sol = solve(
                [
                    Observation("A", "B", 30.0, 0, w_blowout),
                    Observation("B", "A", -30.0, 0, w_blowout),
                    Observation("A", "B", 0.0, 0, w_even),
                    Observation("B", "A", 0.0, 0, w_even),
                ],
                estimate_home_edge=False,
            )
            return sol.ratings["A"] - sol.ratings["B"]

        self.assertAlmostEqual(gap(1.0, 1.0), 15.0, places=6)
        self.assertAlmostEqual(gap(9.0, 1.0), 27.0, places=6)

    def test_jacobi_oscillation_does_not_come_back(self):
        """Two teams who have only played each other used to never converge.

        Computing a whole pass against the previous one leaves A pulling to +21,
        B answering -21, A answering 0 -- for ever. The reported rating was then
        whichever side of the oscillation the pass limit landed on: +21 at 199
        passes, 0 at 200. The answer the single game implies is half of it each
        way, and it should be reached in a couple of sweeps.
        """
        obs = [Observation("A", "B", 21.0), Observation("B", "A", -21.0)]
        sol = solve(obs, estimate_home_edge=False)
        self.assertTrue(sol.converged)
        self.assertLess(sol.passes, 10)
        self.assertAlmostEqual(sol.ratings["A"], 10.5, places=9)
        self.assertAlmostEqual(sol.ratings["B"], -10.5, places=9)
        for limit in (199, 200, 201):
            capped = solve(obs, estimate_home_edge=False, max_passes=limit)
            self.assertAlmostEqual(capped.ratings["A"], 10.5, places=9, msg=f"{limit} passes")

    def test_separate_schedules_are_reported_not_hidden(self):
        """Two groups that never played cannot be compared, and must say so.

        Recentring pins one degree of freedom per group, so a rating from a
        six-team island is not a national one. Week 1 looks exactly like this.
        """
        obs = [
            Observation("A", "B", 10.0),
            Observation("B", "A", -10.0),
            Observation("Y", "Z", 10.0),
            Observation("Z", "Y", -10.0),
        ]
        sol = solve(obs, estimate_home_edge=False)
        self.assertEqual([len(c) for c in sol.components], [2, 2])
        groups = {frozenset(c) for c in sol.components}
        self.assertEqual(groups, {frozenset({"A", "B"}), frozenset({"Y", "Z"})})

    def test_components_are_named_in_a_stable_order(self):
        obs = [Observation("A", "B", 1.0), Observation("C", "D", 1.0), Observation("B", "C", 1.0)]
        self.assertEqual(components(obs), [["A", "B", "C", "D"]])

    def test_input_order_does_not_change_the_answer(self):
        truth = {"A": 12.0, "B": 4.0, "C": -3.0, "D": -6.0, "E": -7.0}
        obs = round_robin(truth, home_edge=3.0)
        first = solve(obs, estimate_home_edge=True)
        rng = random.Random(20260101)
        for _ in range(5):
            shuffled = list(obs)
            rng.shuffle(shuffled)
            again = solve(shuffled, estimate_home_edge=True)
            self.assertEqual(
                {t: round(r, 9) for t, r in first.ratings.items()},
                {t: round(r, 9) for t, r in again.ratings.items()},
            )
            self.assertAlmostEqual(first.home_edge, again.home_edge, places=9)

    def test_an_opponent_with_no_games_of_its_own_still_gets_a_rating(self):
        # An FCS team that appears only as somebody's opponent. Without this it
        # would be treated as exactly average, which flatters whoever played it.
        sol = solve([Observation("A", "FCS", 40.0)], estimate_home_edge=False)
        self.assertIn("FCS", sol.ratings)
        self.assertGreater(sol.ratings["A"], sol.ratings["FCS"])

    def test_no_observations_is_not_an_error(self):
        sol = solve([])
        self.assertEqual(sol.ratings, {})
        self.assertTrue(sol.converged)

    def test_a_nonsense_site_is_rejected(self):
        with self.assertRaises(ValueError):
            Observation("A", "B", 1.0, site=2)

    def test_a_zero_weight_is_rejected(self):
        with self.assertRaises(ValueError):
            Observation("A", "B", 1.0, weight=0.0)


class TestMarket(unittest.TestCase):
    CFG = {"min_games": 1, "horizon_weeks": 0, "fit_home_field": False, "home_field_points": 0.0}

    def test_the_spread_sign_follows_the_book(self):
        """A negative spread means the HOME team is favoured.

        Getting this backwards inverts the entire board and nothing else in the
        pipeline would notice, so it is pinned on its own.
        """
        lines = [line("Home", "Away", -17.5, game_id=1)]
        self.assertAlmostEqual(lines[0].home_margin, 17.5)
        obs = market.observations(lines)
        self.assertAlmostEqual(obs[0].value, 17.5)
        self.assertEqual(obs[0].team, "Home")

    def test_a_favourite_outrates_the_team_it_was_favoured_over(self):
        lines = [line("A", "B", -21.0, game_id=1), line("B", "A", 21.0, week=9, game_id=2)]
        r = market.compute(lines, FBS, None, self.CFG)
        self.assertGreater(r.ratings["A"], r.ratings["B"])
        self.assertEqual(r.ranks["A"], 1)
        self.assertAlmostEqual(r.edge("A", "B") or 0.0, 21.0, places=4)

    def test_a_neutral_game_takes_no_venue_adjustment(self):
        obs = market.observations([line("A", "B", -3.0, neutral=True, game_id=1)])
        self.assertEqual([o.site for o in obs], [0, 0])

    def test_games_after_the_cutoff_are_invisible(self):
        """The whole look-ahead defence, in one assertion."""
        lines = [
            line("A", "B", -7.0, week=3, game_id=1),
            line("C", "D", -40.0, week=12, game_id=2),
        ]
        kept = market.usable_lines(lines, FBS, (0, 5, "￿"), {1, 2}, horizon_weeks=0)
        self.assertEqual([l.game_id for l in kept], [1])

    def test_next_week_is_allowed_and_the_week_after_is_not(self):
        lines = [
            line("A", "B", -7.0, week=6, game_id=10),   # next week: the market's view now
            line("C", "D", -7.0, week=7, game_id=11),   # closes after week 6 is played
        ]
        kept = market.usable_lines(lines, FBS, (0, 5, "￿"), set(), horizon_weeks=1)
        self.assertEqual([l.game_id for l in kept], [10])

    def test_an_unplayed_game_inside_the_window_is_not_evidence(self):
        lines = [line("A", "B", -7.0, week=3, game_id=1)]
        kept = market.usable_lines(lines, FBS, (0, 5, "￿"), played=set(), horizon_weeks=0)
        self.assertEqual(kept, [])

    def test_games_against_non_fbs_teams_are_dropped(self):
        lines = [line("A", "FCS Team", -45.0, game_id=1)]
        self.assertEqual(market.usable_lines(lines, FBS, None), [])

    def test_too_few_games_means_no_rating_and_a_warning(self):
        lines = [line("A", "B", -7.0, game_id=1)]
        r = market.compute(lines, FBS, None, {**self.CFG, "min_games": 3})
        self.assertEqual(r.ratings, {})
        self.assertIn("market_too_few_games", [w.code for w in r.warnings])

    def test_no_lines_at_all_is_a_warning_not_a_crash(self):
        r = market.compute([], FBS, None, self.CFG)
        self.assertFalse(r)
        self.assertEqual([w.code for w in r.warnings], ["market_unavailable"])

    def test_recency_weighting_ships_off(self):
        lines = [line("A", "B", -7.0, week=1, game_id=1), line("A", "C", -7.0, week=9, game_id=2)]
        self.assertEqual(market.recency_weights(lines, 0.0), {})
        weighted = market.recency_weights(lines, 4.0)
        self.assertAlmostEqual(weighted[1], 1.0)
        self.assertAlmostEqual(weighted[0], 0.5 ** 2)

    def test_the_postseason_counts_as_later_than_the_regular_season(self):
        regular = line("A", "B", -1.0, week=15, game_id=1)
        bowl = line("C", "D", -1.0, week=1, season_type="postseason", game_id=2)
        self.assertGreater(market._week_index(bowl), market._week_index(regular))


class TestPerformance(unittest.TestCase):
    CFG = {"min_games": 1, "fit_home_edge": False}

    def test_net_is_offence_less_defence(self):
        row = ppa("A", "B", offense=0.30, defense=-0.10)
        self.assertAlmostEqual(row.net, 0.40)

    def test_the_better_performance_outrates_the_worse(self):
        rows = [
            ppa("A", "B", 0.40, -0.10, game_id=1, was_home=True),
            ppa("B", "A", -0.10, 0.40, game_id=1, was_home=False),
        ]
        r = performance.compute(rows, FBS, None, self.CFG)
        self.assertGreater(r.ratings["A"], r.ratings["B"])
        self.assertEqual(r.ranks["A"], 1)

    def test_fcs_games_are_excluded(self):
        """A 0.6-per-play afternoon against an FCS defence says nothing."""
        rows = [
            ppa("A", "FCS Team", 0.80, -0.40, game_id=1),
            ppa("A", "B", 0.05, 0.05, week=6, game_id=2),
        ]
        kept = performance.usable_rows(rows, FBS, None)
        self.assertEqual([row.opponent for row in kept], ["B"])

    def test_games_after_the_cutoff_are_invisible(self):
        rows = [ppa("A", "B", 0.1, 0.0, week=3, game_id=1), ppa("A", "C", 0.9, 0.0, week=12, game_id=2)]
        kept = performance.usable_rows(rows, FBS, (0, 5, "￿"), {1, 2})
        self.assertEqual([row.game_id for row in kept], [1])

    def test_the_unadjusted_average_is_kept_for_the_site(self):
        rows = [
            ppa("A", "B", 0.20, 0.00, game_id=1),
            ppa("A", "C", 0.10, 0.10, week=6, game_id=2),
        ]
        r = performance.compute(rows, FBS, None, self.CFG)
        self.assertAlmostEqual(r.raw["A"], 0.10)

    def test_venue_is_read_from_the_game_when_known(self):
        home, away, neutral = (
            ppa("A", "B", 0.1, 0.0, was_home=True),
            ppa("A", "B", 0.1, 0.0, was_home=False),
            ppa("A", "B", 0.1, 0.0, neutral=True),
        )
        self.assertEqual([o.site for o in performance.observations([home, away, neutral])], [1, -1, 0])

    def test_an_unknown_venue_is_treated_as_neutral(self):
        row = ppa("A", "B", 0.1, 0.0, was_home=None)
        self.assertEqual(performance.observations([row])[0].site, 0)

    def test_no_rows_is_a_warning_not_a_crash(self):
        r = performance.compute([], FBS, None, self.CFG)
        self.assertFalse(r)
        self.assertEqual([w.code for w in r.warnings], ["performance_unavailable"])


class TestAgainstTheRealSeason(unittest.TestCase):
    """The fixtures are a completed season, so these are stable facts."""

    @classmethod
    def setUpClass(cls):
        from cfbrank.engine import h2h
        from cfbrank.sources.fixtures import FixtureSource
        from cfbrank.sources.loader import build_dataset
        from tests.helpers import FIXTURES

        cls.ds = build_dataset(FixtureSource(FIXTURES, 2025), 2025)
        cls.fbs = {
            name
            for g in cls.ds.games
            for name, cls_ in ((g.home_team, g.home_classification), (g.away_team, g.away_classification))
            if (cls_ or "").lower() == "fbs"
        }
        _, _, cls.cutoff = h2h.resolve_week(cls.ds.games, cls.ds.calendar, "auto", "both")
        cls.played = {
            g.game_id
            for g in cls.ds.games
            if g.game_id is not None and g.completed and g.home_points is not None
        }

    def test_every_fbs_matchup_has_a_line(self):
        """Coverage is the reason the renormalisation fallback rarely fires."""
        kept = market.usable_lines(self.ds.lines, self.fbs, self.cutoff, self.played, 0)
        fbs_games = [
            g
            for g in self.ds.games
            if g.completed
            and g.home_points is not None
            and g.home_team in self.fbs
            and g.away_team in self.fbs
        ]
        self.assertGreaterEqual(len(kept), len(fbs_games) * 0.99)

    def test_home_field_lands_near_the_value_it_used_to_assume(self):
        """The hardcoded 2.5 in engine/evidence.py turns out to have been right.

        An earlier read of this data said +4.32, which was the mean of how much
        home teams were favoured by -- that conflates home field with home teams
        simply being better. Fitting it jointly with the ratings gives +2.4.
        """
        r = market.compute(
            self.ds.lines,
            self.fbs,
            self.cutoff,
            {"min_games": 3, "horizon_weeks": 0, "fit_home_field": True},
            self.played,
        )
        self.assertTrue(2.0 <= r.home_field_points <= 3.0, r.home_field_points)
        self.assertEqual(r.components, 1, "a full season's schedule is connected")
        self.assertTrue(r.converged)

    def test_both_inputs_cover_the_whole_board(self):
        cfg = {"min_games": 3, "horizon_weeks": 0}
        mkt = market.compute(self.ds.lines, self.fbs, self.cutoff, cfg, self.played)
        perf = performance.compute(self.ds.ppa, self.fbs, self.cutoff, cfg, self.played)
        self.assertGreater(len(mkt.ratings), 120)
        self.assertGreater(len(perf.ratings), 120)
        self.assertEqual(perf.components, 1)


if __name__ == "__main__":
    unittest.main()


class TestWinProbability(unittest.TestCase):
    """The curve that turns a point spread into a chance of winning.

    Pinned against reality because getting it wrong is quiet: an earlier draft
    used a LOGISTIC at the same scale, which priced a 30-point favourite at 0.90
    against a real ~0.99 and made every resume look harder to earn than it was.
    """

    KNOWN = ((0, 0.500), (3, 0.588), (7, 0.698), (10, 0.771), (14, 0.850), (21, 0.940), (30, 0.987))

    def test_matches_the_real_curve(self):
        from cfbrank.engine.resume_strength import win_probability

        for points, expected in self.KNOWN:
            self.assertAlmostEqual(win_probability(points), expected, places=3, msg=f"{points:+}")

    def test_a_logistic_at_this_scale_would_not(self):
        import math

        from cfbrank.engine.resume_strength import win_probability

        logistic = lambda p: 1 / (1 + math.exp(-p / 13.5))
        self.assertGreater(win_probability(30) - logistic(30), 0.08)

    def test_it_is_symmetric_about_a_pick_em(self):
        from cfbrank.engine.resume_strength import win_probability

        for points in (3, 7, 14, 28):
            self.assertAlmostEqual(win_probability(points) + win_probability(-points), 1.0)

    def test_a_degenerate_sigma_does_not_divide_by_zero(self):
        from cfbrank.engine.resume_strength import win_probability

        self.assertEqual(win_probability(7, sigma=0), 1.0)
        self.assertEqual(win_probability(-7, sigma=0), 0.0)
        self.assertEqual(win_probability(0, sigma=0), 0.5)


class TestPoissonBinomial(unittest.TestCase):
    def test_three_coin_flips(self):
        from cfbrank.engine.resume_strength import at_least

        self.assertAlmostEqual(at_least([0.5, 0.5, 0.5], 2), 0.5)
        self.assertAlmostEqual(at_least([0.5, 0.5, 0.5], 3), 0.125)

    def test_hand_computable_unequal_odds(self):
        from cfbrank.engine.resume_strength import at_least

        # P(both) = 0.8*0.6; P(exactly one) = 0.8*0.4 + 0.2*0.6
        self.assertAlmostEqual(at_least([0.8, 0.6], 2), 0.48)
        self.assertAlmostEqual(at_least([0.8, 0.6], 1), 0.92)

    def test_the_edges(self):
        from cfbrank.engine.resume_strength import at_least

        self.assertEqual(at_least([0.3, 0.7], 0), 1.0, "winning none is certain")
        self.assertEqual(at_least([0.3], 5), 0.0, "cannot win more than you played")

    def test_order_does_not_change_the_answer(self):
        from cfbrank.engine.resume_strength import at_least

        ps = [0.91, 0.42, 0.77, 0.13, 0.65]
        first = at_least(ps, 3)
        for shift in range(1, len(ps)):
            rotated = ps[shift:] + ps[:shift]
            self.assertAlmostEqual(at_least(rotated, 3), first, places=12)


class TestResumeStrength(unittest.TestCase):
    def setUp(self):
        from cfbrank.sources.fixtures import FixtureSource
        from cfbrank.sources.loader import build_dataset
        from tests.helpers import FIXTURES

        self.ds = build_dataset(FixtureSource(FIXTURES, 2026), 2026)
        self.fbs = {
            n
            for g in self.ds.games
            for n, c in ((g.home_team, g.home_classification), (g.away_team, g.away_classification))
            if (c or "").lower() == "fbs"
        }

    def ratings(self):
        from cfbrank.engine import market

        played = {
            g.game_id
            for g in self.ds.games
            if g.game_id is not None and g.completed and g.home_points is not None
        }
        return market.compute(self.ds.lines, self.fbs, None, {"min_games": 3}, played)

    def test_a_hard_schedule_beats_a_soft_one_at_the_same_record(self):
        """Texas 4-0 having played Ohio State and Tennessee outranks 5-0 runs."""
        from cfbrank.engine import resume_strength

        mkt = self.ratings()
        out = resume_strength.compute(
            self.ds.games, mkt.ratings, mkt.home_field_points, self.fbs, None, {"min_games": 3}
        )
        self.assertEqual(out.ranks["Texas"], 1)
        self.assertLess(out.probability["Texas"], out.probability["Notre Dame"])
        self.assertLess(out.probability["Texas"], 0.05)

    def test_a_good_three_win_resume_can_beat_an_empty_five_win_one(self):
        from cfbrank.engine import resume_strength

        mkt = self.ratings()
        out = resume_strength.compute(
            self.ds.games, mkt.ratings, mkt.home_field_points, self.fbs, None, {"min_games": 3}
        )
        # Ole Miss 3-1 (beat LSU) against Indiana 5-0 (North Texas, Howard, ...)
        self.assertLess(out.ranks["Ole Miss"], out.ranks["Indiana"])

    def test_expected_wins_sit_below_a_perfect_record(self):
        from cfbrank.engine import resume_strength

        mkt = self.ratings()
        out = resume_strength.compute(
            self.ds.games, mkt.ratings, mkt.home_field_points, self.fbs, None, {"min_games": 3}
        )
        self.assertEqual(out.actual_wins("Notre Dame"), 5)
        self.assertLess(out.expected_wins("Notre Dame"), 5)

    def test_games_after_the_cutoff_are_invisible(self):
        from cfbrank.engine import resume_strength

        mkt = self.ratings()
        early = resume_strength.compute(
            self.ds.games, mkt.ratings, mkt.home_field_points, self.fbs,
            (0, 2, "￿"), {"min_games": 1},
        )
        self.assertLessEqual(len(early.games["Notre Dame"]), 2)

    def test_no_market_ratings_is_a_warning_not_a_crash(self):
        from cfbrank.engine import resume_strength

        out = resume_strength.compute(self.ds.games, {}, 2.5, self.fbs, None, {})
        self.assertFalse(out)
        self.assertEqual([w.code for w in out.warnings], ["resume_unavailable"])


class TestCover(unittest.TestCase):
    def test_a_favourite_winning_by_less_than_the_line_is_punished(self):
        from cfbrank.engine.cover import cover_margins
        from tests.helpers import game, line

        g = game("Home", "Away", 37, 26, week=5)
        g = type(g)(**{**{f.name: getattr(g, f.name) for f in g.__dataclass_fields__.values()},
                       "game_id": 1})
        ln = line("Home", "Away", -21.0, week=5, game_id=1)
        rec = cover_margins([ln], [g])
        self.assertAlmostEqual(rec["Home"].mean_margin, -10.0)
        self.assertFalse(rec["Home"].games[0].covered)
        # The loser's side is the mirror image: they beat the number.
        self.assertAlmostEqual(rec["Away"].mean_margin, +10.0)
        self.assertTrue(rec["Away"].games[0].covered)

    def test_an_underdog_losing_narrowly_is_not_punished(self):
        from cfbrank.engine.cover import cover_margins
        from tests.helpers import game, line

        g = game("Home", "Away", 24, 21, week=3)
        g = type(g)(**{**{f.name: getattr(g, f.name) for f in g.__dataclass_fields__.values()},
                       "game_id": 7})
        ln = line("Home", "Away", -14.0, week=3, game_id=7)
        rec = cover_margins([ln], [g])
        self.assertGreater(rec["Away"].mean_margin, 0, "losing by 3 as a 14-point dog beats the number")

    def test_few_games_are_shrunk_toward_zero(self):
        from cfbrank.engine.cover import CoverGame, CoverRecord

        def rec(n):
            return CoverRecord(tuple(
                CoverGame(opponent="X", week=i + 1, season_type="regular", expected=0.0, actual=20.0)
                for i in range(n)
            ))

        self.assertAlmostEqual(rec(3).mean_margin, 20.0)
        self.assertLess(rec(3).shrunk_margin, rec(12).shrunk_margin)
        self.assertLess(rec(3).shrunk_margin, 20.0)

    def test_a_game_with_no_line_is_skipped(self):
        from cfbrank.engine.cover import cover_margins
        from tests.helpers import game

        self.assertEqual(cover_margins([], [game("A", "B", 30, 0)]), {})
