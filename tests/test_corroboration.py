from __future__ import annotations

import pytest

from tpd.corroboration import (
    ADS_TXT_AND_SELLERS_JSON,
    TRAFFIC_AND_TRACKER,
    confirmed_by_sellers_json,
    corroborated,
    disclosed_in_prose,
    observed_as_tracker,
    prune_to_corroborated,
    pruning_summary,
    rule_for_hop,
)
from tpd.sharing_graph import (
    EdgeKind,
    Evidence,
    EvidenceSource,
    EvidenceType,
    Node,
    NodeType,
    SharingGraph,
)

SEED = "target::site"


def _graph() -> SharingGraph:
    g = SharingGraph()
    g.add_node(Node(id=SEED, type=NodeType.TARGET, display_name="site.example",
                    hop_first_seen=0))
    return g


def _party(g: SharingGraph, node_id: str, hop: int = 1) -> str:
    g.add_node(Node(id=node_id, type=NodeType.ENTITY, display_name=node_id,
                    hop_first_seen=hop))
    return node_id


def _prose(g: SharingGraph, dst: str, negative: bool = False) -> None:
    g.add_edge(EdgeKind.DISCLOSES_RELATION_WITH, SEED, dst,
               Evidence(source=EvidenceSource.POLICY,
                        evidence_type=EvidenceType.POLICY_RELATION,
                        negative=negative))


def _tracker(g: SharingGraph, dst: str, domain: str, hop: int = 1,
             confirmed: bool = True) -> None:
    did = f"domain::{domain}"
    g.add_node(Node(id=did, type=NodeType.DOMAIN, display_name=domain,
                    owner_entity_id=dst, hop_first_seen=hop))
    evidence = [Evidence(source=EvidenceSource.TRAFFIC,
                         evidence_type=EvidenceType.NETWORK_CONTACT)]
    if confirmed:
        evidence.append(Evidence(
            source=EvidenceSource.REGISTRY,
            evidence_type=EvidenceType.TRACKER_LIST_CONFIRMATION))
    for ev in evidence:
        g.add_edge(EdgeKind.CONTACTS_DOMAIN, SEED, did, ev)
    g.add_edge(EdgeKind.RESOLVES_TO, did, dst,
               Evidence(source=EvidenceSource.RESOLUTION,
                        evidence_type=EvidenceType.DOMAIN_RESOLUTION))


def _sellers(g: SharingGraph, src: str, dst: str) -> None:
    g.add_edge(EdgeKind.AUTHORISES_INVENTORY_SALE, src, dst,
               Evidence(source=EvidenceSource.REGISTRY,
                        evidence_type=EvidenceType.ADS_TXT_AUTHORISATION,
                        qualifier="direct", publisher_ids=["pub-1"]))
    g.add_edge(EdgeKind.AUTHORISES_INVENTORY_SALE, src, dst,
               Evidence(source=EvidenceSource.REGISTRY,
                        evidence_type=EvidenceType.SELLERS_JSON_CONFIRMATION,
                        match_basis="seller_id", relationship_valid=True))


class TestTheRule:
    def test_the_first_hop_asks_for_traffic_and_a_tracker_match(self):
        assert rule_for_hop(1) == TRAFFIC_AND_TRACKER

    def test_beyond_the_first_hop_there_is_no_traffic_to_ask_for(self):
        assert rule_for_hop(2) == ADS_TXT_AND_SELLERS_JSON
        assert rule_for_hop(5) == ADS_TXT_AND_SELLERS_JSON


