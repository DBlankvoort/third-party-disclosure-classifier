"""A canonical graph of data-sharing arrangements across targets."""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path

from .entities import is_shared_platform, resolve_name


class NodeType(str, Enum):
    TARGET = "target"        # an analysed app or website
    ENTITY = "entity"        # a named organisation
    DOMAIN = "domain"        # a host contacted by a target
    GENERIC = "generic"      # an unnamed category ("advertising partners")


class EdgeKind(str, Enum):
    CONTACTS = "contacts"                              # Target  -> Domain
    OWNED_BY = "owned_by"                              # Domain  -> Entity
    DISCLOSES_SHARING_WITH = "discloses_sharing_with"  # Target  -> Entity
    SUPPLIES = "supplies"                              # Entity  -> Target


class EvidenceSource(str, Enum):
    POLICY = "policy"
    REGISTRY = "registry"
    TRAFFIC = "traffic"
    RESOLUTION = "resolution"


GENERIC_PREFIX = "generic::"


def generic_node_id(category: str) -> str:
    return f"{GENERIC_PREFIX}{category.strip().lower()}"


def entity_node_id(name: str) -> str:
    return f"entity::{resolve_name(name).key or name.strip().lower()}"


def entity_node(name: str, hop: int = 0) -> Node:
    """The node one organisation name stands for."""
    resolved = resolve_name(name)
    surface = name.strip()
    node = Node(
        id=f"entity::{resolved.key or surface.lower()}",
        type=NodeType.ENTITY,
        display_name=resolved.display or surface,
        resolution_basis=resolved.basis,
        hop_first_seen=hop,
    )
    if surface and surface.lower() != node.display_name.lower():
        node.aliases = [surface]
    # A name written as a domain states where the party publishes.
    if (resolved.domain and resolved.basis != "domain_map"
            and not is_shared_platform(resolved.domain)):
        node.primary_domain = resolved.domain
    return node


def domain_node_id(domain: str) -> str:
    return f"domain::{domain.strip().lower()}"


def target_node_id(target_id: str) -> str:
    return f"target::{target_id}"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


@dataclass
class Node:
    id: str
    type: NodeType
    display_name: str = ""
    # Entity-only.
    aliases: list[str] = field(default_factory=list)
    primary_domain: str = ""
    # Domain-only.
    owner_entity_id: str = ""
    resolution_basis: str = ""
    # Target-only.
    target_type: str = ""
    # Distance from the seed target the crawl started at.
    hop_first_seen: int | None = None
    # Whether the node has been analysed in its own right.
    expanded: bool = False

    def to_dict(self) -> dict:
        d = asdict(self)
        d["type"] = self.type.value
        return d

    @classmethod
    def from_dict(cls, d: dict) -> Node:
        d = dict(d)
        d["type"] = NodeType(d["type"])
        return cls(**d)


@dataclass
class Evidence:
    source: EvidenceSource
    observed_at: str = field(default_factory=_now)
    hop: int = 0
    doc_ids: list[str] = field(default_factory=list)
    snippet: str = ""
    data_type: str = ""
    purposes: list[str] = field(default_factory=list)
    negative: bool = False

    def to_dict(self) -> dict:
        d = asdict(self)
        d["source"] = self.source.value
        return d

    @classmethod
    def from_dict(cls, d: dict) -> Evidence:
        d = dict(d)
        d["source"] = EvidenceSource(d["source"])
        return cls(**d)


@dataclass
class Edge:
    kind: EdgeKind
    src: str
    dst: str
    evidence: list[Evidence] = field(default_factory=list)

    @property
    def key(self) -> tuple[str, str, str]:
        return (self.kind.value, self.src, self.dst)

    @property
    def sources(self) -> set[str]:
        return {e.source.value for e in self.evidence}

    def to_dict(self) -> dict:
        return {
            "kind": self.kind.value, "src": self.src, "dst": self.dst,
            "evidence": [e.to_dict() for e in self.evidence],
        }

    @classmethod
    def from_dict(cls, d: dict) -> Edge:
        return cls(
            kind=EdgeKind(d["kind"]), src=d["src"], dst=d["dst"],
            evidence=[Evidence.from_dict(e) for e in d.get("evidence", [])],
        )


