"""Collect a Google Play app's document set."""

from __future__ import annotations

import re
import time
from urllib.parse import urljoin

from ..extract import parse_html
from .base import CollectedDoc, Corpus, Target, fetch

DETAILS_URL = "https://play.google.com/store/apps/details?id={app_id}&hl=en&gl=US"
DATASAFETY_URL = "https://play.google.com/store/apps/datasafety?id={app_id}&hl=en&gl=US"

# Developer privacy-policy link patterns sometimes present in rendered HTML.
_POLICY_HREF_RE = re.compile(r'https?://[^\s"\'<>]+', re.I)
_POLICY_CONTEXT_RE = re.compile(r"privacy", re.I)


def _extract_policy_url(html: str, base: str) -> str:
    parsed = parse_html(html)
    for text, href in parsed.links:
        if href.startswith(("javascript:", "mailto:", "#")):
            continue
        if _POLICY_CONTEXT_RE.search(f"{text} {href}"):
            absu = urljoin(base, href)
            # skip links back into Google's own help/policy pages
            if "play.google.com" in absu or "policies.google.com" in absu:
                continue
            if "support.google.com" in absu:
                continue
            return absu
    return ""


def collect_play_app(
    target: Target,
    corpus: Corpus,
    force: bool = False,
    delay: float = 0.3,
) -> list[CollectedDoc]:
    app_id = target.app_id or target.id
    docs: list[CollectedDoc] = []
    cache = corpus.cache_dir

    def _save(url: str, role: str) -> CollectedDoc | None:
        res = fetch(url, cache_dir=cache, force=force, delay=delay)
        doc = CollectedDoc(
            doc_id=f"{role}-{len(docs):02d}",
            url=res.final_url or url,
            role=role,
            http_status=res.status,
            content_type=res.content_type,
            fetched_at=time.time(),
            error=res.error,
        )
        if res.ok and res.text:
            corpus.save_doc(target.id, doc, res.text)
            docs.append(doc)
        return doc if res.ok else None

    listing = _save(DETAILS_URL.format(app_id=app_id), "store_listing")
    safety = _save(DATASAFETY_URL.format(app_id=app_id), "play_data_safety")

    # follow the developer's privacy policy link if recoverable
    policy_url = target.seed_policy_url
    if not policy_url:
        for src in (safety, listing):
            if src is not None:
                policy_url = _extract_policy_url(corpus.read_doc_html(src), src.url)
                if policy_url:
                    break
    if policy_url:
        _save(policy_url, "privacy_policy")

    corpus.write_manifest(target, docs)
    return docs
