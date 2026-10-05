"""End-to-end pipeline behaviour against the real checked-in seasons."""

from __future__ import annotations

import json
import random
import unittest
from dataclasses import replace

from cfbrank.config import load
from cfbrank.engine.pipeline import rank
from cfbrank.models import Dataset
from cfbrank.output.schema import build_payload, content_hash, validate_payload
from cfbrank.output.writer import dumps_stable
from cfbrank.sources.fixtures import FixtureSource
from cfbrank.sources.loader import build_dataset

GENERATED_AT = "2026-01-01T00:00:00Z"


def dataset(year):
    return build_dataset(FixtureSource("tests/fixtures/cfbd", year), year)


def config(year, *overrides):
    return load("config/ranking.toml", [f"season.year={year}", *overrides])


class TestCompletedSeason(unittest.TestCase):
    """2025 is a finished season: the numbers below cannot drift under us."""

    @classmethod
    def setUpClass(cls):
        cls.ds = dataset(2025)
        cls.cfg = config(2025)
        cls.result = rank(cls.ds, cls.cfg)

    def test_snapshot_identity(self):
        self.assertEqual(self.result.snapshot_id, "2025-postseason-01")
        self.assertEqual(self.result.season_type, "postseason")

    def test_pool_and_output_sizes(self):
        self.assertEqual(len(self.result.order), 40)
        self.assertEqual(len(set(self.result.order)), 40)

    def test_detects_the_large_tangle(self):
        # Real seasons do not produce tidy triangles. This one has a 22-team
        # strongly connected component, which is why cycles are reported
        # rather than resolved one at a time.
        sizes = sorted((c.size for c in self.result.cycles), reverse=True)
        self.assertEqual(sizes, [22, 8])

    def test_most_results_are_honoured(self):
        c = self.result.counts
        self.assertGreater(c["h2h_honored"], 8 * c["h2h_overridden"])

    def test_overridden_results_are_the_least_convincing(self):
        weights = [self.result.edge_facts[k].weight for k in self.result.ordering.violated]
        honored = [self.result.edge_facts[k].weight for k in self.result.ordering.honored]
        self.assertLess(sum(weights) / len(weights), sum(honored) / len(honored))

    def test_displacement_stays_bounded(self):
        worst = max(abs(d) for d in self.result.ordering.drift.values())
        self.assertLessEqual(worst, 12, "the drift term should stop a team crossing the board")

    def test_the_known_upset_regressions_fire(self):
        pairs = {(e.winner, e.loser) for e in self.result.regressions}
        self.assertIn(("SMU", "Miami"), pairs)
        self.assertIn(("Louisville", "Miami"), pairs)
        for e in self.result.regressions:
            self.assertGreater(e.gap, self.cfg["stage3.gap"])

    def test_split_series_are_reported(self):
        detail = " ".join(n.detail for n in self.result.series_notes)
        self.assertIn("Alabama", detail)
        self.assertTrue(any(n.meetings > 1 for n in self.result.series_notes))

    def test_every_ranked_team_has_reasons(self):
        for team in self.result.order[:25]:
            self.assertTrue(self.result.reasons[team], team)
            self.assertTrue(any("Resume order" in r for r in self.result.reasons[team]), team)


class TestLiveSeason(unittest.TestCase):
    def test_partial_season_ranks_cleanly(self):
        result = rank(dataset(2026), config(2026))
        self.assertEqual(result.snapshot_id, "2026-regular-05")
        self.assertEqual(len(result.order), 40)
        self.assertEqual(result.counts["rated_teams"], 138)


class TestDeterminism(unittest.TestCase):
    def test_identical_bytes_across_shuffled_inputs(self):
        ds = dataset(2025)
        cfg = config(2025)
        reference = None
        for seed in range(8):
            r = random.Random(seed)
            ratings = list(ds.ratings)
            games = list(ds.games)
            r.shuffle(ratings)
            r.shuffle(games)
            shuffled = replace(ds, ratings=tuple(ratings), games=tuple(games))
            payload = build_payload(rank(shuffled, cfg), shuffled, cfg, GENERATED_AT)
            text = dumps_stable(payload, 4)
            if reference is None:
                reference = text
            self.assertEqual(text, reference, f"seed {seed} produced different bytes")

    def test_repeated_runs_agree(self):
        ds, cfg = dataset(2026), config(2026)
        self.assertEqual(rank(ds, cfg).order, rank(ds, cfg).order)


