"""Run the `tpd` tool against a single live URL."""

from __future__ import annotations

import re
from pathlib import Path
from urllib.parse import urlparse

from tpd.collect.base import Corpus, Target
from tpd.collect.runner import fetch_target
from tpd.classify.named_entities import first_party_tokens
from tpd.classify.poligraph_connector import (
    merge_relations,
    poligraph_available,
    target_relations,
)
from tpd.classify.structured_relations import structured_relations_for_target
from tpd.classify.run import classify_corpus
from tpd.extract import parse_html
from tpd.typology import TargetType, media_of


def origin_of(url: str) -> str:
    """Get origin of URL."""
    p = urlparse(url.strip())
    if p.scheme not in ("http", "https") or not p.netloc:
        raise ValueError(f"not an http(s) URL: {url!r}")
    return f"{p.scheme}://{p.netloc}"


def _target_for(origin: str) -> Target:
    """A website Target for one origin."""
    host = urlparse(origin).netloc
    return Target(
        id=f"{TargetType.WEBSITE.value}__{Target.make_id(host)}",
        type=TargetType.WEBSITE.value,
        name=host,
        url=origin,
    )


_EMAIL_RE = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9][A-Za-z0-9.-]*\.[A-Za-z]{2,}")
# False positives for e-mails.
_EMAIL_STOP_RE = re.compile(
    r"\.(?:png|jpe?g|gif|svg|webp|css|js)$|@(?:\dx\.|example\.|sentry\.)", re.I,
)
_PRIVACY_LOCAL_RE = re.compile(
    r"^(?:privacy|dpo|data[.-]?protection|legal|compliance|ccpa|gdpr)", re.I,
)
_RIGHTS_LINK_ROLES = ("do_not_sell", "privacy_policy", "cookie_policy", "dpa")


def _rights_info(corpus: Corpus, raw_docs) -> dict:
    """Links + contacts to act on."""
    links = {}
    for role in _RIGHTS_LINK_ROLES:
        doc = next((d for d in raw_docs if d.role == role and d.ok), None)
        if doc:
            links[role] = doc.url

    emails: list[str] = []
    seen: set[str] = set()

    def add(addr: str) -> None:
        addr = addr.strip().strip(".,;:")
        k = addr.lower()
        if (k and k not in seen and _EMAIL_RE.fullmatch(addr)
                and not _EMAIL_STOP_RE.search(k)):
            seen.add(k)
            emails.append(addr)

    for d in raw_docs:
        if d.role not in _RIGHTS_LINK_ROLES or not d.ok:
            continue
        doc = parse_html(corpus.read_doc_html(d))
        for _, href in doc.links:
            if href.lower().startswith("mailto:"):
                add(href[7:].split("?")[0])
        for m in _EMAIL_RE.findall(doc.text):
            add(m)
    # Contacts whose mailbox names a privacy function first.
    emails.sort(key=lambda e: (not _PRIVACY_LOCAL_RE.match(e), len(e)))
    return {"links": links, "emails": emails[:3]}


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
        "sharing_relations": [],
        "poligraph": False,
        "rights": {"links": {}, "emails": []},
    }


def analyze_url(
    url: str,
    corpus_root: str | Path,
    use_ner: bool = True,
    use_poligraph: bool = True,
    force: bool = False,
    delay: float = 0.2,
) -> dict:
    """Collect + classify the origin of a URL."""
    origin = origin_of(url)
    corpus = Corpus(corpus_root)
    target = _target_for(origin)

    _html_cache: dict[str, str] = {}
    _read_doc_html = corpus.read_doc_html

    def _cached_read_doc_html(doc):
        if doc.doc_id not in _html_cache:
            _html_cache[doc.doc_id] = _read_doc_html(doc)
        return _html_cache[doc.doc_id]

    corpus.read_doc_html = _cached_read_doc_html

    manifest = corpus.root / target.id / "manifest.json"
    cached = manifest.exists() and not force

    if not cached:
        fetch_target(target, corpus, force=force, delay=delay)

    # Classify the target.
    result = classify_corpus(corpus, use_ner=use_ner, target_ids=[target.id])
    if not result.targets:
        return _empty(origin, target.id, cached)
    tc = result.targets[0]

    _, raw_docs = corpus.read_manifest(target.id)
    usable = any(d.medium for d in tc.docs)
    fetched = len(raw_docs)
    failed = [d.url for d in raw_docs if not d.ok]

    # Run PoliGraph to capture sharing relationships.
    fp_urls = [target.seed_policy_url] + [
        d.url for d in raw_docs
        if d.role in ("privacy_policy", "cookie_policy", "do_not_sell")
    ]
    first_party = first_party_tokens(fp_urls, name=target.name)
    poligraph_on = use_poligraph and poligraph_available()
    prose_rels = target_relations(
        corpus, target.id, raw_docs, first_party=first_party, force=force,
    ) if poligraph_on else []
    structured_rels = structured_relations_for_target(
        corpus, raw_docs, first_party=first_party,
    )
    sharing = merge_relations([prose_rels, structured_rels])

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
        "sharing_relations": sharing,
        "poligraph": poligraph_on,
        "rights": _rights_info(corpus, raw_docs),
    }
