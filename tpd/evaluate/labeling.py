"""Code for hand-labelling sheets."""

from __future__ import annotations

import csv
import random
from pathlib import Path

from ..classify.run import CorpusResult

# Default seed for the random ordering of targets in sheets.
DEFAULT_ORDER_SEED = 1234

RELEVANCE_FIELDS = [
    "label_order", "target_id", "target_type", "doc_id", "role", "url",
    "predicted_medium", "predicted_relevant", "predicted_evidence",
    "gold_relevant", "notes",
]
TYPOLOGY_FIELDS = [
    "label_order", "target_id", "target_type", "doc_id", "role", "url",
    "predicted_medium", "predicted_doc_facets", "predicted_target_class",
    "gold_facets", "notes",
]
PROPAGATION_FIELDS = [
    "label_order", "clause_id", "target_id", "entity", "data_type",
    "predicted_propagated", "gold_correct", "notes",
]


def _shuffled_targets(result: CorpusResult, order_seed: int,
                      prior_order: dict[str, int] | None = None):
    """A shuffled list of (label_order, target) pairs."""
    targets = list(result.targets)
    random.Random(order_seed).shuffle(targets)
    if not prior_order:
        return list(enumerate(targets, start=1))
    known = [t for t in targets if t.target_id in prior_order]
    fresh = [t for t in targets if t.target_id not in prior_order]
    known.sort(key=lambda t: prior_order[t.target_id])
    start = max(prior_order.values(), default=0) + 1
    out = [(prior_order[t.target_id], t) for t in known]
    out += list(enumerate(fresh, start=start))
    return out

def _load_prior_gold(prior_path, gold_field: str):
    """Read gold from an existing sheet."""
    order: dict[str, int] = {}
    gold: dict[tuple[str, str], tuple[str, str]] = {}
    with open(prior_path, newline="", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            tid = row.get("target_id") or ""
            if not tid:
                continue
            try:
                order.setdefault(tid, int(row["label_order"]))
            except (KeyError, ValueError):
                pass
            gold[(tid, row.get("doc_id") or "")] = (
                (row.get(gold_field) or ""), (row.get("notes") or "")
            )
    return order, gold

def write_relevance_sheet(result: CorpusResult, path: str | Path,
                          order_seed: int = DEFAULT_ORDER_SEED,
                          prior_path: str | Path | None = None) -> int:
    """Write a per-document relevance sheet."""
    prior_order, prior_gold = (
        _load_prior_gold(prior_path, "gold_relevant") if prior_path else ({}, {})
    )
    rows = 0
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=RELEVANCE_FIELDS)
        w.writeheader()
        for order, tc in _shuffled_targets(result, order_seed, prior_order):
            for d in tc.docs:
                gold, notes = prior_gold.get((tc.target_id, d.doc_id), ("", ""))
                w.writerow({
                    "label_order": order,
                    "target_id": tc.target_id,
                    "target_type": tc.target_type,
                    "doc_id": d.doc_id,
                    "role": d.role,
                    "url": d.url,
                    "predicted_medium": d.medium,
                    "predicted_relevant": int(d.relevant),
                    "predicted_evidence": d.evidence[:200],
                    "gold_relevant": gold,
                    "notes": notes,
                })
                rows += 1
    return rows


def write_typology_sheet(result: CorpusResult, path: str | Path,
                         order_seed: int = DEFAULT_ORDER_SEED,
                         prior_path: str | Path | None = None) -> int:
    """Write the per-document typology sheet."""
    prior_order, prior_gold = (
        _load_prior_gold(prior_path, "gold_facets") if prior_path else ({}, {})
    )
    rows = 0
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=TYPOLOGY_FIELDS)
        w.writeheader()
        for order, tc in _shuffled_targets(result, order_seed, prior_order):
            target_class = tc.typology_class
            for d in tc.docs:
                gold, notes = prior_gold.get((tc.target_id, d.doc_id), ("", ""))
                w.writerow({
                    "label_order": order,
                    "target_id": tc.target_id,
                    "target_type": tc.target_type,
                    "doc_id": d.doc_id,
                    "role": d.role,
                    "url": d.url,
                    "predicted_medium": d.medium,
                    "predicted_doc_facets": ";".join(d.facets),
                    "predicted_target_class": target_class,
                    "gold_facets": gold,
                    "notes": notes,
                })
                rows += 1
    return rows


