"""Run the spaCy pipeline through ``PoliGrapher.from_text``. Assert on the resulting knowledge graph."""

from __future__ import annotations

import pytest

from tpd.poligraph.graph import UNSPECIFIED_ACTOR, Action, EdgeType

pytestmark = pytest.mark.nlp


def edges(graph, include_negative=True):
    return list(graph.collect_edges(include_negative=include_negative))


def edge_set(graph):
    return {(e.entity, e.data_type, e.edge_type, e.action) for e in edges(graph)}


class TestCollectionAnnotator:
    def test_share_with_recipient(self, poligrapher):
        g = poligrapher.from_text("We share your email address with advertisers.")
        es = edge_set(g)
        assert ("we", "email address", EdgeType.COLLECT, Action.COLLECT) in es
        assert ("advertiser", "email address", EdgeType.COLLECT, Action.BE_SHARED) in es

    def test_do_not_sell_without_recipient(self, poligrapher):
        g = poligrapher.from_text("We do not sell your personal information.")
        es = edge_set(g)
        assert (UNSPECIFIED_ACTOR, "personal information",
                EdgeType.NOT_COLLECT, Action.BE_SOLD) in es
        assert all(e.entity != "we" for e in edges(g))

    def test_negated_collection(self, poligrapher):
        g = poligrapher.from_text("We do not collect your precise location.")
        neg = [e for e in edges(g) if e.edge_type == EdgeType.NOT_COLLECT]
        assert any(e.entity == "we" and "location" in e.data_type for e in neg)
        assert not any(e.edge_type == EdgeType.COLLECT for e in edges(g))

    def test_conjoined_data_types(self, poligrapher):
        g = poligrapher.from_text(
            "We collect your phone number and your email address."
        )
        collected = {e.data_type for e in edges(g) if e.entity == "we"}
        assert "phone number" in collected and "email address" in collected

    def test_questions_are_ignored(self, poligrapher):
        g = poligrapher.from_text("Do we sell your personal information?")
        assert edges(g) == []

    def test_child_data_subject(self, poligrapher):
        g = poligrapher.from_text("We do not collect personal information from children.")
        assert any(e.subject == "child" for e in edges(g))

    def test_provide_entity_with_data(self, poligrapher):
        g = poligrapher.from_text("We provide advertisers with your email address.")
        assert any(e.entity == "advertiser" and e.data_type == "email address"
                   and e.action == Action.BE_SHARED for e in edges(g))


class TestSubsumptionAnnotator:
    def test_such_as(self, poligrapher):
        g = poligrapher.from_text(
            "We collect personal information such as your email address."
        )
        assert g.subsumes("personal information", "email address")

    def test_including(self, poligrapher):
        g = poligrapher.from_text(
            "We share device information, including your IP address, with vendors."
        )
        assert g.subsumes("device information", "ip address")

    def test_no_cross_label_subsumption(self, poligrapher):
        g = poligrapher.from_text(
            "We work with partners such as advertisers to show you ads."
        )
        for hyper, hypo in g.subsume_edges():
            assert g.node_type(hyper) == g.node_type(hypo)

    def test_subsumption_is_sentence_local(self, poligrapher):
        g = poligrapher.from_text(
            "We collect payment details. "
            "We also use tools such as Google Analytics."
        )
        assert not g.subsumes("payment details", "google analytics")

    def test_collect_propagates_through_subsumption(self, poligrapher):
        g = poligrapher.from_text(
            "We collect personal information such as your email address."
        )
        assert g.collects("we", "email address")


class TestGraphShape:
    def test_graph_is_valid_dag(self, poligrapher):
        g = poligrapher.from_text(
            "We collect personal information such as contact information. "
            "Contact information includes your email address."
        )
        assert g.validate() is g

    def test_roundtrip_to_dict(self, poligrapher):
        g = poligrapher.from_text("We share your email address with advertisers.")
        d = g.to_dict()
        names = {n["name"] for n in d["nodes"]}
        assert {"we", "advertiser", "email address"} <= names
