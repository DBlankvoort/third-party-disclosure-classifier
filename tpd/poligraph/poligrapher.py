"""Generate a PoliGraph."""

from __future__ import annotations

from typing import Optional

from .annotators import DEFAULT_ANNOTATORS, AnnotatorContext, ParsedSentence
from .document import DocumentTree
from .graph import Action, EdgeType, Purpose, PoliGraph
from .nlp import NLP, get_nlp
from .normalize import PhraseNormalizer
from .phrase_graph import PhraseEdge, PhraseGraph, PhraseLabel
from .purpose import PurposeClassifier, default_purpose_classifier


class PoliGrapher:
    """Builds a PoliGraph from a policy's text."""

    def __init__(
        self,
        nlp: Optional[NLP] = None,
        normalizer: Optional[PhraseNormalizer] = None,
        purpose_classifier: Optional[PurposeClassifier] = None,
        annotators=None,
    ):
        self.nlp = nlp or get_nlp()
        self.normalizer = normalizer or PhraseNormalizer(
            lemmatizer=_make_lemmatizer(self.nlp))
        self.purpose_clf = purpose_classifier or default_purpose_classifier()
        self.annotator_classes = annotators or DEFAULT_ANNOTATORS

    # ------------------------------------------------------------------ public
    def from_html(self, html: str, policy_id: Optional[str] = None) -> PoliGraph:
        return self._run(DocumentTree.from_html(html), policy_id)

    def from_text(self, text: str, policy_id: Optional[str] = None) -> PoliGraph:
        return self._run(DocumentTree.from_text(text), policy_id)

    def build_phrase_graph(self, tree: DocumentTree) -> tuple[PhraseGraph, list[ParsedSentence]]:
        parsed = self._parse(tree)
        pg = PhraseGraph()
        ctx = AnnotatorContext(parsed, pg)
        for cls in self.annotator_classes:
            cls().annotate(ctx)
        return pg, parsed

    # --------------------------------------------------------------- internals
    def _run(self, tree: DocumentTree, policy_id) -> PoliGraph:
        pg, _ = self.build_phrase_graph(tree)
        return self._build_poligraph(pg, policy_id)

    def _parse(self, tree: DocumentTree) -> list[ParsedSentence]:
        sentences = tree.sentences()
        parsed: list[ParsedSentence] = []
        # Deduplicate identical texts.
        texts = [s.text for s in sentences]
        docs = list(self.nlp.nlp.pipe(texts))
        for sid, (sent, doc) in enumerate(zip(sentences, docs)):
            spans = self.nlp.entities(doc)
            parsed.append(ParsedSentence(sid, doc, spans, source=sent))
        return parsed

    # -------------------------------------------- phrase graph -> PoliGraph
    def _build_poligraph(self, pg: PhraseGraph, policy_id) -> PoliGraph:
        norm = self._normalized_forms(pg)
        graph = PoliGraph(policy_id=policy_id)

        # Pre-compute purpose set.
        data_purposes: dict[str, set[Purpose]] = {}
        for e in pg.edges_of(PhraseEdge.PURPOSE):
            pphrase = pg.phrases.get(e.dst)
            if pphrase is None:
                continue
            labels = self.purpose_clf.classify(pphrase.text)
            data_purposes.setdefault(e.src, set()).update(labels)

        # COLLECT / NOT_COLLECT edges.
        for e in pg.edges:
            if e.etype not in (PhraseEdge.COLLECT, PhraseEdge.NOT_COLLECT):
                continue
            src, dst = pg.phrases.get(e.src), pg.phrases.get(e.dst)
            if not src or not dst:
                continue
            ent, data = norm.get(e.src), norm.get(e.dst)
            if not ent or not data:
                continue
            graph.add_entity(ent)
            graph.add_data(data)
            purposes = data_purposes.get(e.dst, set())
            action = Action(e.attrs.get("action", "collect"))
            subject = e.attrs.get("subject", "general user")
            graph.add_collect(
                ent, data,
                negative=(e.etype == PhraseEdge.NOT_COLLECT),
                action=action, purposes=purposes, subject=subject,
                text=src.text + " ... " + dst.text,
            )

        # SUBSUME edges.
        for e in pg.edges_of(PhraseEdge.SUBSUME):
            hyper, hypo = norm.get(e.src), norm.get(e.dst)
            src, dst = pg.phrases.get(e.src), pg.phrases.get(e.dst)
            if not hyper or not hypo or not src or not dst:
                continue
            if src.label != dst.label:
                continue
            if src.label == PhraseLabel.DATA:
                graph.add_data(hyper)
                graph.add_data(hypo)
            else:
                graph.add_entity(hyper)
                graph.add_entity(hypo)
            graph.add_subsume(hyper, hypo)

        return graph.validate()

    def _normalized_forms(self, pg: PhraseGraph) -> dict[str, str]:
        """Map each phrase key to its normalized term."""
        coref = {e.src: e.dst for e in pg.edges_of(PhraseEdge.COREF)}

        def resolve(key: str, seen=None) -> str:
            seen = seen or set()
            while key in coref and key not in seen:
                seen.add(key)
                key = coref[key]
            return key

        out: dict[str, str] = {}
        for key, phrase in pg.phrases.items():
            target = resolve(key)
            tphrase = pg.phrases[target]
            if tphrase.label == PhraseLabel.DATA:
                out[key] = self.normalizer.normalize_data(tphrase.text)
            elif tphrase.label == PhraseLabel.ENTITY:
                out[key] = self.normalizer.normalize_entity(tphrase.text)
            else:
                out[key] = None
        return out


def _make_lemmatizer(nlp: NLP):
    def _lem(text: str) -> str:
        try:
            doc = nlp.nlp(text)
            return " ".join(t.lemma_.lower() for t in doc if not t.is_punct and not t.is_space)
        except Exception:
            return text
    return _lem
