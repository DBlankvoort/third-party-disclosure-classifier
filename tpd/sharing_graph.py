"""A canonical graph of data-sharing arrangements across targets."""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field, fields
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path

from .entities import (
    country_for,
    is_investor_parent,
    is_shared_platform,
    resolve_name,
    service_purpose,
    tcf_vendor,
)
from .tracks import (
    PERSONAL_DATA,
    TRACKS,
    UNKNOWN,
    carries_upstream_data,
    track_for_sources,
)


class NodeType(str, Enum):
    TARGET = "target"        # an analysed app or website
    ENTITY = "entity"        # a named organisation
    DOMAIN = "domain"        # a host contacted by a target
    GENERIC = "generic"      # an unnamed category ("advertising partners")


class EdgeKind(str, Enum):
    DISCLOSES_RELATION_WITH = "discloses_relation_with"
    LISTS_VENDOR = "lists_vendor"
    AUTHORISES_INVENTORY_SALE = "authorises_inventory_sale"
    CONTACTS_DOMAIN = "contacts_domain"
    RESOLVES_TO = "resolves_to"
    DECLARES_SUPPLY_CHAIN = "declares_supply_chain"

    # Source compatibility for callers; persisted output uses the values above.
    DISCLOSES_SHARING_WITH = DISCLOSES_RELATION_WITH
    SUPPLIES = DISCLOSES_RELATION_WITH
    CONTACTS = CONTACTS_DOMAIN
    OWNED_BY = RESOLVES_TO


class EvidenceSource(str, Enum):
    POLICY = "policy"
    REGISTRY = "registry"
    TRAFFIC = "traffic"
    RESOLUTION = "resolution"


class EvidenceType(str, Enum):
    POLICY_RELATION = "policy_relation"
    STRUCTURED_TABLE_RELATION = "structured_table_relation"
    ADS_TXT_AUTHORISATION = "ads_txt_authorisation"
    SELLERS_JSON_PARTICIPATION = "sellers_json_participation"
    SELLERS_JSON_CONFIRMATION = "sellers_json_confirmation"
    TRACKER_LIST_CONFIRMATION = "tracker_list_confirmation"
    TCF_VENDOR_REGISTRATION = "tcf_vendor_registration"
    CMP_VENDOR_LISTING = "cmp_vendor_listing"
    NETWORK_CONTACT = "network_contact"
    OBSERVED_TRANSMISSION = "observed_transmission"
    NAME_RESOLUTION = "name_resolution"
    DOMAIN_RESOLUTION = "domain_resolution"
    MANUAL_CORRECTION = "manual_correction"
    OPENRTB_SUPPLY_CHAIN = "openrtb_supply_chain"


GENERIC_PREFIX = "generic::"
TARGET_PREFIX = "target::"
ENTITY_PREFIX = "entity::"
DOMAIN_PREFIX = "domain::"


def generic_node_id(category: str) -> str:
    return f"{GENERIC_PREFIX}{category.strip().lower()}"


def entity_node_id(name: str) -> str:
    return f"{ENTITY_PREFIX}{resolve_name(name).key or name.strip().lower()}"


def entity_node(name: str, hop: int = 0, grounded: bool | None = None,
                confidence: float = 0.0) -> Node:
    """The node one organisation name stands for."""
    resolved = resolve_name(name)
    surface = name.strip()
    node = Node(
        id=f"{ENTITY_PREFIX}{resolved.key or surface.lower()}",
        type=NodeType.ENTITY,
        display_name=resolved.display or surface,
        resolution_basis=resolved.basis,
        hop_first_seen=hop,
        country=resolved.country or country_for(resolved.display),
        grounded=resolved.grounded if grounded is None else bool(grounded),
        confidence=confidence,
    )
    registered = tcf_vendor(resolved.display)
    if registered is not None:
        node.tcf_vendor_id = registered.id
    if surface and surface.lower() != node.display_name.lower():
        node.aliases = [surface]
    # A name written as a domain states where the party publishes.
    if (resolved.domain and resolved.basis != "domain_map"
            and not is_shared_platform(resolved.domain)):
        node.primary_domain = resolved.domain
    return node


