"""Shared infrastructure for annotators."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

from ..document import Sentence
from ..nlp import EntitySpan
from ..phrase_graph import PhraseGraph, PhraseLabel


@dataclass
class ParsedSentence:
    sent_id: int
    doc: object               # spaCy Doc
    spans: list[EntitySpan]   # NER spans
    source: Optional[Sentence] = None
    _tok2span: dict = field(default_factory=dict)

    def __post_init__(self):
        for sp in self.spans:
            for i in range(sp.start, sp.end):
                self._tok2span[i] = sp

    def span_at(self, tok_index: int) -> Optional[EntitySpan]:
        return self._tok2span.get(tok_index)

    def span_covering_subtree(self, token) -> Optional[EntitySpan]:
        """Find an NER span whose root is ``token`` or that contains it."""
        sp = self.span_at(token.i)
        if sp is not None:
            return sp
        for child in token.subtree:
            sp = self.span_at(child.i)
            if sp is not None and sp.root == token.i:
                return sp
        return None


class AnnotatorContext:
    """Holds parsed sentences and the phrase graph."""

    def __init__(self, sentences: list[ParsedSentence], graph: PhraseGraph):
        self.sentences = sentences
        self.graph = graph

    def phrase_key(self, parsed: ParsedSentence, span: EntitySpan) -> str:
        label = PhraseLabel.DATA if span.label == "DATA" else PhraseLabel.ENTITY
        key = f"{parsed.sent_id}:{span.start}:{span.end}"
        self.graph.add_phrase(key, span.text, label, parsed.sent_id)
        return key

    def add_purpose_phrase(self, parsed: ParsedSentence, start: int, end: int,
                           text: str) -> str:
        key = f"{parsed.sent_id}:p:{start}:{end}"
        self.graph.add_phrase(key, text, PhraseLabel.PURPOSE, parsed.sent_id)
        return key


class Annotator:
    name = "annotator"

    def annotate(self, ctx: AnnotatorContext) -> None:  # pragma: no cover
        raise NotImplementedError
