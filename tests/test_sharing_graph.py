"""Validate tpd.sharing_graph."""

from __future__ import annotations

from tpd.sharing_graph import (
    EdgeKind,
    EvidenceSource,
    EvidenceType,
    Node,
    NodeType,
    SharingGraph,
    add_target,
    entity_node_id,
    target_node_id,
)


def _rel(entity, **kw):
    base = {
        "entity": entity, "party": "third", "unspecified": False,
        "data_type": "personal data", "action": "be_shared", "negative": False,
        "direction": "downstream", "purposes": [], "examples": [],
        "qualifier": "", "sources": ["policy"], "text": "", "doc_ids": [],
    }
    base.update(kw)
    return base


class TestAddTarget:
    def test_relations_become_disclosure_edges(self):
        g = SharingGraph()
        tid = add_target(g, "website__example", "example.com", [_rel("criteo")])
        edge = g.edges[(EdgeKind.DISCLOSES_RELATION_WITH.value, tid,
                        entity_node_id("criteo"))]
        assert edge.sources == {"policy"}

    def test_upstream_relations_point_into_the_target(self):
        g = SharingGraph()
        tid = add_target(g, "website__exchange", "exchange.com",
                         [_rel("Publisher A", direction="upstream",
                               sources=["sellers_json"])])
        key = (EdgeKind.LISTS_VENDOR.value, entity_node_id("Publisher A"), tid)
        assert key in g.edges
        assert g.edges[key].sources == {"registry"}

    def test_specific_relation_provenance_survives_graph_construction(self):
        cases = {
            "policy": (EvidenceType.POLICY_RELATION,
                       EdgeKind.DISCLOSES_RELATION_WITH),
            "cookie_table": (EvidenceType.STRUCTURED_TABLE_RELATION,
                             EdgeKind.DISCLOSES_RELATION_WITH),
            "vendor_table": (EvidenceType.STRUCTURED_TABLE_RELATION,
                             EdgeKind.DISCLOSES_RELATION_WITH),
            "ads_txt": (EvidenceType.ADS_TXT_AUTHORISATION,
                        EdgeKind.AUTHORISES_INVENTORY_SALE),
            "sellers_json": (EvidenceType.SELLERS_JSON_PARTICIPATION,
                             EdgeKind.LISTS_VENDOR),
            "tcf_gvl": (EvidenceType.TCF_VENDOR_REGISTRATION,
                        EdgeKind.LISTS_VENDOR),
            "cmp": (EvidenceType.CMP_VENDOR_LISTING, EdgeKind.LISTS_VENDOR),
        }
        for index, (source, (evidence_type, edge_kind)) in enumerate(cases.items()):
            g = SharingGraph()
            add_target(g, str(index), str(index), [_rel("Criteo", sources=[source])])
            edge = next(iter(g.edges.values()))
            assert edge.evidence[0].evidence_type is evidence_type
            assert edge.kind is edge_kind

    def test_first_party_relations_are_skipped(self):
        g = SharingGraph()
        add_target(g, "t", "t", [_rel("example", party="first")])
        assert not g.edges

    def test_corporate_variants_share_one_node(self):
        g = SharingGraph()
        add_target(g, "a", "a", [_rel("Google LLC")])
        add_target(g, "b", "b", [_rel("Google, Inc.")])
        entities = [n for n in g.nodes.values() if n.type is NodeType.ENTITY]
        assert len(entities) == 1

    def test_observations_link_target_through_domain_to_owner(self):
        g = SharingGraph()
        tid = add_target(g, "t", "t", [], observed=[
            {"entity": "Google", "basis": "domain_map",
             "domains": ["doubleclick.net"], "types": ["script"], "requests": 2},
        ])
        assert (EdgeKind.CONTACTS_DOMAIN.value, tid, "domain::doubleclick.net") in g.edges
        assert (EdgeKind.RESOLVES_TO.value, "domain::doubleclick.net",
                entity_node_id("Google")) in g.edges

    def test_network_contacts_do_not_become_personal_data_relations(self):
        g = SharingGraph()
        tid = add_target(g, "t", "t", [], observed=[
            {"entity": "Google", "basis": "domain_map",
             "domains": ["doubleclick.net"], "types": ["script"], "requests": 2},
        ])
        assert not any(
            edge.kind is EdgeKind.DISCLOSES_RELATION_WITH
            for edge in g.out_edges(tid)
        )
        edge = g.edges[(EdgeKind.CONTACTS_DOMAIN.value, tid, "domain::doubleclick.net")]
        assert edge.evidence[0].evidence_type is EvidenceType.NETWORK_CONTACT
        assert edge.evidence[0].data_type == ""
        assert edge.evidence[0].purposes == []
        assert edge.evidence[0].track == ""

    def test_unspecified_parties_become_generic_nodes(self):
        g = SharingGraph()
        add_target(g, "t", "t", [_rel("advertising partners", unspecified=True)])
        assert any(n.type is NodeType.GENERIC for n in g.nodes.values())


