"""Validate tpd.classify.named_entities."""

from __future__ import annotations

from tpd.classify.named_entities import (
    _is_first_party,
    classify_org,
    clean_ner_org,
    detect_orgs,
    first_party_tokens,
    gazetteer_orgs,
)


class TestCleanNerOrg:
    def test_strips_determiners_and_bullets(self):
        assert clean_ner_org("• the Google LLC") == "Google LLC"

    def test_blocks_legal_regimes_and_acronyms(self):
        for noise in ("GDPR", "CCPA", "the Privacy Shield", "SSL",
                      "Data Protection Authority", "European Union"):
            assert clean_ner_org(noise) is None, noise

    def test_blocks_defined_terms(self):
        for noise in ("the Service", "Website", "Our Platform", "Cookies"):
            assert clean_ner_org(noise) is None, noise

    def test_blocks_uncapitalised(self):
        assert clean_ner_org("some vendor") is None

    def test_blocks_category_phrases(self):
        assert clean_ner_org("Advertising Partners") is None
        assert clean_ner_org("Third Parties") is None

    def test_keeps_real_companies(self):
        assert clean_ner_org("Google") == "Google"
        assert clean_ner_org("Acme Corp.") == "Acme Corp"  # trailing dot stripped

    def test_unknown_acronyms_dropped_but_gazetteer_acronyms_kept(self):
        assert clean_ner_org("XYZQ") is None
        assert clean_ner_org("AWS") == "AWS"


class TestFirstParty:
    def test_first_party_tokens_from_urls_and_name(self):
        toks = first_party_tokens(
            ["https://www.exampleshop.com/privacy"], name="Example Shop Inc."
        )
        assert "exampleshop" in toks
        assert "example" in toks and "shop" in toks
        assert "inc" not in toks  # corporate suffix

    def test_is_first_party(self):
        fp = {"exampleshop", "example"}
        assert _is_first_party("ExampleShop GmbH", fp)
        assert not _is_first_party("Google", fp)
        assert not _is_first_party("Google", None)


class TestGazetteer:
    def test_simple_hits(self):
        names = dict(gazetteer_orgs("We use Google Analytics and Hotjar."))
        assert names.get("google analytics") == "service"
        assert names.get("hotjar") == "service"

    def test_meta_requires_company_context(self):
        # "meta data"/"meta tags" alone must not surface Meta the company.
        assert "meta" not in dict(gazetteer_orgs("We collect meta data and meta tags."))
        assert "meta" in dict(gazetteer_orgs("We share data with Meta."))

    def test_ambiguous_names_need_capitalisation(self):
        assert "branch" not in dict(gazetteer_orgs("a branch of our business"))
        assert "branch" in dict(gazetteer_orgs("We use Branch for attribution."))

    def test_classify_org(self):
        assert classify_org("Google Analytics") == "service"
        assert classify_org("Google") == "company"
        assert classify_org("Acme Analytics") == "service"  # tail word
        assert classify_org("Some Startup") == "unknown"


class TestDetectOrgs:
    def test_gazetteer_only(self):
        orgs, spec = detect_orgs("We use Google Analytics.", ner_fn=None)
        assert orgs == ["google analytics"]
        assert spec == "service"

    def test_mixed_specificity(self):
        orgs, spec = detect_orgs("We use Google Analytics and share with Google.",
                                 ner_fn=None)
        assert set(orgs) == {"google analytics", "google"}
        assert spec == "mixed"

    def test_first_party_excluded(self):
        orgs, spec = detect_orgs("Google shares data with Hotjar.",
                                 ner_fn=None, first_party={"google"})
        assert orgs == ["hotjar"]

    def test_precomputed_ner_ents(self):
        orgs, spec = detect_orgs(
            "We share data with our partner.",
            ner_ents=["Acme Corp", "Advertising Partners", "GDPR"],
        )
        assert orgs == ["Acme Corp"]
        assert spec == "unknown"

    def test_prose_precision_gates_unknown_ner_names(self):
        orgs, _ = detect_orgs("text", ner_ents=["Mystery Startup"],
                              prose_precision=True)
        assert orgs == []
        orgs, _ = detect_orgs("text", ner_ents=["Mystery Startup Inc."],
                              prose_precision=True)
        assert orgs == ["Mystery Startup Inc"]

    def test_nothing_found(self):
        assert detect_orgs("We value privacy.", ner_fn=None) == ([], "")
