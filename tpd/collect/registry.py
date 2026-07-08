"""Augment a corpus with machine-readable registry files."""

from __future__ import annotations

import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from urllib.parse import urljoin, urlparse

from .. import lexicons
from ..typology import TargetType
from .base import CollectedDoc, Corpus, Target, fetch

# (conventional path, doc role) per target type.
_WEB_PATHS = [
    ("/ads.txt", "ads_txt"),
    ("/sellers.json", "sellers_json"),
    ("/vendors.json", "vendors_json"),
]
_APP_PATHS = [
    ("/app-ads.txt", "app_ads_txt"),
    ("/sellers.json", "sellers_json"),
    ("/vendors.json", "vendors_json"),
]
_APP_TYPES = {TargetType.PLAY_STORE_APP.value, TargetType.APP_STORE_APP.value}


def registry_paths_for(target_type: str) -> list[tuple[str, str]]:
    return _APP_PATHS if target_type in _APP_TYPES else _WEB_PATHS


def _root_url(target: Target, docs: list[CollectedDoc]) -> str:
    """Find the best host root for a target's domain."""
    skip = ("play.google.com", "itunes.apple.com", "apps.apple.com", "policies.google.com")
    for cand in (target.seed_policy_url, target.url, *(d.url for d in docs if d.role == "privacy_policy"),
                 *(d.url for d in docs)):
        if not cand:
            continue
        p = urlparse(cand)
        if p.netloc and not any(s in p.netloc for s in skip):
            return f"{p.scheme or 'https'}://{p.netloc}"
    return ""


def augment_target(
    corpus: Corpus,
    target: Target,
    docs: list[CollectedDoc],
    force: bool = False,
    delay: float = 0.3,
) -> list[CollectedDoc]:
    """Fetch registry files."""
    root = _root_url(target, docs)
    if not root:
        return []
    existing_roles = {d.role for d in docs}
    new_docs: list[CollectedDoc] = []
    base_idx = len(docs)
    for path, role in registry_paths_for(target.type):
        if role in existing_roles:
            continue
        url = urljoin(root + "/", path.lstrip("/"))
        res = fetch(url, cache_dir=corpus.cache_dir, force=force, delay=delay)
        kind = lexicons.machine_readable_kind(res.text) if (res.ok and res.text) else ""
        if not kind:
            continue  # dead URL / 404 HTML / empty
        doc = CollectedDoc(
            doc_id=f"{role}-{base_idx + len(new_docs):02d}",
            url=res.final_url or url,
            role=role,
            http_status=res.status,
            content_type=res.content_type,
            fetched_at=time.time(),
        )
        corpus.save_doc(target.id, doc, res.text)
        new_docs.append(doc)
    if new_docs:
        corpus.write_manifest(target, docs + new_docs)
    return new_docs


def augment_corpus(
    corpus: Corpus,
    target_ids: list[str] | None = None,
    force: bool = False,
    delay: float = 0.3,
    workers: int = 8,
    progress=None,
) -> dict[str, list[CollectedDoc]]:
    """Augment every (or a chosen subset of) target with its registry files."""
    ids = target_ids if target_ids is not None else corpus.list_targets()
    added: dict[str, list[CollectedDoc]] = {}

    def _one(tid: str):
        target, docs = corpus.read_manifest(tid)
        return tid, target, augment_target(corpus, target, docs, force=force, delay=delay)

    with ThreadPoolExecutor(max_workers=workers) as ex:
        for fut in as_completed(ex.submit(_one, tid) for tid in ids):
            try:
                tid, target, new = fut.result()
            except Exception as exc:  # noqa: BLE001
                if progress:
                    progress(None, f"ERROR {exc}")
                continue
            if new:
                added[tid] = new
            if progress:
                roles = ",".join(d.role for d in new) or "-"
                progress(target, f"{len(new)} registry doc(s): {roles}")
    return added