class TestTraversal:
    def _chain(self):
        g = SharingGraph()
        add_target(g, "a", "a", [_rel("Bravo")])
        # Bravo, analysed in its own right, shares onward with Charlie.
        g.add_node(Node(id="target::b", type=NodeType.TARGET, expanded=True))
        g.add_edge(EdgeKind.DISCLOSES_RELATION_WITH, entity_node_id("Bravo"),
                   entity_node_id("Charlie"))
        return g

    def test_downstream_reports_hop_distance(self):
        g = self._chain()
        reach = g.downstream(target_node_id("a"), hops=3)
        assert reach[1] == {entity_node_id("Bravo")}
        assert reach[2] == {entity_node_id("Charlie")}

    def test_upstream_inverts_the_walk(self):
        g = self._chain()
        reach = g.upstream(entity_node_id("Charlie"), hops=2)
        assert reach[1] == {entity_node_id("Bravo")}
        assert reach[2] == {target_node_id("a")}

    def test_hop_limit_is_respected(self):
        g = self._chain()
        assert set(g.downstream(target_node_id("a"), hops=1)) == {1}

    def test_paths_enumerate_multi_hop_arrangements(self):
        g = self._chain()
        assert any(len(p) == 2 for p in g.paths(target_node_id("a"), hops=4))


class TestTermination:
    def test_unexpanded_leaf_is_marked_distinctly(self):
        g = SharingGraph()
        add_target(g, "a", "a", [_rel("Bravo")])
        assert g.termination(entity_node_id("Bravo")) == "unexpanded"

    def test_analysed_leaf_is_terminal(self):
        g = SharingGraph()
        add_target(g, "a", "a", [_rel("Bravo")])
        g.nodes[entity_node_id("Bravo")].expanded = True
        assert g.termination(entity_node_id("Bravo")) == "terminal"

    def test_node_with_successors_is_internal(self):
        g = SharingGraph()
        tid = add_target(g, "a", "a", [_rel("Bravo")])
        assert g.termination(tid) == "internal"


class TestSerialisation:
    def test_round_trip_preserves_edges_and_evidence(self):
        g = SharingGraph()
        add_target(g, "a", "a", [_rel("Criteo", text="we share with Criteo")])
        back = SharingGraph.from_dict(g.to_dict())
        assert set(back.edges) == set(g.edges)
        (edge,) = list(back.edges.values())
        assert edge.evidence[0].source is EvidenceSource.POLICY
        assert edge.evidence[0].evidence_type is EvidenceType.POLICY_RELATION
        assert edge.evidence[0].snippet == "we share with Criteo"

    def test_legacy_mixed_edge_is_split_by_evidence_type(self):
        old = {
            "nodes": [],
            "edges": [{
                "kind": "discloses_sharing_with", "src": "target::a",
                "dst": "entity::b", "evidence": [
                    {"source": "policy", "evidence_type": "policy_relation"},
                    {"source": "registry", "evidence_type": "ads_txt_authorisation"},
                    {"source": "registry", "evidence_type": "cmp_vendor_listing"},
                ],
            }],
        }
        graph = SharingGraph.from_dict(old)
        assert set(kind for kind, _src, _dst in graph.edges) == {
            EdgeKind.DISCLOSES_RELATION_WITH.value,
            EdgeKind.AUTHORISES_INVENTORY_SALE.value,
            EdgeKind.LISTS_VENDOR.value,
        }
        assert sum(len(edge.evidence) for edge in graph.edges.values()) == 3

    def test_legacy_contact_and_resolution_kinds_load_with_new_names(self):
        old = {"nodes": [], "edges": [
            {"kind": "contacts", "src": "target::a", "dst": "domain::b"},
            {"kind": "owned_by", "src": "domain::b", "dst": "entity::b"},
        ]}
        graph = SharingGraph.from_dict(old)
        assert set(kind for kind, _src, _dst in graph.edges) == {
            EdgeKind.CONTACTS_DOMAIN.value, EdgeKind.RESOLVES_TO.value,
        }
        assert {edge["kind"] for edge in graph.to_dict()["edges"]} == {
            "contacts_domain", "resolves_to",
        }


