"""Validate the separation of inventory from personal-data arrangements."""

from __future__ import annotations

from tpd.classify.structured_relations import registry_relations, table_relations
from tpd.sharing_graph import (
    EdgeKind,
    SharingGraph,
    add_target,
    chains_by_track,
    entity_node_id,
    flow_hops,
    sharing_chains,
)
from tpd.tracks import (
    INVENTORY,
    NOT_APPLICABLE,
    PERSONAL_DATA,
    SERVICE_DATA,
    SITE_VISITOR,
    UNKNOWN,
    track_for_source,
    track_for_sources,
)


def _rel(entity, **kw):
    base = {
        "entity": entity, "party": "third", "unspecified": False,
        "data_type": "personal data", "action": "be_shared", "negative": False,
        "direction": "downstream", "track": PERSONAL_DATA,
        "subject": SERVICE_DATA, "purposes": [], "examples": [],
        "qualifier": "", "sources": ["policy"], "text": "", "doc_ids": [],
    }
    base.update(kw)
    return base


class TestTrackAssignment:
    def test_registries_of_inventory_are_the_inventory_track(self):
        for source in ("ads_txt", "app_ads_txt", "sellers_json"):
            assert track_for_source(source) == INVENTORY

    def test_every_other_source_is_the_personal_data_track(self):
        for source in ("policy", "cookie_table", "vendor_table", "cmp",
                       "traffic", "tcf_gvl"):
            assert track_for_source(source) == PERSONAL_DATA

    def test_a_relation_corroborated_by_both_states_the_stronger_claim(self):
        assert track_for_sources(["ads_txt", "policy"]) == PERSONAL_DATA
        assert track_for_sources(["ads_txt", "sellers_json"]) == INVENTORY

    def test_an_ads_txt_row_concerns_no_person(self):
        rows = registry_relations("example.com, 1234, DIRECT\n")
        assert rows and rows[0]["track"] == INVENTORY
        assert rows[0]["subject"] == NOT_APPLICABLE

    def test_a_sellers_json_entry_concerns_no_person(self):
        raw = '{"sellers": [{"seller_id": "7", "name": "A Publisher"}]}'
        rows = registry_relations(raw)
        assert rows and rows[0]["track"] == INVENTORY
        assert rows[0]["subject"] == NOT_APPLICABLE

    def test_a_cookie_table_describes_the_publishers_own_visitors(self):
        html = (
            "<table><tr><th>Cookie</th><th>Provider</th><th>Duration</th></tr>"
            "<tr><td>_ga</td><td>Google</td><td>2 years</td></tr></table>"
        )
        rows = table_relations(html, role="cookie_policy")
        assert rows and rows[0]["track"] == PERSONAL_DATA
        assert rows[0]["subject"] == SITE_VISITOR

    def test_a_subprocessor_list_describes_data_received_from_customers(self):
        html = (
            "<table><tr><th>Sub-processor</th><th>Purpose</th></tr>"
            "<tr><td>Amazon Web Services</td><td>hosting</td></tr></table>"
        )
        rows = table_relations(html, role="subprocessor_list")
        assert rows and rows[0]["subject"] == SERVICE_DATA


class TestGraphKeepsTracksApart:
    def _graph(self):
        g = SharingGraph()
        add_target(g, "website__pub", "pub.example", [
            _rel("Exchange A", sources=["ads_txt"], track=INVENTORY,
                 subject=NOT_APPLICABLE, data_type="advertising bid data"),
            _rel("Exchange A"),
        ])
        return g

    def test_edge_counts_are_reported_per_track(self):
        counts = self._graph().edge_counts()
        assert counts[INVENTORY] == 1
        assert counts[PERSONAL_DATA] == 1

    def test_one_edge_may_hold_both_tracks_of_evidence(self):
        g = self._graph()
        edge = g.edges[(EdgeKind.DISCLOSES_SHARING_WITH.value,
                        "target::website__pub", entity_node_id("Exchange A"))]
        assert edge.tracks == {INVENTORY, PERSONAL_DATA}

    def test_a_subgraph_keeps_only_one_tracks_evidence(self):
        sub = self._graph().subgraph(INVENTORY)
        assert len(sub.edges) == 1
        assert all(e.track == INVENTORY
                   for edge in sub.edges.values() for e in edge.evidence)

    def test_adjacency_within_a_track_excludes_the_other(self):
        g = self._graph()
        hops = flow_hops(g, track=INVENTORY)["target::website__pub"]
        assert [h.track for h in hops] == [INVENTORY]


class TestChainsStayWithinOneTrack:
    def _chain_graph(self, second_subject=SERVICE_DATA):
        """A publisher, an exchange, and the exchange's own recipients."""
        g = SharingGraph()
        add_target(g, "website__pub", "pub.example",
                   [_rel("Exchange", subject=SITE_VISITOR)])
        add_target(g, "website__exchange", "exchange.example", [])
        # The exchange discloses onward.
        from tpd.sharing_graph import attach

        attach(g, entity_node_id("Exchange"),
               [_rel("Broker", subject=second_subject)], hop=1)
        attach(g, entity_node_id("Broker"),
               [_rel("Analytics Co", subject=second_subject)], hop=2)
        return g

    def test_a_chain_forms_when_each_onward_hop_carries_received_data(self):
        chains = sharing_chains(self._chain_graph(), parties=4)
        assert any(len(c.parties) == 4 for c in chains)
        assert all(c.track == PERSONAL_DATA for c in chains)

    def test_a_hop_about_a_partys_own_visitors_does_not_continue_a_chain(self):
        chains = sharing_chains(self._chain_graph(second_subject=SITE_VISITOR),
                                parties=4)
        assert chains == []

    def test_an_unstated_subject_is_refused_under_the_strict_reading(self):
        graph = self._chain_graph(second_subject=UNKNOWN)
        assert sharing_chains(graph, parties=4, subject_strict=False)
        assert sharing_chains(graph, parties=4, subject_strict=True) == []

    def test_an_inventory_edge_cannot_join_two_personal_data_edges(self):
        g = SharingGraph()
        add_target(g, "website__pub", "pub.example",
                   [_rel("Exchange", subject=SERVICE_DATA)])
        from tpd.sharing_graph import attach

        attach(g, entity_node_id("Exchange"),
               [_rel("Reseller", sources=["ads_txt"], track=INVENTORY,
                     subject=NOT_APPLICABLE)], hop=1)
        attach(g, entity_node_id("Reseller"),
               [_rel("Broker", subject=SERVICE_DATA)], hop=2)
        assert sharing_chains(g, parties=4, track=PERSONAL_DATA) == []
        assert sharing_chains(g, parties=4, track=INVENTORY) == []

    def test_both_tracks_are_enumerated_separately(self):
        counts = chains_by_track(self._chain_graph(), parties=4)
        assert set(counts) == {PERSONAL_DATA, INVENTORY}
        assert counts[INVENTORY] == []
