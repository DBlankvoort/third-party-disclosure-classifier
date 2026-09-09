"""Validate the outward walk from a seed origin."""

from __future__ import annotations

import json

import pytest

from tpd import expand as expand_mod
from tpd.collect.base import CollectedDoc, Corpus, Target
from tpd.expand import Expansion, origin_of, target_for_origin
from tpd.sharing_graph import (
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


@pytest.fixture
def recorded(monkeypatch):
    """Wire the walk to a fixed site-to-recipients map, recording each fetch."""
    fetched: list[str] = []
    relations: dict[str, list[dict]] = {}
    observed: dict[str, list[dict]] = {}

    def fake_fetch(corpus, origin, force=False, delay=0.2, render=True, target=None):
        fetched.append(origin)
        return True

    def fake_analyse(corpus, origin, requests=None, force=False, delay=0.2,
                     fetched=False, cmp=None, probe=False, evidence_kind="main",
                     target=None, site_kind=""):
        return list(relations.get(origin, [])), list(observed.get(origin, []))

    monkeypatch.setattr(expand_mod, "fetch_origin", fake_fetch)
    monkeypatch.setattr(expand_mod, "analyse_origin", fake_analyse)
    return {"fetched": fetched, "relations": relations, "observed": observed}


def _walk(tmp_path, hops, **kw):
    kw.setdefault("render", False)
    kw.setdefault("analysis_workers", 1)
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

    def test_any_depth_is_accepted(self, tmp_path, recorded):
        assert _walk(tmp_path, 40).hops == 40
        assert _walk(tmp_path, 0).hops == 1

    def test_five_hops_reach_the_fifth_ring(self, tmp_path, recorded):
        chain = ["https://seed.example", "https://criteo.com", "https://adobe.com",
                 "https://oracle.com", "https://salesforce.com"]
        names = ["Criteo", "Adobe", "Oracle", "Salesforce"]
        for origin, name in zip(chain[:-1], names, strict=True):
            recorded["relations"][origin] = [_rel(name)]
        walk = _walk(tmp_path, 5)
        walk.run()
        assert recorded["fetched"] == chain
        assert walk.graph.nodes[entity_node_id("Salesforce")].hop_first_seen == 4


class TestRingWidth:
    def test_a_ring_wider_than_one_batch_is_collected_whole(self, tmp_path, recorded):
        recorded["relations"]["https://seed.example"] = [
            _rel(name) for name in ("Criteo", "Adobe", "Oracle")
        ]
        walk = _walk(tmp_path, 2, chunk=2)
        walk.run()
        assert len(recorded["fetched"]) == 4

    def test_a_party_in_a_later_batch_still_opens_the_ring_beyond(
        self, tmp_path, recorded,
    ):
        recorded["relations"]["https://seed.example"] = [
            _rel(name) for name in ("Criteo", "Adobe", "Oracle")
        ]
        recorded["relations"]["https://oracle.com"] = [_rel("Salesforce")]
        walk = _walk(tmp_path, 3, chunk=2)
        walk.run()
        assert "https://salesforce.com" in recorded["fetched"]

    def test_a_ring_is_collected_best_evidenced_first(self, tmp_path, recorded):
        recorded["relations"]["https://seed.example"] = [_rel("Criteo"), _rel("Adobe")]
        recorded["relations"]["https://criteo.com"] = [_rel("Oracle")]
        recorded["relations"]["https://adobe.com"] = [_rel("Oracle"), _rel("Salesforce")]
        walk = _walk(tmp_path, 3, chunk=1)
        walk.run()
        order = recorded["fetched"]
        assert order.index("https://oracle.com") < order.index("https://salesforce.com")


class TestTimeLimit:
    def test_a_spent_clock_ends_the_walk(self, tmp_path, recorded):
        recorded["relations"]["https://seed.example"] = [_rel("Criteo")]
        walk = _walk(tmp_path, 3, time_limit=1e-6)
        walk.run()
        assert walk.progress.phase == "timed out"
        assert recorded["fetched"] == ["https://seed.example"]

    def test_an_unbounded_walk_reports_no_limit(self, tmp_path, recorded):
        recorded["relations"]["https://seed.example"] = [_rel("Criteo")]
        walk = _walk(tmp_path, 2)
        walk.run()
        assert walk.progress.phase == "done"
        assert walk.snapshot()["progress"]["time_limit"] == 0.0


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
        assert snapshot["progress"]["hop"] == 1
        assert len(snapshot["graph"]["nodes"]) == 2

    def test_completed_walk_reports_the_requested_depth(self, tmp_path, recorded):
        recorded["relations"]["https://seed.example"] = []
        walk = _walk(tmp_path, 4)
        walk.run()
        assert walk.snapshot()["progress"]["hop"] == 4

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
                    fetched=False, cmp=None, probe=False, evidence_kind="main",
                     target=None, site_kind=""):
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

    def test_traffic_view_does_not_visit_contacted_domains(
        self, tmp_path, monkeypatch,
    ):
        def no_fetch(*args, **kwargs):
            raise AssertionError("traffic walks must not fetch document sets")

        def probe(target_dir, origin, force=False, max_age=0.0):
            domain = "doubleclick.net" if "seed.example" in origin else "criteo.com"
            return {"requests": [{"url": f"https://{domain}/pixel", "type": "image"}],
                    "accepted": "", "cached": False, "available": True}

        monkeypatch.setattr(expand_mod, "fetch_origin", no_fetch)
        monkeypatch.setattr(expand_mod, "cached_probe", probe)
        walk = _walk(tmp_path, 2, evidence_kind="contacts_domain")
        walk.run()
        assert entity_node_id("Google") in walk.graph.nodes
        assert entity_node_id("Criteo") not in walk.graph.nodes


