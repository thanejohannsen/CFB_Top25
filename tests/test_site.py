"""The static site: things that have broken in the browser rather than in CI.

Everything here reads `docs/` off disk. No network, no browser.
"""

from __future__ import annotations

import hashlib
import pathlib
import re
import unittest

DOCS = pathlib.Path(__file__).resolve().parent.parent / "docs"

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


if __name__ == "__main__":
    unittest.main()