class TestOneRecordAtATime:
    def test_prose_is_read_from_a_disclosure_edge(self):
        g = _graph()
        _party(g, "entity::a")
        _prose(g, "entity::a")
        assert disclosed_in_prose(g, "entity::a")

    def test_a_denial_is_not_a_disclosure(self):
        g = _graph()
        _party(g, "entity::a")
        _prose(g, "entity::a", negative=True)
        assert not disclosed_in_prose(g, "entity::a")

    def test_a_party_is_a_tracker_through_the_domain_it_owns(self):
        g = _graph()
        _party(g, "entity::a")
        _tracker(g, "entity::a", "a.example")
        assert observed_as_tracker(g, "entity::a")

    def test_a_contact_no_list_recognises_is_not_one(self):
        g = _graph()
        _party(g, "entity::a")
        _tracker(g, "entity::a", "a.example", confirmed=False)
        assert not observed_as_tracker(g, "entity::a")

    def test_a_sellers_json_confirmation_counts_for_the_party_it_names(self):
        g = _graph()
        _party(g, "entity::a", hop=2)
        _sellers(g, SEED, "entity::a")
        assert confirmed_by_sellers_json(g, "entity::a")


class TestFirstHop:
    def test_tracker_confirmed_traffic_survives_without_prose(self):
        g = _graph()
        _party(g, "entity::a")
        _tracker(g, "entity::a", "a.example")
        assert corroborated(g, "entity::a", 1)
        assert prune_to_corroborated(g, 1) == []
        assert "entity::a" in g.nodes

    @pytest.mark.parametrize("with_prose", [True, False])
    def test_missing_tracker_evidence_fails_regardless_of_prose(self, with_prose):
        g = _graph()
        _party(g, "entity::a")
        if with_prose:
            _prose(g, "entity::a")
        dropped = prune_to_corroborated(g, 1)
        assert [row["node"] for row in dropped][:1] == ["entity::a"]
        assert "entity::a" not in g.nodes

    def test_the_edges_that_reached_a_dropped_party_go_with_it(self):
        g = _graph()
        _party(g, "entity::a")
        _prose(g, "entity::a")
        prune_to_corroborated(g, 1)
        assert not g.edges

    def test_a_domain_is_kept_for_the_party_it_names(self):
        g = _graph()
        _party(g, "entity::a")
        _prose(g, "entity::a")
        _tracker(g, "entity::a", "a.example")
        prune_to_corroborated(g, 1)
        assert "domain::a.example" in g.nodes

    def test_a_confirmed_tracker_domain_survives_without_an_owner(self):
        g = _graph()
        _party(g, "entity::a")
        _tracker(g, "entity::a", "a.example")   # no disclosure
        prune_to_corroborated(g, 1)
        assert "domain::a.example" in g.nodes

    def test_the_seed_is_never_judged(self):
        g = _graph()
        prune_to_corroborated(g, 0)
        assert SEED in g.nodes


class TestBeyondTheFirstHop:
    def test_ads_txt_and_sellers_json_together_survive_without_prose(self):
        g = _graph()
        a = _party(g, "entity::a", hop=2)
        _sellers(g, SEED, a)
        assert corroborated(g, a, 2)
        assert prune_to_corroborated(g, 2) == []

    def test_traffic_no_longer_counts_at_this_distance(self):
        g = _graph()
        a = _party(g, "entity::a", hop=2)
        _prose(g, a)
        _tracker(g, a, "a.example", hop=2)
        assert not corroborated(g, a, 2)

    def test_a_valid_registry_pair_is_enough(self):
        g = _graph()
        a = _party(g, "entity::a", hop=2)
        _sellers(g, SEED, a)
        prune_to_corroborated(g, 2)
        assert a in g.nodes

    def test_only_the_ring_named_is_judged(self):
        g = _graph()
        near = _party(g, "entity::near", hop=1)
        _prose(g, near)
        _tracker(g, near, "near.example")
        far = _party(g, "entity::far", hop=2)
        prune_to_corroborated(g, 2)
        assert near in g.nodes and far not in g.nodes


class TestSummary:
    def test_it_says_how_many_got_halfway(self):
        g = _graph()
        _party(g, "entity::half")
        _prose(g, "entity::half")
        _party(g, "entity::none")
        summary = pruning_summary(prune_to_corroborated(g, 1))
        assert summary["dropped"] == 2
        assert summary["half_met"] == 0
        assert summary["by_rule"] == {TRAFFIC_AND_TRACKER: 2}
