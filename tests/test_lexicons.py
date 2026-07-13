"""Pin the clause-level heuristics in tpd.lexicons."""

from __future__ import annotations

import json

from tpd.lexicons import (
    CATEGORY_RE,
    COLLECTION_RE,
    GENERIC_RE,
    SHARING_RE,
    _affirmative,
    clause_window,
    machine_readable_kind,
    positive_collection,
    positive_sharing,
    registry_named_orgs,
    third_party_collects,
)


# --------------------------------------------------------------------------- #
# Core lexicon regexes
# --------------------------------------------------------------------------- #
class TestCategoryLexicon:
    def test_matches_common_categories(self):
        for phrase in (
            "advertisers", "advertising partners", "ad networks",
            "service providers", "sub-processors", "business partners",
            "payment processors", "credit bureaus", "law enforcement",
        ):
            assert CATEGORY_RE.search(phrase), phrase

    def test_advertising_alone_is_not_a_category(self):
        assert not CATEGORY_RE.search("we use advertising on our site")
        assert CATEGORY_RE.search("we work with advertising networks")

    def test_generic_lexicon(self):
        for phrase in ("third parties", "3rd parties", "other companies",
                       "outside parties", "external organisations"):
            assert GENERIC_RE.search(phrase), phrase
        assert not GENERIC_RE.search("our own services")


class TestVerbLexicons:
    def test_sharing_verbs(self):
        for seg in ("we share your data", "information is disclosed",
                    "we sell personal information", "we transfer data abroad"):
            assert SHARING_RE.search(seg), seg

    def test_provide_requires_to_or_with(self):
        assert SHARING_RE.search("we provide to our partners your data")
        assert not SHARING_RE.search("we provide a service")

    def test_collection_verbs(self):
        for seg in ("they collect your data", "we use cookies",
                    "advertisers place cookies", "partners serve ads"):
            assert COLLECTION_RE.search(seg), seg


# --------------------------------------------------------------------------- #
# _affirmative: negation, exceptions, self-recipients
# --------------------------------------------------------------------------- #
class TestAffirmative:
    def test_plain_affirmative(self):
        assert _affirmative("we share your data with advertisers", SHARING_RE)

    def test_simple_negation(self):
        assert not _affirmative("we do not share your data", SHARING_RE)
        assert not _affirmative("we will never sell your information", SHARING_RE)
        assert not _affirmative("we don't disclose your data", SHARING_RE)

    def test_negation_with_exception_is_affirmative(self):
        assert _affirmative(
            "we do not share your data except with our service providers",
            SHARING_RE,
        )
        assert _affirmative(
            "we never sell your information unless you consent", SHARING_RE
        )

    def test_negation_does_not_leak_across_sentence_boundary(self):
        assert _affirmative(
            "We do not use your data for profiling. We share your data with partners.",
            SHARING_RE,
        )

    def test_self_recipient_is_ignored(self):
        assert not _affirmative("your report is shared with you", SHARING_RE)
        assert not _affirmative("data you provide to us", SHARING_RE)

    def test_second_verb_can_rescue_segment(self):
        assert _affirmative(
            "We do not sell your data. We share it with analytics providers.",
            SHARING_RE,
        )
        assert _affirmative(
            "we do not sell your data, but we share it with analytics providers",
            SHARING_RE,
        )

    def test_wrappers(self):
        assert positive_sharing("we share data with vendors")
        assert not positive_sharing("we do not share data")
        assert positive_collection("we collect your email")
        assert not positive_collection("we do not collect your email")


# --------------------------------------------------------------------------- #
# third_party_collects
# --------------------------------------------------------------------------- #
class TestThirdPartyCollects:
    def test_generic_party_collecting(self):
        assert third_party_collects("third parties may collect information about you")

    def test_negated_generic_party(self):
        assert not third_party_collects("third parties do not collect information")

    def test_category_needs_first_party_anchor(self):
        anchored = ("On our website, advertisers collect data about your visits.")
        unanchored = "Advertisers collect data."
        assert third_party_collects(anchored)
        assert not third_party_collects(unanchored)

    def test_tracker_attribution(self):
        assert third_party_collects(
            "Our site uses cookies placed by third-party advertising partners "
            "to collect browsing data."
        )

    def test_first_party_on_third_party_surface(self):
        assert third_party_collects(
            "We collect usage data via third-party websites and apps."
        )

    def test_plain_first_party_collection_is_not_flagged(self):
        assert not third_party_collects("We collect your email address.")


# --------------------------------------------------------------------------- #
# clause_window
# --------------------------------------------------------------------------- #
class TestClauseWindow:
    def test_cuts_at_sentence_boundary(self):
        seg = "we work with partners. They are nice people."
        assert clause_window(seg, 0, 200) == "we work with partners"

    def test_returns_width_when_no_boundary(self):
        seg = "a" * 100
        assert clause_window(seg, 0, 40) == "a" * 40


# --------------------------------------------------------------------------- #
# Machine-readable registries
# --------------------------------------------------------------------------- #
ADS_TXT = """# comment line
google.com, pub-1234567890, DIRECT, f08c47fec0942fa0
appnexus.com, 1234, RESELLER
"""

SELLERS_JSON = json.dumps({
    "sellers": [
        {"seller_id": "1", "name": "Example Ads", "domain": "exampleads.com"},
        {"seller_id": "2", "is_confidential": 1},
    ]
})

TCF_GVL = json.dumps({
    "gvlSpecificationVersion": 3,
    "vendorListVersion": 100,
    "vendors": {"1": {"id": 1, "name": "Vendor One", "purposes": [1, 2]}},
})


class TestMachineReadableKind:
    def test_kinds(self):
        assert machine_readable_kind(ADS_TXT) == "ads_txt"
        assert machine_readable_kind(SELLERS_JSON) == "sellers_json"
        assert machine_readable_kind(TCF_GVL) == "tcf_gvl"
        assert machine_readable_kind('{"vendors": [{"name": "x"}]}') == "vendors_json"
        assert machine_readable_kind("just some prose") == ""


class TestRegistryNamedOrgs:
    def test_ads_txt_domains(self):
        assert registry_named_orgs(ADS_TXT, "ads_txt") == ["google.com", "appnexus.com"]

    def test_sellers_json_names_and_domains(self):
        orgs = registry_named_orgs(SELLERS_JSON, "sellers_json")
        assert "exampleads.com" in orgs
        assert "Example Ads" in orgs

    def test_gvl_vendor_names(self):
        assert registry_named_orgs(TCF_GVL, "tcf_gvl") == ["Vendor One"]

    def test_cap_and_dedup(self):
        many = "\n".join(f"d{i}.com, {i}, DIRECT" for i in range(300))
        orgs = registry_named_orgs(many + "\n" + many, "ads_txt", cap=50)
        assert len(orgs) == 50
        assert len(set(orgs)) == 50
