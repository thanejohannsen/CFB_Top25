"""Config loading, strict key checking, and --set overrides."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from cfbrank.config import DEFAULTS, apply_override, load, validate
from cfbrank.errors import ConfigError

SHIPPED = "config/ranking.toml"


class TestLoad(unittest.TestCase):
    BASE_WEIGHTS = ("w_sor", "w_market", "w_perf", "w_sos", "w_fpi")

    def test_shipped_config_is_valid(self):
        cfg = load(SHIPPED)
        self.assertEqual(cfg["schema_version"], 1)
        total = sum(cfg[f"stage1.{k}"] for k in self.BASE_WEIGHTS)
        self.assertAlmostEqual(total, 1.0, msg="the base weights should sum to 1")

    def test_fpi_ships_out_of_the_base(self):
        """FPI has no week dimension, so no weight on it can be backtested.

        It stays in the file as a knob and keeps its stage-4 and tiebreak jobs;
        it must not be the thing that orders the board. See config/ranking.toml.
        """
        self.assertEqual(load(SHIPPED)["stage1.w_fpi"], 0.0)

    def test_both_quality_signals_carry_weight(self):
        """Market and play-by-play know different things, so both must be on.

        The ratio between them is the owner's call -- it has been 50/50 and is
        now 25/15 -- and measurement cannot settle it (0.75/0.25 and 0.50/0.50
        were within noise of each other). What the data does say clearly is that
        the pair beats either alone, so neither may quietly go to zero.
        """
        cfg = load(SHIPPED)
        self.assertGreater(cfg["stage1.w_market"], 0.0)
        self.assertGreater(cfg["stage1.w_perf"], 0.0)

    def test_the_resume_carries_the_most_weight(self):
        """What a team has achieved should outrank how good it looks."""
        cfg = load(SHIPPED)
        self.assertGreater(
            cfg["stage1.w_sor"], cfg["stage1.w_market"] + cfg["stage1.w_perf"]
        )

    def test_lines_two_weeks_out_are_rejected(self):
        """A line that closes after next week has been played is look-ahead."""
        load(SHIPPED, ["market.horizon_weeks=0"])
        load(SHIPPED, ["market.horizon_weeks=1"])
        with self.assertRaises(ConfigError):
            load(SHIPPED, ["market.horizon_weeks=2"])

    def test_schedule_weight_ships_off(self):
        # Deliberate: Strength of Record already accounts for the schedule, so
        # weighting SoS again double-counts it. See config/ranking.toml.
        self.assertEqual(load(SHIPPED)["stage1.w_sos"], 0.0)

    def test_defaults_alone_are_valid(self):
        validate(DEFAULTS)

    def test_missing_file_is_reported(self):
        with self.assertRaises(ConfigError):
            load("config/does-not-exist.toml")

    def test_unknown_key_is_rejected(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "c.toml"
            p.write_text("schema_version = 1\n[stage1]\nw_sosu = 0.3\n")
            with self.assertRaises(ConfigError) as cm:
                load(p)
            self.assertIn("stage1.w_sosu", str(cm.exception))

    def test_malformed_toml_is_reported(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "c.toml"
            p.write_text("this is not = = toml\n")
            with self.assertRaises(ConfigError):
                load(p)

    def test_section_and_missing_key(self):
        cfg = load(SHIPPED)
        self.assertIn("w_margin", cfg.section("stage4.evidence"))
        self.assertEqual(cfg.get("nope.nope", "fallback"), "fallback")
        with self.assertRaises(ConfigError):
            cfg["nope.nope"]


class TestOverrides(unittest.TestCase):
    def test_types_are_coerced(self):
        cfg = load(SHIPPED, ["stage1.w_sor=0.6", "stage1.pool_size=30",
                             "stage3.enabled=false", "season.week=auto"])
        self.assertIsInstance(cfg["stage1.w_sor"], float)
        self.assertIsInstance(cfg["stage1.pool_size"], int)
        self.assertIs(cfg["stage3.enabled"], False)
        self.assertEqual(cfg["season.week"], "auto")

    def test_unknown_path_is_rejected(self):
        for bad in ["nope.x=1", "stage1.nope=1", "stage4.evidence.nope=1"]:
            with self.assertRaises(ConfigError, msg=bad):
                load(SHIPPED, [bad])

    def test_malformed_assignment_is_rejected(self):
        for bad in ["no-equals-sign", "=5"]:
            with self.assertRaises(ConfigError, msg=bad):
                load(SHIPPED, [bad])

    def test_cannot_replace_a_whole_table(self):
        with self.assertRaises(ConfigError):
            apply_override({"stage1": {"w_sor": 1}}, "stage1=5")


class TestValidation(unittest.TestCase):
    def _bad(self, assignment):
        with self.assertRaises(ConfigError, msg=assignment):
            load(SHIPPED, [assignment])

    def test_rejects_out_of_range_values(self):
        self._bad("stage1.output_size=99")      # > pool_size
        self._bad("stage1.output_size=0")
        self._bad("stage1.pool_size=0")
        self._bad("stage1.w_sor=-1")
        self._bad("stage1.w_fpi=-1")
        self._bad("stage3.strength=1.5")
        self._bad("stage3.gap=-1")
        self._bad("stage4.strength=-1")
        self._bad("stage4.drift_weight=-0.5")
        self._bad("stage4.drift_exponent=0.5")
        self._bad("stage4.drift_exponent=4")
        self._bad("stage4.max_passes=0")
        self._bad("stage4.split_series=coin-flip")
        self._bad("stage4.evidence.margin_cap=0")
        self._bad("stage4.evidence.recency_floor=2")
        self._bad("stage4.evidence.home_field_points=-1")
        self._bad("season.week=0")
        self._bad("season.week=99")
        self._bad("season.season_type=sometime")
        self._bad("output.float_precision=-1")
        self._bad("schema_version=2")

    ZERO_BASE = [
        "stage1.w_sor=0", "stage1.w_market=0", "stage1.w_perf=0",
        "stage1.w_sos=0", "stage1.w_fpi=0",
    ]

    def test_all_base_weights_zero_is_rejected(self):
        with self.assertRaises(ConfigError):
            load(SHIPPED, self.ZERO_BASE)

    def test_one_nonzero_base_weight_is_enough(self):
        load(SHIPPED, [*self.ZERO_BASE, "stage1.w_fpi=1"])
        load(SHIPPED, [*self.ZERO_BASE, "stage1.w_sor=1"])
        load(SHIPPED, [*self.ZERO_BASE, "stage1.w_market=1"])

    def test_a_free_override_is_rejected(self):
        """Overriding a result has to cost something, or H2H is decorative."""
        with self.assertRaises(ConfigError):
            load(SHIPPED, ["stage4.evidence.weight_floor=0"])

    def test_accepts_the_documented_edges(self):
        load(SHIPPED, ["stage3.strength=0", "stage3.strength=1.0"])
        load(SHIPPED, ["stage4.drift_exponent=1.0"])
        load(SHIPPED, ["season.week=1"])
        load(SHIPPED, ["stage1.output_size=40"])


if __name__ == "__main__":
    unittest.main()
