"""Validate tpd.traffic and tpd.entities."""

from __future__ import annotations

from tpd.entities import (
    canonical_key,
    clean_company_name,
    entity_for_domain,
    registrable_domain,
)
from tpd.traffic import observed_hosts, traffic_relations


class TestCleanCompanyName:
    def test_strips_corporate_forms(self):
        for raw in ("Google LLC", "Google, Inc.", "Google Limited"):
            assert clean_company_name(raw) == "Google"

    def test_strips_region_markers(self):
        assert clean_company_name("Acme Ltd (EU)") == "Acme"

    def test_leaves_plain_names(self):
        assert clean_company_name("Skimlinks") == "Skimlinks"

    def test_never_empties_a_name(self):
        assert clean_company_name("Ltd") == "Ltd"


class TestCanonicalKey:
    def test_corporate_variants_share_a_key(self):
        assert canonical_key("Google LLC") == canonical_key("Google, Inc.")

    def test_distinct_names_differ(self):
        assert canonical_key("Criteo") != canonical_key("Taboola")


class TestRegistrableDomain:
    def test_strips_subdomains(self):
        assert registrable_domain("securepubads.g.doubleclick.net") == "doubleclick.net"

    def test_handles_multipart_suffixes(self):
        assert registrable_domain("www.theguardian.co.uk") == "theguardian.co.uk"

    def test_passes_through_addresses(self):
        assert registrable_domain("127.0.0.1") == "127.0.0.1"


class TestEntityForDomain:
    def test_curated_mapping_wins(self):
        assert entity_for_domain("securepubads.g.doubleclick.net") == ("Google", "domain_map")

    def test_unknown_domain_falls_back_to_its_label(self):
        name, basis = entity_for_domain("tracker.example-vendor.com")
        assert name == "Example-Vendor"
        assert basis == "domain"


class TestObservedHosts:
    ORIGIN = "https://www.theguardian.com"
    REQUESTS = [
        {"url": "https://www.theguardian.com/assets/app.js", "type": "script"},
        {"url": "https://securepubads.g.doubleclick.net/tag.js", "type": "script"},
        {"url": "https://pagead2.googlesyndication.com/px.gif", "type": "image"},
        {"url": "https://widgets.skimresources.com/s.js", "type": "script"},
    ]

    def test_first_party_requests_are_excluded(self):
        seen = {o["entity"] for o in observed_hosts(self.REQUESTS, self.ORIGIN)}
        assert "Theguardian" not in seen

    def test_domains_resolve_to_owning_organisations(self):
        seen = {o["entity"] for o in observed_hosts(self.REQUESTS, self.ORIGIN)}
        assert seen == {"Google", "Skimlinks"}

    def test_hosts_of_one_owner_merge(self):
        google = next(o for o in observed_hosts(self.REQUESTS, self.ORIGIN)
                      if o["entity"] == "Google")
        assert google["domains"] == ["doubleclick.net", "googlesyndication.com"]
        assert google["requests"] == 2

    def test_shared_infrastructure_is_excluded_by_default(self):
        reqs = [{"url": "https://cdn.jsdelivr.net/x.js", "type": "script"}]
        assert observed_hosts(reqs, self.ORIGIN) == []
        assert observed_hosts(reqs, self.ORIGIN, include_infrastructure=True)

    def test_first_party_tokens_are_honoured(self):
        reqs = [{"url": "https://static.guim.co.uk/x.js", "type": "script"}]
        assert observed_hosts(reqs, self.ORIGIN, first_party={"guim"}) == []


class TestTrafficRelations:
    def test_relations_carry_the_traffic_source(self):
        rels = traffic_relations(
            [{"url": "https://securepubads.g.doubleclick.net/t.js", "type": "script"}],
            "https://example.com",
        )
        assert [r["sources"] for r in rels] == [["traffic"]]
        assert rels[0]["entity"] == "google"
        assert rels[0]["direction"] == "downstream"

    def test_no_requests_yields_no_relations(self):
        assert traffic_relations([], "https://example.com") == []
