from __future__ import annotations

import json

import pytest

from tpd.collect.base import CollectedDoc, Corpus, Target
from tpd.reconcile import (
    ABSENT,
    BY_DOMAIN,
    BY_NAME,
    BY_SELLER_ID,
    BY_TRACKER_RADAR,
    CONFIRMED,
    KNOWN_NOT_TRACKING,
    NO_SELLER_ID,
    NO_SELLERS_JSON,
    NOT_COLLECTED,
    RELATIONSHIP_MISMATCH,
    UNLISTED,
    RegistryStore,
    ads_txt_accounts,
    confirm_tracker_domains,
    corroborate_relations,
    corroboration_summary,
    parse_sellers_json,
    reconcile_sellers,
    reconciliation_summary,
    sellers_json_host,
    tracker_listing,
    tracker_summary,
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

SELLERS = {
    "contact_email": "ads@ssp.example",
    "sellers": [
        {"seller_id": "pub-1", "name": "Site Example",
         "domain": "site.example", "seller_type": "PUBLISHER"},
        {"seller_id": "int-2", "name": "Middle Exchange",
         "domain": "middle.example", "seller_type": "INTERMEDIARY"},
        {"seller_id": "hidden-3", "is_confidential": 1},
    ],
}

ADS_TXT = (
    "# a publisher's authorisations\n"
    "ssp.example, pub-1, DIRECT, f08c47fec0942fa0\n"
    "other.example, 99, RESELLER\n"
)


class TestSellersJson:
    def test_every_way_in_is_indexed(self):
        record = parse_sellers_json(json.dumps(SELLERS))
        assert record.disclosed == 2
        assert record.confidential == 1
        assert "pub-1" in record.by_id
        assert "site.example" in record.by_domain

    def test_a_confidential_entry_names_nobody(self):
        record = parse_sellers_json(json.dumps(SELLERS))
        assert all(e.name != "" for e in record.entries)

    def test_the_account_outranks_the_domain(self):
        record = parse_sellers_json(json.dumps(SELLERS))
        entry, basis = record.lookup(domain="middle.example", seller_ids=["pub-1"])
        assert basis == BY_SELLER_ID
        assert entry.seller_id == "pub-1"

    def test_a_domain_matches_across_a_www_prefix(self):
        record = parse_sellers_json(json.dumps(
            {"sellers": [{"seller_id": "1", "name": "S", "domain": "www.site.example"}]}))
        entry, basis = record.lookup(domain="site.example")
        assert basis == BY_DOMAIN and entry.seller_id == "1"

    def test_a_name_matches_where_no_domain_does(self):
        record = parse_sellers_json(json.dumps(SELLERS))
        entry, basis = record.lookup(domain="", name="Middle Exchange")
        assert basis == BY_NAME and entry.seller_id == "int-2"

    def test_a_party_the_file_omits_is_not_matched(self):
        record = parse_sellers_json(json.dumps(SELLERS))
        entry, basis = record.lookup(domain="absent.example", name="Absent")
        assert entry is None and basis == ""

    def test_documents_that_are_not_sellers_json(self):
        assert parse_sellers_json("<html>404</html>") is None
        assert parse_sellers_json(json.dumps({"vendors": []})) is None
        assert parse_sellers_json("") is None


class TestAdsTxtAccounts:
    def test_the_account_named_for_one_ad_system_is_read(self):
        assert ads_txt_accounts(ADS_TXT, "ssp.example") == ["pub-1"]

    def test_other_ad_systems_are_left_alone(self):
        assert ads_txt_accounts(ADS_TXT, "unrelated.example") == []

    def test_several_accounts_with_one_system_are_all_read(self):
        raw = "ssp.example, a, DIRECT\nssp.example, b, RESELLER\n"
        assert ads_txt_accounts(raw, "ssp.example") == ["a", "b"]


# --------------------------------------------------------------------------- #
# A corpus holding the two sides of one arrangement
# --------------------------------------------------------------------------- #
def _write_target(corpus: Corpus, host: str, docs: dict[str, str]) -> None:
    target = Target(id=f"website__{Target.make_id(host)}", type="website",
                    name=host, url=f"https://{host}")
    collected = []
    for index, (role, raw) in enumerate(docs.items()):
        doc = CollectedDoc(doc_id=f"{role}-{index:02d}", url=f"https://{host}/{role}",
                           role=role, http_status=200, content_type="text/plain")
        collected.append(corpus.save_doc(target.id, doc, raw))
    corpus.write_manifest(target, collected)


@pytest.fixture
def corpus(tmp_path) -> Corpus:
    store = Corpus(tmp_path)
    _write_target(store, "site.example", {"ads_txt": ADS_TXT})
    _write_target(store, "ssp.example", {"sellers_json": json.dumps(SELLERS)})
    _write_target(store, "bare.example", {"ads_txt": "ssp.example, x, DIRECT\n"})
    return store


class TestRegistryStore:
    def test_a_collected_domain_yields_its_sellers_json(self, corpus):
        store = RegistryStore(corpus)
        assert store.sellers("ssp.example").disclosed == 2

    def test_a_collected_domain_without_one_is_not_a_missing_domain(self, corpus):
        store = RegistryStore(corpus)
        assert store.sellers("bare.example") is None
        assert store.crawled("bare.example")
        assert not store.crawled("never.example")

    def test_accounts_come_from_the_supplier_side(self, corpus):
        store = RegistryStore(corpus)
        assert store.accounts("site.example", "ssp.example") == ["pub-1"]


def _pair_graph() -> tuple[SharingGraph, str, str]:
    graph = SharingGraph()
    seed = "target::website__site-example"
    graph.add_node(Node(id=seed, type=NodeType.TARGET, display_name="site.example"))
    ssp = graph.add_node(Node(id="entity::ssp", type=NodeType.ENTITY,
                              display_name="SSP Example",
                              primary_domain="ssp.example"))
    graph.add_edge(
        EdgeKind.AUTHORISES_INVENTORY_SALE, seed, ssp.id,
        Evidence(source=EvidenceSource.REGISTRY,
                 evidence_type=EvidenceType.ADS_TXT_AUTHORISATION,
                 track="inventory", qualifier="direct",
                 publisher_ids=["pub-1"]),
    )
    return graph, seed, ssp.id


class TestReconciliation:
    def test_a_claim_the_other_side_confirms_gains_evidence(self, corpus):
        graph, seed, ssp = _pair_graph()
        report = reconcile_sellers(graph, corpus, domains={seed: "site.example"})
        (row,) = report
        assert row["status"] == CONFIRMED
        assert row["basis"] == BY_SELLER_ID
        assert row["seller_id"] == "pub-1"

    def test_the_confirmation_lands_on_the_edge_it_speaks_to(self, corpus):
        graph, seed, ssp = _pair_graph()
        reconcile_sellers(graph, corpus, domains={seed: "site.example"})
        edge = graph.edges[(EdgeKind.AUTHORISES_INVENTORY_SALE.value, seed, ssp)]
        types = [e.evidence_type for e in edge.evidence]
        assert EvidenceType.SELLERS_JSON_CONFIRMATION in types
        assert len(edge.evidence) == 2

    def test_the_confirmation_quotes_the_entry_it_found(self, corpus):
        graph, seed, ssp = _pair_graph()
        reconcile_sellers(graph, corpus, domains={seed: "site.example"})
        edge = graph.edges[(EdgeKind.AUTHORISES_INVENTORY_SALE.value, seed, ssp)]
        snippet = [e.snippet for e in edge.evidence
                   if e.evidence_type is EvidenceType.SELLERS_JSON_CONFIRMATION][0]
        assert "pub-1" in snippet and "ssp.example" in snippet

    def test_a_party_the_file_omits_is_reported_but_not_written_down(self, corpus):
        graph = SharingGraph()
        seed = "target::absent"
        graph.add_node(Node(id=seed, type=NodeType.TARGET,
                            display_name="absent.example"))
        graph.add_node(Node(id="entity::ssp", type=NodeType.ENTITY,
                            display_name="SSP Example", primary_domain="ssp.example"))
        graph.add_edge(EdgeKind.AUTHORISES_INVENTORY_SALE, seed, "entity::ssp",
                       Evidence(source=EvidenceSource.REGISTRY,
                                evidence_type=EvidenceType.ADS_TXT_AUTHORISATION))
        report = reconcile_sellers(graph, corpus, domains={seed: "absent.example"})
        assert report[0]["status"] == NO_SELLER_ID
        edge = graph.edges[
            (EdgeKind.AUTHORISES_INVENTORY_SALE.value, seed, "entity::ssp")]
        assert len(edge.evidence) == 1

    def test_exact_seller_id_does_not_depend_on_a_domain_fallback(self, corpus):
        graph, seed, ssp = _pair_graph()
        report = reconcile_sellers(graph, corpus, domains={seed: "unknown.example"})
        assert report[0]["status"] == CONFIRMED
        assert report[0]["basis"] == BY_SELLER_ID

    def test_a_receiving_party_with_no_sellers_json_is_told_apart_from_one_uncollected(
            self, corpus):
        graph = SharingGraph()
        seed = "target::site"
        graph.add_node(Node(id=seed, type=NodeType.TARGET, display_name="site"))
        for domain, node_id in (("bare.example", "entity::bare"),
                                ("never.example", "entity::never")):
            graph.add_node(Node(id=node_id, type=NodeType.ENTITY,
                                display_name=domain, primary_domain=domain))
            graph.add_edge(EdgeKind.AUTHORISES_INVENTORY_SALE, seed, node_id,
                           Evidence(source=EvidenceSource.REGISTRY,
                                    evidence_type=EvidenceType.ADS_TXT_AUTHORISATION))
        report = reconcile_sellers(graph, corpus, domains={seed: "site.example"})
        by_dst = {row["dst"]: row["status"] for row in report}
        assert by_dst["entity::bare"] == NO_SELLERS_JSON
        assert by_dst["entity::never"] == NOT_COLLECTED

    def test_a_file_is_never_used_to_confirm_itself(self, corpus):
        """The edge sellers.json already produced is not re-read as a check."""
        graph, seed, ssp = _pair_graph()
        graph.add_edge(
            EdgeKind.LISTS_VENDOR, seed, ssp,
            Evidence(source=EvidenceSource.REGISTRY,
                     evidence_type=EvidenceType.SELLERS_JSON_PARTICIPATION),
        )
        report = reconcile_sellers(graph, corpus, domains={seed: "site.example"})
        assert [row["kind"] for row in report] == \
            [EdgeKind.AUTHORISES_INVENTORY_SALE.value]

    def test_running_the_check_twice_writes_one_confirmation(self, corpus):
        graph, seed, ssp = _pair_graph()
        reconcile_sellers(graph, corpus, domains={seed: "site.example"})
        reconcile_sellers(graph, corpus, domains={seed: "site.example"})
        edge = graph.edges[(EdgeKind.AUTHORISES_INVENTORY_SALE.value, seed, ssp)]
        assert sum(1 for e in edge.evidence
                   if e.evidence_type is EvidenceType.SELLERS_JSON_CONFIRMATION) == 1

    def test_the_summary_counts_each_status(self):
        report = [{"status": CONFIRMED}, {"status": CONFIRMED}, {"status": ABSENT}]
        summary = reconciliation_summary(report)
        assert summary == {"checked": 3, "counts": {CONFIRMED: 2, ABSENT: 1}}

    def test_a_direct_account_must_be_a_publisher(self, corpus):
        graph, seed, ssp = _pair_graph()
        edge = graph.edges[(EdgeKind.AUTHORISES_INVENTORY_SALE.value, seed, ssp)]
        edge.evidence[0].publisher_ids = ["int-2"]
        report = reconcile_sellers(graph, corpus, domains={seed: "site.example"})
        assert report[0]["status"] == RELATIONSHIP_MISMATCH
        assert len(edge.evidence) == 1

    def test_missing_account_cannot_use_a_domain_fallback(self, corpus):
        graph, seed, ssp = _pair_graph()
        edge = graph.edges[(EdgeKind.AUTHORISES_INVENTORY_SALE.value, seed, ssp)]
        edge.evidence[0].publisher_ids = []
        report = reconcile_sellers(graph, corpus, domains={seed: "unknown.example"})
        assert report[0]["status"] == NO_SELLER_ID


class TestCorroboratingOneTarget:
    """What the evidence overview reads: a site's own authorisations, marked
    with what the ad system's record says about them."""

    def _ads_txt_relation(self, domain="ssp.example", qualifier="direct",
                          accounts=("pub-1",)):
        return {
            "entity": domain, "party": "third", "sources": ["ads_txt"],
            "qualifier": qualifier, "publisher_ids": list(accounts),
            "data_type": "advertising bid data", "purposes": ["advertising"],
        }

    def test_an_authorisation_the_ad_system_names_is_marked_confirmed(self, corpus):
        rels = [self._ads_txt_relation()]
        corroborate_relations(rels, corpus, site_domain="site.example",
                              lookups=0)
        assert rels[0]["corroboration"] == CONFIRMED
        assert rels[0]["seller_id"] == "pub-1"
        assert rels[0]["seller_type"] == "publisher"
        assert rels[0]["corroborated_by"] == "ssp.example"

    def test_an_authorisation_it_omits_keeps_its_place(self, corpus):
        rels = [self._ads_txt_relation(accounts=())]
        checked = corroborate_relations(rels, corpus, site_domain="absent.example",
                                        site_name="Absent", lookups=0)
        assert checked == rels
        assert rels[0]["corroboration"] == NO_SELLER_ID
        assert "seller_id" not in rels[0]

    def test_a_relation_of_another_kind_is_left_alone(self, corpus):
        rels = [{"entity": "some analytics", "sources": ["policy"]}]
        assert corroborate_relations(rels, corpus, site_domain="site.example",
                                     lookups=0) == []
        assert "corroboration" not in rels[0]

    def test_nothing_is_fetched_when_the_budget_is_spent(self, corpus, monkeypatch):
        def refuse(*args, **kwargs):
            raise AssertionError("no lookup should be made")

        monkeypatch.setattr("tpd.reconcile._fetch_sellers", refuse)
        rels = [self._ads_txt_relation(domain="never.example")]
        corroborate_relations(rels, corpus, site_domain="site.example", lookups=0)
        assert rels[0]["corroboration"] == NOT_COLLECTED

    def test_the_budget_is_spent_on_direct_relationships_first(self, corpus):
        asked: list[list[str]] = []

        def record(store, corpus_, domains, delay=0.0):
            asked.append(list(domains))

        import tpd.reconcile as mod
        held, mod._fetch_sellers = mod._fetch_sellers, record
        try:
            rels = [
                self._ads_txt_relation(domain="resold.example", qualifier="reseller"),
                self._ads_txt_relation(domain="direct.example", qualifier="direct"),
            ]
            corroborate_relations(rels, corpus, site_domain="site.example",
                                  lookups=1)
        finally:
            mod._fetch_sellers = held
        assert asked == [["direct.example"]]

    def test_the_summary_counts_what_was_reached(self, corpus):
        rels = [self._ads_txt_relation(),
                self._ads_txt_relation(domain="never.example", accounts=())]
        checked = corroborate_relations(rels, corpus, site_domain="site.example",
                                        lookups=0)
        assert corroboration_summary(checked) == {
            "checked": 2, "counts": {CONFIRMED: 1, NOT_COLLECTED: 1}}


class TestForgettingMissingDomains:
    def test_a_domain_collected_later_is_read_on_the_next_pass(self, tmp_path):
        store = Corpus(tmp_path)
        _write_target(store, "site.example", {"ads_txt": ADS_TXT})
        registry = RegistryStore(store)
        assert registry.sellers("ssp.example") is None

        _write_target(store, "ssp.example", {"sellers_json": json.dumps(SELLERS)})
        assert registry.sellers("ssp.example") is None  # still the held answer
        registry.forget_missing()
        assert registry.sellers("ssp.example").disclosed == 2

    def test_what_was_parsed_is_kept(self, corpus):
        registry = RegistryStore(corpus)
        record = registry.sellers("ssp.example")
        registry.forget_missing()
        assert registry.sellers("ssp.example") is record


class TestPublishingHost:
    def test_the_convention_is_the_domain_the_row_names(self):
        assert sellers_json_host("pubmatic.example") == "pubmatic.example"

    def test_a_system_that_publishes_elsewhere_is_looked_for_there(self):
        """An ads.txt row names google.com; the record is not served there."""
        assert sellers_json_host("google.com") == "realtimebidding.google.com"
        assert sellers_json_host("www.google.com") == "realtimebidding.google.com"


class TestLookupsCoverCollectedDomains:
    """A domain in the corpus can still be missing the file."""

    def test_a_collected_domain_without_the_file_is_still_looked_up(self, corpus):
        asked: list[list[str]] = []

        import tpd.reconcile as mod
        held = mod._fetch_sellers
        mod._fetch_sellers = lambda store, c, domains, delay=0.0: asked.append(
            list(domains))
        try:
            rels = [{"entity": "bare.example", "sources": ["ads_txt"],
                     "qualifier": "direct", "publisher_ids": ["x"]}]
            corroborate_relations(rels, corpus, site_domain="site.example",
                                  lookups=5)
        finally:
            mod._fetch_sellers = held
        assert asked == [["bare.example"]]
        # Having asked and found nothing is not the same as never asking.
        assert rels[0]["corroboration"] == NO_SELLERS_JSON

    def test_a_domain_left_unasked_says_so(self, corpus):
        rels = [{"entity": "never.example", "sources": ["ads_txt"],
                 "qualifier": "direct", "publisher_ids": ["x"]}]
        corroborate_relations(rels, corpus, site_domain="site.example", lookups=0)
        assert rels[0]["corroboration"] == NOT_COLLECTED


class TestTrackerList:
    """Observed traffic checked against a list nobody in the arrangement keeps."""

    def test_an_advertising_domain_is_recognised(self):
        status, basis, categories = tracker_listing("doubleclick.net")
        assert status == CONFIRMED
        assert basis == BY_TRACKER_RADAR
        assert "Advertising" in categories

    def test_a_content_delivery_host_is_listed_but_not_as_a_tracker(self):
        status, _, categories = tracker_listing("fonts.googleapis.com")
        assert status == KNOWN_NOT_TRACKING
        assert "CDN" in categories

    def test_a_www_prefix_does_not_hide_a_domain(self):
        assert tracker_listing("www.doubleclick.net")[0] == CONFIRMED

    def test_a_domain_no_list_holds_is_reported_as_such(self):
        status, basis, categories = tracker_listing("not-a-real-domain-xyz.test")
        assert (status, basis, categories) == (UNLISTED, "", [])

    def _traffic_graph(self, domain: str) -> SharingGraph:
        g = SharingGraph()
        g.add_node(Node(id="target::site", type=NodeType.TARGET,
                        display_name="site.example"))
        g.add_node(Node(id=f"domain::{domain}", type=NodeType.DOMAIN,
                        display_name=domain))
        g.add_edge(EdgeKind.CONTACTS_DOMAIN, "target::site", f"domain::{domain}",
                   Evidence(source=EvidenceSource.TRAFFIC,
                            evidence_type=EvidenceType.NETWORK_CONTACT))
        return g

    def test_a_recognised_contact_gains_evidence_for_it(self):
        g = self._traffic_graph("doubleclick.net")
        (row,) = confirm_tracker_domains(g)
        assert row["status"] == CONFIRMED
        edge = g.edges[(EdgeKind.CONTACTS_DOMAIN.value, "target::site",
                        "domain::doubleclick.net")]
        assert any(e.evidence_type is EvidenceType.TRACKER_LIST_CONFIRMATION
                   for e in edge.evidence)

    def test_an_unrecognised_contact_is_reported_but_not_written_down(self):
        g = self._traffic_graph("fonts.googleapis.com")
        (row,) = confirm_tracker_domains(g)
        assert row["status"] == KNOWN_NOT_TRACKING
        edge = next(iter(g.edges.values()))
        assert len(edge.evidence) == 1

    def test_the_snippet_says_which_list_and_what_it_said(self):
        g = self._traffic_graph("doubleclick.net")
        confirm_tracker_domains(g)
        edge = next(iter(g.edges.values()))
        snippet = [e.snippet for e in edge.evidence
                   if e.evidence_type is EvidenceType.TRACKER_LIST_CONFIRMATION][0]
        assert "tracker radar" in snippet and "Advertising" in snippet

    def test_running_the_check_twice_writes_one_confirmation(self):
        g = self._traffic_graph("doubleclick.net")
        confirm_tracker_domains(g)
        assert confirm_tracker_domains(g) == []
        edge = next(iter(g.edges.values()))
        assert sum(1 for e in edge.evidence
                   if e.evidence_type is EvidenceType.TRACKER_LIST_CONFIRMATION) == 1

    def test_only_observed_contacts_are_checked(self):
        g = self._traffic_graph("doubleclick.net")
        g.add_node(Node(id="entity::x", type=NodeType.ENTITY, display_name="X"))
        g.add_edge(EdgeKind.DISCLOSES_RELATION_WITH, "target::site", "entity::x",
                   Evidence(source=EvidenceSource.POLICY,
                            evidence_type=EvidenceType.POLICY_RELATION))
        assert [r["dst"] for r in confirm_tracker_domains(g)] \
            == ["domain::doubleclick.net"]

    def test_the_summary_counts_each_status(self):
        report = [{"status": CONFIRMED}, {"status": UNLISTED}]
        assert tracker_summary(report) == {
            "checked": 2, "counts": {CONFIRMED: 1, UNLISTED: 1}}