def domain_node_id(domain: str) -> str:
    return f"{DOMAIN_PREFIX}{domain.strip().lower()}"


def target_node_id(target_id: str) -> str:
    return f"{TARGET_PREFIX}{target_id}"


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
    # ISO-3166 alpha-2 headquarters country, where a reference table records one.
    country: str = ""
    # Whether a record outside the document naming the party recognises it.
    grounded: bool = True
    # The strongest reading behind the name, where one was measured.
    confidence: float = 0.0
    # The party's registration in the IAB TCF Global Vendor List.
    tcf_vendor_id: int = 0
    # Corrections applied by hand through the graph editor.
    edited: bool = False

    def to_dict(self) -> dict:
        d = asdict(self)
        d["type"] = self.type.value
        return d

    @classmethod
    def from_dict(cls, d: dict) -> Node:
        known = {f.name for f in fields(cls)}
        d = {k: v for k, v in d.items() if k in known}
        d["type"] = NodeType(d["type"])
        return cls(**d)


@dataclass
class Evidence:
    source: EvidenceSource
    evidence_type: EvidenceType | None = None
    observed_at: str = field(default_factory=_now)
    hop: int = 0
    doc_ids: list[str] = field(default_factory=list)
    snippet: str = ""
    data_type: str = ""
    purposes: list[str] = field(default_factory=list)
    negative: bool = False
    # Whether the arrangement concerns advertising inventory or personal data.
    track: str = PERSONAL_DATA
    # Whose data the arrangement concerns.
    subject: str = UNKNOWN
    # How strongly the reading that produced the relation was supported.
    confidence: float = 0.0
    # For observed traffic, the consent state the contact was made under.
    consent: str = ""
    # Relationship and account identifiers retained from registry records.
    qualifier: str = ""
    publisher_ids: list[str] = field(default_factory=list)
    authorizations: list[dict] = field(default_factory=list)
    # How a corroborating record matched and whether its role is consistent.
    match_basis: str = ""
    relationship_valid: bool | None = None
    transaction_id: str = ""
    chain_complete: bool | None = None

    def to_dict(self) -> dict:
        d = asdict(self)
        d["source"] = self.source.value
        if self.evidence_type is not None:
            d["evidence_type"] = self.evidence_type.value
        return d

    @classmethod
    def from_dict(cls, d: dict) -> Evidence:
        known = {f.name for f in fields(cls)}
        d = {k: v for k, v in d.items() if k in known}
        d["source"] = EvidenceSource(d["source"])
        if d.get("evidence_type"):
            d["evidence_type"] = EvidenceType(d["evidence_type"])
        return cls(**d)


_LEGACY_EDGE_KINDS = {
    "discloses_sharing_with", "supplies", "contacts", "owned_by",
}


def _kind_for_evidence(evidence_type: EvidenceType | None) -> EdgeKind:
    if evidence_type in {
        EvidenceType.ADS_TXT_AUTHORISATION,
        EvidenceType.SELLERS_JSON_CONFIRMATION,
    }:
        return EdgeKind.AUTHORISES_INVENTORY_SALE
    if evidence_type in {
        EvidenceType.SELLERS_JSON_PARTICIPATION,
        EvidenceType.TCF_VENDOR_REGISTRATION,
        EvidenceType.CMP_VENDOR_LISTING,
    }:
        return EdgeKind.LISTS_VENDOR
    if evidence_type in {
        EvidenceType.NETWORK_CONTACT, EvidenceType.OBSERVED_TRANSMISSION,
        EvidenceType.TRACKER_LIST_CONFIRMATION,
    }:
        return EdgeKind.CONTACTS_DOMAIN
    if evidence_type in {
        EvidenceType.NAME_RESOLUTION, EvidenceType.DOMAIN_RESOLUTION,
    }:
        return EdgeKind.RESOLVES_TO
    return EdgeKind.DISCLOSES_RELATION_WITH


