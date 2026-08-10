"""Code for hand-labelling sheets."""

from __future__ import annotations

import csv
import random
import sys
from pathlib import Path

from tpd.classify.run import CorpusResult

csv.field_size_limit(min(sys.maxsize, 2**31 - 1))

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
ENTITY_RESOLUTION_FIELDS = [
    "entity", "canonical_key", "named_by", "resolved_domain", "basis",
    "gold_domain", "notes",
]
CHAIN_FIELDS = [
    "label_order", "chain_id", "parties", "hop_kinds", "hop_sources",
    "hop_data_types", "evidence", "traffic_only_hops", "gold_verified", "notes",
]
COVERAGE_FIELDS = [
    "label_order", "target_id", "entity", "arrangement_id", "detected",
    "detected_data_types", "detected_sources", "evidence",
    "gold_arrangement", "notes",
]


def interleave_fresh(known: list, fresh: list, order_seed: int) -> list:
    """Insert items at random positions."""
    rng = random.Random(order_seed)
    out = list(known)
    for item in fresh:
        out.insert(rng.randint(0, len(out)), item)
    return out


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
    return list(enumerate(interleave_fresh(known, fresh, order_seed), start=1))

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


def load_typology_gold_by_doc(
    path: str | Path, reviewed_path: str | Path | None = None
) -> dict[tuple[str, str], set]:
    """Per-document typology gold."""
    settled = load_typology_gold_docs(path, reviewed_path=reviewed_path)
    gold: dict[tuple[str, str], set] = {}
    with open(path, newline="", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            tid = row.get("target_id") or ""
            did = row.get("doc_id") or ""
            if did not in settled.get(tid, set()):
                continue
            facets = {
                code.strip()
                for code in (row.get("gold_facets") or "").split(";")
                if code.strip() and code.strip().lower() != "none"
            }
            gold[(tid, did)] = facets
    return gold


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


def load_presence_doc_ids(path: str | Path, column: str) -> dict[str, set[str]]:
    """The documents an annotator identified as the privacy policy / list."""
    out: dict[str, set[str]] = {}
    with open(path, newline="", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            tid = row.get("target_id") or ""
            ids = {v.strip() for v in (row.get(column) or "").split(";") if v.strip()}
            if tid and ids:
                out[tid] = ids
    return out


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
    from tpd.poligraph.ontology import global_data_ontology

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


# --------------------------------------------------------------------------- #
# Organisation -> site resolution
# --------------------------------------------------------------------------- #
def write_entity_resolution_sheet(graph, path: str | Path,
                                  overrides: dict[str, str] | None = None,
                                  prior_path: str | Path | None = None) -> int:
    """Write the organisation-to-domain sheet a graph's expansion depends on."""
    from tpd.entities import resolve_entity_domain, resolve_name
    from tpd.sharing_graph import NodeType

    prior: dict[str, tuple[str, str]] = {}
    if prior_path:
        with open(prior_path, newline="", encoding="utf-8") as f:
            for row in csv.DictReader(f):
                key = row.get("canonical_key") or ""
                if key:
                    prior[key] = ((row.get("gold_domain") or ""),
                                  (row.get("notes") or ""))

    rows = []
    for nid, node in graph.nodes.items():
        if node.type is not NodeType.ENTITY:
            continue
        named_by = sorted({
            (graph.nodes[e.src].display_name or e.src)
            for e in graph.in_edges(nid) if e.src in graph.nodes
        })
        domain, basis = resolve_entity_domain(node.display_name, overrides=overrides)
        if not domain and node.primary_domain:
            domain, basis = node.primary_domain, "name_domain"
        key = resolve_name(node.display_name).key
        gold, notes = prior.get(key, ("", ""))
        rows.append({
            "entity": node.display_name,
            "canonical_key": key,
            "named_by": ";".join(named_by[:5]),
            "resolved_domain": domain,
            "basis": basis,
            "gold_domain": gold,
            "notes": notes,
        })
    rows.sort(key=lambda r: (bool(r["resolved_domain"]), r["entity"].lower()))

    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=ENTITY_RESOLUTION_FIELDS)
        w.writeheader()
        w.writerows(rows)
    return len(rows)


# --------------------------------------------------------------------------- #
# Onward-sharing chains
# --------------------------------------------------------------------------- #
def write_chain_sheet(chains, graph, path: str | Path,
                      order_seed: int = DEFAULT_ORDER_SEED,
                      prior_path: str | Path | None = None) -> int:
    chains = list(chains)
    random.Random(order_seed).shuffle(chains)

    prior: dict[str, tuple[str, str]] = {}
    if prior_path:
        with open(prior_path, newline="", encoding="utf-8") as f:
            for row in csv.DictReader(f):
                cid = row.get("chain_id") or ""
                if cid:
                    prior[cid] = ((row.get("gold_verified") or ""),
                                  (row.get("notes") or ""))

    def label(node_id: str) -> str:
        node = graph.nodes.get(node_id)
        return (node.display_name if node and node.display_name else node_id)

    rows = 0
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=CHAIN_FIELDS)
        w.writeheader()
        for order, chain in enumerate(chains, start=1):
            gold, notes = prior.get(chain.id, ("", ""))
            evidence = " | ".join(
                f"{label(h.src)} -> {label(h.dst)}"
                + (f" via {h.via_domain.split('::', 1)[-1]}" if h.via_domain else "")
                + f" [{','.join(h.sources)}]"
                for h in chain.hops
            )
            w.writerow({
                "label_order": order,
                "chain_id": chain.id,
                "parties": " -> ".join(label(p) for p in chain.parties),
                "hop_kinds": ";".join(h.kind for h in chain.hops),
                "hop_sources": ";".join(",".join(h.sources) for h in chain.hops),
                "hop_data_types": ";".join(
                    ",".join(h.data_types) for h in chain.hops
                ),
                "evidence": evidence,
                "traffic_only_hops": chain.traffic_only_hops,
                "gold_verified": gold,
                "notes": notes,
            })
            rows += 1
    return rows


