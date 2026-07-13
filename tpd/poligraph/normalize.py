"""Phrase normalization."""

from __future__ import annotations

import json
import re
from functools import lru_cache
from importlib import resources
from typing import Optional

from .graph import UNSPECIFIED_ACTOR, UNSPECIFIED_DATA, FIRST_PARTY
from .ontology import global_entity_ontology

_STOPWORDS = {
    "a", "an", "the", "your", "our", "my", "their", "his", "her", "its", "this",
    "that", "these", "those", "such", "any", "all", "some", "other", "others",
    "certain", "various", "including", "include", "of", "and", "or", "for",
    "from", "to", "with", "you", "user", "users", "about", "related", "relating",
    "additional", "more", "etc",
}

_FIRST_PARTY_WORDS = {"we", "us", "our", "ourselves", "company", "i", "me"}
_THIRD_PARTY_CUES = {"third party", "third parties", "third-party", "partner",
                     "partners", "vendor", "vendors", "provider", "providers",
                     "affiliate", "affiliates", "service provider"}
_GENERIC_DATA_HEADS = {"information", "data", "datum", "detail", "details"}


@lru_cache(maxsize=1)
def _data_patterns():
    with resources.files("tpd.poligraph.data").joinpath("data_type_synonyms.json").open() as f:
        spec = json.load(f)
    return [(term, re.compile(rx, re.I)) for term, rx in spec["patterns"]]


@lru_cache(maxsize=1)
def _company_patterns():
    """Regexes for company names."""
    oe = global_entity_ontology()
    pats = []
    for member in oe.leaves():
        # word-boundary match on the company name
        rx = re.compile(r"\b" + re.escape(member) + r"\b", re.I)
        pats.append((member, rx))
    # longer names first so "google analytics" wins over "google"
    pats.sort(key=lambda p: -len(p[0]))
    return pats


class PhraseNormalizer:
    def __init__(self, lemmatizer=None):
        # ``lemmatizer`` is an optional callable phrase.
        self._lemmatize = lemmatizer or self._naive_lemmatize

    # ------------------------------------------------------------- data types
    def normalize_data(self, phrase: str) -> str:
        text = phrase.strip().lower()
        cleaned = self._strip_stops(text)
        for term, rx in _data_patterns():
            if rx.search(text) or rx.search(cleaned):
                return term
        # Unspecified data: a blanket head word with no qualifier left.
        lemmas = self._lemmatize(cleaned).split()
        if not lemmas or (len(lemmas) == 1 and lemmas[0] in _GENERIC_DATA_HEADS):
            return UNSPECIFIED_DATA
        if all(w in _GENERIC_DATA_HEADS for w in lemmas):
            return UNSPECIFIED_DATA
        return self._lemmatize(cleaned) or UNSPECIFIED_DATA

    # ---------------------------------------------------------------- entities
    def normalize_entity(self, phrase: str) -> str:
        text = phrase.strip().lower()
        if any(w in _FIRST_PARTY_WORDS for w in text.split()):
            # "we", "our company", "us"
            if text.split() and text.split()[0] in _FIRST_PARTY_WORDS:
                return FIRST_PARTY
        # known company -> normalized company name
        for member, rx in _company_patterns():
            if rx.search(text):
                return member
        cleaned = self._strip_stops(text)
        lemmas = self._lemmatize(cleaned)
        # blanket third party
        if not lemmas or any(cue in text for cue in _THIRD_PARTY_CUES) and \
                self._is_generic_third_party(cleaned):
            return UNSPECIFIED_ACTOR
        return lemmas or UNSPECIFIED_ACTOR

    def classify_party(self, phrase: str) -> str:
        """Return 'first', 'third', or 'other' for an entity phrase."""
        norm = self.normalize_entity(phrase)
        if norm == FIRST_PARTY:
            return "first"
        return "third"

    # ------------------------------------------------------------- helpers
    @staticmethod
    def _strip_stops(text: str) -> str:
        words = re.findall(r"[a-z0-9'/]+", text.lower())
        kept = [w for w in words if w not in _STOPWORDS]
        return " ".join(kept) if kept else " ".join(words)

    @staticmethod
    def _is_generic_third_party(cleaned: str) -> bool:
        words = set(cleaned.split())
        generic = {"third", "party", "parties", "partner", "partners", "vendor",
                   "vendors", "provider", "providers", "service", "affiliate",
                   "affiliates", "company", "companies", "business"}
        return bool(words) and words <= generic

    @staticmethod
    def _naive_lemmatize(text: str) -> str:
        out = []
        for w in text.split():
            if len(w) > 4 and w.endswith("ies"):
                out.append(w[:-3] + "y")
            elif len(w) > 3 and w.endswith("ses"):
                out.append(w[:-2])
            elif len(w) > 3 and w.endswith("s") and not w.endswith("ss"):
                out.append(w[:-1])
            else:
                out.append(w)
        return " ".join(out)