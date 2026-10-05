"""The CLI's exit-code protocol, which the Actions workflow depends on."""

from __future__ import annotations

import io
import json
import os
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest import mock

from cfbrank.cli import EXIT_CONFIG, EXIT_OK, EXIT_UNCHANGED, EXIT_UPSTREAM, main

BASE = ["--offline", "--fixture-year", "2026", "--year", "2026", "-q"]


def run(args):
    out, err = io.StringIO(), io.StringIO()
    with redirect_stdout(out), redirect_stderr(err):
        code = main(args)
    return code, out.getvalue(), err.getvalue()


class TestExitCodes(unittest.TestCase):
    def test_dry_run_writes_nothing(self):
        with tempfile.TemporaryDirectory() as d:
            target = Path(d) / "r.json"
            code, _, _ = run(BASE + ["--dry-run", "--out", str(target)])
            self.assertEqual(code, EXIT_OK)
            self.assertFalse(target.exists())

    def test_first_write_then_unchanged(self):
        with tempfile.TemporaryDirectory() as d:
            args = BASE + [
                "--out", f"{d}/r.json",
                "--set", f"output.history_dir={d}/weeks",
                "--set", f"output.index_path={d}/index.json",
            ]
            self.assertEqual(run(args)[0], EXIT_OK)
            self.assertTrue(Path(f"{d}/r.json").exists())
            self.assertTrue(Path(f"{d}/index.json").exists())
            self.assertEqual(run(args)[0], EXIT_UNCHANGED, "a rerun must be a no-op")
            self.assertEqual(run(args + ["--force"])[0], EXIT_OK, "--force overrides the no-op")

    def test_changing_the_configuration_makes_it_change_again(self):
        with tempfile.TemporaryDirectory() as d:
            args = BASE + [
                "--out", f"{d}/r.json",
                "--set", f"output.history_dir={d}/weeks",
                "--set", f"output.index_path={d}/index.json",
            ]
            self.assertEqual(run(args)[0], EXIT_OK)
            self.assertEqual(run(args)[0], EXIT_UNCHANGED)
            self.assertEqual(run(args + ["--set", "stage1.w_sor=0.5"])[0], EXIT_OK)

    def test_bad_config_exits_four(self):
        code, _, err = run(BASE + ["--set", "stage1.output_size=999"])
        self.assertEqual(code, EXIT_CONFIG)
        self.assertIn("configuration error", err)

    def test_unknown_fixture_year_exits_three(self):
        code, _, err = run(["--offline", "--fixture-year", "1999", "-q"])
        self.assertEqual(code, EXIT_UPSTREAM)
        self.assertIn("fixture directory not found", err)

    def test_missing_key_without_offline_is_actionable(self):
        with mock.patch.dict(os.environ, {"CFBD_API_KEY": ""}, clear=False):
            code, _, err = run(["--year", "2026", "-q"])
        self.assertEqual(code, EXIT_UPSTREAM)
        self.assertIn("CFBD_API_KEY", err)
        self.assertIn("--offline", err, "the error should name the way out")

    def test_offline_env_var_is_honoured(self):
        with mock.patch.dict(os.environ, {"CFB_OFFLINE": "1", "CFBD_API_KEY": ""}, clear=False):
            code, _, _ = run(["--year", "2026", "--fixture-year", "2026", "--dry-run", "-q"])
        self.assertEqual(code, EXIT_OK)


class TestOutput(unittest.TestCase):
    def test_no_trailing_whitespace(self):
        _, out, _ = run(BASE + ["--dry-run", "--print-top", "10"])
        for line in out.splitlines():
            self.assertEqual(line, line.rstrip(), repr(line))

    def test_print_top_renders_a_table(self):
        _, out, _ = run(BASE + ["--dry-run", "--print-top", "5"])
        self.assertIn("CFB Top 25", out)
        self.assertEqual(out.count("\n    1. "), 1)
        self.assertIn("\n    5. ", out)
        self.assertNotIn("\n    6. ", out, "--print-top 5 should stop at five")
        self.assertIn("head-to-head results honoured", out)

    def test_explain_reports_a_team(self):
        _, out, _ = run(BASE + ["--dry-run", "--explain", "Texas"])
        self.assertIn("Texas", out)
        self.assertIn("Resume order", out)
        self.assertIn("head-to-head inside the pool", out)

    def test_explain_matches_case_insensitively(self):
        _, out, _ = run(BASE + ["--dry-run", "--explain", "ohio state"])
        self.assertIn("Ohio State", out)

    def test_explain_unknown_team_fails_cleanly(self):
        code, _, err = run(BASE + ["--dry-run", "--explain", "Nowhere Tech"])
        self.assertNotEqual(code, EXIT_OK)
        self.assertIn("not in the pool", err)

    def test_written_snapshot_matches_the_history_copy(self):
        with tempfile.TemporaryDirectory() as d:
            run(BASE + [
                "--out", f"{d}/r.json",
                "--set", f"output.history_dir={d}/weeks",
                "--set", f"output.index_path={d}/index.json",
            ])
            current = Path(f"{d}/r.json").read_text()
            snap = Path(f"{d}/weeks/2026-regular-05.json").read_text()
            self.assertEqual(current, snap)
            index = json.loads(Path(f"{d}/index.json").read_text())
            self.assertEqual(index["current"], "2026-regular-05")


if __name__ == "__main__":
    unittest.main()
