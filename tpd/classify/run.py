"""Run the classifiers over a collected corpus."""

from __future__ import annotations

import os
import time
from concurrent.futures import ProcessPoolExecutor
from dataclasses import dataclass, field

from ..collect.base import Corpus
from ..extract import MAX_HTML_BYTES, parse_html
from .named_entities import first_party_tokens, load_ner
from .typology_clf import (
    DocClassification,
    TargetClassification,
    assemble_target,
    classify_document,
    classify_target,
)

_SHELL_ROLES = {"store_listing", "play_data_safety"}
# Set limit high enough to reach to the label.
_SHELL_MAX_BYTES = 800_000
_MR_ROLES = {"ads_txt", "app_ads_txt", "sellers_json", "vendors_json", "tcf_gvl"}
# Detect machine-readable from head.
_MR_MAX_BYTES = 200_000


@dataclass
class CorpusResult:
    targets: list[TargetClassification] = field(default_factory=list)
    doc_seconds: list[float] = field(default_factory=list)     # per-document
    target_seconds: list[float] = field(default_factory=list)  # per-document-set


def _max_bytes_for_role(role: str) -> int:
    if role in _SHELL_ROLES:
        return _SHELL_MAX_BYTES
    if role in _MR_ROLES:
        return _MR_MAX_BYTES
    return MAX_HTML_BYTES


def _init_worker() -> None:
    """Pool initializer"""
    for var in ("OMP_NUM_THREADS", "OPENBLAS_NUM_THREADS", "MKL_NUM_THREADS"):
        os.environ.setdefault(var, "1")


def _warm_ner_worker(use_ner: bool) -> None:
    """Load model before measuring latency."""
    load_ner(enable=use_ner)


def _classify_doc_job(payload: tuple) -> tuple[DocClassification, float]:
    """Parse + classify one document."""
    html, role, doc_id, url, target_type, first_party, use_ner = payload
    t0 = time.perf_counter()
    doc = parse_html(html, max_bytes=_max_bytes_for_role(role))
    ner_fn, _ = load_ner(enable=use_ner)  # cached
    dc = classify_document(
        doc, role=role, target_type=target_type, doc_id=doc_id, url=url,
        ner_fn=ner_fn, first_party=first_party,
    )
    return dc, time.perf_counter() - t0


def classify_corpus(
    corpus: Corpus,
    use_ner: bool = True,
    cache=None,
    target_ids: list[str] | None = None,
    workers: int = 1,
) -> CorpusResult:
    """Classify every target in the corpus."""
    result = CorpusResult()
    ids = target_ids if target_ids is not None else corpus.list_targets()
    parallel = workers > 1 and cache is None

    ner_fn, _ = load_ner(enable=use_ner) if not parallel else (None, None)
    pool = (
        ProcessPoolExecutor(max_workers=workers, initializer=_init_worker)
        if parallel else None
    )
    if pool is not None:
        # Load model first
        list(pool.map(_warm_ner_worker, [use_ner] * workers))
    try:
        for tid in ids:
            target, docs = corpus.read_manifest(tid)
            # Identify first-party tokens
            fp_urls = [target.seed_policy_url] + [
                d.url for d in docs if d.role in ("privacy_policy", "cookie_policy", "do_not_sell")
            ]
            first_party = first_party_tokens(fp_urls, name=target.name)
            ok_docs = [d for d in docs if d.ok]
            t_start = time.perf_counter()

            if parallel:
                payloads = [
                    (corpus.read_doc_html(d), d.role, d.doc_id,
                     d.url, target.type, first_party, use_ner)
                    for d in ok_docs
                ]
                doc_results = list(pool.map(_classify_doc_job, payloads))
                for _, secs in doc_results:
                    result.doc_seconds.append(secs)
                tc = assemble_target(target.type, tid, [dc for dc, _ in doc_results])
            else:
                parsed = []
                for d in ok_docs:
                    html = corpus.read_doc_html(d)
                    d0 = time.perf_counter()
                    doc = parse_html(html, max_bytes=_max_bytes_for_role(d.role))
                    parsed.append((doc, d.role, d.doc_id, d.url))
                    result.doc_seconds.append(time.perf_counter() - d0)
                tc = classify_target(
                    target.type, parsed, target_id=tid, ner_fn=ner_fn, backend=cache,
                    first_party=first_party,
                )

            result.target_seconds.append(time.perf_counter() - t_start)
            result.targets.append(tc)
    finally:
        if pool is not None:
            pool.shutdown()
    return result
