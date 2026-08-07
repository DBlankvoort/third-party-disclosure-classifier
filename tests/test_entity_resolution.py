"""Validate organisation-to-site resolution."""

from __future__ import annotations

from tpd.entities import (
    domains_for_entity,
    name_prefixes,
    observed_domain_hints,
    resolve_entity_domain,
    resolve_name,
)


class TestCuratedResolution:
    def test_corporate_home_beats_a_tracker_domain(self):
        # Crawling Google at doubleclick.net would collect an ad server rather
        # than a party's disclosures.
        assert resolve_entity_domain("Google") == ("google.com", "entity_map")

    def test_owner_table_resolves_a_party_with_no_curated_home(self):
        domain, basis = resolve_entity_domain("Criteo")
        assert domain == "criteo.com"
        assert basis == "domain_map"

    def test_unknown_name_resolves_to_nothing(self):
        assert resolve_entity_domain("Acme Analytics") == ("", "unresolved")

    def test_shared_platforms_are_not_offered_as_a_home(self):
        assert "cloudfront.net" not in domains_for_entity("Amazon")

    def test_candidates_prefer_the_brand_matching_com_domain(self):
        assert domains_for_entity("The Trade Desk")[0] == "thetradedesk.com"


class TestQualifiedNames:
    def test_corporate_suffix_and_region_are_stripped_before_lookup(self):
        assert resolve_entity_domain("Oracle America, Inc")[0] == "oracle.com"

    def test_parenthetical_product_is_stripped(self):
        assert resolve_entity_domain("Microsoft (Azure)")[0] == "microsoft.com"

    def test_prefix_resolution_is_recorded_as_such(self):
        assert resolve_entity_domain("Google Cloud Platform (GCP)")[1] == "name_prefix"

    def test_a_prefix_reaching_no_curated_name_resolves_to_nothing(self):
        assert resolve_entity_domain("Nexmo Inc (aka Vonage)") == ("", "unresolved")

    def test_prefixes_run_longest_first(self):
        assert name_prefixes("Amazon Web Services (AWS)") == [
            "Amazon Web Services", "Amazon Web", "Amazon",
        ]


class TestObservedHints:
    def test_a_contacted_domain_resolves_a_party_no_table_names(self):
        hints = observed_domain_hints([
            {"entity": "Acme Analytics", "domains": ["acme.io"]},
        ])
        assert resolve_entity_domain("Acme Analytics", hints=hints) == (
            "acme.io", "observed",
        )

    def test_shared_platforms_are_not_taken_as_a_party_home(self):
        hints = observed_domain_hints([
            {"entity": "Someone", "domains": ["cloudfront.net"]},
        ])
        assert hints == {}


class TestOverrides:
    def test_a_hand_supplied_domain_wins(self):
        assert resolve_entity_domain(
            "Google", overrides={"google": "example.org"},
        ) == ("example.org", "override")


class TestNameResolution:
    """Sources name one organisation in incompatible ways."""

    def test_a_domain_and_a_word_reach_the_same_party(self):
        assert resolve_name("criteo.com").key == resolve_name("Criteo SA").key

    def test_a_subdomain_resolves_to_its_organisation(self):
        assert resolve_name("uis.mobfox.com").key == resolve_name("mobfox.com").key

    def test_a_curated_owner_outranks_the_domain_label(self):
        resolved = resolve_name("doubleclick.net")
        assert resolved.display == "Google"
        assert resolved.basis == "domain_map"

    def test_a_lowercased_surface_regains_its_capitalisation(self):
        assert resolve_name("pubmatic").display == "PubMatic"
        assert resolve_name("openx.com").display == "OpenX"

    def test_a_surface_carrying_capitals_keeps_them(self):
        assert resolve_name("mParticle Inc.").display == "mParticle"

    def test_a_hyphenated_label_capitalises_throughout(self):
        assert resolve_name("ad-generation.jp").display == "Ad-Generation"

    def test_a_domain_with_no_brand_label_stands_as_written(self):
        # "advertising" names no organisation, so the registrable domain is
        # the name, and a policy's mention of advertising stays separate.
        assert resolve_name("advertising.com").display == "advertising.com"
        assert resolve_name("advertising.com").key != resolve_name("advertising").key

    def test_the_domain_a_name_carries_is_recorded(self):
        assert resolve_name("adform.com").domain == "adform.com"
        assert resolve_name("Adform").domain == ""

    def test_an_empty_name_resolves_to_nothing(self):
        assert resolve_name("  ").key == ""


class TestResolutionFeedsExpansion:
    def test_a_registry_domain_becomes_a_crawlable_site(self):
        # An ads.txt row names a party no curated table covers.
        assert resolve_entity_domain("adyoulike.com") == ("adyoulike.com", "name_domain")

    def test_a_tracker_domain_still_resolves_to_its_owner(self):
        assert resolve_entity_domain("doubleclick.net") == ("google.com", "entity_map")

    def test_a_shared_platform_is_not_a_party_home(self):
        assert resolve_entity_domain("cloudfront.net")[0] != "cloudfront.net"
