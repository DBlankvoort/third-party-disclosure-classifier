"""Purpose annotator."""

from __future__ import annotations

from ..phrase_graph import PhraseEdge
from .base import Annotator, AnnotatorContext, ParsedSentence


class PurposeAnnotator(Annotator):
    name = "purpose"

    def annotate(self, ctx: AnnotatorContext) -> None:
        for parsed in ctx.sentences:
            purpose_spans = self._purpose_spans(parsed)
            if not purpose_spans:
                continue
            data_keys = [ctx.phrase_key(parsed, sp) for sp in parsed.spans
                         if sp.label == "DATA"]
            for (start, end, text) in purpose_spans:
                pk = ctx.add_purpose_phrase(parsed, start, end, text)
                for dk in data_keys:
                    ctx.graph.add_edge(dk, pk, PhraseEdge.PURPOSE)

    def _purpose_spans(self, parsed: ParsedSentence):
        doc = parsed.doc
        spans = []
        seen_starts = set()
        for tok in doc:
            # Form (1)+(2): infinitival "to <verb>" purpose clause.
            if tok.lower_ == "to" and tok.i + 1 < len(doc):
                head = tok.head
                # "to" must mark an infinitive (the verb follows "to"), not a
                # prepositional "to" as in "share ... to third parties".
                if head.pos_ == "VERB" and head.i > tok.i and tok.dep_ in ("aux", "mark") \
                        and head.dep_ in (
                        "advcl", "acl", "xcomp", "purpcl", "relcl", "ROOT", "conj", "pcomp"):
                    sub = list(head.subtree)
                    start = min(t.i for t in sub)
                    start = min(start, tok.i)
                    end = max(t.i for t in sub) + 1
                    if start not in seen_starts and self._looks_purposeful(head):
                        seen_starts.add(start)
                        spans.append((start, end, doc[start:end].text))
            # Form (3): "for ... purpose(s)" or "for <gerund/noun>".
            if tok.lower_ == "for" and tok.dep_ == "prep":
                pobj = next((c for c in tok.children if c.dep_ in ("pobj", "pcomp")), None)
                if pobj is not None:
                    sub = list(tok.subtree)
                    start = min(t.i for t in sub)
                    end = max(t.i for t in sub) + 1
                    text = doc[start:end].text
                    if start not in seen_starts and (
                            "purpose" in text.lower()
                            or pobj.pos_ in ("NOUN", "VERB", "PROPN")):
                        seen_starts.add(start)
                        spans.append((start, end, text))
        return spans

    @staticmethod
    def _looks_purposeful(verb) -> bool:
        # crude filter: a purpose clause has its own verb meaning, not just "to be"
        return verb.lemma_.lower() not in ("be", "have")
