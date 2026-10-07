"""Games into results: ties, incompletes, split series, season-type ordering."""

from __future__ import annotations

import unittest

from cfbrank.engine.h2h import (
    build_graph,
    collapse_series,
    opponents,
    resolve_week,
    restrict,
    to_results,
    wins_losses,
)
from cfbrank.models import CalendarWeek
from tests.helpers import game, result


class TestToResults(unittest.TestCase):
    def test_orients_winner_to_loser(self):
        res, _ = to_results([game("Home", "Away", 17, 24)])
        self.assertEqual((res[0].winner, res[0].loser), ("Away", "Home"))
        self.assertFalse(res[0].winner_was_home)
        self.assertEqual(res[0].site, "away")

    def test_tie_produces_no_result(self):
        res, skipped = to_results([game("A", "B", 21, 21)])
        self.assertEqual(res, [])
        self.assertEqual([s.reason for s in skipped], ["tie_no_result"])

    def test_incomplete_game_with_a_live_score_is_skipped(self):
        res, skipped = to_results([game("A", "B", 14, 7, completed=False)])
        self.assertEqual(res, [])
        self.assertEqual([s.reason for s in skipped], ["in_progress"])

    def test_completed_without_a_score_is_flagged(self):
        res, skipped = to_results([game("A", "B", None, None, completed=True)])
        self.assertEqual(res, [])
        self.assertEqual([s.reason for s in skipped], ["no_score"])

    def test_cutoff_excludes_later_games(self):
        games = [game("A", "B", 24, 17, week=3), game("C", "D", 24, 17, week=9)]
        res, _ = to_results(games, cutoff=(0, 5, "￿"))
        self.assertEqual(len(res), 1)
        self.assertEqual(res[0].winner, "A")

    def test_cutoff_spans_season_types(self):
        games = [
            game("A", "B", 24, 17, week=14, season_type="regular"),
            game("C", "D", 24, 17, week=1, season_type="postseason"),
        ]
        res, _ = to_results(games, cutoff=(0, 15, "￿"))
        self.assertEqual([r.winner for r in res], ["A"],
                         "postseason week 1 is later than regular week 14")


class TestCollapseSeries(unittest.TestCase):
    def test_single_meeting_passes_through(self):
        kept, notes = collapse_series([result("A", "B")])
        self.assertEqual(len(kept), 1)
        self.assertEqual(notes, [])

    def test_a_sweep_keeps_one_edge_and_is_noted(self):
        kept, notes = collapse_series([result("A", "B", week=2), result("A", "B", week=9)])
        self.assertEqual([(k.winner, k.loser) for k in kept], [("A", "B")])
        self.assertEqual(notes[0].meetings, 2)
        self.assertIn("swept", notes[0].detail)

    def test_most_recent_wins_a_split(self):
        kept, notes = collapse_series(
            [result("A", "B", week=3), result("B", "A", week=12)], "most_recent"
        )
        self.assertEqual(kept[0].winner, "B")
        self.assertIn("later meeting", notes[0].detail)

    def test_postseason_rematch_beats_a_late_regular_season_week(self):
        # Week number alone would pick the regular-season game; season type
        # must lead the ordering key.
        kept, _ = collapse_series(
            [
                result("A", "B", week=15, season_type="regular"),
                result("B", "A", week=1, season_type="postseason"),
            ],
            "most_recent",
        )
        self.assertEqual(kept[0].winner, "B")

    def test_drop_policy_removes_the_pair_entirely(self):
        kept, notes = collapse_series(
            [result("A", "B", week=3), result("B", "A", week=12)], "drop"
        )
        self.assertEqual(kept, [])
        self.assertIn("ignored", notes[0].detail)

    def test_aggregate_margin_policy(self):
        kept, notes = collapse_series(
            [result("A", "B", 40, 10, week=3), result("B", "A", 21, 20, week=12)],
            "aggregate_margin",
        )
        self.assertEqual(kept[0].winner, "A", "A is +29 on aggregate")
        self.assertIn("aggregate margin", notes[0].detail)

    def test_aggregate_margin_tie_falls_back_to_the_later_meeting(self):
        kept, notes = collapse_series(
            [result("A", "B", 27, 20, week=3), result("B", "A", 27, 20, week=12)],
            "aggregate_margin",
        )
        self.assertEqual(kept[0].winner, "B")
        self.assertIn("tied on aggregate", notes[0].detail)

    def test_three_meetings_are_handled(self):
        kept, notes = collapse_series(
            [result("A", "B", week=2), result("B", "A", week=8), result("A", "B", week=14)],
            "most_recent",
        )
        self.assertEqual(len(kept), 1)
        self.assertEqual(kept[0].winner, "A")
        self.assertEqual(notes[0].meetings, 3)


