"""List annotator."""

from __future__ import annotations

from collections import defaultdict

from ...extract import NodeType
from ..phrase_graph import PhraseEdge, PhraseLabel
from .base import Annotator, AnnotatorContext, ParsedSentence

_LIST_CUES = ("following", "below", "these", "types of", "categories of", "list of")


class ListAnnotator(Annotator):
    name = "list"

    def annotate(self, ctx: AnnotatorContext) -> None:
        self.ctx = ctx
        # Map a source segment to the parsed sentences generated from it.
        seg2parsed: dict[int, list[ParsedSentence]] = defaultdict(list)
        for p in ctx.sentences:
            if p.source is not None and p.source.segment is not None:
                seg2parsed[id(p.source.segment)].append(p)

        # Group list items by their parent segment.
        parent2items: dict[int, list] = defaultdict(list)
        parents: dict[int, object] = {}
        for p in ctx.sentences:
            seg = p.source.segment if p.source else None
            if seg is None or seg.type is not NodeType.LISTITEM:
                continue
            par = seg.parent
            if par is None:
                continue
            parents[id(par)] = par
            parent2items[id(par)].append(p)

        for pid, items in parent2items.items():
            parent = parents[pid]
            parent_parsed = seg2parsed.get(id(parent), [])
            self._subsume_following(ctx, parent, parent_parsed, items)
            self._propagate(ctx, parent_parsed, items)

    def _subsume_following(self, ctx, parent, parent_parsed, items) -> None:
        text = (parent.text or "").lower()
        if not any(cue in text for cue in _LIST_CUES):
            return
        # hypernym = a DATA span in the parent sentence
        hyper = None
        hp = None
        for p in parent_parsed:
            for sp in p.spans:
                if sp.label == "DATA":
                    hyper, hp = sp, p
                    break
            if hyper:
                break
        if hyper is None:
            return
        hk = ctx.phrase_key(hp, hyper)
        for item in items:
            for sp in item.spans:
                if sp.label == "DATA":
                    pk = ctx.phrase_key(item, sp)
                    ctx.graph.add_edge(hk, pk, PhraseEdge.SUBSUME)

    def _propagate(self, ctx, parent_parsed, items) -> None:
        """Copy COLLECT edges from the lead-in sentence onto each list item."""
        # find a COLLECT edge originating in the parent sentence
        lead_edges = []
        for p in parent_parsed:
            for e in ctx.graph.edges:
                if e.etype in (PhraseEdge.COLLECT, PhraseEdge.NOT_COLLECT):
                    src = ctx.graph.phrases.get(e.src)
                    if src and src.sent_id == p.sent_id and src.label == PhraseLabel.ENTITY:
                        lead_edges.append((e, src))
        if not lead_edges:
            return
        for item in items:
            # only propagate if the item has DATA but no entity of its own
            data_spans = [sp for sp in item.spans if sp.label == "DATA"]
            has_entity = any(sp.label == "ENTITY" for sp in item.spans)
            if not data_spans or has_entity:
                continue
            for (e, src) in lead_edges:
                for sp in data_spans:
                    dk = ctx.phrase_key(item, sp)
                    ctx.graph.add_edge(src.key, dk, e.etype,
                                       action=e.attrs.get("action", "collect"))
