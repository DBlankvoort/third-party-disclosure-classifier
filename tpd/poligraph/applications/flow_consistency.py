"""Data flow-to-policy consistency analysis."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from ..graph import CORE_PURPOSES, NON_CORE_PURPOSES, PoliGraph, Purpose
from ..ontology import Ontology, global_data_ontology, global_entity_ontology


class Disclosure(str, Enum):
    CLEAR = "clear"
    VAGUE = "vague"
    INCONSISTENT = "inconsistent"


@dataclass
class FlowResult:
    entity: str
    data_type: str
    disclosure: Disclosure
    purposes: frozenset[Purpose]

    @property
    def consistent(self) -> bool:
        return self.disclosure in (Disclosure.CLEAR, Disclosure.VAGUE)

    @property
    def purpose_class(self) -> str:
        if self.purposes & NON_CORE_PURPOSES and self.purposes & CORE_PURPOSES:
            return "both"
        if self.purposes & NON_CORE_PURPOSES:
            return "non-core"
        if self.purposes & CORE_PURPOSES:
            return "core"
        return "unknown"


def classify_flow(pg: PoliGraph, entity: str, data_type: str,
                  data_ont: Ontology | None = None,
                  entity_ont: Ontology | None = None) -> FlowResult:
    entity, data_type = entity.strip().lower(), data_type.strip().lower()
    data_ont = data_ont or global_data_ontology()
    entity_ont = entity_ont or global_entity_ontology()

    if data_type in pg.g and entity in pg.g and pg.collects(entity, data_type):
        return FlowResult(entity, data_type, Disclosure.CLEAR,
                          pg.purposes_of(entity, data_type))

    for d_prime in data_ont.ancestors(data_type) or {data_type}:
        if d_prime not in pg.g:
            continue
        for n_prime in (entity_ont.ancestors(entity) or {entity}):
            if n_prime not in pg.g:
                continue
            if pg.collects(n_prime, d_prime):
                return FlowResult(entity, data_type, Disclosure.VAGUE,
                                  pg.purposes_of(n_prime, d_prime))
    for d_prime in data_ont.ancestors(data_type) or {data_type}:
        if d_prime in pg.g and pg.collects(entity, d_prime):
            return FlowResult(entity, data_type, Disclosure.VAGUE,
                              pg.purposes_of(entity, d_prime))

    return FlowResult(entity, data_type, Disclosure.INCONSISTENT, frozenset())


def analyze_flows(pg: PoliGraph, flows: list[tuple[str, str]],
                  data_ont: Ontology | None = None,
                  entity_ont: Ontology | None = None) -> list[FlowResult]:
    return [classify_flow(pg, e, d, data_ont, entity_ont) for (e, d) in flows]