def _loaded_edge_kind(value: str, evidence: list[Evidence]) -> EdgeKind:
    """Interpret an old persisted kind using its retained provenance."""
    if value not in _LEGACY_EDGE_KINDS:
        return EdgeKind(value)
    if value == "contacts":
        return EdgeKind.CONTACTS_DOMAIN
    if value == "owned_by":
        return EdgeKind.RESOLVES_TO
    types = {e.evidence_type for e in evidence if e.evidence_type is not None}
    if len(types) == 1:
        return _kind_for_evidence(types.pop())
    return EdgeKind.DISCLOSES_RELATION_WITH


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

    @property
    def tracks(self) -> set[str]:
        """The tracks this edge's evidence speaks to."""
        return {e.track for e in self.evidence}

    def evidence_on(self, track: str | None = None, positive: bool = True) -> list[Evidence]:
        """This edge's evidence, restricted to one track."""
        return [
            e for e in self.evidence
            if (track is None or e.track == track) and (not positive or not e.negative)
        ]

    def to_dict(self) -> dict:
        return {
            "kind": self.kind.value, "src": self.src, "dst": self.dst,
            "evidence": [e.to_dict() for e in self.evidence],
        }

    @classmethod
    def from_dict(cls, d: dict) -> Edge:
        evidence = [Evidence.from_dict(e) for e in d.get("evidence", [])]
        return cls(
            kind=_loaded_edge_kind(d["kind"], evidence),
            src=d["src"], dst=d["dst"], evidence=evidence,
        )


