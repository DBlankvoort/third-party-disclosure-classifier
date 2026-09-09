from __future__ import annotations

from .sharing_graph import (
    EdgeKind,
    EvidenceType,
    NodeType,
    SharingGraph,
)

# Corroboration rule for each part of the crawl.
TRAFFIC_AND_TRACKER = "traffic_and_tracker"
ADS_TXT_AND_SELLERS_JSON = "ads_txt_and_sellers_json"


def rule_for_hop(hop: int) -> str:
    """The pair of records a party first seen at ``hop`` must satisfy."""
    return TRAFFIC_AND_TRACKER if hop <= 1 else ADS_TXT_AND_SELLERS_JSON


def _positive(edge) -> list:
    return [e for e in edge.evidence if not e.negative]


def disclosed_in_prose(graph: SharingGraph, node_id: str) -> bool:
    """Return whether another party's prose names this node."""
    return any(
        edge.kind is EdgeKind.DISCLOSES_RELATION_WITH and _positive(edge)
        for edge in graph.in_edges(node_id)
    )


def confirmed_by_sellers_json(graph: SharingGraph, node_id: str) -> bool:
    """Return whether ads.txt and sellers.json confirm an allowed relationship."""
    return any(
        any(e.evidence_type is EvidenceType.ADS_TXT_AUTHORISATION
            for e in edge.evidence)
        and any(e.evidence_type is EvidenceType.SELLERS_JSON_CONFIRMATION
                and e.match_basis == "seller_id"
                and e.relationship_valid is True
                for e in edge.evidence)
        for edge in graph.in_edges(node_id)
    )


def _domains_of(graph: SharingGraph, node_id: str) -> list[str]:
    """Return the domain nodes that resolve to this party."""
    node = graph.nodes.get(node_id)
    if node is not None and node.type is NodeType.DOMAIN:
        return [node_id]
    return [
        edge.src for edge in graph.in_edges(node_id)
        if edge.kind is EdgeKind.RESOLVES_TO
    ]


def observed_as_tracker(graph: SharingGraph, node_id: str) -> bool:
    """Return whether the browser contacted a recognized tracker domain."""
    return any(
        e.evidence_type is EvidenceType.TRACKER_LIST_CONFIRMATION
        for did in _domains_of(graph, node_id)
        for edge in graph.in_edges(did) for e in edge.evidence
    )


def corroborated(graph: SharingGraph, node_id: str, hop: int) -> bool:
    """Whether two records agree on a party first seen at ``hop``."""
    if rule_for_hop(hop) == TRAFFIC_AND_TRACKER:
        return observed_as_tracker(graph, node_id)
    return confirmed_by_sellers_json(graph, node_id)


def prune_to_corroborated(graph: SharingGraph, hop: int) -> list[dict]:
    """Drop the parties first seen at ``hop`` that only one record supports.

    First-hop tracker domains can survive without an attributed organization.
    """
    dropped: list[dict] = []
    doomed: list[str] = []
    for node_id, node in graph.nodes.items():
        if node.type is NodeType.TARGET or node.hop_first_seen != hop:
            continue
        if node.type is NodeType.DOMAIN:
            if hop <= 1 and observed_as_tracker(graph, node_id):
                continue
            if node.owner_entity_id and node.owner_entity_id in graph.nodes:
                continue
        if corroborated(graph, node_id, hop):
            continue
        doomed.append(node_id)
        dropped.append({
            "node": node_id, "name": node.display_name, "hop": hop,
            "rule": rule_for_hop(hop),
            "prose": disclosed_in_prose(graph, node_id),
            "tracker": observed_as_tracker(graph, node_id),
            "sellers_json": confirmed_by_sellers_json(graph, node_id),
        })
    for node_id in doomed:
        graph.remove_node(node_id)

    # Remove domains tied to an organization rejected by the same gate.
    orphans = [
        node_id for node_id, node in graph.nodes.items()
        if node.type is NodeType.DOMAIN and node.hop_first_seen == hop
        and node.owner_entity_id and node.owner_entity_id not in graph.nodes
    ]
    for node_id in orphans:
        graph.remove_node(node_id)
        dropped.append({"node": node_id, "name": node_id, "hop": hop,
                        "rule": rule_for_hop(hop), "prose": False,
                        "tracker": False, "sellers_json": False})
    return dropped


def pruning_summary(dropped: list[dict]) -> dict:
    """What the rule removed, and how far each party got towards surviving."""
    counts: dict[str, int] = {}
    for row in dropped or ():
        counts[row["rule"]] = counts.get(row["rule"], 0) + 1
    return {
        "dropped": len(dropped or ()),
        "by_rule": counts,
        # Parties that met at least one part of the rule.
        "half_met": sum(1 for row in dropped or ()
                        if row["tracker"] or row["sellers_json"]),
    }
