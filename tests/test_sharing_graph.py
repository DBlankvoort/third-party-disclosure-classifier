"""Validate tpd.sharing_graph."""

from __future__ import annotations

from tpd.sharing_graph import (
    EdgeKind,
    EvidenceSource,
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
        edge = g.edges[(EdgeKind.DISCLOSES_SHARING_WITH.value, tid,
                        entity_node_id("criteo"))]
        assert edge.sources == {"policy"}

    def test_upstream_relations_point_into_the_target(self):
        g = SharingGraph()
        tid = add_target(g, "website__exchange", "exchange.com",
                         [_rel("Publisher A", direction="upstream",
                               sources=["sellers_json"])])
        key = (EdgeKind.SUPPLIES.value, entity_node_id("Publisher A"), tid)
        assert key in g.edges
        assert g.edges[key].sources == {"registry"}

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
        assert (EdgeKind.CONTACTS.value, tid, "domain::doubleclick.net") in g.edges
        assert (EdgeKind.OWNED_BY.value, "domain::doubleclick.net",
                entity_node_id("Google")) in g.edges

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
        g.add_edge(EdgeKind.DISCLOSES_SHARING_WITH, entity_node_id("Bravo"),
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
        assert edge.evidence[0].snippet == "we share with Criteo"


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
        edge = g.edges[(EdgeKind.DISCLOSES_SHARING_WITH.value,
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
        assert g.remove_edge(EdgeKind.DISCLOSES_SHARING_WITH.value, tid, nid)
        assert (EdgeKind.DISCLOSES_SHARING_WITH.value, tid, nid) not in g.edges
        assert nid in g.nodes

    def test_an_arrangement_can_be_moved_to_the_other_track(self):
        from tpd.tracks import INVENTORY

        g = self._graph()
        tid, nid = target_node_id("website__pub"), entity_node_id("Criteo")
        assert g.set_edge_track(EdgeKind.DISCLOSES_SHARING_WITH.value, tid, nid,
                                INVENTORY)
        assert g.edge_counts()[INVENTORY] == 1

    def test_an_edit_naming_no_such_party_changes_nothing(self):
        g = self._graph()
        assert not g.apply_edit({"op": "rename", "node": "entity::nobody",
                                 "display_name": "X"})
        assert not g.apply_edit({"op": "nonsense"})
