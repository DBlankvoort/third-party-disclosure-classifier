"""NLP pipeline and NER."""

from __future__ import annotations

import json
from dataclasses import dataclass
from functools import lru_cache
from importlib import resources
from typing import Optional

try:
    import spacy
    from spacy.tokens import Doc, Span
    _HAVE_SPACY = True
except Exception:  # pragma: no cover
    _HAVE_SPACY = False
    Doc = Span = object  # type: ignore


@lru_cache(maxsize=1)
def _ner_roots() -> dict[str, set[str]]:
    with resources.files("tpd.poligraph.data").joinpath("ner_root_words.json").open() as f:
        spec = json.load(f)
    return {"DATA": set(spec["DATA"]), "ENTITY": set(spec["ENTITY"])}


@dataclass
class EntitySpan:
    """A noun-phrase span labeled DATA or ENTITY by the NER."""

    label: str            # "DATA" or "ENTITY"
    start: int            # token index (inclusive)
    end: int              # token index (exclusive)
    text: str
    root: int             # token index of the syntactic root of the phrase


# Phrases that are too generic / not real entities even though the root word
# matches the ENTITY list.
_ENTITY_STOP_ROOTS = {"code", "term", "policy", "purpose", "feature", "right"}

# Determiners trimmed from the front of a phrase for a cleaner term.
_TRIM_DETS = {"the", "a", "an", "your", "our", "my", "their", "his", "her",
              "its", "any", "all", "certain"}


def _trim_start(doc, start: int, end: int) -> int:
    i = start
    while i < end and doc[i].lower_ in _TRIM_DETS and doc[i].pos_ in ("DET", "PRON", "ADJ"):
        i += 1
    return i if i < end else start


class NLP:
    """Thin wrapper over a spaCy pipeline plus PoliGrapher's NER."""

    def __init__(self, model: str = "en_core_web_sm", model_path: Optional[str] = None):
        if not _HAVE_SPACY:
            raise RuntimeError(
                "spaCy is required for PoliGrapher's linguistic analysis. "
                "Install with: pip install spacy && python -m spacy download en_core_web_sm"
            )
        # Prefer the transformer pipeline.
        self.nlp = None
        for name in ([model_path] if model_path else []) + ["en_core_web_trf", model]:
            if not name:
                continue
            try:
                self.nlp = spacy.load(name)
                break
            except Exception:
                continue
        if self.nlp is None:
            raise RuntimeError(
                "No spaCy English model found. Run: python -m spacy download en_core_web_sm"
            )
        # If a trained DATA/ENTITY spancat/ner component exists, use it.
        self._has_custom_ner = any(
            lbl in self.nlp.pipe_labels.get("ner", [])
            for lbl in ("DATA", "ENTITY")
        )

    def __call__(self, text: str) -> Doc:
        return self.nlp(text)

    # ------------------------------------------------------------------ NER
    def entities(self, doc: Doc) -> list[EntitySpan]:
        if self._has_custom_ner:
            return self._ner_from_model(doc)
        return self._ner_rule_based(doc)

    def _ner_from_model(self, doc: Doc) -> list[EntitySpan]:
        spans = []
        for ent in doc.ents:
            if ent.label_ in ("DATA", "ENTITY"):
                spans.append(EntitySpan(ent.label_, ent.start, ent.end,
                                        ent.text, ent.root.i))
        return spans

    def _ner_rule_based(self, doc: Doc) -> list[EntitySpan]:
        roots = _ner_roots()
        spans: list[EntitySpan] = []
        covered: set[int] = set()
        # (1) noun chunks whose root lemma matches a DATA / ENTITY root word.
        for chunk in doc.noun_chunks:
            if any(i in covered for i in range(chunk.start, chunk.end)):
                continue
            root_lemma = chunk.root.lemma_.lower()
            label = None
            if root_lemma in roots["DATA"]:
                label = "DATA"
            elif root_lemma in roots["ENTITY"] and root_lemma not in _ENTITY_STOP_ROOTS:
                label = "ENTITY"
            if label:
                start = _trim_start(doc, chunk.start, chunk.end)
                spans.append(EntitySpan(label, start, chunk.end,
                                        doc[start:chunk.end].text, chunk.root.i))
                covered.update(range(start, chunk.end))
        # (2) spaCy ORG / PRODUCT / PERSON / FAC -> ENTITY for uncovered spans.
        for ent in doc.ents:
            if ent.label_ in ("ORG", "PRODUCT", "PERSON", "FAC") and \
                    not any(i in covered for i in range(ent.start, ent.end)):
                spans.append(EntitySpan("ENTITY", ent.start, ent.end, ent.text, ent.root.i))
                covered.update(range(ent.start, ent.end))
        # (3) the first-party pronoun "we"/"us"/"our".
        for tok in doc:
            if tok.i in covered:
                continue
            if tok.lower_ in ("we", "us") and tok.pos_ in ("PRON", "PROPN"):
                spans.append(EntitySpan("ENTITY", tok.i, tok.i + 1, tok.text, tok.i))
        spans.sort(key=lambda s: s.start)
        return spans


@lru_cache(maxsize=4)
def get_nlp(model: str = "en_core_web_sm", model_path: Optional[str] = None) -> NLP:
    return NLP(model=model, model_path=model_path)