class SharingGraph:
    """Nodes and evidence-bearing edges, addressable by hop distance."""

    def __init__(self) -> None:
        self.nodes: dict[str, Node] = {}
        self.edges: dict[tuple[str, str, str], Edge] = {}

    # ------------------------------------------------------------- mutation
    def add_node(self, node: Node) -> Node:
        existing = self.nodes.get(node.id)
        if existing is None:
            self.nodes[node.id] = node
            return node
        # Merge: keep the first display name but accumulate what was learned.
        if not existing.display_name:
            existing.display_name = node.display_name
        seen = {a.lower() for a in existing.aliases} | {existing.display_name.lower()}
        for a in node.aliases:
            if a.lower() not in seen:
                seen.add(a.lower())
                existing.aliases.append(a)
        existing.primary_domain = existing.primary_domain or node.primary_domain
        existing.owner_entity_id = existing.owner_entity_id or node.owner_entity_id
        existing.resolution_basis = existing.resolution_basis or node.resolution_basis
        existing.target_type = existing.target_type or node.target_type
        existing.expanded = existing.expanded or node.expanded
        if node.hop_first_seen is not None:
            existing.hop_first_seen = (
                node.hop_first_seen if existing.hop_first_seen is None
                else min(existing.hop_first_seen, node.hop_first_seen)
            )
        return existing

    def add_edge(self, kind: EdgeKind, src: str, dst: str,
                 evidence: Evidence | None = None) -> Edge:
        key = (kind.value, src, dst)
        edge = self.edges.get(key)
        if edge is None:
            edge = Edge(kind=kind, src=src, dst=dst)
            self.edges[key] = edge
        if evidence is not None:
            edge.evidence.append(evidence)
        return edge

    # ------------------------------------------------------------ traversal
    def out_edges(self, node_id: str) -> list[Edge]:
        return [e for e in self.edges.values() if e.src == node_id]

    def in_edges(self, node_id: str) -> list[Edge]:
        return [e for e in self.edges.values() if e.dst == node_id]

    def downstream(self, node_id: str, hops: int = 1) -> dict[int, set[str]]:
        """Nodes reachable from ``node_id``, keyed by hop distance."""
        return self._walk(node_id, hops, forward=True)

    def upstream(self, node_id: str, hops: int = 1) -> dict[int, set[str]]:
        """Nodes that reach ``node_id``, keyed by hop distance."""
        return self._walk(node_id, hops, forward=False)

    def _walk(self, start: str, hops: int, forward: bool) -> dict[int, set[str]]:
        out: dict[int, set[str]] = {}
        seen = {start}
        frontier = {start}
        for depth in range(1, max(0, hops) + 1):
            nxt: set[str] = set()
            for nid in frontier:
                edges = self.out_edges(nid) if forward else self.in_edges(nid)
                for e in edges:
                    other = e.dst if forward else e.src
                    if other not in seen:
                        seen.add(other)
                        nxt.add(other)
            if not nxt:
                break
            out[depth] = nxt
            frontier = nxt
        return out

    def paths(self, start: str, hops: int = 4) -> list[list[Edge]]:
        """Simple edge paths of up to ``hops`` length leaving ``start``."""
        found: list[list[Edge]] = []

        def walk(node: str, trail: list[Edge], visited: set[str]) -> None:
            if len(trail) >= hops:
                return
            for e in self.out_edges(node):
                if e.dst in visited:
                    continue
                path = trail + [e]
                found.append(path)
                walk(e.dst, path, visited | {e.dst})

        walk(start, [], {start})
        return found

    def leaves(self) -> list[Node]:
        """Nodes with no outgoing edges."""
        return [n for nid, n in self.nodes.items() if not self.out_edges(nid)]

    def termination(self, node_id: str) -> str:
        node = self.nodes.get(node_id)
        if node is None:
            return "unknown"
        if self.out_edges(node_id):
            return "internal"
        return "terminal" if node.expanded else "unexpanded"

    # ------------------------------------------------------- serialisation
    def to_dict(self) -> dict:
        return {
            "nodes": [n.to_dict() for n in self.nodes.values()],
            "edges": [e.to_dict() for e in self.edges.values()],
        }

    @classmethod
    def from_dict(cls, d: dict) -> SharingGraph:
        g = cls()
        for nd in d.get("nodes", []):
            node = Node.from_dict(nd)
            g.nodes[node.id] = node
        for ed in d.get("edges", []):
            edge = Edge.from_dict(ed)
            g.edges[edge.key] = edge
        return g

    def save(self, path: str | Path) -> None:
        p = Path(path)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps(self.to_dict(), indent=1), encoding="utf-8")

    @classmethod
    def load(cls, path: str | Path) -> SharingGraph:
        return cls.from_dict(json.loads(Path(path).read_text(encoding="utf-8")))


