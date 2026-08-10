"""Validate the bundled reference knowledge bases."""

from __future__ import annotations

from tpd.entities import (
    country_for,
    is_investor_parent,
    kb_domains,
    known_to_kb,
    resolve_entity_domain,
    resolve_name,
)
from tpd.kb import blocklist, gvl, jurisdiction, tracker_radar


class TestTrackerRadar:
    def test_the_index_is_bundled(self):
        index = tracker_radar.index()
        assert len(index.domains) > 20_000
        assert len(index.entities) > 10_000

    def test_a_tracker_domain_resolves_to_its_operator(self):
        hit = tracker_radar.index().lookup_domain("rubiconproject.com")
        assert hit is not None and hit.entity_name

    def test_an_organisations_domains_come_from_inverting_the_table(self):
        assert "rubiconproject.com" in kb_domains("Magnite")


class TestGlobalVendorList:
    def test_the_list_is_bundled(self):
        assert len(gvl.vendors()) > 1_000

    def test_a_vendor_carries_its_policy_and_purposes(self):
        vendor = gvl.by_id()[1]
        assert vendor.name
        assert vendor.domain
        assert vendor.purposes

    def test_the_edition_is_recorded(self):
        assert gvl.version()["vendor_list_version"]


class TestJurisdiction:
    def test_informal_country_names_normalise_to_iso_codes(self):
        assert jurisdiction.normalise_country("United Kingdom") == "gb"
        assert jurisdiction.normalise_country("uk") == "gb"
        assert jurisdiction.normalise_country("USA") == "us"

    def test_an_unrecognised_country_resolves_to_nothing(self):
        assert jurisdiction.normalise_country("somewhere") == ""

    def test_adequacy_follows_the_commissions_list(self):
        assert jurisdiction.is_adequate("de")
        assert not jurisdiction.is_adequate("us")

    def test_the_override_table_supplies_what_the_registers_omit(self):
        # Neither Tracker Radar nor the GVL carries a country on any record.
        assert country_for("Criteo") == "fr"
        assert country_for("Adjust") == "de"


class TestBlocklist:
    def test_investor_parents_are_listed(self):
        assert blocklist.investor_parents()

    def test_a_holding_company_is_not_a_recipient(self):
        assert is_investor_parent("Vista Equity Partners")
        assert is_investor_parent("Thoma Bravo")

    def test_an_operator_is_not_blocked(self):
        assert not is_investor_parent("Criteo")


class TestResolutionCascade:
    def test_a_company_held_under_several_identities_merges(self):
        keys = {resolve_name(n).key
                for n in ("Telaria", "Tremorhub", "Magnite, Inc.",
                          "rubiconproject.com")}
        assert keys == {"magnite"}

    def test_a_vendor_only_a_register_knows_is_recognised(self):
        for name in ("Demandbase", "MaxMind", "SalesLoft"):
            assert known_to_kb(name), name
            assert resolve_name(name).grounded

    def test_a_register_supplies_a_site_to_crawl(self):
        domain, basis = resolve_entity_domain("Demandbase")
        assert domain == "demandbase.com"
        assert basis == "tracker_radar"

    def test_a_gvl_vendor_resolves_through_its_policy_address(self):
        resolved = resolve_name("Captify Technologies Limited")
        assert resolved.basis == "tcf_gvl"
        assert resolved.domain == "captifytechnologies.com"

    def test_a_surface_no_register_knows_is_not_grounded(self):
        assert not resolve_name("Wickford Analytics").grounded
