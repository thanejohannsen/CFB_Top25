"""End-to-end pipeline behaviour against the real checked-in seasons."""

from __future__ import annotations

import json
import random
import unittest
from dataclasses import replace

from cfbrank.config import load
from cfbrank.engine.graph import Digraph, nontrivial_sccs
from cfbrank.engine.pipeline import rank
from cfbrank.models import Dataset
from pathlib import Path
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

    def test_detects_large_tangles(self):
        # Real seasons do not produce tidy triangles. A completed season leaves
        # multi-team strongly connected components, which is why cycles are
        # reported rather than resolved one at a time: enumerating the orderings
        # of a 20-team tangle is not something anybody is going to wait for.
        #
        # The exact sizes move whenever the pool does, so this pins the property
        # the design rests on, not a number.
        sizes = sorted((c.size for c in self.result.cycles), reverse=True)
        self.assertTrue(sizes, "a completed season should contain contradictions")
        self.assertGreater(sizes[0], 6, "per-cycle enumeration would not be tractable here")
        self.assertTrue(
            all(2 <= n <= len(self.result.order) for n in sizes), f"implausible sizes {sizes}"
        )

    def test_most_results_are_honoured(self):
        """A clear majority of head-to-head results should survive the ordering.

        The bar is "most", not a fixed ratio. `stage4.strength` is the owner's
        dial for exactly this and has moved from 14 to 4; an 8-to-1 assertion was
        really pinning that setting, and failed the moment it was turned down.
        What must stay true is that the ranking does not casually discard
        results -- below about two thirds, the head-to-head stage has stopped
        meaning anything and that is worth failing over.
        """
        c = self.result.counts
        total = c["h2h_honored"] + c["h2h_overridden"]
        self.assertGreater(c["h2h_honored"], 0.66 * total, f"{c['h2h_honored']}/{total}")

    def test_overridden_results_are_the_least_convincing(self):
        weights = [self.result.edge_facts[k].weight for k in self.result.ordering.violated]
        honored = [self.result.edge_facts[k].weight for k in self.result.ordering.honored]
        self.assertLess(sum(weights) / len(weights), sum(honored) / len(honored))

    def test_displacement_stays_bounded(self):
        # Head-to-head is a weighted preference, not a trump card: no result
        # should be able to fling a team across the board. Half the pool is the
        # line -- past that the base order has stopped meaning anything.
        worst = max(abs(d) for d in self.result.ordering.drift.values())
        self.assertLess(
            worst,
            len(self.result.order) // 2,
            "the drift term should stop a team crossing the board",
        )

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