def load_relevance_gold(path: str | Path) -> dict[tuple[str, str], int]:
    """Load relevance gold rows."""
    gold: dict[tuple[str, str], int] = {}
    with open(path, newline="", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            v = (row.get("gold_relevant") or "").strip()
            if v in ("0", "1"):
                gold[(row["target_id"], row["doc_id"])] = int(v)
    return gold


def load_typology_gold(path: str | Path) -> dict[str, set]:
    """Load typology gold rows."""
    agg: dict[str, set] = {}
    touched: set[str] = set()
    with open(path, newline="", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            tid = row.get("target_id") or ""
            raw = (row.get("gold_facets") or "").strip()
            if not raw:
                continue
            touched.add(tid)
            facets = agg.setdefault(tid, set())
            for code in raw.split(";"):
                code = code.strip()
                if not code or code.lower() == "none":
                    continue
                facets.add(code)
    return {tid: agg.get(tid, set()) for tid in touched}


def load_presence_gold(path: str | Path, column: str) -> dict[str, bool]:
    """Load a target-level presence gold column (e.g. "does a PP exist at all").

    Expects a sheet with a `target_id` column and a boolean-ish `column`
    (any of "1"/"true"/"yes", case-insensitive).
    """
    gold: dict[str, bool] = {}
    with open(path, newline="", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            tid = row.get("target_id") or ""
            v = (row.get(column) or "").strip().lower()
            if tid and v:
                gold[tid] = v in ("1", "true", "yes")
    return gold


def distinct_data_type_clauses(relations_by_target: dict[str, list[dict]]) -> list[dict]:
    """One clause per (target, entity) edge."""
    seen: set[tuple[str, str]] = set()
    out: list[dict] = []
    for tid, rels in relations_by_target.items():
        for r in rels:
            if r.get("negative") or not r.get("data_type"):
                continue
            key = (tid, r["entity"])
            if key in seen:
                continue
            seen.add(key)
            out.append({"target_id": tid, "entity": r["entity"], "data_type": r["data_type"]})
    return out


def write_propagation_sheet(relations_by_target: dict[str, list[dict]], path: str | Path,
                            order_seed: int = DEFAULT_ORDER_SEED,
                            prior_path: str | Path | None = None) -> int:
    """Write a hand-review sheet data type propagation."""
    from ..poligraph.ontology import global_data_ontology

    ontology = global_data_ontology()
    clauses = distinct_data_type_clauses(relations_by_target)
    random.Random(order_seed).shuffle(clauses)

    prior_gold: dict[str, tuple[str, str]] = {}
    if prior_path:
        with open(prior_path, newline="", encoding="utf-8") as f:
            for row in csv.DictReader(f):
                cid = row.get("clause_id") or ""
                if cid:
                    prior_gold[cid] = (
                        (row.get("gold_correct") or ""), (row.get("notes") or "")
                    )

    rows = 0
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=PROPAGATION_FIELDS)
        w.writeheader()
        for order, c in enumerate(clauses, start=1):
            clause_id = f"{c['target_id']}::{c['entity']}::{c['data_type']}"
            propagated = sorted(ontology.descendants(c["data_type"]) - {c["data_type"].strip().lower()})
            gold, notes = prior_gold.get(clause_id, ("", ""))
            w.writerow({
                "label_order": order,
                "clause_id": clause_id,
                "target_id": c["target_id"],
                "entity": c["entity"],
                "data_type": c["data_type"],
                "predicted_propagated": ";".join(propagated),
                "gold_correct": gold,
                "notes": notes,
            })
            rows += 1
    return rows


def load_propagation_gold(path: str | Path) -> dict[str, bool]:
    """Load hand-reviewed gold, keyed by clause_id."""
    gold: dict[str, bool] = {}
    with open(path, newline="", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            cid = row.get("clause_id") or ""
            v = (row.get("gold_correct") or "").strip().lower()
            if cid and v:
                gold[cid] = v in ("1", "true", "yes")
    return gold


def load_typology_gold_docs(path: str | Path) -> dict[str, set]:
    """Load docs for which typology gold exists."""
    docs: dict[str, set] = {}
    with open(path, newline="", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            tid = row.get("target_id") or ""
            did = row.get("doc_id") or ""
            if tid and did:
                docs.setdefault(tid, set()).add(did)
    return docs
