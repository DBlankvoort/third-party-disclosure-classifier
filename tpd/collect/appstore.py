"""Collect an Apple App Store app's document set."""

from __future__ import annotations

import json
import re
import time
from urllib.parse import urljoin

from ..extract import parse_html
from .base import CollectedDoc, Corpus, Target, fetch

LOOKUP_URL = "https://itunes.apple.com/lookup?id={app_id}&country=US"
LOOKUP_BUNDLE_URL = "https://itunes.apple.com/lookup?bundleId={bundle}&country=US"

_POLICY_CONTEXT_RE = re.compile(r"privacy", re.I)


def _lookup(app_id: str, cache, force) -> dict:
    url = (LOOKUP_BUNDLE_URL.format(bundle=app_id) if "." in app_id
           else LOOKUP_URL.format(app_id=app_id))
    res = fetch(url, cache_dir=cache, force=force)
    if not res.ok or not res.text:
        return {}
    try:
        data = json.loads(res.text)
    except Exception:  # noqa: BLE001
        return {}
    results = data.get("results") or []
    return results[0] if results else {}


def _extract_policy_url(html: str, base: str) -> str:
    parsed = parse_html(html)
    for text, href in parsed.links:
        if href.startswith(("javascript:", "mailto:", "#")):
            continue
        if _POLICY_CONTEXT_RE.search(f"{text} {href}"):
            absu = urljoin(base, href)
            if "apple.com" in absu:
                continue
            return absu
    return ""


def collect_app_store_app(
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

    meta = _lookup(app_id, cache, force)
    product_url = target.url or meta.get("trackViewUrl", "")
    seed_policy = target.seed_policy_url or meta.get("sellerUrl", "")

    listing = _save(product_url, "store_listing") if product_url else None

    policy_url = target.seed_policy_url
    if not policy_url and listing is not None:
        policy_url = _extract_policy_url(corpus.read_doc_html(listing), listing.url)
    if not policy_url:
        policy_url = seed_policy  # fall back to the developer/seller site
    if policy_url:
        _save(policy_url, "privacy_policy")

    corpus.write_manifest(target, docs)
    return docs
