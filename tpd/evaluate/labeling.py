"""Code for hand-labelling sheets."""

from __future__ import annotations

import csv
import random
import sys
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