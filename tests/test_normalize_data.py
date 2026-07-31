from __future__ import annotations

import pytest

from tpd.poligraph.graph import UNSPECIFIED_DATA
from tpd.poligraph.normalize import PhraseNormalizer
from tpd.poligraph.poligrapher import _make_lemmatizer


@pytest.fixture(scope="module")
def normalizer():
    try:
        from tpd.poligraph.nlp import NLP

        nlp = NLP(model="en_core_web_sm")
    except RuntimeError:
        pytest.skip("spaCy model en_core_web_sm not installed")
    return PhraseNormalizer(lemmatizer=_make_lemmatizer(nlp))


class TestVerbQualifiers:
    @pytest.mark.parametrize("phrase", [
        "accessing information",
        "collected data",
        "what information",
        "automatically collected information",
        "following information",
    ])
    def test_verb_and_wh_qualifiers_yield_unspecified(self, normalizer, phrase):
        assert normalizer.normalize_data(phrase) == UNSPECIFIED_DATA

    def test_nominal_qualifier_survives_a_leading_verb(self, normalizer):
        assert normalizer.normalize_data("use limited data") == "limited data"


class TestAdverbs:
    """An adverb goes only when it modifies one of those verbs."""

    def test_adverb_modifying_an_adjective_is_kept(self, normalizer):
        assert normalizer.normalize_data(
            "personally identifiable information") == "personal information"

    def test_negated_form_maps_to_the_negated_term(self, normalizer):
        assert normalizer.normalize_data(
            "non personally identifiable information") == "non-personal information"
        assert normalizer.normalize_data(
            "not personally identifiable data") == "non-personal information"


class TestOntologyTerms:
    @pytest.mark.parametrize("phrase,term", [
        ("location", "geolocation"),
        ("location data", "geolocation"),
        ("precise location", "precise geolocation"),
        ("personal data", "personal information"),
        ("e-mail address", "email address"),
    ])
    def test_phrase_resolves_to_its_ontology_term(self, normalizer, phrase, term):
        assert normalizer.normalize_data(phrase) == term