class TestPayload(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.ds = dataset(2025)
        cls.cfg = config(2025)
        cls.payload = build_payload(rank(cls.ds, cls.cfg), cls.ds, cls.cfg, GENERATED_AT)

    def test_validates(self):
        self.assertEqual(validate_payload(self.payload), [])

    def test_has_twenty_five_contiguous_ranks(self):
        ranks = [r["rank"] for r in self.payload["rankings"]]
        self.assertEqual(ranks, list(range(1, 26)))

    def test_every_cycle_reference_resolves(self):
        ids = {c["cycle_id"] for c in self.payload["cycles"]}
        for row in self.payload["rankings"]:
            if row["cycle"]:
                self.assertIn(row["cycle"], ids)

    def test_overridden_results_carry_a_reason(self):
        self.assertTrue(self.payload["overridden_results"])
        for e in self.payload["overridden_results"]:
            self.assertTrue(e["reason"])
            self.assertEqual(e["status"], "overridden")

    def test_h2h_entries_are_all_labelled(self):
        for row in self.payload["rankings"]:
            for e in row["h2h"]["wins_vs_pool"] + row["h2h"]["losses_vs_pool"]:
                self.assertIn(e["status"], ("honored", "overridden"))

    def test_content_hash_ignores_timestamps(self):
        a = build_payload(rank(self.ds, self.cfg), self.ds, self.cfg, "2026-01-01T00:00:00Z")
        b = build_payload(rank(self.ds, self.cfg), self.ds, self.cfg, "2030-06-06T12:34:56Z")
        self.assertEqual(a["meta"]["content_hash"], b["meta"]["content_hash"])
        self.assertNotEqual(a["meta"]["generated_at"], b["meta"]["generated_at"])

    def test_content_hash_tracks_configuration(self):
        other = build_payload(
            rank(self.ds, config(2025, "stage1.w_sor=0.5", "stage1.w_sos=0.5")),
            self.ds, config(2025, "stage1.w_sor=0.5", "stage1.w_sos=0.5"), GENERATED_AT,
        )
        self.assertNotEqual(self.payload["meta"]["content_hash"], other["meta"]["content_hash"])

    def test_serialization_is_valid_json_with_no_nan(self):
        text = dumps_stable(self.payload, 4)
        reparsed = json.loads(text)
        self.assertEqual(reparsed["meta"]["content_hash"], self.payload["meta"]["content_hash"])

    def test_movement_is_absent_without_a_previous_snapshot(self):
        for row in self.payload["rankings"]:
            self.assertFalse(row["movement"]["has_previous"])
            self.assertIsNone(row["movement"]["delta"])

    def test_movement_is_computed_against_a_previous_snapshot(self):
        previous = {
            "meta": {"snapshot_id": "2025-regular-15"},
            "rankings": [{"team": self.payload["rankings"][0]["team"], "rank": 5}],
        }
        with_prev = build_payload(
            rank(self.ds, self.cfg), self.ds, self.cfg, GENERATED_AT, previous
        )
        top = with_prev["rankings"][0]
        self.assertEqual(top["movement"]["previous_rank"], 5)
        self.assertEqual(top["movement"]["delta"], 4)
        self.assertEqual(top["movement"]["status"], "up")


class TestValidatorCatchesBreakage(unittest.TestCase):
    def setUp(self):
        ds, cfg = dataset(2026), config(2026)
        self.payload = build_payload(rank(ds, cfg), ds, cfg, GENERATED_AT)

    def test_detects_a_rank_gap(self):
        self.payload["rankings"][3]["rank"] = 99
        self.assertTrue(any("contiguous" in p for p in validate_payload(self.payload)))

    def test_detects_a_duplicate_team(self):
        self.payload["rankings"][1]["team"] = self.payload["rankings"][0]["team"]
        self.assertTrue(any("duplicate" in p for p in validate_payload(self.payload)))

    def test_detects_a_dangling_cycle_reference(self):
        self.payload["rankings"][0]["cycle"] = "C99"
        self.assertTrue(any("C99" in p for p in validate_payload(self.payload)))

    def test_detects_a_missing_section(self):
        del self.payload["cycles"]
        self.assertTrue(any("cycles" in p for p in validate_payload(self.payload)))


class TestPublishedData(unittest.TestCase):
    """Whatever is committed under docs/data must be servable."""

    def test_committed_snapshots_validate(self):
        import glob

        files = glob.glob("docs/data/rankings.json") + glob.glob("docs/data/weeks/*.json")
        self.assertTrue(files, "no published data found")
        for path in files:
            with open(path, encoding="utf-8") as fh:
                payload = json.load(fh)
            self.assertEqual(validate_payload(payload), [], path)
            self.assertEqual(payload["meta"]["content_hash"], content_hash(payload), path)

    def test_index_points_at_files_that_exist(self):
        import os

        with open("docs/data/index.json", encoding="utf-8") as fh:
            index = json.load(fh)
        ids = []
        for season in index["seasons"]:
            for snap in season["snapshots"]:
                ids.append(snap["id"])
                self.assertTrue(os.path.exists(os.path.join("docs/data", snap["path"])), snap["path"])
        self.assertIn(index["current"], ids)


if __name__ == "__main__":
    unittest.main()


class TestGolden(unittest.TestCase):
    """A completed season pinned byte for byte.

    2025 fixtures never change, so any diff here is a deliberate ranking
    change. Regenerate with `python3 scripts/make_golden.py` and read the diff.
    """

    GOLDEN = "tests/fixtures/golden/rankings_2025.json"

    def test_matches(self):
        import difflib
        import sys
        from pathlib import Path

        sys.path.insert(0, "scripts")
        from make_golden import build  # noqa: E402

        actual = build()
        expected = Path(self.GOLDEN).read_text(encoding="utf-8")
        if actual != expected:
            diff = "\n".join(
                list(
                    difflib.unified_diff(
                        expected.splitlines(), actual.splitlines(),
                        fromfile="golden", tofile="actual", lineterm="", n=2,
                    )
                )[:60]
            )
            self.fail(
                "pipeline output changed.\n"
                "If this was intended, run: python3 scripts/make_golden.py\n\n" + diff
            )

    def test_golden_describes_the_expected_season(self):
        with open(self.GOLDEN, encoding="utf-8") as fh:
            payload = json.load(fh)
        self.assertEqual(payload["meta"]["snapshot_id"], "2025-postseason-01")
        self.assertEqual(len(payload["rankings"]), 25)
        self.assertEqual(validate_payload(payload), [])
        sizes = sorted((c["size"] for c in payload["cycles"]), reverse=True)
        self.assertEqual(sizes, [22, 8], "the 22-team tangle is the point of this fixture")