class SharingGraph:
    """Nodes and evidence-bearing edges, addressable by hop distance."""

    def __init__(self) -> None:
        self.nodes: dict[str, Node] = {}
        self.edges: dict[tuple[str, str, str], Edge] = {}
        self._out: dict[str, list[Edge]] = {}
        self._in: dict[str, list[Edge]] = {}

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
        existing.country = existing.country or node.country
        existing.grounded = existing.grounded or node.grounded
        existing.confidence = max(existing.confidence, node.confidence)
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
            self._index(edge)
        if evidence is not None:
            edge.evidence.append(evidence)
        return edge

    def _index(self, edge: Edge) -> None:
        self.edges[edge.key] = edge
        self._out.setdefault(edge.src, []).append(edge)
        self._in.setdefault(edge.dst, []).append(edge)

    def _unindex(self, edge: Edge) -> None:
        self.edges.pop(edge.key, None)
        for side, nid in ((self._out, edge.src), (self._in, edge.dst)):
            held = side.get(nid)
            if held is None:
                continue
            side[nid] = [e for e in held if e is not edge]
            if not side[nid]:
                del side[nid]

    def remove_edge(self, kind: str, src: str, dst: str) -> bool:
        edge = self.edges.get((kind, src, dst))
        if edge is None:
            return False
        self._unindex(edge)
        return True

    def remove_node(self, node_id: str) -> bool:
        if node_id not in self.nodes:
            return False
        for edge in list(self.out_edges(node_id)) + list(self.in_edges(node_id)):
            self._unindex(edge)
        del self.nodes[node_id]
        return True

    def rename_node(self, node_id: str, display_name: str) -> bool:
        node = self.nodes.get(node_id)
        if node is None or not display_name.strip():
            return False
        previous = node.display_name
        node.display_name = display_name.strip()
        node.edited = True
        if previous and previous.lower() not in {a.lower() for a in node.aliases}:
            node.aliases.append(previous)
        return True

    def merge_nodes(self, node_id: str, into_id: str) -> bool:
        """Fold one node's edges and surfaces into another."""
        node = self.nodes.get(node_id)
        target = self.nodes.get(into_id)
        if node is None or target is None or node_id == into_id:
            return False
        for edge in list(self.out_edges(node_id)) + list(self.in_edges(node_id)):
            self._unindex(edge)
            src = into_id if edge.src == node_id else edge.src
            dst = into_id if edge.dst == node_id else edge.dst
            if src == dst:
                continue
            held = self.edges.get((edge.kind.value, src, dst))
            if held is None:
                self._index(Edge(kind=edge.kind, src=src, dst=dst,
                                 evidence=list(edge.evidence)))
            else:
                held.evidence.extend(edge.evidence)
        surfaces = [node.display_name, *node.aliases]
        seen = {a.lower() for a in target.aliases} | {target.display_name.lower()}
        for s in surfaces:
            if s and s.lower() not in seen:
                seen.add(s.lower())
                target.aliases.append(s)
        target.primary_domain = target.primary_domain or node.primary_domain
        target.country = target.country or node.country
        target.grounded = target.grounded or node.grounded
        target.edited = True
        del self.nodes[node_id]
        return True

    def set_edge_track(self, kind: str, src: str, dst: str, track: str) -> bool:
        edge = self.edges.get((kind, src, dst))
        if edge is None or track not in TRACKS:
            return False
        for ev in edge.evidence:
            ev.track = track
        return True

    def apply_edit(self, edit: dict) -> bool:
        """Apply one correction, returning whether it changed the graph."""
        op = (edit or {}).get("op")
        if op == "rename":
            return self.rename_node(edit.get("node", ""), edit.get("display_name", ""))
        if op == "merge":
            return self.merge_nodes(edit.get("node", ""), edit.get("into", ""))
        if op == "delete_node":
            return self.remove_node(edit.get("node", ""))
        if op == "delete_edge":
            return self.remove_edge(edit.get("kind", ""), edit.get("src", ""),
                                    edit.get("dst", ""))
        if op == "set_track":
            return self.set_edge_track(edit.get("kind", ""), edit.get("src", ""),
                                       edit.get("dst", ""), edit.get("track", ""))
        return False

    # ------------------------------------------------------------ traversal
    def out_edges(self, node_id: str) -> list[Edge]:
        return self._out.get(node_id, [])

    def in_edges(self, node_id: str) -> list[Edge]:
        return self._in.get(node_id, [])

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

    def party_roles(self) -> dict[str, set[str]]:
        """Named parties split by the side of an arrangement they stand on."""
        recipients: set[str] = set()
        suppliers: set[str] = set()
        for edge in self.edges.values():
            if edge.kind in {
                EdgeKind.DISCLOSES_RELATION_WITH,
                EdgeKind.LISTS_VENDOR,
                EdgeKind.AUTHORISES_INVENTORY_SALE,
            }:
                recipients.add(edge.dst)
                if edge.src.startswith("entity::"):
                    suppliers.add(edge.src)
            elif edge.kind is EdgeKind.RESOLVES_TO:
                recipients.add(edge.dst)
        named = {nid for nid, n in self.nodes.items()
                 if n.type in (NodeType.ENTITY, NodeType.GENERIC)}
        recipients &= named
        return {"recipients": recipients, "suppliers": (suppliers & named) - recipients}

    def termination(self, node_id: str) -> str:
        node = self.nodes.get(node_id)
        if node is None:
            return "unknown"
        if self.out_edges(node_id):
            return "internal"
        return "terminal" if node.expanded else "unexpanded"

    # ------------------------------------------------------------- tracks
    def edge_counts(self) -> dict[str, int]:
        """Edges per track. No single total answers for both."""
        counts = {t: 0 for t in TRACKS}
        for edge in self.edges.values():
            for track in edge.tracks:
                if track in counts:
                    counts[track] += 1
        return counts

    def subgraph(self, track: str, prune: bool = True) -> SharingGraph:
        out = SharingGraph()
        for edge in self.edges.values():
            kept = [e for e in edge.evidence if e.track == track]
            if not kept:
                continue
            out._index(Edge(kind=edge.kind, src=edge.src, dst=edge.dst,
                            evidence=kept))
        for nid, node in self.nodes.items():
            if prune and not (out._out.get(nid) or out._in.get(nid)) \
                    and node.type is not NodeType.TARGET:
                continue
            out.nodes[nid] = Node.from_dict(node.to_dict())
        return out

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
            evidence = [Evidence.from_dict(e) for e in ed.get("evidence", [])]
            if ed.get("kind") in {"discloses_sharing_with", "supplies"}:
                grouped: dict[EdgeKind, list[Evidence]] = {}
                for item in evidence:
                    kind = _kind_for_evidence(item.evidence_type)
                    grouped.setdefault(kind, []).append(item)
                if not grouped:
                    grouped[EdgeKind.DISCLOSES_RELATION_WITH] = []
                for kind, records in grouped.items():
                    held = g.add_edge(kind, ed["src"], ed["dst"])
                    held.evidence.extend(records)
            else:
                edge = Edge.from_dict(ed)
                held = g.add_edge(edge.kind, edge.src, edge.dst)
                held.evidence.extend(edge.evidence)
        return g

    def save(self, path: str | Path, track: str | None = None) -> None:
        graph = self.subgraph(track) if track else self
        p = Path(path)
        p.parent.mkdir(parents=True, exist_ok=True)
        with p.open("w", encoding="utf-8") as f:
            f.write('{\n "nodes": [\n')
            for i, node in enumerate(graph.nodes.values()):
                f.write("  " if i == 0 else ",\n  ")
                json.dump(node.to_dict(), f)
            f.write("\n ],\n \"edges\": [\n")
            for i, edge in enumerate(graph.edges.values()):
                f.write("  " if i == 0 else ",\n  ")
                json.dump(edge.to_dict(), f)
            f.write("\n ]\n}\n")

    def save_by_track(self, path: str | Path) -> dict[str, Path]:
        p = Path(path)
        out: dict[str, Path] = {}
        for track in TRACKS:
            target = p.with_name(f"{p.stem}.{track}{p.suffix or '.json'}")
            self.save(target, track=track)
            out[track] = target
        return out

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
    track: str = PERSONAL_DATA
    subjects: list[str] = field(default_factory=list)

    @property
    def traffic_only(self) -> bool:
        return set(self.sources) == {EvidenceSource.TRAFFIC.value}

    @property
    def subject(self) -> str:
        """The widest population this hop's evidence covers."""
        from .tracks import SERVICE_DATA, SITE_VISITOR

        for candidate in (SERVICE_DATA, SITE_VISITOR):
            if candidate in self.subjects:
                return candidate
        return self.subjects[0] if self.subjects else UNKNOWN


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
    def track(self) -> str:
        """The single track every hop of the chain belongs to."""
        tracks = {h.track for h in self.hops}
        return tracks.pop() if len(tracks) == 1 else ""

    @property
    def traffic_only_hops(self) -> int:
        return sum(1 for h in self.hops if h.traffic_only)

    @property
    def unstated_subject_hops(self) -> int:
        """Continuation hops whose document did not state whose data it covers."""
        return sum(1 for h in self.hops[1:] if h.subject in ("", UNKNOWN))

    @property
    def fully_disclosed(self) -> bool:
        """Whether every hop rests on a written disclosure."""
        return self.traffic_only_hops == 0

    @property
    def subject_stated(self) -> bool:
        """Whether every continuation hop states the population it covers."""
        return self.unstated_subject_hops == 0


