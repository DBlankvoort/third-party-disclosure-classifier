"""Validate the outward walk from a seed origin."""

from __future__ import annotations

import pytest

from tpd import expand as expand_mod
from tpd.expand import Expansion, origin_of, target_for_origin
from tpd.sharing_graph import NodeType, entity_node_id, target_node_id


def _rel(entity, **kw):
    base = {
        "entity": entity, "party": "third", "unspecified": False,
        "data_type": "personal data", "action": "be_shared", "negative": False,
        "direction": "downstream", "purposes": [], "examples": [],
        "qualifier": "", "sources": ["policy"], "text": "", "doc_ids": [],
    }
    base.update(kw)
    return base


@pytest.fixture
def recorded(monkeypatch):
    """Wire the walk to a fixed site-to-recipients map, recording each fetch."""
    fetched: list[str] = []
    relations: dict[str, list[dict]] = {}

    def fake_fetch(corpus, origin, force=False, delay=0.2):
        fetched.append(origin)
        return True

    def fake_analyse(corpus, origin, requests=None, force=False, delay=0.2,
                     fetched=False, cmp=None):
        return list(relations.get(origin, [])), []

    monkeypatch.setattr(expand_mod, "fetch_origin", fake_fetch)
    monkeypatch.setattr(expand_mod, "analyse_origin", fake_analyse)
    return {"fetched": fetched, "relations": relations}


def _walk(tmp_path, hops, **kw):
    return Expansion(tmp_path, "https://seed.example", hops=hops, delay=0, **kw)


class TestOrigin:
    def test_a_path_and_query_are_dropped(self):
        assert origin_of("https://a.example/x?y=1") == "https://a.example"

    def test_a_non_http_url_is_rejected(self):
        with pytest.raises(ValueError):
            origin_of("ftp://a.example")

    def test_the_target_id_follows_the_corpus_convention(self):
        assert target_for_origin("https://a.example").id == "website__a-example"


class TestDepth:
    def test_one_hop_collects_only_the_seed(self, tmp_path, recorded):
        recorded["relations"]["https://seed.example"] = [_rel("Criteo")]
        walk = _walk(tmp_path, 1)
        walk.run()
        assert recorded["fetched"] == ["https://seed.example"]

    def test_one_hop_leaves_the_parties_unexpanded(self, tmp_path, recorded):
        recorded["relations"]["https://seed.example"] = [_rel("Criteo")]
        walk = _walk(tmp_path, 1)
        walk.run()
        assert walk.graph.termination(entity_node_id("Criteo")) == "unexpanded"

    def test_two_hops_collect_the_parties_the_seed_names(self, tmp_path, recorded):
        recorded["relations"]["https://seed.example"] = [_rel("Criteo")]
        walk = _walk(tmp_path, 2)
        walk.run()
        assert "https://criteo.com" in recorded["fetched"]

    def test_three_hops_reach_a_party_named_by_a_party(self, tmp_path, recorded):
        recorded["relations"]["https://seed.example"] = [_rel("Criteo")]
        recorded["relations"]["https://criteo.com"] = [_rel("Adobe")]
        walk = _walk(tmp_path, 3)
        walk.run()
        assert "https://adobe.com" in recorded["fetched"]
        assert walk.graph.nodes[entity_node_id("Adobe")].hop_first_seen == 2

    def test_the_walk_stops_at_the_requested_depth(self, tmp_path, recorded):
        recorded["relations"]["https://seed.example"] = [_rel("Criteo")]
        recorded["relations"]["https://criteo.com"] = [_rel("Adobe")]
        walk = _walk(tmp_path, 2)
        walk.run()
        assert "https://adobe.com" not in recorded["fetched"]

    def test_depth_is_held_within_the_offered_range(self, tmp_path, recorded):
        assert _walk(tmp_path, 9).hops == 3
        assert _walk(tmp_path, 0).hops == 1


