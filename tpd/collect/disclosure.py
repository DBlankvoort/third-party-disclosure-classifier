"""Augment an existing corpus with additional documents."""

from __future__ import annotations

import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from urllib.parse import urljoin, urlparse

from ..typology import TargetType
from .base import CollectedDoc, Corpus, Target, fetch
from .web import (
    COMMON_COMPANION_PATHS,
    _COMPANION_ROLES,
    _content_key,
    _discover_links,
    _same_site,
)

_WEB_TYPES = {TargetType.WEBSITE.value, TargetType.DATA_BROKER.value}
# Companion disclosure roles worth back-filling.
_BACKFILL_ROLES = {"vendor_list", "subprocessor_list", "do_not_sell", "partners_page"}


def _policy_host(docs: list[CollectedDoc]) -> str:
    for d in docs:
        if d.role == "privacy_policy" and d.url:
            return urlparse(d.url).netloc
    return ""


def augment_disclosure_target(
    corpus: Corpus,
    target: Target,
    docs: list[CollectedDoc],
    force: bool = False,
    delay: float = 0.3,
) -> list[CollectedDoc]:
    """Discover + append missing companion disclosure docs."""
    if target.type not in _WEB_TYPES:
        return []
    existing_roles = {d.role for d in docs}
    existing_urls = {d.url for d in docs}
    base_host = urlparse(target.url).netloc if target.url else ""
    policy_host = _policy_host(docs)

    # 1. Candidate (url -> role) from links in the already-saved documents
    #    + a live re-fetch of the homepage.
    candidates: dict[str, str] = {}
    htmls = [(corpus.read_doc_html(d), d.url) for d in docs if d.ok]
    if target.url:
        hr = fetch(target.url, cache_dir=corpus.cache_dir, force=force, delay=delay)
        if hr.ok and hr.text:
            htmls.append((hr.text, hr.final_url or target.url))
    for html, base in htmls:
        for role, urls in _discover_links(html, base).items():
            for url in urls:
                if (role in _BACKFILL_ROLES and role not in existing_roles
                        and url not in existing_urls
                        and _same_site(url, base_host, policy_host)):
                    candidates.setdefault(url, role)

    # 2. Conventional paths still missing.
    roots = []
    for h in (base_host, policy_host):
        if h and (r := f"https://{h}") not in roots:
            roots.append(r)
    for role, paths in COMMON_COMPANION_PATHS:
        if role not in _BACKFILL_ROLES or role in existing_roles:
            continue
        if any(r == role for r in candidates.values()):
            continue
        for root in roots:
            for path in paths:
                candidates.setdefault(urljoin(root + "/", path.lstrip("/")), role)

    # 3. Fetch candidates
    seen_hashes = {_content_key(corpus.read_doc_html(d)) for d in docs if d.ok}
    new_docs: list[CollectedDoc] = []
    base_idx = len(docs)
    for url, role in candidates.items():
        res = fetch(url, cache_dir=corpus.cache_dir, force=force, delay=delay)
        if not (res.ok and res.text):
            continue
        key = _content_key(res.text)
        if key in seen_hashes:
            continue
        doc = CollectedDoc(
            doc_id=f"{role}-{base_idx + len(new_docs):02d}",
            url=res.final_url or url,
            role=role,
            http_status=res.status,
            content_type=res.content_type,
            fetched_at=time.time(),
        )
        corpus.save_doc(target.id, doc, res.text)
        seen_hashes.add(key)
        new_docs.append(doc)
    if new_docs:
        corpus.write_manifest(target, docs + new_docs)
    return new_docs


def augment_disclosure_corpus(
    corpus: Corpus,
    target_ids: list[str] | None = None,
    force: bool = False,
    delay: float = 0.3,
    workers: int = 8,
    progress=None,
) -> dict[str, list[CollectedDoc]]:
    """Back-fill companion disclosure docs."""
    ids = target_ids if target_ids is not None else corpus.list_targets()
    added: dict[str, list[CollectedDoc]] = {}

    def _one(tid: str):
        target, docs = corpus.read_manifest(tid)
        return tid, target, augment_disclosure_target(corpus, target, docs,
                                                       force=force, delay=delay)

    with ThreadPoolExecutor(max_workers=workers) as ex:
        for fut in as_completed(ex.submit(_one, tid) for tid in ids):
            try:
                tid, target, new = fut.result()
            except Exception as exc:  # noqa: BLE00
                if progress:
                    progress(None, f"ERROR {exc}")
                continue
            if new:
                added[tid] = new
            if progress and new:
                progress(target, f"+{len(new)}: " + ",".join(d.role for d in new))
    return added