def load_chain_gold(path: str | Path) -> dict[str, bool]:
    """Hand-verified chains, keyed by chain_id."""
    gold: dict[str, bool] = {}
    with open(path, newline="", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            cid = row.get("chain_id") or ""
            v = (row.get("gold_verified") or "").strip().lower()
            if cid and v:
                gold[cid] = v in ("1", "true", "yes")
    return gold


# --------------------------------------------------------------------------- #
# Arrangement coverage
# --------------------------------------------------------------------------- #
COVERAGE_SAMPLE_SIZE = 4


def sample_targets(target_ids, n: int = COVERAGE_SAMPLE_SIZE,
                   order_seed: int = DEFAULT_ORDER_SEED) -> list[str]:
    """A reproducible random sample of targets to label exhaustively."""
    ids = sorted(target_ids)
    if len(ids) <= n:
        return ids
    return sorted(random.Random(order_seed).sample(ids, n))


def arrangement_id(target_id: str, entity: str) -> str:
    """The key one third-party arrangement is counted under."""
    from tpd.entities import canonical_key

    return f"{target_id}::{canonical_key(entity) or entity.strip().lower()}"


def detected_arrangements(relations_by_target: dict[str, list[dict]],
                          target_ids=None) -> dict[str, dict]:
    """Third-party arrangements the analysis found, keyed by arrangement id."""
    ids = set(target_ids) if target_ids is not None else None
    out: dict[str, dict] = {}
    for tid, rels in relations_by_target.items():
        if ids is not None and tid not in ids:
            continue
        for r in rels:
            if r.get("party") != "third" or not r.get("entity"):
                continue
            if r.get("negative"):
                continue
            key = arrangement_id(tid, r["entity"])
            rec = out.setdefault(key, {
                "arrangement_id": key, "target_id": tid, "entity": r["entity"],
                "data_types": set(), "sources": set(), "text": "",
            })
            if r.get("data_type"):
                rec["data_types"].add(r["data_type"])
            rec["sources"].update(r.get("sources") or ())
            rec["text"] = rec["text"] or (r.get("text") or "")
    return out


def write_coverage_sheet(relations_by_target: dict[str, list[dict]],
                         path: str | Path, target_ids,
                         order_seed: int = DEFAULT_ORDER_SEED,
                         prior_path: str | Path | None = None) -> int:
    prior: dict[str, tuple[str, str]] = {}
    extra: list[dict] = []
    if prior_path:
        with open(prior_path, newline="", encoding="utf-8") as f:
            for row in csv.DictReader(f):
                aid = row.get("arrangement_id") or ""
                if not aid:
                    continue
                prior[aid] = ((row.get("gold_arrangement") or ""),
                              (row.get("notes") or ""))
                if (row.get("detected") or "").strip() == "0":
                    extra.append(row)

    detected = detected_arrangements(relations_by_target, target_ids)
    rows = [
        {
            "target_id": rec["target_id"],
            "entity": rec["entity"],
            "arrangement_id": aid,
            "detected": 1,
            "detected_data_types": ";".join(sorted(rec["data_types"])),
            "detected_sources": ";".join(sorted(rec["sources"])),
            "evidence": (rec["text"] or "")[:200],
        }
        for aid, rec in detected.items()
    ]
    for row in extra:
        if row["arrangement_id"] in detected:
            continue
        rows.append({
            "target_id": row.get("target_id") or "",
            "entity": row.get("entity") or "",
            "arrangement_id": row["arrangement_id"],
            "detected": 0,
            "detected_data_types": "",
            "detected_sources": "",
            "evidence": row.get("evidence") or "",
        })
    rows.sort(key=lambda r: (r["target_id"], r["entity"].lower()))
    random.Random(order_seed).shuffle(rows)

    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=COVERAGE_FIELDS)
        w.writeheader()
        for order, row in enumerate(rows, start=1):
            gold, notes = prior.get(row["arrangement_id"], ("", ""))
            w.writerow({**row, "label_order": order,
                        "gold_arrangement": gold, "notes": notes})
    return len(rows)


def load_coverage_gold(path: str | Path) -> dict[str, dict]:
    """Hand-labelled arrangements, keyed by arrangement id."""
    out: dict[str, dict] = {}
    with open(path, newline="", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            aid = row.get("arrangement_id") or ""
            v = (row.get("gold_arrangement") or "").strip().lower()
            if not aid or not v:
                continue
            out[aid] = {
                "arrangement_id": aid,
                "target_id": row.get("target_id") or "",
                "entity": row.get("entity") or "",
                "gold": v in ("1", "true", "yes"),
            }
    return out


def load_typology_gold_docs(
    path: str | Path, reviewed_path: str | Path | None = None
) -> dict[str, set]:
    """Documents whose typology gold is settled."""
    reviewed: dict[str, set] = {}
    if reviewed_path is not None:
        with open(reviewed_path, newline="", encoding="utf-8") as f:
            for row in csv.DictReader(f):
                if (row.get("gold_relevant") or "").strip() in ("0", "1"):
                    reviewed.setdefault(row.get("target_id") or "", set()).add(
                        row.get("doc_id") or ""
                    )

    docs: dict[str, set] = {}
    with open(path, newline="", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            tid = row.get("target_id") or ""
            did = row.get("doc_id") or ""
            if not (tid and did):
                continue
            if reviewed_path is not None and did not in reviewed.get(tid, set()):
                continue
            docs.setdefault(tid, set()).add(did)
    return docs
