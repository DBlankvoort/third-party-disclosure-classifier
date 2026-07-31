"""Segments that are not clauses."""

from __future__ import annotations

from tpd.classify.specificity import _sentence_segments
from tpd.classify.typology_clf import classify_document
from tpd.extract import parse_html

NAV_LABELS = (
    "Membership Renew your membership Payments and support Contact us "
    "Advocacy Position statements Safe transfer of information to and from "
    "our practice Working with government agencies Education "
) * 8

NAV_ONLY_HTML = f"""
<html><head><title>Fellowship Support Programme</title></head><body>
<div>{NAV_LABELS}</div>
<p>The programme supports registrars through fellowship.</p>
</body></html>
"""

QUESTION_HTML = """
<html><head><title>Privacy Policy</title></head><body>
<h2>Do we disclose any information to outside parties?</h2>
<p>We do not sell, trade, or otherwise transfer your personally identifiable
information to outside parties.</p>
<p>This privacy policy describes how we collect personal data, your rights and
choices, our use of cookies, and the consent we rely on. We are the data
controller for the personal information you provide to us when you use this
service. You may opt-out of any marketing communication at any time by
following the instructions in the message, and we will honour your request
within thirty days.</p>
<p>We collect the information you give us when you register an account, the
information your browser sends automatically, and the information stored in
cookies on your device. We keep that personal data for as long as your account
remains open, and we delete it on request in accordance with the GDPR and the
CCPA. How we use the information we collect is described in the section
below.</p>
</body></html>
"""


class TestUnsegmentedRuns:
    def test_run_without_sentence_boundary_is_dropped(self):
        blob = "Education Advocacy Membership " * 60
        assert len(blob) > 1200
        assert _sentence_segments([blob]) == []

    def test_prose_survives_sentence_splitting(self):
        prose = "We share data with partners. " * 60
        out = _sentence_segments([prose])
        assert len(out) == 60
        assert all(s.startswith("We share") for s in out)

    def test_nav_blob_yields_no_facets(self):
        doc = parse_html(NAV_ONLY_HTML)
        dc = classify_document(doc, role="help_doc", target_type="website",
                               url="https://example.org/education/support-programme")
        assert dc.facets == []
        assert not dc.relevant


class TestInterrogativeHeadings:
    def test_question_heading_is_not_a_disclosure(self):
        doc = parse_html(QUESTION_HTML)
        dc = classify_document(doc, role="privacy_policy", target_type="website",
                               url="https://example.com/privacy")
        assert "prose:generic" not in dc.facets


class TestFacetOrdering:
    def test_facets_are_a_sorted_list(self):
        doc = parse_html(QUESTION_HTML)
        dc = classify_document(doc, role="privacy_policy", target_type="website",
                               url="https://example.com/privacy")
        assert isinstance(dc.facets, list)
        assert dc.facets == sorted(dc.facets)
