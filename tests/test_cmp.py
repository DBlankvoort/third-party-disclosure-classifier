"""Validate the parties read out of a captured consent dialog."""

from __future__ import annotations

from tpd.cmp import cmp_relations, cmp_vendors

TCF_PAYLOAD = {
    "source": "tcf",
    "cmp": "cmpId 6",
    "vendors": [
        {"id": 755, "name": "Google Advertising Products", "purposes": [1, 3, 4]},
        {"id": 52, "name": "The Rubicon Project, Inc.", "purposes": [2, 9]},
        {"id": 1, "name": "Exponential Interactive, Inc d/b/a VDX.tv", "purposes": [1]},
    ],
}

DOM_PAYLOAD = {
    "source": "dom",
    "vendors": [
        {"name": "Criteo SA"},
        {"name": "criteo.com"},
        {"name": "Strictly Necessary"},
        {"name": ""},
    ],
}


class TestVendors:
    def test_tcf_purposes_reach_the_purpose_vocabulary(self):
        by_name = {v["entity"]: v for v in cmp_vendors(TCF_PAYLOAD)}
        assert by_name["The Rubicon Project"]["purposes"] == ["advertising", "analytics"]

    def test_a_vendor_resolves_to_its_display_form(self):
        names = [v["entity"] for v in cmp_vendors(DOM_PAYLOAD)]
        assert names.count("Criteo") == 1

    def test_the_surface_the_dialog_rendered_is_kept(self):
        surfaces = {v["surface"] for v in cmp_vendors(DOM_PAYLOAD)}
        assert surfaces & {"Criteo SA", "criteo.com"}

    def test_consent_categories_are_not_parties(self):
        assert "Strictly Necessary" not in {v["entity"] for v in cmp_vendors(DOM_PAYLOAD)}

    def test_the_publisher_itself_is_excluded(self):
        vendors = cmp_vendors(TCF_PAYLOAD, first_party={"google"})
        assert all("Google" not in v["entity"] for v in vendors)

    def test_an_absent_capture_names_nobody(self):
        assert cmp_vendors(None) == []
        assert cmp_vendors({"vendors": "not a list"}) == []


class TestRelations:
    def test_relations_carry_the_dialog_as_their_source(self):
        rels = cmp_relations(TCF_PAYLOAD)
        assert {r["sources"][0] for r in rels} == {"cmp"}
        assert all(r["data_type"] == "cookie / device identifiers" for r in rels)

    def test_the_capture_route_qualifies_the_claim(self):
        assert {r["qualifier"] for r in cmp_relations(DOM_PAYLOAD)} == {"dom"}
        assert {r["qualifier"] for r in cmp_relations(TCF_PAYLOAD)} == {"tcf"}

    def test_relations_are_third_party_and_positive(self):
        rels = cmp_relations(TCF_PAYLOAD)
        assert all(r["party"] == "third" and not r["negative"] for r in rels)