class TestResolution:
    def test_a_party_no_table_names_is_not_collected(self, tmp_path, recorded):
        recorded["relations"]["https://seed.example"] = [_rel("Acme Analytics")]
        walk = _walk(tmp_path, 3)
        walk.run()
        assert recorded["fetched"] == ["https://seed.example"]

    def test_an_unreachable_party_is_reported(self, tmp_path, recorded):
        recorded["relations"]["https://seed.example"] = [_rel("Acme Analytics")]
        walk = _walk(tmp_path, 2)
        walk.run()
        assert "Acme Analytics" in walk.unresolved.values()

    def test_an_unreachable_party_stays_an_unexpanded_leaf(self, tmp_path, recorded):
        recorded["relations"]["https://seed.example"] = [_rel("Acme Analytics")]
        walk = _walk(tmp_path, 2)
        walk.run()
        assert walk.graph.termination(entity_node_id("Acme Analytics")) == "unexpanded"

    def test_a_hand_supplied_domain_opens_the_party_up(self, tmp_path, recorded):
        recorded["relations"]["https://seed.example"] = [_rel("Acme Analytics")]
        walk = _walk(tmp_path, 2, overrides={"acmeanalytics": "acme.io"})
        walk.run()
        assert "https://acme.io" in recorded["fetched"]

    def test_a_generic_category_is_never_followed(self, tmp_path, recorded):
        recorded["relations"]["https://seed.example"] = [
            _rel("advertising partners", unspecified=True),
        ]
        walk = _walk(tmp_path, 3)
        walk.run()
        node = walk.graph.nodes["generic::advertising partners"]
        assert node.type is NodeType.GENERIC
        assert recorded["fetched"] == ["https://seed.example"]


class TestTraversalShape:
    def test_a_party_reached_twice_is_collected_once(self, tmp_path, recorded):
        recorded["relations"]["https://seed.example"] = [_rel("Criteo"), _rel("Adobe")]
        recorded["relations"]["https://criteo.com"] = [_rel("Adobe")]
        walk = _walk(tmp_path, 3)
        walk.run()
        assert recorded["fetched"].count("https://adobe.com") == 1

    def test_a_cycle_does_not_recollect_the_seed(self, tmp_path, recorded):
        recorded["relations"]["https://seed.example"] = [_rel("Criteo")]
        recorded["relations"]["https://criteo.com"] = [_rel("Criteo")]
        walk = _walk(tmp_path, 3)
        walk.run()
        assert recorded["fetched"].count("https://criteo.com") == 1

    def test_the_expanded_party_records_the_site_it_came_from(self, tmp_path, recorded):
        recorded["relations"]["https://seed.example"] = [_rel("Criteo")]
        walk = _walk(tmp_path, 2)
        walk.run()
        assert walk.graph.nodes[entity_node_id("Criteo")].primary_domain == "criteo.com"

    def test_the_seed_is_the_only_target_node(self, tmp_path, recorded):
        recorded["relations"]["https://seed.example"] = [_rel("Criteo")]
        recorded["relations"]["https://criteo.com"] = [_rel("Adobe")]
        walk = _walk(tmp_path, 3)
        walk.run()
        targets = [n for n in walk.graph.nodes.values() if n.type is NodeType.TARGET]
        assert [n.id for n in targets] == [target_node_id("website__seed-example")]


class TestProgress:
    def test_a_finished_walk_reports_what_it_collected(self, tmp_path, recorded):
        recorded["relations"]["https://seed.example"] = [_rel("Criteo")]
        walk = _walk(tmp_path, 2)
        walk.run()
        assert walk.progress.phase == "done"
        assert walk.progress.crawled == 2

    def test_a_snapshot_carries_the_graph_and_the_progress(self, tmp_path, recorded):
        recorded["relations"]["https://seed.example"] = [_rel("Criteo")]
        walk = _walk(tmp_path, 1)
        walk.run()
        snapshot = walk.snapshot()
        assert snapshot["origin"] == "https://seed.example"
        assert snapshot["progress"]["phase"] == "done"
        assert len(snapshot["graph"]["nodes"]) == 2

    def test_a_stopped_walk_expands_nothing_further(self, tmp_path, recorded):
        recorded["relations"]["https://seed.example"] = [_rel("Criteo")]
        walk = _walk(tmp_path, 3)
        walk.stop()
        walk.run()
        assert walk.progress.phase == "stopped"
        assert recorded["fetched"] == ["https://seed.example"]

    def test_a_party_that_cannot_be_analysed_is_not_claimed_as_analysed(
        self, tmp_path, recorded, monkeypatch,
    ):
        recorded["relations"]["https://seed.example"] = [_rel("Criteo")]

        def failing(corpus, origin, requests=None, force=False, delay=0.2,
                    fetched=False, cmp=None):
            if origin == "https://criteo.com":
                raise FileNotFoundError(origin)
            return [_rel("Criteo")], []

        monkeypatch.setattr(expand_mod, "analyse_origin", failing)
        walk = _walk(tmp_path, 2)
        walk.run()
        assert walk.graph.termination(entity_node_id("Criteo")) == "unexpanded"

    def test_a_party_analysed_and_sharing_nothing_is_terminal(self, tmp_path, recorded):
        recorded["relations"]["https://seed.example"] = [_rel("Criteo")]
        recorded["relations"]["https://criteo.com"] = []
        walk = _walk(tmp_path, 2)
        walk.run()
        assert walk.graph.termination(entity_node_id("Criteo")) == "terminal"
