"""Validate resolvability."""

from __future__ import annotations

from tpd.sharing_graph import (
    SharingGraph,
    add_target,
    entity_node_id,
    expand_node,
)

from tpd_eval.findings import resolvability_measures


def _rel(entity, **kw):
    base = {
        "entity": entity, "party": "third", "unspecified": False,
        "data_type": "personal data", "action": "be_shared", "negative": False,
        "direction": "downstream", "purposes": [], "examples": [],
        "qualifier": "", "sources": ["policy"], "text": "", "doc_ids": [],
    }
    base.update(kw)
    return base


def _walked():
    """One ring collected."""
    g = SharingGraph()
    add_target(g, "site", "site.example",
               [_rel("Bravo"), _rel("Zulu"), _rel("Yankee"),
                _rel("advertising partners", unspecified=True)])
    expand_node(g, entity_node_id("Bravo"), [_rel("Delta")], hop=1,
                primary_domain="bravo.example")
    g.nodes[entity_node_id("Yankee")].primary_domain = "yankee.example"
    return g


class TestResolvability:
    def test_a_followed_party_is_counted_as_followed(self):
        assert resolvability_measures(_walked())["followed"] == 1

    def test_a_party_named_past_the_last_ring_is_attributed_to_the_budget(self):
        m = resolvability_measures(_walked())
        assert m["collected_depth"] == 1
        assert m["beyond_budget"] == 1

    def test_a_name_resolving_to_no_site_is_separated_from_a_failed_fetch(self):
        m = resolvability_measures(_walked())
        assert (m["no_site"], m["not_collected"]) == (1, 1)

    def test_every_organisation_falls_into_exactly_one_cause(self):
        m = resolvability_measures(_walked())
        assert m["entities"] == (m["followed"] + m["beyond_budget"]
                                 + m["no_site"] + m["not_collected"])
        assert m["unfollowed"] == m["entities"] - m["followed"]

    def test_generic_recipients_are_reported_apart_from_organisations(self):
        m = resolvability_measures(_walked())
        assert m["generic"] == 1
        assert m["generic_examples"] == ["advertising partners"]

    def test_an_empty_graph_states_nothing(self):
        m = resolvability_measures(SharingGraph())
        assert m["entities"] == 0 and m["unfollowed_pct"] == 0.0
