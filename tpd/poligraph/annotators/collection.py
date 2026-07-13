"""Collection annotator."""

from __future__ import annotations

import json
from functools import lru_cache
from importlib import resources
from typing import Optional

from ..graph import Action
from ..phrase_graph import PhraseEdge
from .base import Annotator, AnnotatorContext, ParsedSentence


@lru_cache(maxsize=1)
def _patterns() -> dict:
    with resources.files("tpd.poligraph.data").joinpath("collection_patterns.json").open() as f:
        return json.load(f)


@lru_cache(maxsize=1)
def _verb2group() -> dict:
    out = {}
    for gname, g in _patterns()["verb_groups"].items():
        for v in g["verbs"]:
            out[v] = (gname, g["subject_action"], g["recipient_action"])
    return out


class CollectionAnnotator(Annotator):
    name = "collection"

    def annotate(self, ctx: AnnotatorContext) -> None:
        verb2group = _verb2group()
        neg_words = set(_patterns()["negation_words"])
        recip_preps = set(_patterns()["recipient_preps"])

        for parsed in ctx.sentences:
            doc = parsed.doc
            for token in doc:
                if token.pos_ not in ("VERB", "AUX"):
                    continue
                lemma = token.lemma_.lower()
                if lemma not in verb2group:
                    continue
                gname, subj_action, recip_action = verb2group[lemma]
                negative = self._is_negated(token, neg_words)
                if self._is_interrogative(doc):
                    continue

                subj_span = self._find_dep(parsed, token, ("nsubj", "nsubjpass"), "ENTITY")
                data_spans = self._find_deps(parsed, token, ("dobj", "obj", "nsubjpass"), "DATA")
                recip_span, prep_data = self._find_recipient(parsed, token, recip_preps)

                # Pattern "provide/give ENTITY with DATA": dobj is the recipient.
                ent_dobj = self._find_dep(parsed, token, ("dobj", "obj"), "ENTITY")
                if ent_dobj is not None and prep_data:
                    recip_span = recip_span or ent_dobj
                    data_spans = prep_data
                elif prep_data and not data_spans:
                    data_spans = prep_data

                if not data_spans:
                    continue

                subj_key = ctx.phrase_key(parsed, subj_span) if subj_span else None
                etype = PhraseEdge.NOT_COLLECT if negative else PhraseEdge.COLLECT
                data_subject = self._data_subject(token)

                # Do not sell does not imply do not collect.
                emit_subject = subj_key and not (negative and recip_action)

                for d in data_spans:
                    d_key = ctx.phrase_key(parsed, d)
                    if emit_subject:
                        self._edge(ctx, subj_key, d_key, etype, Action(subj_action),
                                   data_subject)
                    if recip_span is not None and recip_action:
                        r_key = ctx.phrase_key(parsed, recip_span)
                        self._edge(ctx, r_key, d_key, etype, Action(recip_action),
                                   data_subject)
                    elif recip_span is None and recip_action and negative:
                        r_key = ctx.unspecified_actor_key(parsed)
                        self._edge(ctx, r_key, d_key, etype, Action(recip_action),
                                   data_subject)

    # ----------------------------------------------------------- helpers
    @staticmethod
    def _edge(ctx, src, dst, etype, action, subject="general user"):
        ctx.graph.add_edge(src, dst, etype, action=action.value, subject=subject)

    # Data subjects currently recognised: children vs general user.
    _CHILD_WORDS = {"child", "children", "minor", "minors", "kid", "kids",
                    "teenager", "teenagers", "infant", "toddler"}

    def _data_subject(self, verb) -> str:
        """Detect the data subject, e.g. 'child' in 'collect ... from children'."""
        for tok in verb.subtree:
            if tok.dep_ == "prep" and tok.lower_ in ("from", "of", "about"):
                for pobj in tok.children:
                    if pobj.lemma_.lower() in self._CHILD_WORDS:
                        return "child"
            if tok.lemma_.lower() in self._CHILD_WORDS:
                return "child"
        return "general user"

    @staticmethod
    def _is_negated(verb, neg_words) -> bool:
        for child in verb.children:
            if child.dep_ == "neg":
                return True
            if child.lower_ in neg_words:
                return True
        # auxiliary "do not", "will never", "cannot"
        for child in verb.children:
            if child.dep_ in ("aux", "auxpass"):
                for g in child.children:
                    if g.lower_ in neg_words or g.dep_ == "neg":
                        return True
        return False

    @staticmethod
    def _is_interrogative(doc) -> bool:
        return doc.text.strip().endswith("?")

    def _find_dep(self, parsed: ParsedSentence, verb, deps, label) -> Optional[object]:
        spans = self._find_deps(parsed, verb, deps, label)
        return spans[0] if spans else None

    def _find_deps(self, parsed: ParsedSentence, verb, deps, label) -> list:
        out = []
        for child in verb.children:
            if child.dep_ in deps:
                sp = parsed.span_at(child.i)
                if sp and sp.label == label:
                    out.append(sp)
                # conjunctions: "name and email address"
                for conj in child.conjuncts:
                    spc = parsed.span_at(conj.i)
                    if spc and spc.label == label and spc not in out:
                        out.append(spc)
        # de-duplicate while preserving order
        seen, uniq = set(), []
        for sp in out:
            if (sp.start, sp.end) not in seen:
                seen.add((sp.start, sp.end))
                uniq.append(sp)
        return uniq

    def _find_recipient(self, parsed: ParsedSentence, verb, recip_preps):
        """Return (recipient ENTITY span, [DATA spans]) from with/to prep phrases."""
        recip = None
        data = []
        for tok in verb.subtree:
            if tok.dep_ == "prep" and tok.lower_ in recip_preps:
                for pobj in tok.children:
                    if pobj.dep_ in ("pobj", "obj"):
                        sp = parsed.span_at(pobj.i)
                        if sp and sp.label == "ENTITY" and recip is None:
                            recip = sp
                        elif sp and sp.label == "DATA":
                            data.append(sp)
        return recip, data