class TestRestrictAndGraph(unittest.TestCase):
    def test_restrict_drops_results_outside_the_pool(self):
        res = [result("A", "B"), result("A", "FCS Team")]
        self.assertEqual(len(restrict(res, {"A", "B"})), 1)

    def test_graph_has_an_edge_per_result(self):
        g = build_graph([result("A", "B"), result("B", "C")], ["A", "B", "C"])
        self.assertTrue(g.has_edge("A", "B"))
        self.assertFalse(g.has_edge("B", "A"))

    def test_wins_losses_are_split_correctly(self):
        res = [result("A", "B"), result("C", "A")]
        wins, losses = wins_losses("A", res)
        self.assertEqual([w.loser for w in wins], ["B"])
        self.assertEqual([l.winner for l in losses], ["C"])

    def test_opponents_index_is_symmetric(self):
        idx = opponents([result("A", "B")])
        self.assertIn("B", idx["A"])
        self.assertIn("A", idx["B"])


class TestResolveWeek(unittest.TestCase):
    CAL = [CalendarWeek(1, "regular", "", ""), CalendarWeek(2, "regular", "", "")]

    def test_auto_picks_the_latest_played_week(self):
        games = [game("A", "B", 24, 17, week=3), game("C", "D", 24, 17, week=7)]
        week, st, _ = resolve_week(games, self.CAL, "auto", "both")
        self.assertEqual((week, st), (7, "regular"))

    def test_auto_prefers_postseason_when_it_has_been_played(self):
        games = [
            game("A", "B", 24, 17, week=15, season_type="regular"),
            game("C", "D", 24, 17, week=1, season_type="postseason"),
        ]
        week, st, _ = resolve_week(games, self.CAL, "auto", "both")
        self.assertEqual(st, "postseason")

    def test_auto_with_no_completed_games_falls_back_to_the_calendar(self):
        week, st, _ = resolve_week([game("A", "B", None, None, completed=False)], self.CAL, "auto", "both")
        self.assertEqual((week, st), (1, "regular"))

    def test_explicit_week_is_respected(self):
        games = [game("A", "B", 24, 17, week=3), game("C", "D", 24, 17, week=9)]
        week, _, cutoff = resolve_week(games, self.CAL, 4, "both")
        self.assertEqual(week, 3, "clamps to the latest week actually played at or before 4")
        res, _ = to_results(games, cutoff)
        self.assertEqual(len(res), 1)


if __name__ == "__main__":
    unittest.main()


class TestWeekOnlyAdvancesWhenPlayed(unittest.TestCase):
    """A week becomes current once it is PLAYED, not once it has started.

    Regression for 2026-10-07: one Wednesday fixture (Southern Miss at Troy)
    promoted the board to week 6 with 57 of 58 games unplayed. That moved the
    cutoff, which drove the market term to drop 57 unplayed week-6 betting lines
    and swap in week-7 ones -- roughly 50 of 329 inputs churned on one game.
    """

    def board(self, played_in_6):
        """Week 5 fully played; week 6 scheduled with `played_in_6` finished."""
        games = [game(f"H{i}", f"A{i}", 21, 14, week=5) for i in range(10)]
        games += [
            game(f"X{i}", f"Y{i}", 21, 14, week=6) if i < played_in_6
            else game(f"X{i}", f"Y{i}", None, None, week=6, completed=False)
            for i in range(10)
        ]
        return games

    def test_a_single_midweek_game_does_not_advance_the_week(self):
        week, st, _ = resolve_week(self.board(1), [], "auto", "both")
        self.assertEqual((st, week), ("regular", 5))

    def test_the_week_advances_once_half_of_it_is_final(self):
        week, _, _ = resolve_week(self.board(5), [], "auto", "both")
        self.assertEqual(week, 6)

    def test_the_threshold_is_tunable(self):
        self.assertEqual(resolve_week(self.board(2), [], "auto", "both", 0.2)[0], 6)
        self.assertEqual(resolve_week(self.board(2), [], "auto", "both", 0.9)[0], 5)

    def test_an_opening_night_with_no_settled_week_still_ranks(self):
        """Falls back to the latest week with any result rather than refusing."""
        games = [game("H", "A", 21, 14, week=1)] + [
            game(f"X{i}", f"Y{i}", None, None, week=1, completed=False) for i in range(20)
        ]
        self.assertEqual(resolve_week(games, [], "auto", "both")[0], 1)

    def test_an_explicit_week_is_still_honoured(self):
        """The guard only governs "auto"; --week 6 means week 6."""
        self.assertEqual(resolve_week(self.board(1), [], 6, "both")[0], 6)
