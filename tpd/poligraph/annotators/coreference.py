"""Coreference annotator.
"""

from __future__ import annotations

from ..phrase_graph import PhraseEdge, PhraseLabel
from .base import Annotator, AnnotatorContext, ParsedSentence

_DEMONSTRATIVES = {"this", "that", "these", "those", "such"}
_DATA_ROOTS = {"information", "data", "datum", "detail"}
_PRONOUNS = {"it", "they", "them", "these", "this", "those"}


class CoreferenceAnnotator(Annotator):
    name = "coreference"

    def annotate(self, ctx: AnnotatorContext) -> None:
        self.ctx = ctx
        for idx, parsed in enumerate(ctx.sentences):
            self._resolve_demonstrative_spans(parsed, idx)
            self._resolve_pronouns(parsed, idx)

    def _resolve_demonstrative_spans(self, parsed: ParsedSentence, idx: int) -> None:
        doc = parsed.doc
        for sp in parsed.spans:
            first = doc[sp.start]
            if first.lower_ in _DEMONSTRATIVES and first.dep_ in ("det", "nmod", "amod"):
                root_lemma = doc[sp.root].lemma_.lower()
                want = "DATA" if (sp.label == "DATA" or root_lemma in _DATA_ROOTS) else sp.label
                referent = self._nearest_preceding(idx, sp.start, want_label=want,
                                                   exclude=(parsed.sent_id, sp.start, sp.end))
                if referent:
                    self._link(parsed, sp, referent)

    def _resolve_pronouns(self, parsed: ParsedSentence, idx: int) -> None:
        doc = parsed.doc
        for tok in doc:
            if tok.pos_ == "PRON" and tok.lower_ in _PRONOUNS and parsed.span_at(tok.i) is None:
                # we resolve only pronouns in subject/object position of a verb
                if tok.dep_ not in ("nsubj", "nsubjpass", "dobj", "obj", "pobj"):
                    continue
                want = "ENTITY" if tok.lower_ in ("they", "them") else None
                referent = self._nearest_preceding(idx, tok.i, want_label=want)
                if referent is None:
                    continue
                ref_parsed, ref_span = referent
                label = PhraseLabel.DATA if ref_span.label == "DATA" else PhraseLabel.ENTITY
                key = f"{parsed.sent_id}:pron:{tok.i}"
                self.ctx.graph.add_phrase(key, tok.text, label, parsed.sent_id)
                rk = self.ctx.phrase_key(ref_parsed, ref_span)
                self.ctx.graph.add_edge(key, rk, PhraseEdge.COREF)

    # ------------------------------------------------------------- helpers
    def _link(self, parsed, span, referent):
        ref_parsed, ref_span = referent
        sk = self.ctx.phrase_key(parsed, span)
        rk = self.ctx.phrase_key(ref_parsed, ref_span)
        if sk != rk:
            self.ctx.graph.add_edge(sk, rk, PhraseEdge.COREF)

    def _nearest_preceding(self, idx, tok_index, want_label=None, exclude=None):
        """Search backward (current sentence then earlier ones) for a referent."""
        # current sentence, spans before tok_index
        for j in range(idx, -1, -1):
            parsed = self.ctx.sentences[j]
            cands = sorted(parsed.spans, key=lambda s: s.start, reverse=True)
            for sp in cands:
                if j == idx and sp.start >= tok_index:
                    continue
                if exclude and (parsed.sent_id, sp.start, sp.end) == exclude:
                    continue
                if want_label and sp.label != want_label:
                    continue
                # skip demonstrative referents themselves
                if parsed.doc[sp.start].lower_ in _DEMONSTRATIVES:
                    continue
                return (parsed, sp)
        return None