def _positive_evidence(edge: Edge) -> list[Evidence]:
    return [e for e in edge.evidence if not e.negative]


def flow_hops(graph: SharingGraph, track: str | None = None) -> dict[str, list[ChainHop]]:
    """Adjacency of party-to-party data flow"""
    owners: dict[str, str] = {}
    for edge in graph.edges.values():
        if edge.kind is EdgeKind.RESOLVES_TO:
            owners[edge.src] = edge.dst

    out: dict[str, list[ChainHop]] = {}
    for edge in graph.edges.values():
        positive = [
            e for e in _positive_evidence(edge)
            if track is None or e.track == track
        ]
        if not positive:
            continue
        if edge.kind in {
            EdgeKind.DISCLOSES_RELATION_WITH,
            EdgeKind.LISTS_VENDOR,
            EdgeKind.AUTHORISES_INVENTORY_SALE,
        }:
            src, dst, via = edge.src, edge.dst, ""
        elif edge.kind is EdgeKind.CONTACTS_DOMAIN:
            dst = owners.get(edge.dst, "")
            if not dst:
                continue
            src, via = edge.src, edge.dst
        else:
            continue
        if src == dst:
            continue
        for hop_track in sorted({e.track for e in positive}):
            evidence = [e for e in positive if e.track == hop_track]
            out.setdefault(src, []).append(ChainHop(
                src=src, dst=dst, kind=edge.kind.value,
                sources=sorted({e.source.value for e in evidence}),
                data_types=sorted({e.data_type for e in evidence if e.data_type}),
                via_domain=via,
                track=hop_track,
                subjects=sorted({e.subject for e in evidence if e.subject}),
            ))
    return out


