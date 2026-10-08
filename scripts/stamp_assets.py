#!/usr/bin/env python3
"""Stamp docs/*.html asset links with a hash of the file they point at.

GitHub Pages serves `docs/assets/*` with `cache-control: max-age=600` and no
revalidation, and Safari holds subresources longer than that. So a push that
changes app.js or style.css lands on a browser that keeps showing the old one,
with no visible way back except a hard refresh -- which has now bitten the owner
twice, both times looking like the deploy had failed.

A query string the browser has never seen cannot come from its cache, so each
link carries `?v=<first 8 of sha256 of the file>`. Static hosts ignore the query
and serve the file; the browser treats it as a new URL the moment the content
moves. Nothing else changes, and there is still no build step: run this after
editing an asset.

    python3 scripts/stamp_assets.py

`tests/test_site.py` recomputes the same hashes, so a forgotten stamp reddens CI
rather than shipping a stale page.
"""

from __future__ import annotations

import hashlib
import pathlib
import re
import sys

DOCS = pathlib.Path(__file__).resolve().parent.parent / "docs"

# href="assets/style.css" or src="assets/app.js", with or without an existing
# ?v=... on the end.
LINK = re.compile(r'((?:href|src)="(assets/[^"?]+)")(?:\?v=[0-9a-f]+)?')


def digest(path: pathlib.Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()[:8]


def stamp(html: pathlib.Path) -> bool:
    text = html.read_text()

    def sub(m: re.Match[str]) -> str:
        target = DOCS / m.group(2)
        if not target.exists():
            raise SystemExit(f"{html.name}: no such asset {m.group(2)}")
        return m.group(1)[:-1] + "?v=" + digest(target) + '"'

    new = LINK.sub(sub, text)
    if new == text:
        return False
    html.write_text(new)
    return True


def main() -> int:
    changed = [p.name for p in sorted(DOCS.glob("*.html")) if stamp(p)]
    print("stamped: " + (", ".join(changed) if changed else "nothing to do"))
    return 0


if __name__ == "__main__":
    sys.exit(main())
