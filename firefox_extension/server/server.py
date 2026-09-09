"""Local HTTP bridge for the Firefox extension to talk to."""

from __future__ import annotations

import argparse
import json
import sys
import threading
import traceback
import uuid
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
    "probe": True,
    "allowed_origin": "",
    "entity_domains": "",
    "corroborate": 25,
}


# --------------------------------------------------------------------------- #
# Graph expansion jobs
# --------------------------------------------------------------------------- #
_JOBS: dict[str, dict] = {}
_JOBS_LOCK = threading.Lock()
MAX_JOBS = 8


def _entity_overrides() -> dict:
    path = CONFIG["entity_domains"]
    if not path:
        return {}
    try:
        from tpd.entities import load_entity_domains

        return load_entity_domains(path)
    except Exception as exc:  # noqa: BLE001
        sys.stderr.write(f"  entity domains not loaded: {exc}\n")
        return {}


def start_expansion(url: str, hops: int, requests: list, force: bool,
                    cmp: dict | None = None, time_limit: float = 0.0,
                    evidence_kind: str = "main",
                    corroborated_only: bool = False,
                    schains: list | None = None) -> str:
    from tpd.expand import Expansion

    expansion = Expansion(
        CONFIG["corpus_root"], url, hops=hops, requests=requests, force=force,
        delay=CONFIG["delay"], overrides=_entity_overrides(), cmp=cmp,
        time_limit=time_limit, evidence_kind=evidence_kind,
        probe=None if CONFIG["probe"] else False,
        lookups=CONFIG["corroborate"], corroborated_only=corroborated_only,
        schains=schains,
    )
    job_id = uuid.uuid4().hex[:12]

    def run() -> None:
        try:
            expansion.run()
        except Exception:  # noqa: BLE001
            traceback.print_exc()

    thread = threading.Thread(target=run, name=f"expand-{job_id}", daemon=True)
    with _JOBS_LOCK:
        # Discard completed jobs and stop the oldest job at the capacity limit.
        for old_id, old in list(_JOBS.items()):
            if not old["thread"].is_alive():
                del _JOBS[old_id]
        while len(_JOBS) >= MAX_JOBS:
            oldest, job = next(iter(_JOBS.items()))
            job["expansion"].stop()
            del _JOBS[oldest]
        _JOBS[job_id] = {"expansion": expansion, "thread": thread}
    thread.start()
    return job_id


def job_snapshot(job_id: str) -> dict | None:
    with _JOBS_LOCK:
        job = _JOBS.get(job_id)
    if job is None:
        return None
    snapshot = job["expansion"].snapshot()
    snapshot["job_id"] = job_id
    snapshot["running"] = job["thread"].is_alive()
    return snapshot


def stop_job(job_id: str) -> bool:
    with _JOBS_LOCK:
        job = _JOBS.get(job_id)
    if job is None:
        return False
    job["expansion"].stop()
    return True

EDIT_LOG_NAME = "graph_edits.jsonl"
MAX_EDITS_PER_CALL = 200


def apply_edits(job_id: str, edits: list) -> dict:
    with _JOBS_LOCK:
        job = _JOBS.get(job_id)
    if job is None:
        return {"error": "no such job", "job_id": job_id}
    expansion = job["expansion"]
    applied, rejected = expansion.apply_edits(edits[:MAX_EDITS_PER_CALL])
    if applied:
        _log_edits(expansion, applied)
    return {"applied": len(applied), "rejected": rejected, "job_id": job_id}


def _log_edits(expansion, edits: list) -> None:
    path = Path(CONFIG["corpus_root"]) / EDIT_LOG_NAME
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as f:
            for edit in edits:
                f.write(json.dumps({"origin": expansion.origin, **edit}) + "\n")
    except OSError as exc:
        sys.stderr.write(f"  edit log not written: {exc}\n")


def site_profile(url: str) -> dict:
    """How the extension should present one URL, before anything is collected."""
    from tpd.expand import origin_of, target_for_url
    from tpd.site_kind import profile

    origin = origin_of(url)
    target = target_for_url(url)
    return {"origin": origin, "target_id": target.id, "target_type": target.type,
            **profile(origin, target)}


