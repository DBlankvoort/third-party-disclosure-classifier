"""Store used for annotations."""

from __future__ import annotations

import csv
import json
import os
import threading
import time
from pathlib import Path

from ..collect.base import CollectedDoc, Corpus, Target, fetch
from ..collect.pdf import looks_like_pdf
from ..evaluate.labeling import PROPAGATION_FIELDS, RELEVANCE_FIELDS, TYPOLOGY_FIELDS

PRESENCE_FIELDS = [
    "label_order", "target_id", "target_type", "url",
    "gold_pp_present", "gold_list_present",
    "gold_pp_doc_ids", "gold_list_doc_ids", "notes",
]

RELEVANCE_SHEET = "relevance_labels.csv"
TYPOLOGY_SHEET = "typology_labels.csv"
PROPAGATION_SHEET = "propagation_labels.csv"
PRESENCE_SHEET = "presence_labels.csv"

# Roles whose payload is machine-readable.
MACHINE_READABLE_ROLES = {
    "ads_txt", "app_ads_txt", "sellers_json", "vendors_json", "tcf_gvl",
}

# Manual additions
MANUAL_ROLE = "manual"
MANUAL_SIDECAR = "manual_docs.json"

_GOALS = {
    "relevance_targets": 100,
    "presence_website": 100,
    "presence_app": 100,
    "typology_docs": 50,
    "clauses": 30,
}
_APP_TYPES = {"play_store_app", "app_store_app"}


def _read_sheet(path: Path) -> list[dict]:
    if not path.exists():
        return []
    with open(path, newline="", encoding="utf-8") as f:
        return [dict(row) for row in csv.DictReader(f)]


def _write_sheet(path: Path, fields: list[str], rows: list[dict]) -> None:
    """Atomically rewrite a sheet."""
    tmp = path.with_name(path.name + ".tmp")
    with open(tmp, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=fields, extrasaction="ignore")
        w.writeheader()
        for row in rows:
            w.writerow(row)
    os.replace(tmp, path)


def _order_key(row: dict) -> tuple:
    try:
        return (0, int(row.get("label_order") or 0))
    except ValueError:
        return (1, 0)


