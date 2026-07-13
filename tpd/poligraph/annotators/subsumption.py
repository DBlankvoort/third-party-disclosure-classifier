"""Subsumption annotator."""

from __future__ import annotations

from ..phrase_graph import PhraseEdge
from .base import Annotator, AnnotatorContext, ParsedSentence

# Cue phrases that introduce hyponyms after a hypernym.
_FORWARD_CUES = [
    ("such", "as"),          # X such as Y1, Y2
    ("for", "example"),      # X, for example, Y
    ("including",),          # X including Y / X, including but not limited to Y
    ("like",),               # X like Y
    ("especially",),         # X, especially Y
    ("particularly",),       # X, particularly Y
    ("e.g.",),
    ("i.e.",),
]


class SubsumptionAnnotator(Annotator):
    name = "subsumption"

    def annotate(self, ctx: AnnotatorContext) -> None:
        for parsed in ctx.sentences:
            self._such_as(ctx, parsed)
            self._which_includes(ctx, parsed)
            self._collectively(ctx, parsed)

    # X such as Y1, Y2 ... / X including/like/e.g. Y1, Y2
    def _such_as(self, ctx, parsed: ParsedSentence) -> None:
        doc = parsed.doc
        for i, tok in enumerate(doc):
            cue = self._match_cue(doc, i)
            if not cue:
                continue
            cue_start, cue_end = cue
            # hypernym: nearest DATA/ENTITY span ending at/before cue_start
            hyper = self._span_before(parsed, cue_start)
            if hyper is None:
                continue
            # hyponyms: spans after the cue, same label, until sentence break
            hypos = self._spans_after(parsed, cue_end, hyper.label, limit=8)
            for hypo in hypos:
                self._subsume(ctx, parsed, hyper, hypo)

    # X, which includes Y / X includes Y
    def _which_includes(self, ctx, parsed: ParsedSentence) -> None:
        doc = parsed.doc
        for tok in doc:
            if tok.lemma_.lower() in ("include", "comprise", "encompass") and tok.pos_ in ("VERB", "AUX"):
                subj = None
                for c in tok.children:
                    if c.dep_ in ("nsubj", "nsubjpass", "relcl"):
                        subj = parsed.span_at(c.i)
                if subj is None:
                    subj = self._span_before(parsed, tok.i)
                if subj is None:
                    continue
                for c in tok.children:
                    if c.dep_ in ("dobj", "obj", "attr"):
                        hypo = parsed.span_at(c.i)
                        if hypo:
                            self._subsume(ctx, parsed, subj, hypo)
                        for conj in c.conjuncts:
                            hc = parsed.span_at(conj.i)
                            if hc:
                                self._subsume(ctx, parsed, subj, hc)

    # Y1, Y2 ... (collectively X)
    def _collectively(self, ctx, parsed: ParsedSentence) -> None:
        doc = parsed.doc
        for i, tok in enumerate(doc):
            if tok.lower_ in ("collectively", "together") and i + 1 < len(doc):
                hyper = self._spans_after(parsed, i + 1, None, limit=1)
                if not hyper:
                    continue
                hyper = hyper[0]
                hypos = [sp for sp in parsed.spans
                         if sp.end <= i and sp.label == hyper.label]
                for hypo in hypos[-6:]:
                    self._subsume(ctx, parsed, hyper, hypo)

    # ------------------------------------------------------------- helpers
    def _subsume(self, ctx, parsed, hyper, hypo) -> None:
        if hyper.label != hypo.label or (hyper.start, hyper.end) == (hypo.start, hypo.end):
            return
        hk = ctx.phrase_key(parsed, hyper)
        pk = ctx.phrase_key(parsed, hypo)
        ctx.graph.add_edge(hk, pk, PhraseEdge.SUBSUME)

    @staticmethod
    def _match_cue(doc, i):
        for cue in _FORWARD_CUES:
            if i + len(cue) <= len(doc):
                if all(doc[i + j].lower_.rstrip(",") == cue[j] for j in range(len(cue))):
                    # "including but not limited to" still starts with "including"
                    return (i, i + len(cue))
        return None

    @staticmethod
    def _span_before(parsed, tok_index):
        sent = parsed.doc[tok_index].sent
        best = None
        for sp in parsed.spans:
            if sp.start < sent.start or sp.start >= sent.end:
                continue
            if sp.end <= tok_index + 1 and (best is None or sp.end > best.end):
                best = sp
        return best

    @staticmethod
    def _spans_after(parsed, tok_index, label, limit):
        sent = parsed.doc[tok_index].sent if tok_index < len(parsed.doc) else None
        out = []
        for sp in sorted(parsed.spans, key=lambda s: s.start):
            if sent is not None and (sp.start < sent.start or sp.start >= sent.end):
                continue
            if sp.start >= tok_index and (label is None or sp.label == label):
                out.append(sp)
                if len(out) >= limit:
                    break
        return out