class TestEvidenceKind:
    def test_unknown_kind_is_rejected(self, tmp_path):
        with pytest.raises(ValueError, match="unknown evidence kind"):
            _walk(tmp_path, 1, evidence_kind="unsupported")

    def test_adtech_view_does_not_reinterpret_ad_system_ads_txt(
        self, tmp_path, recorded,
    ):
        recorded["relations"]["https://seed.example"] = [
            _rel("ssp.example", sources=["ads_txt"], qualifier="direct",
                 publisher_ids=["pub-1"]),
        ]
        recorded["relations"]["https://ssp.example"] = [
            _rel("other.example", sources=["ads_txt"], qualifier="direct",
                 publisher_ids=["other-1"]),
        ]
        walk = _walk(tmp_path, 4, evidence_kind="authorises_inventory_sale",
                     probe=False)
        walk.run()
        assert recorded["fetched"] == ["https://seed.example"]


class TestFirstParty:
    """The popup and the walk must agree on which domains are the site itself."""

    ORIGIN = "https://www.smbc-comics.com"

    def _corpus(self, tmp_path, roles=("privacy_policy",)):
        from tpd.collect.base import CollectedDoc, Corpus

        corpus = Corpus(tmp_path)
        target = target_for_origin(self.ORIGIN)
        docs = [
            CollectedDoc(doc_id=f"d{i}", url=f"{self.ORIGIN}/{role}", role=role,
                         http_status=200, raw_path=f"{target.id}/docs/d{i}.html")
            for i, role in enumerate(roles)
        ]
        corpus.write_manifest(target, docs)
        return corpus, target

    def test_a_policy_url_contributes_its_brand_token(self, tmp_path):
        corpus, target = self._corpus(tmp_path)
        assert expand_mod.first_party_for_target(corpus, target) == {
            "smbc", "comics", "smbc-comics",
        }

    def test_the_walk_derives_it_exactly_as_the_popup_does(self, tmp_path):
        from tpd.classify.named_entities import first_party_tokens

        corpus, target = self._corpus(tmp_path)
        _, docs = corpus.read_manifest(target.id)
        popup = first_party_tokens(
            expand_mod.first_party_urls(target, docs), name=target.name)
        assert expand_mod.first_party_for_target(corpus, target) == popup

    def test_a_missing_corpus_still_yields_the_name_s_tokens(self, tmp_path):
        from tpd.collect.base import Corpus

        target = target_for_origin(self.ORIGIN)
        assert expand_mod.first_party_for_target(Corpus(tmp_path), target) == {
            "smbc", "comics",
        }

    def test_the_traffic_walk_drops_the_site_s_own_domains(self, tmp_path):
        corpus, _ = self._corpus(tmp_path)
        requests = [
            {"url": "https://smbc-comics.net/a.png", "type": "image"},
            {"url": "https://doubleclick.net/a", "type": "script"},
        ]
        _relations, observed = expand_mod.analyse_origin(
            corpus, self.ORIGIN, requests=requests, evidence_kind="contacts_domain",
        )
        assert [o["domain"] for o in observed] == ["doubleclick.net"]



class TestCorroborationRule:
    """Test collection-time corroboration."""

    def _seed_relations(self, recorded, *entities):
        recorded["relations"]["https://seed.example"] = [
            _rel(e) for e in entities
        ]

    def test_it_is_off_unless_asked_for(self, tmp_path, recorded):
        self._seed_relations(recorded, "Some Partner")
        walk = _walk(tmp_path, 1, probe=False)
        walk.run()
        assert entity_node_id("Some Partner") in walk.graph.nodes
        assert walk.snapshot()["pruned"]["dropped"] == 0

    def test_a_party_only_the_policy_names_is_left_out(self, tmp_path, recorded):
        self._seed_relations(recorded, "Some Partner")
        walk = _walk(tmp_path, 1, probe=False, corroborated_only=True)
        walk.run()
        assert entity_node_id("Some Partner") not in walk.graph.nodes
        summary = walk.snapshot()["pruned"]
        assert summary["dropped"] == 1
        # Prose alone does not satisfy the first-hop rule.
        assert summary["half_met"] == 0

    def test_a_party_both_records_agree_on_is_kept(self, tmp_path, recorded):
        recorded["relations"]["https://seed.example"] = [_rel("DoubleClick")]
        recorded["observed"]["https://seed.example"] = [{
            "entity": "DoubleClick", "domains": ["doubleclick.net"],
            "basis": "domain_map", "consent": "pre_consent",
        }]
        walk = _walk(tmp_path, 1, probe=False, corroborated_only=True)
        walk.run()
        assert entity_node_id("DoubleClick") in walk.graph.nodes
        assert walk.snapshot()["pruned"]["dropped"] == 0

    def test_a_dropped_party_is_never_expanded(self, tmp_path, recorded):
        self._seed_relations(recorded, "Some Partner")
        walk = _walk(tmp_path, 3, probe=False, corroborated_only=True)
        walk.run()
        assert "https://somepartner.com" not in recorded["fetched"]

    def test_the_rule_belongs_to_the_main_view_alone(self, tmp_path, recorded):
        """Single-source views do not use the main-view rule."""
        self._seed_relations(recorded, "Some Partner")
        walk = _walk(tmp_path, 1, probe=False, corroborated_only=True,
                     evidence_kind="discloses_relation_with")
        assert not walk.corroborated_only
        walk.run()
        assert entity_node_id("Some Partner") in walk.graph.nodes