# --------------------------------------------------------------------------- #
# Chains of onward sharing
# --------------------------------------------------------------------------- #
@dataclass
class ChainHop:
    """One party handing data to the next."""

    src: str
    dst: str
    kind: str
    sources: list[str] = field(default_factory=list)
    data_types: list[str] = field(default_factory=list)
    via_domain: str = ""

    @property
    def traffic_only(self) -> bool:
        return set(self.sources) == {EvidenceSource.TRAFFIC.value}


@dataclass
class SharingChain:
    """A path along which data passes from one organisation to the next."""

    parties: list[str]
    hops: list[ChainHop]

    @property
    def id(self) -> str:
        return " -> ".join(self.parties)

    @property
    def sources(self) -> set[str]:
        return {s for h in self.hops for s in h.sources}

    @property
    def traffic_only_hops(self) -> int:
        return sum(1 for h in self.hops if h.traffic_only)

    @property
    def fully_disclosed(self) -> bool:
        """Whether every hop rests on a written disclosure."""
        return self.traffic_only_hops == 0


def _positive_evidence(edge: Edge) -> list[Evidence]:
    return [e for e in edge.evidence if not e.negative]


def flow_hops(graph: SharingGraph) -> dict[str, list[ChainHop]]:
    """Adjacency of party-to-party data flow."""
    owners: dict[str, str] = {}
    for edge in graph.edges.values():
        if edge.kind is EdgeKind.OWNED_BY:
            owners[edge.src] = edge.dst

    out: dict[str, list[ChainHop]] = {}
    for edge in graph.edges.values():
        positive = _positive_evidence(edge)
        if not positive:
            continue
        if edge.kind in (EdgeKind.DISCLOSES_SHARING_WITH, EdgeKind.SUPPLIES):
            src, dst, via = edge.src, edge.dst, ""
        elif edge.kind is EdgeKind.CONTACTS:
            dst = owners.get(edge.dst, "")
            if not dst:
                continue
            src, via = edge.src, edge.dst
        else:
            continue
        if src == dst:
            continue
        out.setdefault(src, []).append(ChainHop(
            src=src, dst=dst, kind=edge.kind.value,
            sources=sorted({e.source.value for e in positive}),
            data_types=sorted({e.data_type for e in positive if e.data_type}),
            via_domain=via,
        ))
    return out