class TestHardCaps(unittest.TestCase):
    """The four rules, on real data.

    The bug this stage exists for was published: on 2026 week 5 Missouri beat
    Florida 45-17 on the Saturday being ranked, and the board put Florida FIVE
    places above them -- nothing had happened in between, because there was no in
    between. See cfbrank/engine/grounds.py.
    """

    @classmethod
    def setUpClass(cls):
        cls.ds = dataset(2026)
        cls.on = rank(cls.ds, config(2026))
        cls.off = rank(cls.ds, config(2026, "stage4.grounds.enabled=false"))

    def test_every_pool_result_is_judged(self):
        for key, fact in self.on.edge_facts.items():
            self.assertIsNotNone(fact.grounds, key)
            self.assertGreaterEqual(fact.grounds.max_lead, 0.0, key)

    def test_a_result_with_no_exception_is_honoured(self):
        """Missouri beat Florida the week being ranked, so nothing excuses it."""
        pair = ("Missouri", "Florida")
        self.assertEqual(self.on.edge_facts[pair].grounds.rules, ())
        self.assertTrue(self.on.edge_facts[pair].grounds.enforced)
        self.assertIn(pair, self.off.ordering.violated, "unconstrained, it was dropped")
        self.assertNotIn(pair, self.on.ordering.violated)
        rank_of = {t: i for i, t in enumerate(self.on.order)}
        self.assertLess(rank_of["Missouri"], rank_of["Florida"])

    def test_the_pair_met_in_the_middle_rather_than_one_being_dragged(self):
        """Base 20 and 11; parking Missouri just above Florida would be cheaper
        to find but dearer to hold."""
        rank_of = {t: i + 1 for i, t in enumerate(self.on.order)}
        missouri, florida = rank_of["Missouri"], rank_of["Florida"]
        self.assertEqual(self.on.teams["Missouri"].base_rank, 20)
        self.assertEqual(self.on.teams["Florida"].base_rank, 11)
        self.assertLess(missouri, florida)
        self.assertGreater(missouri, 12, f"Missouri was dragged to Florida: {missouri}")
        self.assertLess(florida, 19, f"Florida barely moved: {florida}")

    def test_slipping_since_leaves_the_resume_order_alone(self):
        """Ole Miss beat LSU and has since lost; LSU has not. Any gap is allowed."""
        pair = ("Ole Miss", "LSU")
        g = self.on.edge_facts[pair].grounds
        self.assertIn(2, g.rules)
        self.assertIn(3, g.rules)
        self.assertEqual(g.max_lead, float("inf"))
        self.assertEqual((g.winner_losses_since, g.loser_losses_since), (1, 0))

    def test_a_banded_exception_binds_at_its_cap(self):
        """Oklahoma State beat Oregon in week 2; Oregon has beaten better since."""
        pair = ("Oklahoma State", "Oregon")
        g = self.on.edge_facts[pair].grounds
        self.assertEqual(g.rules, (4,))
        self.assertEqual(g.weeks_since, 3.0)
        self.assertEqual(g.band_rate, 0.75)
        self.assertEqual(g.max_lead, 6.0)
        self.assertLessEqual(self.on.ordering.leads[pair], 6)

    def test_no_cap_is_broken_and_nothing_had_to_be_relaxed(self):
        for pair, cap in self.on.ordering.max_lead.items():
            self.assertLessEqual(self.on.ordering.leads[pair], cap, pair)
        self.assertEqual(self.on.ordering.relaxed, [])

    def test_every_enforced_result_is_honoured(self):
        """The headline property: no exception, no override.

        Note the COUNT need not rise. On 2026 week 5 it is 26 either way and only
        the membership changes -- Missouri/Florida moves from overridden to
        honoured and a different pair takes its place. On the completed 2025
        season the rules do honour four more, 93 to 97.
        """
        honored = set(self.on.ordering.honored)
        self.assertLessEqual(set(self.on.ordering.enforced), honored)
        self.assertNotEqual(
            honored, set(self.off.ordering.honored), "the rules changed something"
        )

    def test_the_enforced_chains_stay_short(self):
        """What keeps this from being the topological-sort pathology.

        If a config change ever pushes these up, order.py's docstring stops being
        true and the measurement needs redoing.
        """
        graph = Digraph(sorted({t for e in self.on.ordering.enforced for t in e}))
        for winner, loser in self.on.ordering.enforced:
            graph.add_edge(winner, loser)
        self.assertEqual(nontrivial_sccs(graph), [], "a cycle would be unsatisfiable")
        depth = {}
        for team in reversed([t for t in self.on.order if t in set(graph.nodes)]):
            depth[team] = 1 + max(
                (depth.get(s, 0) for s in graph.successors(team)), default=0
            )
        self.assertLessEqual(max(depth.values(), default=0), 6)

    def test_switching_it_off_restores_the_old_ordering(self):
        """The escape hatch has to be a real one, so it is pinned."""
        self.assertEqual(self.off.ordering.enforced, [])
        self.assertEqual(self.off.ordering.capped, [])
        self.assertEqual(self.off.ordering.relaxed, [])
        self.assertEqual(self.off.ordering.start, "base")

    def test_the_cost_is_two_terms_again(self):
        o = self.on.ordering
        self.assertAlmostEqual(o.cost, o.violation_cost + o.drift_cost, places=6)

    def test_drift_does_not_grow_to_pay_for_the_constraint(self):
        worst = max(abs(d) for d in self.on.ordering.drift.values())
        self.assertLess(worst, len(self.on.order) // 2)


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

    def test_every_published_result_carries_its_rules(self):
        """The page explains why a contradiction is permitted, with no rules of its own."""
        seen = 0
        for row in self.payload["rankings"]:
            for e in row["h2h"]["wins_vs_pool"] + row["h2h"]["losses_vs_pool"]:
                g = e["grounds"]
                self.assertIsNotNone(g, (e["winner"], e["loser"]))
                self.assertTrue(set(g["rules"]) <= {1, 2, 3, 4}, g["rules"])
                self.assertEqual(g["enforced"], not g["rules"])
                # An unbounded cap publishes as null. A float infinity would be
                # rejected by validate_payload's NaN/Infinity scan, and
                # round_floats passes one straight through.
                if g["max_lead"] is not None:
                    self.assertGreaterEqual(g["max_lead"], 0)
                if g["enforced"]:
                    self.assertEqual(g["max_lead"], 0)
                    self.assertEqual(e["status"], "honored", (e["winner"], e["loser"]))
                if e["status"] == "honored":
                    self.assertLess(e["lead"], 0)
                else:
                    self.assertGreater(e["lead"], 0)
                seen += 1
        self.assertGreater(seen, 0)

    def test_no_infinity_reaches_the_payload(self):
        blob = dumps_stable(self.payload, 4)
        for token in ("Infinity", "-Infinity", "NaN"):
            self.assertNotIn(token, blob)

    def test_the_cost_breakdown_is_two_terms(self):
        cost = self.payload["meta"]["cost"]
        self.assertNotIn("gaps", cost, "the priced gap term was removed")
        self.assertAlmostEqual(
            cost["total"], cost["overrides"] + cost["drift"], places=3
        )

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


class TestBaseWeightsArePublished(unittest.TestCase):
    """The site renders the formula from the payload, so it has to be complete."""

    @classmethod
    def setUpClass(cls):
        cls.ds = dataset(2026)
        cls.cfg = config(2026)
        cls.payload = build_payload(rank(cls.ds, cls.cfg), cls.ds, cls.cfg, GENERATED_AT)

    RANK_FOR = {
        # The SoR term reads our own resume rank, which `base.sor_rank`
        # carries; ESPN's is published separately and is not an input.
        "SoR": lambda row: row["base"]["sor_rank"],
        "SoS": lambda row: row["base"]["sos_rank"],
        "FPI": lambda row: row["fpi"]["rank"],
        "Mkt": lambda row: row["market"]["rank"],
        "PPA": lambda row: row["performance"]["rank"],
    }

    def test_every_weight_is_published(self):
        stage1 = self.payload["meta"]["config"]["stage1"]
        for key in ("w_sor", "w_market", "w_perf", "w_sos", "w_fpi"):
            self.assertIn(key, stage1)
        for row in self.payload["rankings"]:
            self.assertTrue(row["base"]["weights"], row["team"])

    def test_the_rendered_formula_adds_up(self):
        """The published weights must reproduce the published score exactly.

        This is the check that catches a term being added to the base and not to
        the explanation -- the site would then show a formula that does not equal
        the number beside it.
        """
        for row in self.payload["rankings"]:
            b = row["base"]
            expected = sum(w * self.RANK_FOR[label](row) for label, w in b["weights"].items())
            self.assertAlmostEqual(expected, b["raw_score"], places=3, msg=row["team"])
            self.assertTrue(
                b["formula"].endswith(f"= {b['raw_score']:.2f}"),
                f"{row['team']}: {b['formula']} vs raw {b['raw_score']}",
            )

    def test_the_formula_names_no_unweighted_term(self):
        # w_sos and w_fpi both ship at 0, so neither may appear in a formula.
        for row in self.payload["rankings"]:
            self.assertNotIn("SoS", row["base"]["formula"], row["team"])
            self.assertNotIn("FPI", row["base"]["formula"], row["team"])
            self.assertIn("SoR", row["base"]["formula"], row["team"])

    def test_a_renormalised_team_publishes_its_own_weights(self):
        """A team missing an input is scored on what it has, at rescaled weights.

        The weights in its row are then NOT the configured ones, which is the
        point: the row has to explain the score that row actually got.
        """
        # w_adjust lives in [stage1] for discoverability but scales stage 2, so it
        # is not part of the base total these weights must sum to.
        shipped = {
            k: v
            for k, v in self.payload["meta"]["config"]["stage1"].items()
            if k.startswith("w_") and k != "w_adjust"
        }
        nominal = sum(shipped.values())
        for row in self.payload["rankings"] + self.payload["pool_tail"][:0]:
            weights = row["base"]["weights"]
            self.assertAlmostEqual(
                sum(weights.values()), nominal, places=6,
                msg=f"{row['team']}: weights must still sum to {nominal}",
            )


class TestCredentialsNeverReachOutput(unittest.TestCase):
    """The key lives in the config, and the config is echoed into every
    snapshot. Redaction is the only thing keeping it out of published files."""

    @classmethod
    def setUpClass(cls):
        cls.ds = dataset(2026)
        cls.cfg = config(2026, "source.api_key=SUPER-SECRET-SENTINEL")
        cls.payload = build_payload(rank(cls.ds, cls.cfg), cls.ds, cls.cfg, GENERATED_AT)

    def test_the_echoed_config_is_blank(self):
        self.assertEqual(self.payload["meta"]["config"]["source"]["api_key"], "")

    def test_the_key_appears_nowhere_in_the_serialized_payload(self):
        self.assertNotIn("SUPER-SECRET-SENTINEL", dumps_stable(self.payload, 4))

    def test_redaction_does_not_mutate_the_config(self):
        self.assertEqual(self.cfg["source.api_key"], "SUPER-SECRET-SENTINEL")

    def test_the_weights_are_still_echoed(self):
        # methodology.html reads these; over-zealous redaction would blank them.
        stage1 = self.payload["meta"]["config"]["stage1"]
        self.assertEqual(stage1["w_sor"], self.cfg["stage1.w_sor"])
        self.assertEqual(stage1["w_sos"], self.cfg["stage1.w_sos"])

    def test_committed_snapshots_carry_no_key(self):
        import glob

        from cfbrank.config import load

        real = load("config/ranking.toml")["source.api_key"].strip()
        for path in glob.glob("docs/data/rankings.json") + glob.glob("docs/data/weeks/*.json"):
            text = Path(path).read_text(encoding="utf-8")
            self.assertEqual(
                json.loads(text)["meta"]["config"]["source"]["api_key"], "", path
            )
            if real:
                self.assertNotIn(real, text, path)


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
    """A completed season pinned byte for byte, at FROZEN weights.

    2025 fixtures never change, so any diff here is a deliberate ENGINE change.
    Regenerate with `python3 scripts/make_golden.py` and read the diff.

    It runs on `make_golden.FROZEN`, not on the shipped config, so retuning a
    weight in config/ranking.toml does not break the build. That file is meant
    to be edited from the GitHub web UI; a net that snapped every time somebody
    moved a dial would just teach people to ignore it.
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
        self.assertGreater(
            sizes[0], 6, "large tangles are why cycles are reported, not resolved one by one"
        )
        # Every team in a reported loop must also appear in the published pool,
        # or the site would link a contradiction to a team that is not there.
        named = {t for c in payload["cycles"] for t in c["members"]}
        pooled = {r["team"] for r in payload["rankings"]} | {
            r["team"] for r in payload["pool_tail"]
        }
        self.assertTrue(named <= pooled, named - pooled)


class TestIndexIsSelfHealing(unittest.TestCase):
    """The catalogue drives the site's week selector, so it must not list files
    that are gone -- a stale entry is a 404 the moment somebody picks that week.

    Snapshots do legitimately get deleted: a week published off a single midweek
    game, or a season regenerated under a different id.
    """

    def test_an_entry_whose_file_vanished_is_dropped(self):
        import json
        import tempfile
        from pathlib import Path

        from cfbrank.output.history import update_index

        def payload(snapshot_id, year, week):
            return {
                "meta": {
                    "snapshot_id": snapshot_id,
                    "season": {"year": year, "week": week, "season_type": "regular",
                               "label": f"Week {week}"},
                    "generated_at": GENERATED_AT,
                    "content_hash": "sha256:x",
                },
                "rankings": [{"team": "A"}],
            }

        with tempfile.TemporaryDirectory() as d:
            weeks = Path(d) / "weeks"
            weeks.mkdir()
            index_path = Path(d) / "index.json"

            for wk in (5, 6):
                sid = f"2026-regular-0{wk}"
                (weeks / f"{sid}.json").write_text("{}")
                index_path.write_text(json.dumps(update_index(index_path, payload(sid, 2026, wk), weeks)))

            listed = lambda: [
                s["id"] for y in json.loads(index_path.read_text())["seasons"]
                for s in y["snapshots"]
            ]
            self.assertEqual(sorted(listed()), ["2026-regular-05", "2026-regular-06"])
            self.assertEqual(json.loads(index_path.read_text())["current"], "2026-regular-06")

            # week 6 is withdrawn; regenerating week 5 must forget it
            (weeks / "2026-regular-06.json").unlink()
            index_path.write_text(
                json.dumps(update_index(index_path, payload("2026-regular-05", 2026, 5), weeks))
            )
            self.assertEqual(listed(), ["2026-regular-05"])
            self.assertEqual(json.loads(index_path.read_text())["current"], "2026-regular-05")
