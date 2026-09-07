"""Validate the measurement infrastructure for requirements 21 and 24."""

from __future__ import annotations

import csv

from tpd.entities import load_entity_domains
from tpd.sharing_graph import (
    EdgeKind,
    Evidence,
    EvidenceSource,
    Node,
    NodeType,
    SharingGraph,
    add_target,
    entity_node_id,
    expand_node,
    sharing_chains,
    target_node_id,
)

from tpd_eval import (
    arrangement_coverage,
    arrangement_id,
    chain_verification,
    detected_arrangements,
    load_chain_gold,
    load_coverage_gold,
    sample_targets,
    write_chain_sheet,
    write_coverage_sheet,
    write_entity_resolution_sheet,
)
from tpd_eval.metrics import TARGET_VERIFIED_CHAINS


def _fill(path, column, value):
    """Stand in for a labeller filling one column of a sheet."""
    with open(path, newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        fields = reader.fieldnames
        rows = [{**row, column: value} for row in reader]
    with open(path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)


def _rel(entity, **kw):
    base = {
        "entity": entity, "party": "third", "unspecified": False,
        "data_type": "personal data", "action": "be_shared", "negative": False,
        "direction": "downstream", "purposes": [], "examples": [],
        "qualifier": "", "sources": ["policy"], "text": "", "doc_ids": [],
    }
    base.update(kw)
    return base


def _chain_graph():
    """A four-organisation chain plus a two-organisation branch."""
    g = SharingGraph()
    add_target(g, "site", "site.example", [_rel("Bravo"), _rel("Zulu")])
    expand_node(g, entity_node_id("Bravo"), [_rel("Charlie")], hop=1)
    expand_node(g, entity_node_id("Charlie"), [_rel("Delta")], hop=2)
    return g


class TestChainEnumeration:
    def test_a_four_organisation_path_is_found(self):
        chains = sharing_chains(_chain_graph(), parties=4)
        assert [c.parties for c in chains] == [[
            target_node_id("site"), entity_node_id("Bravo"),
            entity_node_id("Charlie"), entity_node_id("Delta"),
        ]]

    def test_three_hops_separate_the_four_parties(self):
        (chain,) = sharing_chains(_chain_graph(), parties=4)
        assert len(chain.hops) == 3

    def test_a_shorter_branch_yields_no_chain(self):
        chains = sharing_chains(_chain_graph(), parties=4)
        assert not any(entity_node_id("Zulu") in c.parties for c in chains)

    def test_denied_sharing_does_not_extend_a_chain(self):
        g = _chain_graph()
        expand_node(g, entity_node_id("Delta"),
                    [_rel("Echo", negative=True)], hop=3)
        assert not sharing_chains(g, parties=5)

    def test_a_generic_category_breaks_the_chain(self):
        # "Our advertising partners" identifies nobody, so a path through it
        # encodes no specific arrangement.
        g = SharingGraph()
        add_target(g, "site", "site.example", [_rel("partners", unspecified=True)])
        expand_node(g, "generic::partners", [_rel("Charlie")], hop=1)
        expand_node(g, entity_node_id("Charlie"), [_rel("Delta")], hop=2)
        assert not sharing_chains(g, parties=4)

    def test_a_contact_does_not_count_as_a_personal_data_hop(self):
        g = SharingGraph()
        add_target(g, "site", "site.example", [], observed=[
            {"entity": "Google", "basis": "domain_map",
             "domains": ["doubleclick.net"], "types": ["script"], "requests": 1},
        ])
        assert sharing_chains(g, parties=2) == []

    def test_the_budget_is_shared_across_starting_parties(self):
        # A hub sitting on more chains than the limit would otherwise consume
        # the whole budget and hide every other part of the graph.
        g = SharingGraph()
        add_target(g, "hub", "hub.example", [_rel(f"H{i}") for i in range(40)])
        add_target(g, "other", "other.example", [_rel("Solo")])
        chains = sharing_chains(g, parties=2, limit=10)
        assert any(entity_node_id("Solo") in c.parties for c in chains)

    def test_traffic_evidence_is_distinguished_from_a_written_disclosure(self):
        g = SharingGraph()
        add_target(g, "site", "site.example",
                   [_rel("Bravo", sources=["traffic"])])
        (chain,) = sharing_chains(g, parties=2)
        assert chain.traffic_only_hops == 1
        assert not chain.fully_disclosed


class TestChainVerification:
    def _gold(self, chains, n):
        return {c.id: True for c in chains[:n]}

    def test_five_verified_chains_pass(self):
        g = SharingGraph()
        for i in range(6):
            add_target(g, f"s{i}", f"s{i}.example", [_rel(f"B{i}")])
            expand_node(g, entity_node_id(f"B{i}"), [_rel(f"C{i}")], hop=1)
            expand_node(g, entity_node_id(f"C{i}"), [_rel(f"D{i}")], hop=2)
        chains = sharing_chains(g, parties=4)
        assert len(chains) >= TARGET_VERIFIED_CHAINS
        assert chain_verification(chains, self._gold(chains, 5)).passed

    def test_the_report_names_the_chain_length_it_scored(self):
        chains = sharing_chains(_chain_graph(), parties=2)
        assert chain_verification(chains, {}).parties == 2

    def test_unverified_chains_do_not_count(self):
        chains = sharing_chains(_chain_graph(), parties=4)
        report = chain_verification(chains, {c.id: False for c in chains})
        assert report.n_rejected == 1
        assert not report.passed

    def test_gold_for_a_chain_no_longer_extracted_is_reported_stale(self):
        chains = sharing_chains(_chain_graph(), parties=4)
        report = chain_verification(chains, {"gone::chain": True})
        assert report.n_stale == 1
        assert report.n_reviewed == 0

    def test_chains_resting_on_traffic_alone_are_counted_apart(self):
        g = SharingGraph()
        add_target(g, "site", "site.example", [_rel("Bravo", sources=["traffic"])])
        chains = sharing_chains(g, parties=2)
        report = chain_verification(chains, {chains[0].id: True})
        assert report.n_verified == 1
        assert report.n_verified_fully_disclosed == 0


class TestChainSheet:
    def test_sheet_carries_the_evidence_behind_every_hop(self, tmp_path):
        g = _chain_graph()
        chains = sharing_chains(g, parties=4)
        path = tmp_path / "chain_labels.csv"
        assert write_chain_sheet(chains, g, path) == 1
        text = path.read_text()
        assert "Bravo -> Charlie" in text
        assert "policy" in text

    def test_prior_verdicts_survive_a_rewrite(self, tmp_path):
        g = _chain_graph()
        chains = sharing_chains(g, parties=4)
        first = tmp_path / "a.csv"
        write_chain_sheet(chains, g, first)
        _fill(first, "gold_verified", "1")
        second = tmp_path / "b.csv"
        write_chain_sheet(chains, g, second, prior_path=first)
        assert load_chain_gold(second) == {chains[0].id: True}


class TestArrangementCoverage:
    def _relations(self):
        return {
            f"t{i}": [_rel("Criteo"), _rel("Google LLC")] for i in range(4)
        }

    def test_an_arrangement_is_one_recipient_of_one_target(self):
        detected = detected_arrangements(self._relations())
        assert len(detected) == 8

    def test_corporate_variants_are_the_same_arrangement(self):
        detected = detected_arrangements({"t": [_rel("Google LLC"), _rel("Google, Inc.")]})
        assert len(detected) == 1

    def test_a_denied_arrangement_is_not_detected(self):
        assert not detected_arrangements({"t": [_rel("Criteo", negative=True)]})

    def test_recall_counts_the_arrangements_a_labeller_added(self, tmp_path):
        gold = {
            arrangement_id("t0", "Criteo"): {
                "arrangement_id": arrangement_id("t0", "Criteo"),
                "target_id": "t0", "entity": "Criteo", "gold": True,
            },
            arrangement_id("t0", "Missed Ltd"): {
                "arrangement_id": arrangement_id("t0", "Missed Ltd"),
                "target_id": "t0", "entity": "Missed Ltd", "gold": True,
            },
        }
        report = arrangement_coverage(gold, {arrangement_id("t0", "Criteo")})
        assert report.recall == 0.5
        assert report.missed == [arrangement_id("t0", "Missed Ltd")]

    def test_a_sample_short_of_four_targets_does_not_pass(self):
        gold = {
            arrangement_id("t0", "Criteo"): {
                "arrangement_id": arrangement_id("t0", "Criteo"),
                "target_id": "t0", "entity": "Criteo", "gold": True,
            },
        }
        report = arrangement_coverage(gold, {arrangement_id("t0", "Criteo")})
        assert report.recall == 1.0
        assert not report.sample_passed
        assert not report.passed

    def test_full_recovery_across_four_targets_passes(self):
        detected = detected_arrangements(self._relations())
        gold = {
            aid: {"arrangement_id": aid, "target_id": rec["target_id"],
                  "entity": rec["entity"], "gold": True}
            for aid, rec in detected.items()
        }
        report = arrangement_coverage(gold, set(detected))
        assert report.n_targets == 4
        assert report.passed

    def test_a_rejected_detection_is_reported_but_not_scored(self):
        aid = arrangement_id("t0", "Not A Party")
        gold = {aid: {"arrangement_id": aid, "target_id": "t0",
                      "entity": "Not A Party", "gold": False}}
        report = arrangement_coverage(gold, {aid})
        assert report.n_false == 1
        assert report.n_gold == 0


class TestCoverageSheet:
    def test_the_sample_is_four_targets_and_reproducible(self):
        ids = [f"t{i}" for i in range(20)]
        assert len(sample_targets(ids)) == 4
        assert sample_targets(ids) == sample_targets(ids)

    def test_a_smaller_corpus_is_taken_whole(self):
        assert sample_targets(["a", "b"]) == ["a", "b"]

    def test_only_the_sampled_targets_are_written(self, tmp_path):
        relations = {f"t{i}": [_rel("Criteo")] for i in range(6)}
        path = tmp_path / "coverage_labels.csv"
        assert write_coverage_sheet(relations, path, ["t0", "t3"]) == 2
        assert "t1" not in path.read_text()

    def test_a_hand_added_arrangement_survives_a_rewrite(self, tmp_path):
        relations = {"t0": [_rel("Criteo")]}
        first = tmp_path / "a.csv"
        write_coverage_sheet(relations, first, ["t0"])
        # The labeller records a recipient the analysis never proposed.
        with open(first, "a", encoding="utf-8") as f:
            f.write("2,t0,Missed Ltd,t0::missed,0,,,,1,by hand\r\n")
        second = tmp_path / "b.csv"
        write_coverage_sheet(relations, second, ["t0"], prior_path=first)
        gold = load_coverage_gold(second)
        assert gold["t0::missed"]["gold"] is True

    def test_gold_is_read_back_from_the_written_sheet(self, tmp_path):
        relations = {"t0": [_rel("Criteo")]}
        path = tmp_path / "coverage_labels.csv"
        write_coverage_sheet(relations, path, ["t0"])
        _fill(path, "gold_arrangement", "1")
        assert load_coverage_gold(path)[arrangement_id("t0", "Criteo")]["gold"] is True


class TestEntityResolutionSheet:
    def test_unreachable_parties_are_listed_first(self, tmp_path):
        g = SharingGraph()
        add_target(g, "site", "site.example", [_rel("Criteo"), _rel("Acme Analytics")])
        path = tmp_path / "entity_resolution.csv"
        assert write_entity_resolution_sheet(g, path) == 2
        rows = path.read_text().splitlines()[1:]
        assert rows[0].startswith("Acme Analytics")

    def test_a_filled_sheet_reads_back_as_an_override(self, tmp_path):
        path = tmp_path / "entity_resolution.csv"
        path.write_text(
            "entity,canonical_key,named_by,resolved_domain,basis,gold_domain,notes\n"
            "Acme Analytics,acmeanalytics,site.example,,unresolved,acme.io,\n",
            encoding="utf-8",
        )
        assert load_entity_domains(path) == {"acmeanalytics": "acme.io"}


class TestExpandNode:
    def test_an_expanded_party_keeps_its_own_node(self):
        g = _chain_graph()
        assert g.nodes[entity_node_id("Bravo")].expanded
        assert g.termination(entity_node_id("Bravo")) == "internal"

    def test_a_party_naming_itself_adds_no_self_edge(self):
        g = SharingGraph()
        add_target(g, "site", "site.example", [_rel("Bravo")])
        expand_node(g, entity_node_id("Bravo"), [_rel("Bravo")], hop=1)
        assert g.termination(entity_node_id("Bravo")) == "terminal"

    def test_an_expansion_records_the_site_it_was_collected_from(self):
        g = SharingGraph()
        add_target(g, "site", "site.example", [_rel("Bravo")])
        expand_node(g, entity_node_id("Bravo"), [], hop=1,
                    primary_domain="bravo.example")
        assert g.nodes[entity_node_id("Bravo")].primary_domain == "bravo.example"


class TestFlowDirection:
    def test_a_supplier_edge_runs_along_the_flow_of_data(self):
        from tpd.tracks import INVENTORY

        g = SharingGraph()
        tid = add_target(g, "exchange", "exchange.example",
                         [_rel("Publisher A", direction="upstream",
                               sources=["sellers_json"], track=INVENTORY)])
        (chain,) = sharing_chains(g, parties=2, track=INVENTORY)
        assert chain.parties == [entity_node_id("Publisher A"), tid]
        assert sharing_chains(g, parties=2) == []

    def test_evidence_survives_into_the_hop(self):
        g = SharingGraph()
        add_target(g, "site", "site.example",
                   [_rel("Bravo", data_type="email address")])
        (chain,) = sharing_chains(g, parties=2)
        assert chain.hops[0].data_types == ["email address"]
        assert chain.hops[0].sources == ["policy"]


class TestUnreachableExclusions:
    def test_a_node_reached_only_by_a_denied_statement_is_no_chain(self):
        g = SharingGraph()
        g.add_node(Node(id="a", type=NodeType.ENTITY, display_name="A"))
        g.add_node(Node(id="b", type=NodeType.ENTITY, display_name="B"))
        g.add_edge(EdgeKind.DISCLOSES_SHARING_WITH, "a", "b",
                   Evidence(source=EvidenceSource.POLICY, negative=True))
        assert not sharing_chains(g, parties=2)