class AnnotationStore:
    """All sheets and the corpus, with a single lock."""

    def __init__(self, corpus_root: str | Path, labels_dir: str | Path):
        self.corpus = Corpus(corpus_root)
        self.labels_dir = Path(labels_dir)
        self._lock = threading.Lock()
        self._manifests: dict[str, tuple[Target, list[CollectedDoc]]] = {}

        self.relevance = _read_sheet(self.labels_dir / RELEVANCE_SHEET)
        if not self.relevance:
            raise FileNotFoundError(
                f"{self.labels_dir / RELEVANCE_SHEET} not found or empty - generate "
                f"sheets first: tpd label --corpus {corpus_root} --out {labels_dir}"
            )
        self.typology = _read_sheet(self.labels_dir / TYPOLOGY_SHEET)
        self.propagation = _read_sheet(self.labels_dir / PROPAGATION_SHEET)
        self.presence = _read_sheet(self.labels_dir / PRESENCE_SHEET)
        self._reconcile_manual_docs()
        self._ensure_presence_rows()

    # ------------------------------------------------------------------ #
    # Corpus
    # ------------------------------------------------------------------ #
    def manifest(self, target_id: str) -> tuple[Target, list[CollectedDoc]]:
        if target_id not in self._manifests:
            self._manifests[target_id] = self.corpus.read_manifest(target_id)
        return self._manifests[target_id]

    def doc_html(self, target_id: str, doc_id: str) -> tuple[CollectedDoc | None, str]:
        try:
            _, docs = self.manifest(target_id)
        except FileNotFoundError:
            docs = []
        for d in docs:
            if d.doc_id == doc_id:
                return d, self.corpus.read_doc_html(d)
        for e in self._read_sidecar(target_id):
            if e.get("doc_id") == doc_id:
                d = CollectedDoc(
                    doc_id=doc_id, url=e.get("url", ""), role=MANUAL_ROLE,
                    http_status=200, raw_path=f"{target_id}/manual/{doc_id}.html",
                )
                return d, self.corpus.read_doc_html(d)
        return None, ""

    def _target_url(self, target_id: str, rows: list[dict]) -> str:
        try:
            target, docs = self.manifest(target_id)
        except FileNotFoundError:
            target, docs = None, []
        if target is not None:
            for cand in (target.url, target.seed_policy_url):
                if cand:
                    return cand
            for d in docs:
                if d.role == "store_listing" and d.url:
                    return d.url
        for row in rows:
            if row.get("url"):
                return row["url"]
        return ""

    # ------------------------------------------------------------------ #
    # Sheets
    # ------------------------------------------------------------------ #
    def _targets_in_order(self) -> list[tuple[str, list[dict]]]:
        """(target_id, relevance rows) grouped, in label_order."""
        by_tid: dict[str, list[dict]] = {}
        for row in self.relevance:
            tid = row.get("target_id") or ""
            if tid:
                by_tid.setdefault(tid, []).append(row)
        return sorted(by_tid.items(), key=lambda kv: _order_key(kv[1][0]))

    def _ensure_presence_rows(self) -> None:
        """One presence row per target in the relevance sheet."""
        existing = {r.get("target_id") for r in self.presence}
        added = False
        for tid, rows in self._targets_in_order():
            if tid in existing:
                continue
            self.presence.append({
                "label_order": rows[0].get("label_order", ""),
                "target_id": tid,
                "target_type": rows[0].get("target_type", ""),
                "url": self._target_url(tid, rows),
                "gold_pp_present": "",
                "gold_list_present": "",
                "gold_pp_doc_ids": "",
                "gold_list_doc_ids": "",
                "notes": "",
            })
            added = True
        if added or not (self.labels_dir / PRESENCE_SHEET).exists():
            self.presence.sort(key=_order_key)
            _write_sheet(self.labels_dir / PRESENCE_SHEET, PRESENCE_FIELDS, self.presence)

    def _find(self, rows: list[dict], **key: str) -> dict | None:
        for row in rows:
            if all(row.get(k) == v for k, v in key.items()):
                return row
        return None

    # ------------------------------------------------------------------ #
    # Manually added documents
    # ------------------------------------------------------------------ #
    def _sidecar_path(self, target_id: str) -> Path:
        return self.corpus.root / target_id / MANUAL_SIDECAR

    def _read_sidecar(self, target_id: str) -> list[dict]:
        p = self._sidecar_path(target_id)
        if not p.exists():
            return []
        try:
            return json.loads(p.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return []

    def _write_sidecar(self, target_id: str, entries: list[dict]) -> None:
        self._sidecar_path(target_id).write_text(
            json.dumps(entries, indent=2), encoding="utf-8"
        )

    def _manual_row(self, target_id: str, template: dict, entry: dict,
                    fields: list[str]) -> dict:
        """A relevance/typology sheet row for a manual doc."""
        row = {f: "" for f in fields}
        row.update({
            "label_order": template.get("label_order", ""),
            "target_id": target_id,
            "target_type": template.get("target_type", ""),
            "doc_id": entry["doc_id"],
            "role": MANUAL_ROLE,
            "url": entry.get("url", ""),
        })
        if "gold_relevant" in fields:
            row["gold_relevant"] = entry.get("gold_relevant", "")
            row["notes"] = entry.get("notes", "")
        if "gold_facets" in fields:
            row["gold_facets"] = entry.get("gold_facets", "")
            row["notes"] = entry.get("typology_notes", "")
        return row

    @staticmethod
    def _insert_after_target(rows: list[dict], row: dict, target_id: str) -> None:
        idx = max(
            (i for i, r in enumerate(rows) if r.get("target_id") == target_id),
            default=len(rows) - 1,
        )
        rows.insert(idx + 1, row)

    def _reconcile_manual_docs(self) -> None:
        """Re-inject manual docs."""
        changed = {"relevance": False, "typology": False}
        for tid, rows in self._targets_in_order():
            entries = self._read_sidecar(tid)
            if not entries:
                continue
            for sheet_name, rows_list, fields in (
                ("relevance", self.relevance, RELEVANCE_FIELDS),
                ("typology", self.typology, TYPOLOGY_FIELDS),
            ):
                have = {r.get("doc_id") for r in rows_list if r.get("target_id") == tid}
                for entry in entries:
                    if entry.get("doc_id") in have:
                        continue
                    self._insert_after_target(
                        rows_list, self._manual_row(tid, rows[0], entry, fields), tid
                    )
                    changed[sheet_name] = True
        if changed["relevance"]:
            _write_sheet(self.labels_dir / RELEVANCE_SHEET, RELEVANCE_FIELDS, self.relevance)
        if changed["typology"]:
            _write_sheet(self.labels_dir / TYPOLOGY_SHEET, TYPOLOGY_FIELDS, self.typology)

    def _fetch_manual(self, url: str, render: bool) -> tuple[str, str, str]:
        """(html, final_url, error) for a manual fetch."""
        if render and not looks_like_pdf(url=url):
            try:
                from ..collect.runner import Renderer

                with Renderer() as r:
                    html = r.render(url) if r.available else None
                if html:
                    return html, url, ""
            except Exception:  # noqa: BLE001 - fall back to a static fetch
                pass
        res = fetch(url, force=True)
        if not res.ok or not res.text:
            if res.error:
                err = res.error
            elif res.status == 200:
                err = f"no text could be extracted ({res.content_type or 'unknown content type'})"
            else:
                err = f"HTTP {res.status or 'error'}"
            return "", "", err
        return res.text, res.final_url or url, ""

    def add_manual_doc(self, target_id: str, url: str, render: bool = False) -> str:
        """Fetch ``url`` and add it to the target's annotatable document set."""
        url = (url or "").strip()
        if not url.startswith(("http://", "https://")):
            raise ValueError("enter a full http(s):// URL")
        with self._lock:
            rows = [r for r in self.relevance if r.get("target_id") == target_id]
            if not rows:
                raise KeyError(f"unknown target: {target_id}")
            if any((r.get("url") or "").rstrip("/") == url.rstrip("/") for r in rows):
                raise ValueError("that URL is already in the document set")

        html, final_url, err = self._fetch_manual(url, render)  # slow: outside the lock
        if err:
            raise ValueError(f"fetch failed: {err}")

        with self._lock:
            existing = {r.get("doc_id") for r in self.relevance
                        if r.get("target_id") == target_id}
            i = 0
            while f"manual-{i:02d}" in existing:
                i += 1
            doc_id = f"manual-{i:02d}"
            path = self.corpus.root / target_id / "manual" / f"{doc_id}.html"
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(html, encoding="utf-8")
            entries = self._read_sidecar(target_id)
            entries.append({
                "doc_id": doc_id, "url": final_url, "role": MANUAL_ROLE,
                "fetched_at": time.time(), "gold_relevant": "", "notes": "",
                "gold_facets": "", "typology_notes": "",
            })
            self._write_sidecar(target_id, entries)
            template = next(r for r in self.relevance if r.get("target_id") == target_id)
            self._insert_after_target(
                self.relevance,
                self._manual_row(target_id, template, entries[-1], RELEVANCE_FIELDS),
                target_id,
            )
            self._insert_after_target(
                self.typology,
                self._manual_row(target_id, template, entries[-1], TYPOLOGY_FIELDS),
                target_id,
            )
            _write_sheet(self.labels_dir / RELEVANCE_SHEET, RELEVANCE_FIELDS, self.relevance)
            _write_sheet(self.labels_dir / TYPOLOGY_SHEET, TYPOLOGY_FIELDS, self.typology)
            return doc_id

    def remove_manual_doc(self, target_id: str, doc_id: str) -> None:
        with self._lock:
            row = self._find(self.relevance, target_id=target_id, doc_id=doc_id)
            if row is None:
                raise KeyError(f"no document {target_id}/{doc_id}")
            if row.get("role") != MANUAL_ROLE:
                raise ValueError("only manually added documents can be removed")
            self.relevance.remove(row)
            _write_sheet(self.labels_dir / RELEVANCE_SHEET, RELEVANCE_FIELDS, self.relevance)
            typ_row = self._find(self.typology, target_id=target_id, doc_id=doc_id)
            if typ_row is not None:
                self.typology.remove(typ_row)
                _write_sheet(self.labels_dir / TYPOLOGY_SHEET, TYPOLOGY_FIELDS, self.typology)
            self._write_sidecar(target_id, [
                e for e in self._read_sidecar(target_id) if e.get("doc_id") != doc_id
            ])
            (self.corpus.root / target_id / "manual" / f"{doc_id}.html").unlink(missing_ok=True)
            pres = self._find(self.presence, target_id=target_id)
            if pres:
                dirty = False
                for col in ("gold_pp_doc_ids", "gold_list_doc_ids"):
                    ids = [x for x in (pres.get(col) or "").split(";") if x and x != doc_id]
                    if ";".join(ids) != (pres.get(col) or ""):
                        pres[col] = ";".join(ids)
                        dirty = True
                if dirty:
                    _write_sheet(self.labels_dir / PRESENCE_SHEET, PRESENCE_FIELDS, self.presence)

    # ------------------------------------------------------------------ #
    # Saving gold
    # ------------------------------------------------------------------ #
    def save_relevance(self, target_id: str, doc_id: str,
                       gold_relevant: str, notes: str | None = None) -> None:
        with self._lock:
            row = self._find(self.relevance, target_id=target_id, doc_id=doc_id)
            if row is None:
                raise KeyError(f"no relevance row for {target_id}/{doc_id}")
            row["gold_relevant"] = gold_relevant
            if notes is not None:
                row["notes"] = notes
            _write_sheet(self.labels_dir / RELEVANCE_SHEET, RELEVANCE_FIELDS, self.relevance)
            if row.get("role") == MANUAL_ROLE:
                # Sidecar keeps manual gold safe across sheet regeneration.
                entries = self._read_sidecar(target_id)
                for e in entries:
                    if e.get("doc_id") == doc_id:
                        e["gold_relevant"] = gold_relevant
                        if notes is not None:
                            e["notes"] = notes
                self._write_sidecar(target_id, entries)

    def save_typology(self, target_id: str, doc_id: str,
                      gold_facets: str, notes: str | None = None) -> None:
        with self._lock:
            row = self._find(self.typology, target_id=target_id, doc_id=doc_id)
            if row is None:
                rel = self._find(self.relevance, target_id=target_id, doc_id=doc_id) or {}
                row = {f: rel.get(f, "") for f in TYPOLOGY_FIELDS}
                row.update({"target_id": target_id, "doc_id": doc_id,
                            "gold_facets": "", "notes": ""})
                self.typology.append(row)
                self.typology.sort(key=_order_key)
            row["gold_facets"] = gold_facets
            if notes is not None:
                row["notes"] = notes
            _write_sheet(self.labels_dir / TYPOLOGY_SHEET, TYPOLOGY_FIELDS, self.typology)
            if row.get("role") == MANUAL_ROLE:
                entries = self._read_sidecar(target_id)
                for e in entries:
                    if e.get("doc_id") == doc_id:
                        e["gold_facets"] = gold_facets
                        if notes is not None:
                            e["typology_notes"] = notes
                self._write_sidecar(target_id, entries)

    def save_presence(self, target_id: str, **values: str) -> None:
        allowed = {"gold_pp_present", "gold_list_present",
                   "gold_pp_doc_ids", "gold_list_doc_ids", "notes"}
        with self._lock:
            row = self._find(self.presence, target_id=target_id)
            if row is None:
                raise KeyError(f"no presence row for {target_id}")
            for k, v in values.items():
                if k in allowed and v is not None:
                    row[k] = v
            _write_sheet(self.labels_dir / PRESENCE_SHEET, PRESENCE_FIELDS, self.presence)

    def save_propagation(self, clause_id: str, gold_correct: str,
                         notes: str | None = None) -> None:
        with self._lock:
            row = self._find(self.propagation, clause_id=clause_id)
            if row is None:
                raise KeyError(f"no propagation row for {clause_id}")
            row["gold_correct"] = gold_correct
            if notes is not None:
                row["notes"] = notes
            _write_sheet(self.labels_dir / PROPAGATION_SHEET, PROPAGATION_FIELDS,
                         self.propagation)

    # ------------------------------------------------------------------ #
    # Read models for the UI
    # ------------------------------------------------------------------ #
    @staticmethod
    def _filled(v: str | None) -> bool:
        return bool((v or "").strip())

    def _target_steps(self, tid: str, rows: list[dict]) -> dict:
        """Per-target completion for annotation steps."""
        presence = self._find(self.presence, target_id=tid) or {}
        typ_by_doc = {r.get("doc_id"): r for r in self.typology
                      if r.get("target_id") == tid}
        clauses = [r for r in self.propagation if r.get("target_id") == tid]

        docs_done = sum(1 for r in rows if (r.get("gold_relevant") or "").strip() in ("0", "1"))
        s1 = (docs_done == len(rows)
              and self._filled(presence.get("gold_pp_present"))
              and self._filled(presence.get("gold_list_present")))
        relevant_docs = [r for r in rows if (r.get("gold_relevant") or "").strip() == "1"]
        s2 = bool(docs_done == len(rows)) and all(
            self._filled((typ_by_doc.get(r.get("doc_id")) or {}).get("gold_facets"))
            for r in relevant_docs
        )
        s3 = all(self._filled(r.get("gold_correct")) for r in clauses)
        return {
            "docs_done": docs_done, "docs_total": len(rows),
            "s1": s1, "s2": s2, "s3": s3,
            "clauses_total": len(clauses),
            "clauses_done": sum(1 for r in clauses if self._filled(r.get("gold_correct"))),
        }

    def state(self) -> dict:
        with self._lock:
            targets = []
            for tid, rows in self._targets_in_order():
                first = rows[0]
                presence = self._find(self.presence, target_id=tid) or {}
                targets.append({
                    "target_id": tid,
                    "target_type": first.get("target_type", ""),
                    "label_order": first.get("label_order", ""),
                    "url": presence.get("url") or self._target_url(tid, rows),
                    "steps": self._target_steps(tid, rows),
                })
            return {"targets": targets, "progress": self._progress()}

    def _progress(self) -> dict:
        by_tid: dict[str, list[dict]] = {}
        for row in self.relevance:
            if row.get("target_id"):
                by_tid.setdefault(row["target_id"], []).append(row)

        rel_done = sum(
            1 for rows in by_tid.values()
            if all((r.get("gold_relevant") or "").strip() in ("0", "1") for r in rows)
        )
        pres = {"website": [0, 0], "app": [0, 0]}
        for row in self.presence:
            group = "app" if row.get("target_type") in _APP_TYPES else "website"
            pres[group][1] += 1
            if self._filled(row.get("gold_pp_present")) and self._filled(row.get("gold_list_present")):
                pres[group][0] += 1
        typ_done = sum(1 for r in self.typology if self._filled(r.get("gold_facets")))
        cl_done = sum(1 for r in self.propagation if self._filled(r.get("gold_correct")))

        def entry(name: str, done: int, total: int) -> dict:
            return {"done": done, "total": total, "goal": _GOALS[name]}

        return {
            "relevance_targets": entry("relevance_targets", rel_done, len(by_tid)),
            "presence_website": entry("presence_website", *pres["website"]),
            "presence_app": entry("presence_app", *pres["app"]),
            "typology_docs": entry("typology_docs", typ_done, len(self.typology)),
            "clauses": entry("clauses", cl_done, len(self.propagation)),
        }

    def target_detail(self, tid: str) -> dict:
        with self._lock:
            rows = [r for r in self.relevance if r.get("target_id") == tid]
            if not rows:
                raise KeyError(f"unknown target: {tid}")
            typ_by_doc = {r.get("doc_id"): r for r in self.typology
                          if r.get("target_id") == tid}
            presence = self._find(self.presence, target_id=tid) or {}
            pp_ids = set((presence.get("gold_pp_doc_ids") or "").split(";")) - {""}
            list_ids = set((presence.get("gold_list_doc_ids") or "").split(";")) - {""}
            docs = []
            for r in rows:
                t = typ_by_doc.get(r.get("doc_id")) or {}
                docs.append({
                    "doc_id": r.get("doc_id", ""),
                    "role": r.get("role", ""),
                    "url": r.get("url", ""),
                    "predicted_medium": r.get("predicted_medium", ""),
                    "predicted_relevant": r.get("predicted_relevant", ""),
                    "predicted_evidence": r.get("predicted_evidence", ""),
                    "predicted_doc_facets": t.get("predicted_doc_facets", ""),
                    "gold_relevant": r.get("gold_relevant", ""),
                    "relevance_notes": r.get("notes", ""),
                    "gold_facets": t.get("gold_facets", ""),
                    "typology_notes": t.get("notes", ""),
                    "is_pp": r.get("doc_id") in pp_ids,
                    "is_list": r.get("doc_id") in list_ids,
                    "machine_readable": r.get("role", "") in MACHINE_READABLE_ROLES,
                    "manual": r.get("role", "") == MANUAL_ROLE,
                })
            clauses = [
                {
                    "clause_id": r.get("clause_id", ""),
                    "entity": r.get("entity", ""),
                    "data_type": r.get("data_type", ""),
                    "predicted_propagated": r.get("predicted_propagated", ""),
                    "gold_correct": r.get("gold_correct", ""),
                    "notes": r.get("notes", ""),
                }
                for r in self.propagation if r.get("target_id") == tid
            ]
            return {
                "target_id": tid,
                "target_type": rows[0].get("target_type", ""),
                "label_order": rows[0].get("label_order", ""),
                "url": presence.get("url") or self._target_url(tid, rows),
                "presence": {
                    "gold_pp_present": presence.get("gold_pp_present", ""),
                    "gold_list_present": presence.get("gold_list_present", ""),
                    "notes": presence.get("notes", ""),
                },
                "docs": docs,
                "clauses": clauses,
                "steps": self._target_steps(tid, rows),
            }
