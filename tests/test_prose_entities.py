"""Validate the recall-tuned extraction of named organisations from prose."""

from __future__ import annotations

import pytest
from conftest import requires_model

from tpd.classify.prose_entities import ADMIT_CONFIDENCE, scan_document
from tpd.extract import parse_html
from tpd.tracks import SERVICE_DATA, SITE_VISITOR, UNKNOWN


def _ner(*surfaces):
    def fn(text):
        return [s for s in surfaces if s in text]

    fn.batch = lambda texts: [fn(t) for t in texts]
    return fn


def _scan(html, ner_fn=None, role="privacy_policy", first_party=None):
    return scan_document(parse_html(html), ner_fn=ner_fn, role=role,
                         first_party=first_party)


def _names(scan):
    return {e.name.lower() for e in scan.admitted}


def _by_name(scan, name):
    for e in scan.entities:
        if e.name.lower() == name.lower():
            return e
    raise AssertionError(f"{name!r} not among {[e.name for e in scan.entities]}")


class TestAdmission:
    def test_a_name_a_register_knows_is_admitted_without_a_sharing_verb(self):
        scan = _scan("<h2>Our partners</h2><ul><li>Criteo</li></ul>")
        assert "criteo" in _names(scan)

    def test_a_vendor_a_reference_table_knows_is_admitted(self):
        scan = _scan("<h2>Service providers</h2><ul><li>Demandbase</li>"
                     "<li>MaxMind</li></ul>",
                     ner_fn=_ner("Demandbase", "MaxMind"))
        assert {"demandbase", "maxmind"} <= _names(scan)

    def test_a_name_no_register_knows_is_admitted_and_marked(self):
        scan = _scan("<p>We share personal data with document360 to run our "
                     "help centre.</p>", ner_fn=_ner("document360"))
        entity = _by_name(scan, "document360")
        assert entity.admitted
        assert entity.grounded is False

    def test_a_corporate_form_in_a_disclosure_clause_is_admitted(self):
        scan = _scan("<p>We disclose your personal data to Wickford Analytics "
                     "Ltd for measurement.</p>",
                     ner_fn=_ner("Wickford Analytics Ltd"))
        assert any("wickford" in n for n in _names(scan))

    def test_a_capitalised_defined_term_is_not_a_party(self):
        scan = _scan("<p>These Terms govern your use of the Service.</p>",
                     ner_fn=_ner("Terms", "Service"))
        assert _names(scan) == set()

    def test_a_holding_company_is_not_a_recipient(self):
        scan = _scan("<p>We share personal data with Vista Equity Partners "
                     "and Criteo.</p>", ner_fn=_ner("Vista Equity Partners"))
        assert "vista equity partners" not in _names(scan)

    def test_the_publishers_own_name_is_not_a_third_party(self):
        scan = _scan("<p>Criteo shares personal data with Google.</p>",
                     first_party={"criteo"})
        assert "criteo" not in _names(scan)


class TestConfidence:
    def test_a_recognised_name_outranks_one_resting_on_its_document(self):
        scan = _scan("<p>We share personal data with Criteo and with "
                     "Wickford Analytics Ltd.</p>",
                     ner_fn=_ner("Wickford Analytics Ltd"))
        assert _by_name(scan, "Criteo").confidence > ADMIT_CONFIDENCE

    def test_the_signals_behind_a_reading_are_reported(self):
        scan = _scan("<h2>Vendors</h2><ul><li>Criteo</li></ul>")
        assert "gazetteer" in _by_name(scan, "Criteo").signals or \
            "knowledge_base" in _by_name(scan, "Criteo").signals


class TestDataSubject:
    def test_a_sharing_clause_concerns_the_publishers_own_users(self):
        scan = _scan("<p>We share your personal data with Criteo.</p>")
        assert _by_name(scan, "Criteo").subject == SITE_VISITOR

    def test_a_subprocessor_list_concerns_data_received_from_customers(self):
        scan = _scan("<h2>Sub-processors</h2><ul><li>Criteo</li></ul>",
                     role="subprocessor_list")
        assert _by_name(scan, "Criteo").subject == SERVICE_DATA

    def test_a_bare_list_states_no_population(self):
        scan = _scan("<h2>Partners</h2><ul><li>Criteo</li></ul>",
                     role="partners_page")
        assert _by_name(scan, "Criteo").subject == UNKNOWN


class TestScope:
    def test_a_document_read_whole_reports_no_truncation(self):
        scan = _scan("<p>We share personal data with Criteo.</p>")
        assert scan.truncated is False
        assert scan.segments_read == scan.segments_total

    def test_a_vendor_column_names_its_rows(self):
        html = (
            "<table><tr><th>Vendor</th><th>Purpose</th></tr>"
            "<tr><td>Wickford Analytics Ltd</td><td>measurement</td></tr>"
            "</table>"
        )
        assert any("wickford" in n for n in _names(_scan(html)))


@requires_model
class TestWithLanguageModel:
    @pytest.fixture(scope="class")
    @classmethod
    def ner_fn(cls):
        from tpd.classify.named_entities import load_ner

        fn, _ = load_ner()
        return fn

    def test_a_named_vendor_outside_a_sharing_clause_is_still_read(self, ner_fn):
        html = ("<h2>Analytics</h2>"
                "<p>Measurement on this site is performed by Chartbeat.</p>")
        assert "chartbeat" in _names(_scan(html, ner_fn=ner_fn))


class TestDirection:
    def test_a_party_a_policy_acquires_data_via_is_read_as_a_source(self):
        html = ("<p>Our policy also covers the data we acquire via Criteo, "
                "and they have a robust privacy policy.</p>")
        assert _by_name(_scan(html), "criteo").direction == "upstream"

    def test_a_party_a_policy_shares_with_is_read_as_a_recipient(self):
        html = "<p>We share your personal data with Criteo for advertising.</p>"
        assert _by_name(_scan(html), "criteo").direction == "downstream"

    def test_one_sharing_clause_settles_a_party_named_both_ways(self):
        html = ("<p>We acquire order data from Criteo.</p>"
                "<p>We also share your email address with Criteo.</p>")
        assert _by_name(_scan(html), "criteo").direction == "downstream"

    def test_a_clause_stating_no_flow_leaves_the_direction_alone(self):
        html = ("<p>Our policy also covers the data we acquire via Criteo.</p>"
                "<p>To opt out of Criteo, go to that site.</p>")
        assert _by_name(_scan(html), "criteo").direction == "upstream"


class TestCoordination:
    def test_a_name_listed_beside_a_known_party_is_admitted(self):
        html = "<p>We work with Criteo, Wickford Analytics and Talling Media.</p>"
        names = _names(_scan(html))
        assert {"wickford analytics", "talling media"} <= names

    def test_a_list_naming_no_known_party_admits_none_of_it(self):
        html = "<p>Our offices are in Harrogate, Perpignan and Trondheim.</p>"
        names = _names(_scan(html))
        assert not {"harrogate", "perpignan", "trondheim"} & names

    def test_a_pair_is_not_read_as_a_list(self):
        html = "<p>We work with Criteo and Talling Media.</p>"
        entity = next((e for e in _scan(html).entities
                       if e.name.lower() == "talling media"), None)
        assert entity is None or "coordinated" not in entity.signals