# Share of the budget the analysed targets keep between them.
TARGET_BUDGET_SHARE = 0.5


def _is_target(node_id: str) -> bool:
    return node_id.startswith(TARGET_PREFIX)


def _start_budgets(starts: list[str], limit: int) -> dict[str, int]:
    """Chains each starting party may contribute."""
    even = max(1, limit // len(starts))
    targets = [nid for nid in starts if _is_target(nid)]
    if not targets:
        return {nid: even for nid in starts}
    reserved = max(even, int(limit * TARGET_BUDGET_SHARE) // len(targets))
    return {nid: (reserved if _is_target(nid) else even) for nid in starts}


def sharing_chains(
    graph: SharingGraph,
    parties: int = 4,
    limit: int = 1000,
    track: str = PERSONAL_DATA,
    subject_strict: bool = False,
) -> list[SharingChain]:
    """Chains along which data passes through ``parties`` distinct organisations."""
    adjacency = flow_hops(graph, track=track)
    named = {
        nid for nid, n in graph.nodes.items()
        if n.type in (NodeType.TARGET, NodeType.ENTITY)
    }
    found: list[SharingChain] = []
    starts = sorted((nid for nid in named if adjacency.get(nid)),
                    key=lambda nid: (not _is_target(nid), nid))
    budgets = _start_budgets(starts, limit) if starts else {}

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
            if hops and not carries_upstream_data(hop.subject, strict=subject_strict):
                continue
            walk(path + [hop.dst], hops + [hop], budget)

    for start in starts:
        if len(found) >= limit:
            break
        walk([start], [], [budgets[start]])
    return found


def chains_by_track(
    graph: SharingGraph, parties: int = 4, limit: int = 1000,
    subject_strict: bool = False,
) -> dict[str, list[SharingChain]]:
    """Chains for each track separately."""
    return {
        track: sharing_chains(graph, parties=parties, limit=limit, track=track,
                              subject_strict=subject_strict)
        for track in TRACKS
    }


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


def _evidence_type(relation: dict) -> EvidenceType:
    sources = set(relation.get("sources") or ())
    if "traffic" in sources:
        return EvidenceType.NETWORK_CONTACT
    if "cmp" in sources:
        return EvidenceType.CMP_VENDOR_LISTING
    if sources & {"ads_txt", "app_ads_txt"}:
        return EvidenceType.ADS_TXT_AUTHORISATION
    if "sellers_json" in sources:
        return EvidenceType.SELLERS_JSON_PARTICIPATION
    if sources & {"tcf_gvl", "vendors_json"}:
        return EvidenceType.TCF_VENDOR_REGISTRATION
    if sources & {"cookie_table", "vendor_table"}:
        return EvidenceType.STRUCTURED_TABLE_RELATION
    return EvidenceType.POLICY_RELATION


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
            if is_investor_parent(name):
                continue
            node = entity_node(
                name, hop=hop + 1,
                grounded=rel.get("grounded"),
                confidence=float(rel.get("confidence") or 0.0),
            )
            nid = node.id
            if nid == tid:
                continue
            graph.add_node(node)
        purposes = list(rel.get("purposes") or ())
        attested = service_purpose(name)
        if attested and attested not in purposes:
            purposes.append(attested)
        ev = Evidence(
            source=_evidence_source(rel), evidence_type=_evidence_type(rel), hop=hop,
            doc_ids=list(rel.get("doc_ids") or ()),
            snippet=rel.get("text") or "",
            data_type=rel.get("data_type") or "",
            purposes=purposes,
            negative=bool(rel.get("negative")),
            track=rel.get("track") or track_for_sources(rel.get("sources")),
            subject=rel.get("subject") or UNKNOWN,
            confidence=float(rel.get("confidence") or 0.0),
            consent=rel.get("consent") or "",
            qualifier=rel.get("qualifier") or "",
            publisher_ids=[str(v) for v in rel.get("publisher_ids") or ()],
            authorizations=[dict(v) for v in rel.get("authorizations") or ()],
        )
        kind = _kind_for_evidence(ev.evidence_type)
        if rel.get("direction") == "upstream":
            graph.add_edge(kind, nid, tid, ev)
        else:
            graph.add_edge(kind, tid, nid, ev)

    for obs in observed or ():
        entity = obs.get("entity") or ""
        domains = obs.get("domains") or ([obs.get("domain")] if obs.get("domain") else [])
        eid = ""
        if entity:
            owner = entity_node(entity, hop=hop + 1)
            eid = owner.id
            if eid != tid:
                graph.add_node(owner)
            else:
                eid = ""
        for domain in domains:
            did = domain_node_id(domain)
            graph.add_node(Node(
                id=did, type=NodeType.DOMAIN, display_name=domain,
                owner_entity_id=eid, resolution_basis=obs.get("basis", ""),
                hop_first_seen=hop + 1,
            ))
            graph.add_edge(EdgeKind.CONTACTS_DOMAIN, tid, did, Evidence(
                source=EvidenceSource.TRAFFIC,
                evidence_type=EvidenceType.NETWORK_CONTACT, hop=hop,
                snippet=(f"{obs.get('requests', 0)} request(s); "
                         f"types={','.join(obs.get('types') or ())}"),
                data_type="IP address and connection metadata",
                track=PERSONAL_DATA, subject="site_visitor",
                consent=obs.get("consent") or "",
            ))
            if eid:
                graph.add_edge(EdgeKind.RESOLVES_TO, did, eid, Evidence(
                    source=EvidenceSource.RESOLUTION,
                    evidence_type=EvidenceType.DOMAIN_RESOLUTION, hop=hop,
                    snippet=obs.get("basis", ""),
                    track="", subject=UNKNOWN,
                ))

    for obs in observed or ():
        source = domain_node_id(obs.get("domain") or "")
        if source not in graph.nodes:
            continue
        for target in obs.get("redirect_targets") or ():
            destination = domain_node_id(target)
            graph.add_node(Node(
                id=destination, type=NodeType.DOMAIN, display_name=target,
                hop_first_seen=hop + 2,
            ))
            graph.add_edge(EdgeKind.CONTACTS_DOMAIN, source, destination, Evidence(
                source=EvidenceSource.TRAFFIC,
                evidence_type=EvidenceType.OBSERVED_TRANSMISSION, hop=hop + 1,
                snippet="observed HTTP redirect",
                data_type="IP address and connection metadata",
                track=PERSONAL_DATA, subject="site_visitor",
                consent=obs.get("consent") or "",
            ))


def attach_schains(graph: SharingGraph, src_id: str, chains, hop: int = 0) -> None:
    """Add ordered participants declared by captured OpenRTB supply chains."""
    for chain in chains or ():
        previous = src_id
        transaction = str(chain.get("request_id") or chain.get("transaction_id") or "")
        complete = chain.get("complete")
        for index, item in enumerate(chain.get("nodes") or ()):
            domain = str(item.get("asi") or item.get("domain") or "").lower()
            sid = str(item.get("sid") or "")
            if not domain or not sid:
                continue
            nid = domain_node_id(domain)
            graph.add_node(Node(
                id=nid, type=NodeType.DOMAIN, display_name=domain,
                hop_first_seen=hop + index + 1,
            ))
            graph.add_edge(EdgeKind.DECLARES_SUPPLY_CHAIN, previous, nid, Evidence(
                source=EvidenceSource.TRAFFIC,
                evidence_type=EvidenceType.OPENRTB_SUPPLY_CHAIN,
                hop=hop + index,
                snippet=(f"{domain} seller_id={sid}; "
                         f"captured via {chain.get('source') or 'unknown'}"),
                track="inventory", subject=UNKNOWN,
                publisher_ids=[sid], transaction_id=transaction,
                chain_complete=bool(complete) if complete is not None else None,
            ))
            previous = nid
