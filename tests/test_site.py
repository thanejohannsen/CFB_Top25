"""The static site: things that have broken in the browser rather than in CI.

Everything here reads `docs/` off disk. No network, no browser.
"""

from __future__ import annotations

import hashlib
import importlib.util
import pathlib
import re
import unittest

ROOT = pathlib.Path(__file__).resolve().parent.parent
DOCS = ROOT / "docs"


def _stamper():
    """Import scripts/stamp_assets.py, which is not inside a package."""
    spec = importlib.util.spec_from_file_location(
        "stamp_assets", ROOT / "scripts" / "stamp_assets.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module

ASSET = re.compile(r'(?:href|src)="(assets/[^"?]+)(\?v=([0-9a-f]+))?"')


class TestAssetStamps(unittest.TestCase):
    """Every asset link carries a hash of the file it points at.

    GitHub Pages serves these with `max-age=600` and no revalidation, so an
    unstamped link leaves a browser showing last week's JavaScript against this
    week's payload -- which has twice looked like a failed deploy. The stamp
    makes the URL change whenever the bytes do.

    `python3 scripts/stamp_assets.py` fixes a failure here.
    """

    def pages(self):
        return sorted(DOCS.glob("*.html"))

    def test_there_are_pages_to_check(self):
        self.assertTrue(self.pages(), "no HTML in docs/, so this suite proves nothing")

    def test_every_asset_link_is_stamped_with_its_own_hash(self):
        seen = 0
        for page in self.pages():
            for target, _q, stamp in ASSET.findall(page.read_text()):
                seen += 1
                asset = DOCS / target
                self.assertTrue(asset.exists(), f"{page.name} -> missing {target}")
                want = hashlib.sha256(asset.read_bytes()).hexdigest()[:8]
                self.assertEqual(
                    stamp, want,
                    f"{page.name} -> {target} is stamped {stamp or '(not at all)'}, "
                    f"expected {want}. Run python3 scripts/stamp_assets.py",
                )
        self.assertGreater(seen, 0, "no asset links found, so the regex has rotted")


class TestStamperRewritesItsOwnOutput(unittest.TestCase):
    """Re-stamping a stamped page must replace the stamp, not skip the link.

    The first version anchored the closing quote directly after the path, so it
    matched `src="assets/app.js"` and never `src="assets/app.js?v=abc12345"`.
    Every stamp after the first reported "nothing to do" and left the page
    pointing at a hash of the previous bytes -- the exact staleness the stamp
    exists to prevent, now with a plausible-looking version on it.
    """

    def test_an_existing_stamp_is_replaced(self):
        link = _stamper().LINK
        for text in (
            '<script src="assets/app.js"></script>',
            '<script src="assets/app.js?v=deadbeef"></script>',
            '<link rel="stylesheet" href="assets/style.css?v=00000000">',
        ):
            with self.subTest(text=text):
                m = link.search(text)
                self.assertIsNotNone(m, "the link was not matched at all")
                self.assertIn("assets/", m.group(2))
                self.assertNotIn("?", m.group(2), "the stamp leaked into the path")
                rebuilt = link.sub(lambda x: x.group(1) + x.group(2) + "?v=abc12345" + x.group(3), text)
                self.assertIn("?v=abc12345", rebuilt)
                self.assertNotIn("deadbeef", rebuilt)
                self.assertNotIn("00000000", rebuilt)
                self.assertEqual(rebuilt.count("?v="), 1)


if __name__ == "__main__":
    unittest.main()