def sharing_chains(
    graph: SharingGraph, parties: int = 4, limit: int = 1000,
) -> list[SharingChain]:
    """Chains along which data passes through ``parties`` distinct organisations."""
    adjacency = flow_hops(graph)
    named = {
        nid for nid, n in graph.nodes.items()
        if n.type in (NodeType.TARGET, NodeType.ENTITY)
    }
    found: list[SharingChain] = []
    starts = sorted(nid for nid in named if adjacency.get(nid))
    per_start = max(1, limit // len(starts)) if starts else limit

    def walk(path: list[str], hops: list[ChainHop], budget: list[int]) -> None:
        if budget[0] <= 0 or len(found) >= limit:
            return
        if len(path) == parties:
            found.append(SharingChain(parties=list(path), hops=list(hops)))
            budget[0] -= 1
            return
        for hop in adjacency.get(path[-1], ()):
            if hop.dst in path or hop.dst not in named:
                continue
            walk(path + [hop.dst], hops + [hop], budget)

    for start in starts:
        if len(found) >= limit:
            break
        walk([start], [], [per_start])
    return found


# --------------------------------------------------------------------------- #
# Construction from analysis output
# --------------------------------------------------------------------------- #
def _evidence_source(relation: dict) -> EvidenceSource:
    sources = set(relation.get("sources") or ())
    if "traffic" in sources:
        return EvidenceSource.TRAFFIC
    if sources - {"policy"}:
        return EvidenceSource.REGISTRY
    return EvidenceSource.POLICY


def add_target(
    graph: SharingGraph,
    target_id: str,
    display_name: str,
    relations,
    target_type: str = "website",
    observed=None,
    hop: int = 0,
    expanded: bool = True,
) -> str:
    """Fold one target's relations and observations into ``graph``."""
    tid = target_node_id(target_id)
    graph.add_node(Node(
        id=tid, type=NodeType.TARGET, display_name=display_name,
        target_type=target_type, hop_first_seen=hop, expanded=expanded,
    ))
    attach(graph, tid, relations, observed=observed, hop=hop)
    return tid


def expand_node(
    graph: SharingGraph,
    node_id: str,
    relations,
    observed=None,
    hop: int = 1,
    primary_domain: str = "",
    expanded: bool = True,
) -> str:
    """Fold the arrangements an already-present party discloses into ``graph``."""
    node = graph.nodes.get(node_id)
    if node is not None:
        node.expanded = node.expanded or expanded
        node.primary_domain = node.primary_domain or primary_domain
    attach(graph, node_id, relations, observed=observed, hop=hop)
    return node_id


def attach(
    graph: SharingGraph,
    src_id: str,
    relations,
    observed=None,
    hop: int = 0,
) -> None:
    """Add the edges one party's relations and observations support."""
    tid = src_id
    for rel in relations or ():
        if rel.get("party") == "first":
            continue
        name = rel.get("entity") or ""
        if not name:
            continue
        if rel.get("unspecified"):
            nid = generic_node_id(name)
            graph.add_node(Node(id=nid, type=NodeType.GENERIC, display_name=name,
                                hop_first_seen=hop + 1))
        else:
            node = entity_node(name, hop=hop + 1)
            nid = node.id
            if nid == tid:
                continue
            graph.add_node(node)
        ev = Evidence(
            source=_evidence_source(rel), hop=hop,
            doc_ids=list(rel.get("doc_ids") or ()),
            snippet=rel.get("text") or "",
            data_type=rel.get("data_type") or "",
            purposes=list(rel.get("purposes") or ()),
            negative=bool(rel.get("negative")),
        )
        if rel.get("direction") == "upstream":
            graph.add_edge(EdgeKind.SUPPLIES, nid, tid, ev)
        else:
            graph.add_edge(EdgeKind.DISCLOSES_SHARING_WITH, tid, nid, ev)

    for obs in observed or ():
        entity = obs.get("entity") or ""
        if not entity:
            continue
        owner = entity_node(entity, hop=hop + 1)
        eid = owner.id
        if eid == tid:
            continue
        graph.add_node(owner)
        for domain in obs.get("domains") or ():
            did = domain_node_id(domain)
            graph.add_node(Node(
                id=did, type=NodeType.DOMAIN, display_name=domain,
                owner_entity_id=eid, resolution_basis=obs.get("basis", ""),
                hop_first_seen=hop + 1,
            ))
            graph.add_edge(EdgeKind.CONTACTS, tid, did,
                           Evidence(source=EvidenceSource.TRAFFIC, hop=hop))
            graph.add_edge(EdgeKind.OWNED_BY, did, eid,
                           Evidence(source=EvidenceSource.RESOLUTION, hop=hop,
                                    snippet=obs.get("basis", "")))
