#!/usr/bin/env python3
"""Serve docs/ locally. fetch() is blocked on file:// URLs, so a server is required.

    python3 scripts/serve_docs.py [--port 8000]
"""

from __future__ import annotations

import argparse
import functools
import http.server
import socketserver
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1] / "docs"


class Handler(http.server.SimpleHTTPRequestHandler):
    def end_headers(self) -> None:
        # Local dev should never serve a stale snapshot from the browser cache.
        self.send_header("Cache-Control", "no-store")
        super().end_headers()

    def log_message(self, fmt: str, *args) -> None:
        print(f"  {fmt % args}")


class Server(socketserver.TCPServer):
    # Without this, restarting straight after a ctrl-c fails with
    # "Address already in use" while the old socket sits in TIME_WAIT.
    allow_reuse_address = True


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=8000)
    args = ap.parse_args()
    handler = functools.partial(Handler, directory=str(ROOT))
    with Server(("127.0.0.1", args.port), handler) as httpd:
        print(f"serving {ROOT} at http://localhost:{args.port}/  (ctrl-c to stop)")
        try:
            httpd.serve_forever()
        except KeyboardInterrupt:
            print("\nstopped")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
