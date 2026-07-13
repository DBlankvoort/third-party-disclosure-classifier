"""Validate tpd.classify.structured_relations."""

from __future__ import annotations

import json

from tpd.classify.structured_relations import (
    purposes_from_text,
    registry_relations,
    table_relations,
)


# --------------------------------------------------------------------------- #
# purposes_from_text
# --------------------------------------------------------------------------- #
def test_purposes_from_text():
    assert purposes_from_text("Used for targeted advertising") == ["advertising"]
    assert purposes_from_text("Traffic measurement and statistics") == ["analytics"]
    assert purposes_from_text("fraud prevention and security") == ["security"]
    assert purposes_from_text("strictly necessary session cookie") == ["services"]
    assert purposes_from_text("") == []
    assert purposes_from_text("ads and measurement") == ["advertising", "analytics"]


# --------------------------------------------------------------------------- #
# table_relations
# --------------------------------------------------------------------------- #
def _table(headers: list[str], rows: list[list[str]]) -> str:
    head = "".join(f"<th>{h}</th>" for h in headers)
    body = "".join(
        "<tr>" + "".join(f"<td>{c}</td>" for c in r) + "</tr>" for r in rows
    )
    return f"<html><body><table><tr>{head}</tr>{body}</table></body></html>"


class TestCookieTables:
    HTML = _table(
        ["Cookie name", "Provider", "Purpose", "Duration"],
        [
            ["_ga", "Google Analytics", "Statistics on site usage", "2 years"],
            ["_fbp", "Facebook", "Advertising", "3 months"],
            ["session", "This website", "Login session", "Session"],
        ],
    )

    def test_extracts_third_party_rows(self):
        rels = {r["entity"]: r for r in table_relations(self.HTML)}
        assert set(rels) == {"google analytics", "facebook"}

    def test_cookie_semantics(self):
        rels = {r["entity"]: r for r in table_relations(self.HTML)}
        ga = rels["google analytics"]
        assert ga["data_type"] == "cookie / device identifiers"
        assert ga["action"] == "collect"
        assert ga["purposes"] == ["analytics"]
        assert ga["sources"] == ["cookie_table"]
        assert rels["facebook"]["purposes"] == ["advertising"]

    def test_first_party_cell_is_dropped(self):
        rels = table_relations(self.HTML)
        assert all(r["entity"] != "this website" for r in rels)

    def test_first_party_tokens_filter(self):
        html = _table(
            ["Provider", "Purpose", "Duration of cookie"],
            [["Acme Analytics", "stats", "1y"], ["Hotjar", "heatmaps", "1y"]],
        )
        rels = table_relations(html, first_party={"acme"})
        assert [r["entity"] for r in rels] == ["hotjar"]

    def test_duplicate_rows_merge_purposes(self):
        html = _table(
            ["Provider", "Purpose", "Duration"],
            [["Google", "advertising", "1y"], ["Google", "statistics", "1y"]],
        )
        rels = table_relations(html)
        assert len(rels) == 1
        assert rels[0]["purposes"] == ["advertising", "analytics"]


class TestVendorTables:
    def test_subprocessor_role_defaults(self):
        html = _table(
            ["Sub-processor", "Location"],
            [["Amazon Web Services", "USA"], ["Twilio", "USA"]],
        )
        rels = {r["entity"]: r for r in table_relations(html, role="subprocessor_list")}
        assert set(rels) == {"amazon web services", "twilio"}
        aws = rels["amazon web services"]
        assert aws["data_type"] == "personal data"
        assert aws["action"] == "be_shared"
        assert aws["purposes"] == ["services"]
        assert aws["sources"] == ["vendor_table"]

    def test_table_without_entity_column_is_ignored(self):
        html = _table(["Question", "Answer"], [["Why?", "Because."]])
        assert table_relations(html) == []
        html = _table(["Product", "Revenue"], [["Initech", "$1M"]])
        assert table_relations(html) == []

    def test_company_column_alone_makes_a_vendor_table(self):
        html = _table(["Company", "Revenue"], [["Initech", "$1M"]])
        (rel,) = table_relations(html)
        assert rel["entity"] == "initech"
        assert rel["action"] == "be_shared"

    def test_leading_dot_domain_is_cleaned(self):
        html = _table(
            ["Host", "Purpose", "Expiry"],
            [[".doubleclick.net", "advertising", "1y"]],
        )
        rels = table_relations(html)
        assert rels[0]["entity"] == "doubleclick.net"

    def test_empty_html(self):
        assert table_relations("") == []


# --------------------------------------------------------------------------- #
# registry_relations
# --------------------------------------------------------------------------- #
class TestAdsTxt:
    RAW = (
        "google.com, pub-123, DIRECT, f08c47\n"
        "appnexus.com, 456, RESELLER\n"
        "google.com, pub-789, RESELLER\n"
    )

    def test_one_relation_per_domain(self):
        rels = {r["entity"]: r for r in registry_relations(self.RAW)}
        assert set(rels) == {"google.com", "appnexus.com"}

    def test_direct_wins_over_reseller(self):
        raw = "x.com, 1, RESELLER\nx.com, 2, DIRECT\n"
        (rel,) = registry_relations(raw)
        assert rel["qualifier"] == "direct"

    def test_semantics(self):
        rels = {r["entity"]: r for r in registry_relations(self.RAW)}
        g = rels["google.com"]
        assert g["action"] == "be_sold"
        assert g["purposes"] == ["advertising"]
        assert g["sources"] == ["ads_txt"]


class TestSellersJson:
    def test_confidential_sellers_skipped(self):
        raw = json.dumps({"sellers": [
            {"seller_id": "1", "name": "Open Ads"},
            {"seller_id": "2", "name": "Hidden", "is_confidential": 1},
        ]})
        rels = registry_relations(raw)
        assert [r["entity"] for r in rels] == ["open ads"]
        assert rels[0]["action"] == "be_shared"

    def test_invalid_json(self):
        assert registry_relations('{"sellers": not json') == []


class TestGvl:
    def test_tcf_purposes_mapped(self):
        raw = json.dumps({
            "vendorListVersion": 1,
            "vendors": {"1": {"name": "Vendor One", "purposes": [1, 2, 8]}},
        })
        (rel,) = registry_relations(raw)
        assert rel["entity"] == "vendor one"
        assert rel["purposes"] == ["advertising", "analytics", "services"]
        assert rel["data_type"] == "cookie / device identifiers"

    def test_prose_yields_nothing(self):
        assert registry_relations("We value your privacy.") == []