class Handler(BaseHTTPRequestHandler):
    server_version = "tpd-extension-bridge/0.1"

    # -- helpers ---------------------------------------------------------- #
    def _cors(self) -> None:
        origin = self.headers.get("Origin", "")
        allowed = CONFIG["allowed_origin"]
        if allowed:
            ok = origin == allowed
        else:
            ok = origin.startswith("moz-extension://")
        if ok:
            self.send_header("Access-Control-Allow-Origin", origin)
            self.send_header("Vary", "Origin")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
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

    # Observed requests arrive as a body because a page's request list runs to
    # hundreds of URLs, well past what a query string will carry.
    _MAX_BODY = 4 * 1024 * 1024

    _PATHS = ["/health", "/site", "/analyze", "/graph", "/graph/stop",
              "/graph/edit"]

    def _body(self) -> dict | None:
        try:
            length = int(self.headers.get("Content-Length") or 0)
        except ValueError:
            length = 0
        if length <= 0 or length > self._MAX_BODY:
            self._json(400, {"error": "missing or oversized body"})
            return None
        try:
            return json.loads(self.rfile.read(length).decode("utf-8"))
        except Exception as exc:  # noqa: BLE001
            self._json(400, {"error": f"bad JSON body: {exc}"})
            return None

    def do_POST(self) -> None:  # noqa: N802
        parsed = urlparse(self.path)
        if parsed.path not in ("/analyze", "/graph", "/graph/stop", "/graph/edit"):
            self._json(404, {"error": "not found", "paths": self._PATHS})
            return
        payload = self._body()
        if payload is None:
            return
        qs = parse_qs(parsed.query)

        if parsed.path == "/graph/stop":
            job_id = payload.get("job_id") or ""
            self._json(200, {"stopped": stop_job(job_id), "job_id": job_id})
            return

        if parsed.path == "/graph/edit":
            result = apply_edits(payload.get("job_id") or "",
                                 payload.get("edits") or [])
            self._json(404 if result.get("error") else 200, result)
            return

        url = payload.get("url") or (qs.get("url") or [""])[0]
        if not url:
            self._json(400, {"error": "missing url"})
            return
        force = bool(payload.get("force")) or (
            qs.get("force") or ["0"])[0] in ("1", "true", "yes")

        if parsed.path == "/graph":
            try:
                hops = max(1, int(payload.get("hops") or 1))
            except (TypeError, ValueError):
                hops = 1
            try:
                time_limit = max(0.0, float(payload.get("time_limit") or 0))
            except (TypeError, ValueError):
                time_limit = 0.0
            try:
                job_id = start_expansion(url, hops, payload.get("requests") or [],
                                         force, payload.get("cmp"),
                                         time_limit=time_limit,
                                         evidence_kind=payload.get("evidence_kind") or "main",
                                         corroborated_only=bool(
                                             payload.get("corroborated_only")),
                                         schains=payload.get("schains") or [])
            except ValueError as exc:
                self._json(400, {"error": str(exc)})
                return
            self._json(202, {"job_id": job_id, "hops": hops,
                             "time_limit": time_limit,
                             "evidence_kind": payload.get("evidence_kind") or "main"})
            return

        self._run(url, force, payload.get("requests") or [], payload.get("cmp"))

    def _run(self, url: str, force: bool, requests: list | None = None,
             cmp: dict | None = None) -> None:
        try:
            result = analyze_url(
                url,
                corpus_root=CONFIG["corpus_root"],
                use_ner=CONFIG["use_ner"],
                use_poligraph=CONFIG["use_poligraph"],
                force=force,
                delay=CONFIG["delay"],
                requests=requests,
                cmp=cmp,
                probe=CONFIG["probe"],
                corroborate=CONFIG["corroborate"],
            )
            self._json(200, result)
        except ValueError as exc:
            self._json(400, {"error": str(exc)})
        except Exception as exc:  # noqa: BLE001
            traceback.print_exc()
            self._json(500, {"error": f"{type(exc).__name__}: {exc}"})

    def do_GET(self) -> None:  # noqa: N802
        parsed = urlparse(self.path)
        if parsed.path == "/health":
            self._json(200, {"ok": True, "ner": CONFIG["use_ner"],
                             "poligraph": CONFIG["use_poligraph"],
                             "probe": CONFIG["probe"],
                             "corpus": CONFIG["corpus_root"]})
            return
        if parsed.path == "/site":
            url = (parse_qs(parsed.query).get("url") or [""])[0]
            if not url:
                self._json(400, {"error": "missing ?url="})
                return
            try:
                self._json(200, site_profile(url))
            except ValueError as exc:
                self._json(400, {"error": str(exc)})
            return
        if parsed.path == "/graph":
            job_id = (parse_qs(parsed.query).get("job") or [""])[0]
            snapshot = job_snapshot(job_id)
            if snapshot is None:
                self._json(404, {"error": "no such job", "job_id": job_id})
                return
            self._json(200, snapshot)
            return
        if parsed.path != "/analyze":
            self._json(404, {"error": "not found", "paths": self._PATHS})
            return

        qs = parse_qs(parsed.query)
        url = (qs.get("url") or [""])[0]
        force = (qs.get("force") or ["0"])[0] in ("1", "true", "yes")
        if not url:
            self._json(400, {"error": "missing ?url="})
            return
        self._run(url, force)

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
    ap.add_argument("--no-probe", action="store_true",
                    help="report only the browser's own requests; skip the "
                         "clean-profile load that both the popup and the graph "
                         "otherwise share")
    ap.add_argument("--delay", type=float, default=CONFIG["delay"],
                    help="polite per-request delay (s)")
    ap.add_argument("--corroborate", type=int, default=CONFIG["corroborate"],
                    metavar="N",
                    help="ad systems whose sellers.json may be read to check "
                         "this site's inventory authorisations (0 to read only "
                         "what the corpus already holds)")
    ap.add_argument("--entity-domains", default="",
                    help="hand-filled entity_resolution.csv supplying "
                         "organisation domains for graph expansion")
    args = ap.parse_args()

    CONFIG["corpus_root"] = args.corpus
    CONFIG["use_ner"] = not args.no_ner
    CONFIG["use_poligraph"] = not args.no_poligraph
    CONFIG["delay"] = args.delay
    CONFIG["probe"] = not args.no_probe
    CONFIG["entity_domains"] = args.entity_domains
    CONFIG["corroborate"] = max(0, args.corroborate)

    httpd = ThreadingHTTPServer((args.host, args.port), Handler)
    print(f"tpd extension bridge on http://{args.host}:{args.port}  "
          f"(ner={CONFIG['use_ner']}, poligraph={CONFIG['use_poligraph']}, "
          f"probe={CONFIG['probe']}, corpus={CONFIG['corpus_root']})")
    print("  GET  /site?url=https://example.com   ->  kind + applicable views")
    print("  GET  /analyze?url=https://example.com")
    print("  POST /graph {url, hops}  ->  GET /graph?job=<id>   ·   Ctrl-C to stop")
    print("  POST /graph/edit {job_id, edits}  ->  corrections applied to a walk")
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\nbye")


if __name__ == "__main__":
    main()
