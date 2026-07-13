"""Contradiction analysis."""

from __future__ import annotations

from dataclasses import dataclass
from itertools import product

from ..graph import EdgeType, PoliGraph, Purpose
from ..ontology import Ontology


@dataclass
class Contradiction:
    positive: object   # CollectEdge
    negative: object   # CollectEdge
    reason: str = "all parameters conflict"


def _data_conflict(pg: PoliGraph, d_pos: str, d_neg: str,
                   global_ont: Ontology | None) -> bool:
    """d_pos and d_neg conflict iff they are equal or share a common subsumee."""
    if d_pos == d_neg:
        return True
    if pg.descendants(d_pos) & pg.descendants(d_neg):
        return True
    if pg.subsumes(d_pos, d_neg) or pg.subsumes(d_neg, d_pos):
        return True
    if global_ont is not None:
        if global_ont.descendants(d_pos) & global_ont.descendants(d_neg):
            return True
    return False


def _entity_conflict(pg: PoliGraph, n_pos: str, n_neg: str) -> bool:
    if n_pos == n_neg:
        return True
    if pg.descendants(n_pos) & pg.descendants(n_neg):
        return True
    return pg.subsumes(n_pos, n_neg) or pg.subsumes(n_neg, n_pos)


def _purpose_conflict(p_pos: frozenset[Purpose], p_neg: frozenset[Purpose]) -> bool:
    if not p_neg:
        return True
    return bool(set(p_pos) & set(p_neg))


def find_contradictions(pg: PoliGraph, global_data_ont: Ontology | None = None,
                        global_entity_ont: Ontology | None = None) -> list[Contradiction]:
    """Return all conflicting (positive, negative) edge pairs."""
    positives, negatives = [], []
    for e in pg.collect_edges(include_negative=True):
        (positives if e.edge_type == EdgeType.COLLECT else negatives).append(e)

    out: list[Contradiction] = []
    for pos, neg in product(positives, negatives):
        if pos.action != neg.action:
            continue
        if pos.subject != neg.subject:
            continue
        if not _entity_conflict(pg, pos.entity, neg.entity):
            continue
        if not _data_conflict(pg, pos.data_type, neg.data_type, global_data_ont):
            continue
        if not _purpose_conflict(pos.purposes, neg.purposes):
            continue
        out.append(Contradiction(pos, neg))
    return out


def format_contradictions(cons: list[Contradiction]) -> str:
    if not cons:
        return "No contradictions found."
    lines = [f"Found {len(cons)} conflicting edge pair(s):"]
    for c in cons:
        p, n = c.positive, c.negative
        lines.append(
            f"  [+] {p.entity} COLLECT[{p.action.value}] {p.data_type}"
            f"  vs  [-] {n.entity} NOT_COLLECT[{n.action.value}] {n.data_type}"
            f"  (subject={p.subject})"
        )
    return "\n".join(lines)