class TestNameResolutionInTheGraph:
    def test_a_registry_domain_and_a_policy_name_share_one_node(self):
        g = SharingGraph()
        add_target(g, "a", "a", [_rel("criteo.com", sources=["ads_txt"])])
        add_target(g, "b", "b", [_rel("Criteo SA")])
        entities = [n for n in g.nodes.values() if n.type is NodeType.ENTITY]
        assert len(entities) == 1
        assert entities[0].display_name == "Criteo"

    def test_the_surfaces_a_node_was_named_by_are_kept(self):
        g = SharingGraph()
        add_target(g, "a", "a", [_rel("criteo.com", sources=["ads_txt"])])
        add_target(g, "b", "b", [_rel("Criteo SA")])
        node = next(n for n in g.nodes.values() if n.type is NodeType.ENTITY)
        assert set(node.aliases) == {"criteo.com", "Criteo SA"}

    def test_a_party_named_by_domain_carries_its_site(self):
        g = SharingGraph()
        add_target(g, "a", "a", [_rel("adyoulike.com", sources=["ads_txt"])])
        node = next(n for n in g.nodes.values() if n.type is NodeType.ENTITY)
        assert node.primary_domain == "adyoulike.com"

    def test_a_tracker_domain_does_not_stand_as_its_owner_site(self):
        # doubleclick.net resolves to Google, which publishes elsewhere.
        g = SharingGraph()
        add_target(g, "a", "a", [_rel("doubleclick.net", sources=["ads_txt"])])
        node = next(n for n in g.nodes.values() if n.type is NodeType.ENTITY)
        assert node.display_name == "Google"
        assert node.primary_domain == ""


class TestCorrections:
    """A reader's corrections to a built graph."""

    def _graph(self):
        g = SharingGraph()
        add_target(g, "website__pub", "pub.example",
                   [_rel("Telaria"), _rel("Criteo")])
        return g

    def test_a_party_can_be_renamed_and_keeps_its_old_surface(self):
        g = self._graph()
        nid = entity_node_id("Criteo")
        assert g.rename_node(nid, "Criteo SA")
        assert g.nodes[nid].display_name == "Criteo SA"
        assert "Criteo" in g.nodes[nid].aliases
        assert g.nodes[nid].edited

    def test_a_party_can_be_folded_into_another(self):
        g = self._graph()
        telaria, criteo = entity_node_id("Telaria"), entity_node_id("Criteo")
        assert g.merge_nodes(telaria, criteo)
        assert telaria not in g.nodes
        assert not g.out_edges(telaria) and not g.in_edges(telaria)
        edge = g.edges[(EdgeKind.DISCLOSES_RELATION_WITH.value,
                        target_node_id("website__pub"), criteo)]
        assert len(edge.evidence) == 2

    def test_a_removed_party_takes_its_arrangements_with_it(self):
        g = self._graph()
        nid = entity_node_id("Criteo")
        assert g.remove_node(nid)
        assert nid not in g.nodes
        assert not any(e.dst == nid for e in g.edges.values())

    def test_an_arrangement_can_be_removed_on_its_own(self):
        g = self._graph()
        tid, nid = target_node_id("website__pub"), entity_node_id("Criteo")
        assert g.remove_edge(EdgeKind.DISCLOSES_RELATION_WITH.value, tid, nid)
        assert (EdgeKind.DISCLOSES_RELATION_WITH.value, tid, nid) not in g.edges
        assert nid in g.nodes

    def test_an_arrangement_can_be_moved_to_the_other_track(self):
        from tpd.tracks import INVENTORY

        g = self._graph()
        tid, nid = target_node_id("website__pub"), entity_node_id("Criteo")
        assert g.set_edge_track(EdgeKind.DISCLOSES_RELATION_WITH.value, tid, nid,
                                INVENTORY)
        assert g.edge_counts()[INVENTORY] == 1

    def test_an_edit_naming_no_such_party_changes_nothing(self):
        g = self._graph()
        assert not g.apply_edit({"op": "rename", "node": "entity::nobody",
                                 "display_name": "X"})
        assert not g.apply_edit({"op": "nonsense"})


class TestPartyRoles:
    def _graph(self):
        from tpd.sharing_graph import (
            SharingGraph,
            add_target,
            entity_node_id,
            expand_node,
        )

        def rel(entity, **kw):
            base = {"entity": entity, "party": "third", "unspecified": False,
                    "data_type": "personal data", "action": "be_shared",
                    "negative": False, "direction": "downstream", "purposes": [],
                    "examples": [], "qualifier": "", "sources": ["policy"],
                    "text": "", "doc_ids": []}
            base.update(kw)
            return base

        g = SharingGraph()
        add_target(g, "site", "site.example", [rel("Criteo")])
        expand_node(g, entity_node_id("Criteo"),
                    [rel("Adobe"), rel("A Publisher", direction="upstream")], hop=1)
        return g

    def test_a_disclosed_party_counts_as_a_recipient(self):
        from tpd.sharing_graph import entity_node_id

        roles = self._graph().party_roles()
        assert entity_node_id("Criteo") in roles["recipients"]
        assert entity_node_id("Adobe") in roles["recipients"]

    def test_a_party_that_only_hands_data_in_counts_as_a_supplier(self):
        from tpd.sharing_graph import entity_node_id

        roles = self._graph().party_roles()
        assert entity_node_id("A Publisher") in roles["suppliers"]
        assert entity_node_id("A Publisher") not in roles["recipients"]

    def test_the_two_roles_do_not_overlap(self):
        roles = self._graph().party_roles()
        assert not roles["recipients"] & roles["suppliers"]
