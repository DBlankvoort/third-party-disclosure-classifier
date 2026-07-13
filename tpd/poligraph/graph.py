"""The PoliGraph data model."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from enum import Enum
from typing import Iterable, Iterator, Optional

import networkx as nx


# --------------------------------------------------------------------------- #
# Node / edge vocabulary
# --------------------------------------------------------------------------- #
class NodeType(str, Enum):
    DATA = "DATA"      # a data type, d in D
    ENTITY = "ENTITY"  # an entity, n in N


class EdgeType(str, Enum):
    SUBSUME = "SUBSUME"
    COLLECT = "COLLECT"
    NOT_COLLECT = "NOT_COLLECT"


class Action(str, Enum):
    COLLECT = "collect"
    BE_SHARED = "be_shared"
    BE_SOLD = "be_sold"
    USE = "use"
    STORE = "store"

class Purpose(str, Enum):
    SERVICES = "services"
    SECURITY = "security"
    LEGAL = "legal"
    ADVERTISING = "advertising"
    ANALYTICS = "analytics"
    OTHER = "other"


CORE_PURPOSES = {Purpose.SERVICES, Purpose.SECURITY, Purpose.LEGAL}
NON_CORE_PURPOSES = {Purpose.ADVERTISING, Purpose.ANALYTICS}

UNSPECIFIED_DATA = "unspecified data"
UNSPECIFIED_ACTOR = "unspecified third party"
FIRST_PARTY = "we"


@dataclass(frozen=True)
class CollectEdge:
    """A COLLECT or NOT_COLLECT edge with attributes."""

    entity: str
    data_type: str
    edge_type: EdgeType = EdgeType.COLLECT
    action: Action = Action.COLLECT
    purposes: frozenset[Purpose] = field(default_factory=frozenset)
    subject: str = "general user"
    text: tuple[str, ...] = ()


class PoliGraph:
    """A knowledge-graph representation of a single privacy policy."""

    def __init__(self, policy_id: Optional[str] = None) -> None:
        self.policy_id = policy_id
        self.g = nx.MultiDiGraph()

    # ----------------------------------------------------------------- nodes
    def add_data(self, name: str, subject: str = "general user") -> str:
        name = name.strip().lower()
        if not self.g.has_node(name):
            self.g.add_node(name, ntype=NodeType.DATA, subjects={subject})
        else:
            self.g.nodes[name].setdefault("subjects", set()).add(subject)
        return name

    def add_entity(self, name: str) -> str:
        name = name.strip().lower()
        if not self.g.has_node(name):
            self.g.add_node(name, ntype=NodeType.ENTITY, subjects=set())
        return name

    def node_type(self, name: str) -> Optional[NodeType]:
        if name in self.g:
            return self.g.nodes[name]["ntype"]
        return None

    @property
    def data_nodes(self) -> list[str]:
        return [n for n, a in self.g.nodes(data=True) if a["ntype"] == NodeType.DATA]

    @property
    def entity_nodes(self) -> list[str]:
        return [n for n, a in self.g.nodes(data=True) if a["ntype"] == NodeType.ENTITY]

    # ----------------------------------------------------------------- edges
    def add_subsume(self, hypernym: str, hyponym: str) -> None:
        """Add a SUBSUME edge"""
        hypernym, hyponym = hypernym.strip().lower(), hyponym.strip().lower()
        if hypernym == hyponym or hypernym not in self.g or hyponym not in self.g:
            return
        if self.g.nodes[hypernym]["ntype"] != self.g.nodes[hyponym]["ntype"]:
            return
        if self._has_key(hypernym, hyponym, EdgeType.SUBSUME):
            return
        # Would this edge close a cycle?
        if nx.has_path(self.g, hyponym, hypernym):
            return
        self.g.add_edge(hypernym, hyponym, key=EdgeType.SUBSUME.value,
                        etype=EdgeType.SUBSUME)

    def add_collect(
        self,
        entity: str,
        data_type: str,
        *,
        negative: bool = False,
        action: Action = Action.COLLECT,
        purposes: Iterable[Purpose] = (),
        subject: str = "general user",
        text: Optional[str] = None,
    ) -> None:
        """Add a COLLECT (or NOT_COLLECT) edge with purposes/action attributes."""
        entity, data_type = entity.strip().lower(), data_type.strip().lower()
        self.add_entity(entity)
        self.add_data(data_type, subject=subject)
        etype = EdgeType.NOT_COLLECT if negative else EdgeType.COLLECT
        key = f"{etype.value}:{action.value}:{subject}"
        if self.g.has_edge(entity, data_type, key):
            attrs = self.g[entity][data_type][key]
        else:
            self.g.add_edge(entity, data_type, key=key, etype=etype,
                            action=action, subject=subject, purposes=set(), text=set())
            attrs = self.g[entity][data_type][key]
        attrs["purposes"].update(purposes)
        if text:
            attrs["text"].add(text)

    def _has_key(self, u: str, v: str, etype: EdgeType) -> bool:
        return self.g.has_edge(u, v, etype.value)

    # --------------------------------------------------------- relations/queries
    def subsumes(self, t1: str, t2: str) -> bool:
        t1, t2 = t1.strip().lower(), t2.strip().lower()
        if t1 == t2:
            return True
        if t1 not in self.g or t2 not in self.g:
            return False
        sub = self._subsume_view()
        return sub.has_node(t1) and sub.has_node(t2) and nx.has_path(sub, t1, t2)

    def _subsume_view(self) -> nx.DiGraph:
        sg = nx.DiGraph()
        sg.add_nodes_from(self.g.nodes())
        for u, v, k in self.g.edges(keys=True):
            if k == EdgeType.SUBSUME.value:
                sg.add_edge(u, v)
        return sg

    def descendants(self, term: str) -> set[str]:
        """All terms subsumed by ``term``."""
        term = term.strip().lower()
        sub = self._subsume_view()
        if term not in sub:
            return {term}
        return {term} | nx.descendants(sub, term)

    def collects(self, entity: str, data_type: str) -> bool:
        entity, data_type = entity.strip().lower(), data_type.strip().lower()
        for n_prime, d_prime, k in self.g.edges(keys=True):
            if not k.startswith(EdgeType.COLLECT.value + ":"):
                continue
            if self.subsumes(entity, n_prime) and self.subsumes(d_prime, data_type):
                return True
        return False

    def purposes_of(self, entity: str, data_type: str) -> set[Purpose]:
        entity, data_type = entity.strip().lower(), data_type.strip().lower()
        result: set[Purpose] = set()
        for n_prime, d_prime, k, attrs in self.g.edges(keys=True, data=True):
            if not k.startswith(EdgeType.COLLECT.value + ":"):
                continue
            if self.subsumes(entity, n_prime) and self.subsumes(d_prime, data_type):
                result |= attrs["purposes"]
        return result

    def collect_edges(self, include_negative: bool = False) -> Iterator[CollectEdge]:
        for u, v, k, a in self.g.edges(keys=True, data=True):
            etype = a.get("etype")
            if etype == EdgeType.COLLECT or (include_negative and etype == EdgeType.NOT_COLLECT):
                yield CollectEdge(
                    entity=u, data_type=v, edge_type=etype,
                    action=a.get("action", Action.COLLECT),
                    purposes=frozenset(a.get("purposes", set())),
                    subject=a.get("subject", "general user"),
                    text=tuple(sorted(a.get("text", set()))),
                )

    def subsume_edges(self) -> Iterator[tuple[str, str]]:
        for u, v, k in self.g.edges(keys=True):
            if k == EdgeType.SUBSUME.value:
                yield (u, v)

    def validate(self) -> "PoliGraph":
        """Ensure the graph is a DAG."""
        sub = self._subsume_view()
        while not nx.is_directed_acyclic_graph(sub):
            cycle = nx.find_cycle(sub)
            u, v = cycle[0]
            sub.remove_edge(u, v)
            if self.g.has_edge(u, v, EdgeType.SUBSUME.value):
                self.g.remove_edge(u, v, EdgeType.SUBSUME.value)
        return self

    # ------------------------------------------------------------- (de)serialize
    def to_dict(self) -> dict:
        nodes = [
            {"name": n, "type": a["ntype"].value,
             "subjects": sorted(a.get("subjects", set()))}
            for n, a in self.g.nodes(data=True)
        ]
        edges = []
        for u, v, k, a in self.g.edges(keys=True, data=True):
            e = {"from": u, "to": v, "type": a["etype"].value}
            if a["etype"] in (EdgeType.COLLECT, EdgeType.NOT_COLLECT):
                e["action"] = a.get("action", Action.COLLECT).value
                e["subject"] = a.get("subject", "general user")
                e["purposes"] = sorted(p.value for p in a.get("purposes", set()))
                e["text"] = sorted(a.get("text", set()))
            edges.append(e)
        return {"policy_id": self.policy_id, "nodes": nodes, "edges": edges}

    @classmethod
    def from_dict(cls, d: dict) -> "PoliGraph":
        pg = cls(policy_id=d.get("policy_id"))
        for n in d["nodes"]:
            if n["type"] == NodeType.DATA.value:
                name = pg.add_data(n["name"])
                pg.g.nodes[name]["subjects"] = set(n.get("subjects", ["general user"]))
            else:
                pg.add_entity(n["name"])
        for e in d["edges"]:
            et = EdgeType(e["type"])
            if et == EdgeType.SUBSUME:
                pg.add_subsume(e["from"], e["to"])
            else:
                pg.add_collect(
                    e["from"], e["to"],
                    negative=(et == EdgeType.NOT_COLLECT),
                    action=Action(e.get("action", "collect")),
                    subject=e.get("subject", "general user"),
                    purposes={Purpose(p) for p in e.get("purposes", [])},
                )
                for t in e.get("text", []):
                    pg.add_collect(e["from"], e["to"],
                                   negative=(et == EdgeType.NOT_COLLECT),
                                   action=Action(e.get("action", "collect")),
                                   subject=e.get("subject", "general user"), text=t)
        return pg

    def to_json(self, **kw) -> str:
        return json.dumps(self.to_dict(), indent=2, **kw)

    def __repr__(self) -> str:
        n_c = sum(1 for _ in self.collect_edges())
        n_s = sum(1 for _ in self.subsume_edges())
        return (f"<PoliGraph {self.policy_id!r}: {self.g.number_of_nodes()} nodes, "
                f"{n_c} COLLECT, {n_s} SUBSUME>")
