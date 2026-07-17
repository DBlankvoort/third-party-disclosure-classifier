"""HTTP server for manual annotation."""

from __future__ import annotations

import json
import re
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from .store import AnnotationStore

_STATIC = Path(__file__).resolve().parent / "static"
_HEAD_RE = re.compile(r"<head[^>]*>", re.I)
# Payload cap for the plain-text document view.
_RAW_VIEW_MAX = 400_000


def _iframe_page(html: str, base_url: str) -> str:
    """Saved document HTML prepared for a sandboxed iframe."""
    from html import escape

    tag = f'<base href="{escape(base_url, quote=True)}">' if base_url else ""
    if not tag:
        return html
    m = _HEAD_RE.search(html[:4096])
    if m:
        return html[: m.end()] + tag + html[m.end():]
    return tag + html


class _Handler(BaseHTTPRequestHandler):
    store: AnnotationStore  # set on the server class

    # ------------------------------------------------------------------ #
    def _send(self, code: int, body: bytes, ctype: str) -> None:
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _json(self, payload, code: int = 200) -> None:
        self._send(code, json.dumps(payload).encode("utf-8"),
                   "application/json; charset=utf-8")

    def _error(self, code: int, message: str) -> None:
        self._json({"error": message}, code=code)

    def log_message(self, fmt, *args) -> None:  # noqa: A003 - quiet server
        pass

    # ------------------------------------------------------------------ #
    def do_GET(self) -> None:  # noqa: N802
        try:
            self._route_get()
        except BrokenPipeError:
            pass
        except Exception as exc:  # noqa: BLE001
            self._error(500, str(exc))

    def _route_get(self) -> None:
        path, _, query = self.path.partition("?")
        parts = [p for p in path.split("/") if p]

        if not parts:
            page = (_STATIC / "index.html").read_bytes()
            self._send(200, page, "text/html; charset=utf-8")
        elif parts[:2] == ["api", "state"]:
            self._json(self.store.state())
        elif parts[:2] == ["api", "target"] and len(parts) == 3:
            try:
                self._json(self.store.target_detail(parts[2]))
            except KeyError as exc:
                self._error(404, str(exc))
        elif parts[:2] == ["api", "doc"] and len(parts) == 4:
            self._serve_doc(parts[2], parts[3], query)
        else:
            self._error(404, f"unknown path: {path}")

    def _serve_doc(self, target_id: str, doc_id: str, query: str) -> None:
        view = "page"
        for kv in query.split("&"):
            if kv.startswith("view="):
                view = kv[5:]
        doc, html = self.store.doc_html(target_id, doc_id)
        if doc is None:
            self._error(404, f"unknown document: {target_id}/{doc_id}")
            return
        if view == "raw":
            self._send(200, html[:_RAW_VIEW_MAX].encode("utf-8"),
                       "text/plain; charset=utf-8")
        elif view == "text":
            from ..extract import parse_html

            parsed = parse_html(html)
            self._json({"title": parsed.title, "segments": parsed.segments})
        else:
            page = _iframe_page(html, doc.url)
            self._send(200, page.encode("utf-8"), "text/html; charset=utf-8")

    # ------------------------------------------------------------------ #
    def do_POST(self) -> None:  # noqa: N802
        try:
            length = int(self.headers.get("Content-Length") or 0)
            payload = json.loads(self.rfile.read(length) or b"{}")
        except (ValueError, json.JSONDecodeError):
            self._error(400, "invalid JSON body")
            return
        try:
            self._route_post(payload)
        except KeyError as exc:
            self._error(404, str(exc))
        except ValueError as exc:
            self._error(400, str(exc))
        except Exception as exc:  # noqa: BLE001
            self._error(500, str(exc))

    def _route_post(self, payload: dict) -> None:
        path = self.path.rstrip("/")
        if path == "/api/add_doc":
            doc_id = self.store.add_manual_doc(
                payload.get("target_id", ""), payload.get("url", ""),
                render=bool(payload.get("render")),
            )
            self._json({"ok": True, "doc_id": doc_id,
                        "progress": self.store.state()["progress"]})
            return
        if path == "/api/remove_doc":
            self.store.remove_manual_doc(
                payload.get("target_id", ""), payload.get("doc_id", "")
            )
            self._json({"ok": True, "progress": self.store.state()["progress"]})
            return
        if path != "/api/save":
            self._error(404, f"unknown path: {self.path}")
            return
        sheet = payload.get("sheet")
        if sheet == "relevance":
            self.store.save_relevance(
                payload["target_id"], payload["doc_id"],
                payload.get("gold_relevant", ""), payload.get("notes"),
            )
        elif sheet == "typology":
            self.store.save_typology(
                payload["target_id"], payload["doc_id"],
                payload.get("gold_facets", ""), payload.get("notes"),
            )
        elif sheet == "presence":
            self.store.save_presence(
                payload["target_id"],
                **{k: payload.get(k) for k in (
                    "gold_pp_present", "gold_list_present",
                    "gold_pp_doc_ids", "gold_list_doc_ids", "notes",
                )},
            )
        elif sheet == "propagation":
            self.store.save_propagation(
                payload["clause_id"], payload.get("gold_correct", ""),
                payload.get("notes"),
            )
        else:
            self._error(400, f"unknown sheet: {sheet!r}")
            return
        self._json({"ok": True, "progress": self.store.state()["progress"]})


def run_server(corpus_root: str, labels_dir: str,
               host: str = "127.0.0.1", port: int = 8765) -> None:
    """Serve the annotation UI until interrupted."""
    store = AnnotationStore(corpus_root, labels_dir)
    handler = type("Handler", (_Handler,), {"store": store})
    httpd = ThreadingHTTPServer((host, port), handler)
    n = len(store.state()["targets"])
    print(f"annotating {n} targets from {corpus_root}")
    print(f"sheets: {labels_dir}")
    print(f"open http://{host}:{port}/ in a browser (Ctrl-C to stop)")
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        httpd.server_close()
