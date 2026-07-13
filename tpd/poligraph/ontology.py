"""Local and global ontologies."""

from __future__ import annotations

import json
from functools import lru_cache
from importlib import resources
from typing import Iterable, Optional

import networkx as nx

from .graph import EdgeType, NodeType, PoliGraph


def _load(name: str) -> dict:
    with resources.files("tpd.poligraph.data").joinpath(name).open("r", encoding="utf-8") as f:
        return json.load(f)


class Ontology:
    """A subsumption DAG over data types or entities."""

    def __init__(self, graph: nx.DiGraph, summary_categories: Optional[set[str]] = None,
                 definitions: Optional[dict[str, str]] = None):
        self.g = graph  # edges: hypernym -> hyponym
        self.summary_categories = summary_categories or set()
        self.definitions = definitions or {}

    # ---------------------------------------------------------------- queries
    def __contains__(self, term: str) -> bool:
        return self.g.has_node(term.strip().lower())

    def subsumes(self, hypernym: str, hyponym: str) -> bool:
        """Reflexive + transitive subsumption (Definition 3.2)."""
        a, b = hypernym.strip().lower(), hyponym.strip().lower()
        if a == b:
            return True
        if a not in self.g or b not in self.g:
            return False
        return nx.has_path(self.g, a, b)

    def descendants(self, term: str) -> set[str]:
        term = term.strip().lower()
        if term not in self.g:
            return set()
        return {term} | nx.descendants(self.g, term)

    def ancestors(self, term: str) -> set[str]:
        term = term.strip().lower()
        if term not in self.g:
            return set()
        return {term} | nx.ancestors(self.g, term)

    def roots(self) -> list[str]:
        return [n for n in self.g if self.g.in_degree(n) == 0]

    def leaves(self) -> list[str]:
        return [n for n in self.g if self.g.out_degree(n) == 0]

    def categorize(self, term: str) -> set[str]:
        """Return the summary categories a term belongs to."""
        term = term.strip().lower()
        if term not in self.g:
            return set()
        return {c for c in self.summary_categories if c in self.ancestors(term)}

    def define(self, term: str) -> Optional[str]:
        return self.definitions.get(term.strip().lower())


class DataOntology(Ontology):
    @classmethod
    def ccpa(cls) -> "DataOntology":
        spec = _load("ccpa_data_ontology.json")
        g = nx.DiGraph()
        summary: set[str] = set()
        defs: dict[str, str] = {}
        id2label = {c["id"]: c["label"] for c in spec["concepts"]}
        for c in spec["concepts"]:
            label = c["label"]
            g.add_node(label)
            defs[label] = c.get("def", "")
            if c.get("summary_category"):
                summary.add(label)
        for c in spec["concepts"]:
            for p in c.get("parents", []):
                g.add_edge(id2label[p], c["label"])
        return cls(g, summary, defs)

    def is_personal(self, term: str) -> bool:
        """Whether a term is (subsumed by) 'personal information'."""
        return self.subsumes("personal information", term)


class EntityOntology(Ontology):
    @classmethod
    def default(cls) -> "EntityOntology":
        from .. import gazetteer

        known = gazetteer.COMPANIES | gazetteer.SERVICES
        spec = _load("entity_ontology.json")
        g = nx.DiGraph()
        summary: set[str] = set()
        for cat in spec["categories"]:
            label = cat["label"].lower()
            g.add_node(label)
            summary.add(label)
            for m in cat["members"]:
                member = m.lower()
                if member not in known:
                    raise ValueError(
                        f"entity_ontology.json member {member!r} is not in "
                        "tpd.gazetteer (COMPANIES/SERVICES)."
                    )
                g.add_edge(label, member)
        return cls(g, summary)

    def category_of(self, entity: str) -> Optional[str]:
        """The service-type category of a concrete entity, if known."""
        entity = entity.strip().lower()
        if entity in self.summary_categories:
            return entity
        for cat in self.summary_categories:
            if self.subsumes(cat, entity):
                return cat
        return None


class LocalOntology(Ontology):
    @classmethod
    def from_poligraph(cls, pg: PoliGraph, node_type: NodeType) -> "LocalOntology":
        g = nx.DiGraph()
        for n, a in pg.g.nodes(data=True):
            if a["ntype"] == node_type:
                g.add_node(n)
        for u, v in pg.subsume_edges():
            if pg.node_type(u) == node_type and pg.node_type(v) == node_type:
                g.add_edge(u, v)
        return cls(g)


@lru_cache(maxsize=1)
def global_data_ontology() -> DataOntology:
    return DataOntology.ccpa()


@lru_cache(maxsize=1)
def global_entity_ontology() -> EntityOntology:
    return EntityOntology.default()
