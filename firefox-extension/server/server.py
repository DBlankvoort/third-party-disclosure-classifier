"""Local HTTP bridge for the Firefox extension to talk to."""

from __future__ import annotations

import argparse
import json
import sys
import traceback
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

_HERE = Path(__file__).resolve().parent
_REPO_ROOT = _HERE.parent.parent
sys.path.insert(0, str(_REPO_ROOT))
sys.path.insert(0, str(_HERE))
from analyze import analyze_url  # noqa: E402

# Defaults
CONFIG = {
    "corpus_root": str(_HERE.parent / "_cache_corpus"),
    "use_ner": True,
    "use_poligraph": True,
    "delay": 0.2,
}


class Handler(BaseHTTPRequestHandler):
    server_version = "tpd-extension-bridge/0.1"

    # -- helpers ---------------------------------------------------------- #
    def _cors(self) -> None:
        # The popup runs from a moz-extension:// origin; allow it.
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")

    def _json(self, code: int, payload: dict) -> None:
        body = json.dumps(payload).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self._cors()
        self.end_headers()
        self.wfile.write(body)

    def do_OPTIONS(self) -> None:  # noqa: N802
        self.send_response(204)
        self._cors()
        self.end_headers()

    def do_GET(self) -> None:  # noqa: N802
        parsed = urlparse(self.path)
        if parsed.path == "/health":
            self._json(200, {"ok": True, "ner": CONFIG["use_ner"],
                             "poligraph": CONFIG["use_poligraph"],
                             "corpus": CONFIG["corpus_root"]})
            return
        if parsed.path != "/analyze":
            self._json(404, {"error": "not found", "paths": ["/health", "/analyze"]})
            return

        qs = parse_qs(parsed.query)
        url = (qs.get("url") or [""])[0]
        force = (qs.get("force") or ["0"])[0] in ("1", "true", "yes")
        if not url:
            self._json(400, {"error": "missing ?url="})
            return
        try:
            result = analyze_url(
                url,
                corpus_root=CONFIG["corpus_root"],
                use_ner=CONFIG["use_ner"],
                use_poligraph=CONFIG["use_poligraph"],
                force=force,
                delay=CONFIG["delay"],
            )
            self._json(200, result)
        except ValueError as exc:
            self._json(400, {"error": str(exc)})
        except Exception as exc:  # noqa: BLE001
            traceback.print_exc()
            self._json(500, {"error": f"{type(exc).__name__}: {exc}"})

    def log_message(self, fmt: str, *args) -> None:
        sys.stderr.write(f"  {self.address_string()} - {fmt % args}\n")


def main() -> None:
    ap = argparse.ArgumentParser(description="Local bridge for the tpd Firefox extension")
    ap.add_argument("--host", default="127.0.0.1", help="bind address (default: loopback)")
    ap.add_argument("--port", type=int, default=8765)
    ap.add_argument("--corpus", default=CONFIG["corpus_root"],
                    help="per-origin result/fetch cache dir")
    ap.add_argument("--no-ner", action="store_true",
                    help="gazetteer-only (faster; lower named-org recall)")
    ap.add_argument("--no-poligraph", action="store_true",
                    help="skip PoliGraph sharing-relationship extraction")
    ap.add_argument("--delay", type=float, default=CONFIG["delay"],
                    help="polite per-request delay (s)")
    args = ap.parse_args()

    CONFIG["corpus_root"] = args.corpus
    CONFIG["use_ner"] = not args.no_ner
    CONFIG["use_poligraph"] = not args.no_poligraph
    CONFIG["delay"] = args.delay

    httpd = ThreadingHTTPServer((args.host, args.port), Handler)
    print(f"tpd extension bridge on http://{args.host}:{args.port}  "
          f"(ner={CONFIG['use_ner']}, poligraph={CONFIG['use_poligraph']}, "
          f"corpus={CONFIG['corpus_root']})")
    print("  GET /analyze?url=https://example.com   ·   Ctrl-C to stop")
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\nbye")


if __name__ == "__main__":
    main()
