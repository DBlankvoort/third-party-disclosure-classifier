"""Validate how recipients are told apart from sources, staff and own products."""

from __future__ import annotations

from tpd.classify.named_entities import (
    _is_first_party,
    clean_ner_org,
    frame_named_orgs,
    with_brand_family,
)
from tpd.classify.named_relations import _singular
from tpd.classify.specificity import _nav_junk, party_terms

FP = {"google"}


def categories(text: str) -> list[str]:
    return party_terms(text, [], FP)[0]


def generics(text: str) -> list[str]:
    return party_terms(text, [], FP)[1]


class TestNamedRecipients:
    def test_share_with_frame_names_the_recipient(self):
        text = ("Google shares data about requests for removal of content "
                "from our services with Lumen, which collects these requests.")
        assert "Lumen" in frame_named_orgs(text)

    def test_share_with_frame_needs_a_proper_name(self):
        assert frame_named_orgs("We do not share information with advertisers.") == []

    def test_heading_fragment_is_not_a_name(self):
        assert clean_ner_org("Exporting &") is None


class TestBrandFamily:
    def test_own_products_are_first_party(self):
        fp = with_brand_family(FP)
        for name in ("YouTube", "Gmail", "Fitbit", "Pixel Watch", "Drive"):
            assert _is_first_party(name, fp), name

    def test_other_organisations_stay_third_party(self):
        fp = with_brand_family(FP)
        for name in ("Lumen", "Nielsen", "Nest Labs"):
            assert not _is_first_party(name, fp), name

    def test_a_brand_pulls_in_its_family(self):
        assert "google" in with_brand_family({"youtube"})

    def test_unrelated_first_party_is_unchanged(self):
        assert with_brand_family({"acme"}) == {"acme"}


class TestCategoryTerms:
    def test_collection_sources_are_not_recipients(self):
        text = ("We may also collect information about you from trusted partners, "
                "such as directory services who provide us with business information, "
                "and marketing partners who provide us with information.")
        assert categories(text) == []

    def test_recipients_after_a_source_still_count(self):
        text = "We may share information we receive from partners with advertisers."
        assert categories(text) == ["advertisers"]

    def test_requesters_are_recipients(self):
        text = ("Google regularly receives requests from governments and courts "
                "to disclose user data.")
        assert categories(text) == ["governments", "courts"]

    def test_own_workforce_is_not_a_recipient(self):
        text = ("We restrict access to personal information to Google employees, "
                "contractors, and agents who need that information.")
        assert categories(text) == []

    def test_new_recipient_roles(self):
        text = ("Your domain administrator and resellers will have access to your "
                "account, and we report statistics to rights holders.")
        assert categories(text) == ["domain administrator", "resellers", "rights holders"]

    def test_own_advertising_service_is_not_a_party(self):
        assert categories("A website might use our advertising services.") == []


class TestGenericTerms:
    def test_outside_the_first_party(self):
        text = "We'll share personal information outside of Google with your consent."
        assert "outside of Google" in generics(text)

    def test_other_recipients(self):
        text = "We may also disclose your information to other recipients."
        assert generics(text) == ["other recipients"]

    def test_comparison_is_not_a_party(self):
        text = "We share data, though some laws provide more protection than others."
        assert generics(text) == []

    def test_providers_of_other_services_are_not_recipients(self):
        assert generics("Services offered by other companies or individuals.") == []


def test_table_of_contents_is_navigation():
    toc = ("Privacy Policy Introduction Information Google collects Why Google "
           "collects data Your privacy controls Sharing your information Keeping "
           "your information secure Exporting & deleting your information "
           "Compliance & cooperation with regulators Swiss requirements")
    assert _nav_junk(toc)


def test_party_types_are_singular():
    assert [_singular(t) for t in ("authorities", "rights holders",
                                   "other trusted businesses", "courts")] == [
        "authority", "rights holder", "other trusted business", "court"]
