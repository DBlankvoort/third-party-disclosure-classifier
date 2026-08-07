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
    "additional", "more", "etc", "one", "and/or",
}

_FIRST_PARTY_WORDS = {"we", "us", "our", "ourselves", "company", "i", "me"}

_SELF_REFERENCE_SURFACES = {
    "site", "sites", "web site", "web sites", "website", "websites",
    "app", "apps", "application", "applications",
    "platform", "platforms", "service", "services",
}
_THIRD_PARTY_CUES = {"third party", "third parties", "third-party", "partner",
                     "partners", "vendor", "vendors", "provider", "providers",
                     "affiliate", "affiliates", "service provider",
                     "recipient", "recipients"}
_GENERIC_DATA_HEADS = {"information", "data", "datum", "detail", "details"}

_DOCUMENT_TERMS = {
    "agreement", "agreements", "addendum", "addenda", "annex", "annexes",
    "appendix", "appendices", "schedule", "schedules", "exhibit", "exhibits",
    "attachment", "attachments", "clause", "clauses", "section", "sections",
    "article", "articles", "amendment", "amendments", "contract", "contracts",
    "policy", "policies", "notice", "notices", "term", "terms", "statement",
    "statements", "document", "documents", "dpa", "form", "forms", "sow",
    "order", "orders",
}
_QUANTIFIERS = {
    "most", "many", "some", "several", "few", "other", "others", "another",
    "certain", "various", "each", "either", "both", "every", "any", "all",
    "numerous", "multiple",
}

COMMON_POLICY_NOUNS = {
    "ad", "ads", "advertiser", "advertisers", "advertising", "affiliate",
    "affiliates", "agency", "agencies", "analytics", "app", "apps",
    "application", "applications", "business", "businesses", "carrier",
    "carriers", "company", "companies", "content", "controller", "controllers",
    "customer", "customers", "client", "clients", "data", "device", "devices",
    "information", "network", "networks", "operator", "operators", "partner",
    "partners", "party", "parties", "platform", "platforms", "processor",
    "processors", "product", "products", "provider", "providers", "publisher",
    "publishers", "purpose", "purposes", "recipient", "recipients", "service",
    "services", "site", "sites", "software", "subsidiary", "subsidiaries",
    "supplier", "suppliers", "third", "user", "users", "vendor", "vendors",
    "website", "websites",
}

_NON_NOMINAL_QUALIFIERS = {
    "access", "collect", "disclose", "gather", "obtain", "process", "provide",
    "receive", "record", "require", "retain", "send", "share", "store",
    "transfer", "use", "follow", "give", "get", "make",
    "what", "which", "whatever", "whichever",
}


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
        lemmas = self._drop_non_nominal(self._lemmatize(cleaned).split())
        if not lemmas or (len(lemmas) == 1 and lemmas[0] in _GENERIC_DATA_HEADS):
            return UNSPECIFIED_DATA
        if all(w in _GENERIC_DATA_HEADS for w in lemmas):
            return UNSPECIFIED_DATA
        return " ".join(lemmas)

    # ---------------------------------------------------------------- entities
    def normalize_entity(self, phrase: str) -> str:
        """The term an entity phrase denotes, or "" when it denotes no party."""
        text = phrase.strip().lower()
        words = text.split()
        if words and words[0] in _FIRST_PARTY_WORDS:
            rest = self._strip_stops(" ".join(words[1:])) if len(words) > 1 else ""
            if not rest or rest in _SELF_REFERENCE_SURFACES or rest in _FIRST_PARTY_WORDS:
                return FIRST_PARTY
            if self._is_generic_third_party(self._lemmatize(rest)):
                return UNSPECIFIED_ACTOR
            text = " ".join(words[1:])
        # known company -> normalized company name
        for member, rx in _company_patterns():
            if rx.search(text):
                return member
        if self._is_document(text):
            return ""
        words = re.findall(r"[a-z0-9'/-]+", text)
        if words and words[0] in _QUANTIFIERS:
            rest = self._lemmatize(" ".join(words[1:]))
            if (self._is_generic_third_party(rest)
                    or rest in _SELF_REFERENCE_SURFACES):
                return UNSPECIFIED_ACTOR
        cleaned = self._strip_stops(text)
        if cleaned in _SELF_REFERENCE_SURFACES:
            return FIRST_PARTY
        lemmas = self._lemmatize(self._strip_stops(phrase, fold=False)).lower()
        # blanket third party
        if not lemmas or self._is_generic_third_party(lemmas):
            return UNSPECIFIED_ACTOR
        return lemmas or UNSPECIFIED_ACTOR

    @classmethod
    def _is_document(cls, text: str) -> bool:
        words = [w for w in re.findall(r"[a-z0-9'-]+", text) if w not in _STOPWORDS]
        body = [w for w in words if not re.fullmatch(r"[0-9ivxlc]+", w)]
        if not body:
            return False
        heads = {cls._naive_lemmatize(body[0]), cls._naive_lemmatize(body[-1])}
        return bool(heads & _DOCUMENT_TERMS)

    def classify_party(self, phrase: str) -> str:
        """Return 'first', 'third', or 'other' for an entity phrase."""
        norm = self.normalize_entity(phrase)
        if norm == FIRST_PARTY:
            return "first"
        return "third"

    # ------------------------------------------------------------- helpers
    @staticmethod
    def _drop_non_nominal(lemmas: list[str]) -> list[str]:
        """Drop the leading verbs and interrogatives of a data phrase."""
        i = 0
        while i < len(lemmas) - 1:
            word = lemmas[i]
            if word in _NON_NOMINAL_QUALIFIERS:
                i += 1
            elif word.endswith("ly") and lemmas[i + 1] in _NON_NOMINAL_QUALIFIERS:
                i += 1
            else:
                break
        return lemmas[i:]

    @staticmethod
    def _strip_stops(text: str, fold: bool = True) -> str:
        """Drop the phrase's stopwords."""
        words = re.findall(r"[A-Za-z0-9'/]+", text.lower() if fold else text)
        kept = [w for w in words if w.lower() not in _STOPWORDS]
        return " ".join(kept) if kept else " ".join(words)

    @staticmethod
    def _is_generic_third_party(cleaned: str) -> bool:
        words = set(re.findall(r"[a-z0-9'/-]+", cleaned))
        generic = {
            "third", "party", "parties", "partner", "partners", "vendor",
            "vendors", "provider", "providers", "service", "affiliate",
            "affiliates", "company", "companies", "business", "recipient",
            "recipients",
            "processor", "processors", "subprocessor", "subprocessors",
            "sub-processor", "sub-processors", "sub", "subcontractor",
            "subcontractors", "controller", "controllers", "customer",
            "customers", "client", "clients", "subscriber", "subscribers",
            "supplier", "suppliers", "importer", "exporter",
            "site", "sites", "website", "websites", "app", "apps",
            "application", "applications", "platform", "platforms",
        }
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