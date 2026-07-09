"""Run the `tpd` tool against a single live URL."""

from __future__ import annotations

from pathlib import Path
from urllib.parse import urlparse

from tpd.collect.base import Corpus, Target
from tpd.collect.registry import augment_corpus
from tpd.collect.runner import collect_target, docset_usable
from tpd.classify.run import classify_corpus
from tpd.typology import TargetType, media_of

def _empty(origin: str, target_id: str, cached: bool) -> dict:
    return {
        "origin": origin,
        "target_id": target_id,
        "cached": cached,
        "classified": False,
        "usable": False,
        "typology_class": "",
        "facets": [],
        "media_present": [],
        "specificities": [],
        "relevant_docs": 0,
        "fetched_docs": 0,
        "failed_urls": [],
        "named_orgs": [],
        "category_terms": [],
        "documents": [],
    }


def analyze_url(
    url: str,
    corpus_root: str | Path,
    use_ner: bool = True,
    force: bool = False,
    delay: float = 0.2,
) -> dict:
    """Collect + classify the origin of a URL and return a UI-ready dict."""
    origin = origin_of(url)
    corpus = Corpus(corpus_root)
    target = _target_for(origin)

    manifest = corpus.root / target.id / "manifest.json"
    cached = manifest.exists() and not force

    if not cached:
        # Discover the document set, then machine-readable registries.
        collect_target(target, corpus, force=force, delay=delay)
        augment_corpus(corpus, target_ids=[target.id], force=force, delay=delay, workers=4)

    # Classify the target.
    result = classify_corpus(corpus, use_ner=use_ner, target_ids=[target.id])
    if not result.targets:
        return _empty(origin, target.id, cached)
    tc = result.targets[0]

    _, raw_docs = corpus.read_manifest(target.id)
    usable = docset_usable(corpus, target.type, raw_docs)
    fetched = len(raw_docs)
    failed = [d.url for d in raw_docs if not d.ok]

    # Per-document view.
    documents = [
        {
            "role": d.role,
            "url": d.url,
            "medium": d.medium or None,
            "relevant": bool(d.relevant),
            "facets": list(d.facets),
            "named_orgs": list(d.named_orgs),
            "category_terms": list(d.category_terms),
            "reason": d.doc_class_reason,
        }
        for d in tc.docs
    ]

    # Aggregate view.
    media_present = sorted(m.value for m in media_of(set(tc.facets)))
    named = sorted({o for d in tc.docs for o in d.named_orgs}, key=str.lower)
    categories = sorted({c for d in tc.docs for c in d.category_terms}, key=str.lower)
    specificities = sorted({f.split(":", 1)[1] for f in tc.facets if ":" in f})

    return {
        "origin": origin,
        "target_id": target.id,
        "cached": cached,
        "classified": bool(tc.classified),
        "usable": bool(usable),
        "typology_class": tc.typology_class,
        "facets": list(tc.facets),
        "media_present": media_present,
        "specificities": specificities,
        "relevant_docs": tc.relevant_docs,
        "fetched_docs": fetched,
        "failed_urls": failed,
        "named_orgs": named,
        "category_terms": categories,
        "documents": documents,
    }