class TestTrackerConfirmation:
    def test_an_observed_tracker_is_marked_in_the_graph(self, tmp_path, recorded):
        recorded["observed"]["https://seed.example"] = [{
            "entity": "DoubleClick", "domains": ["doubleclick.net"],
            "basis": "domain_map", "consent": "pre_consent",
        }]
        walk = _walk(tmp_path, 1, probe=False)
        walk.run()
        assert walk.snapshot()["tracker_check"]["counts"] == {"confirmed": 1}

    def test_a_walk_over_documents_alone_checks_nothing(self, tmp_path, recorded):
        self._observed = None
        walk = _walk(tmp_path, 1, probe=False,
                     evidence_kind="discloses_relation_with")
        walk.run()
        assert walk.snapshot()["tracker_check"]["checked"] == 0


class TestChecksAccumulate:
    """Keep results when later passes skip confirmed edges."""

    def test_a_later_pass_does_not_shorten_the_report(self, tmp_path, recorded):
        recorded["observed"]["https://seed.example"] = [{
            "entity": "DoubleClick", "domains": ["doubleclick.net"],
            "basis": "domain_map", "consent": "pre_consent",
        }]
        walk = _walk(tmp_path, 3, probe=False)
        walk.run()
        # Report the contact once across all verification passes.
        assert walk.snapshot()["tracker_check"] == {
            "checked": 1, "counts": {"confirmed": 1}}


class TestSellersJsonNeverBuildsEdges:
    """Use sellers.json for corroboration, not graph construction."""

    SELLERS = json.dumps({"sellers": [
        {"seller_id": "1", "name": "Upstream One", "domain": "one.example",
         "seller_type": "PUBLISHER"},
        {"seller_id": "2", "name": "Upstream Two", "domain": "two.example",
         "seller_type": "INTERMEDIARY"},
    ]})
    VENDORS = json.dumps({"vendors": [{"name": "Listed Vendor", "purposes": [2]}]})

    @pytest.fixture
    def broker(self, tmp_path):
        corpus = Corpus(tmp_path)
        target = Target(id="website__ssp-example", type="website",
                        name="ssp.example", url="https://ssp.example")
        docs = []
        for i, (role, raw) in enumerate(
                (("sellers_json", self.SELLERS), ("vendors_json", self.VENDORS))):
            doc = CollectedDoc(doc_id=f"{role}-{i:02d}",
                               url=f"https://ssp.example/{role}", role=role,
                               http_status=200, content_type="application/json")
            docs.append(corpus.save_doc(target.id, doc, raw))
        corpus.write_manifest(target, docs)
        return corpus, target, docs

    def _sources(self, corpus, target, docs, evidence_kind):
        return {s for rel in expand_mod.relations_for_target(
            corpus, target.id, docs, first_party=set(),
            evidence_kind=evidence_kind, site_kind="data_broker",
        ) for s in rel.get("sources", ())}

    def test_the_main_view_reads_no_sellers_json(self, broker):
        sources = self._sources(*broker, "main")
        assert "sellers_json" not in sources

    def test_the_vendor_view_reads_no_sellers_json_either(self, broker):
        sources = self._sources(*broker, "lists_vendor")
        assert "sellers_json" not in sources

    def test_vendor_registries_do_not_feed_the_main_view(self, broker):
        assert "vendors_json" not in self._sources(*broker, "main")

    def test_no_upstream_party_reaches_the_graph(self, broker):
        corpus, target, docs = broker
        relations = expand_mod.relations_for_target(
            corpus, target.id, docs, first_party=set(),
            evidence_kind="main", site_kind="data_broker",
        )
        graph = SharingGraph()
        add_target(graph, target.id, target.name, relations)
        assert entity_node_id("Upstream One") not in graph.nodes
        assert not [e for e in graph.edges.values()
                    if e.dst == target_node_id(target.id)]
