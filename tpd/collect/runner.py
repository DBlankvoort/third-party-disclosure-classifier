"""Crawl runner infrastructure."""

from __future__ import annotations

import csv
import random
from collections import OrderedDict
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait
from pathlib import Path

from ..classify.document_class import classify_medium
from ..extract import parse_html
from ..typology import TargetType
from .appstore import collect_app_store_app
from .base import CollectedDoc, Corpus, Target
from .playstore import collect_play_app
from .web import collect_website

_DISPATCH = {
    TargetType.WEBSITE.value: collect_website,
    TargetType.DATA_BROKER.value: collect_website,   # data brokers crawled as websites
    TargetType.PLAY_STORE_APP.value: collect_play_app,
    TargetType.APP_STORE_APP.value: collect_app_store_app,
}

# Bytes of a store/registry shell we bother to parse for usability check.
_USABILITY_MAX_BYTES = 400_000


def collect_target(
    target: Target, corpus: Corpus, force: bool = False, delay: float = 0.3
) -> list[CollectedDoc]:
    fn = _DISPATCH.get(target.type)
    if fn is None:
        raise ValueError(f"unknown target type: {target.type!r}")
    return fn(target, corpus, force=force, delay=delay)


def load_seeds(path: str | Path) -> list[Target]:
    """Load one seed CSV into :class:`Target` objects."""
    targets: list[Target] = []
    with open(path, newline="", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            if not row or (row.get("type") or "").startswith("#"):
                continue
            ttype = (row.get("type") or "").strip()
            if not ttype:
                continue
            name = (row.get("name") or "").strip()
            url = (row.get("url") or "").strip()
            app_id = (row.get("app_id") or "").strip()
            raw_id = name or url or app_id
            targets.append(
                Target(
                    id=f"{ttype}__{Target.make_id(raw_id)}",
                    type=ttype,
                    name=name,
                    url=url,
                    app_id=app_id,
                    seed_policy_url=(row.get("seed_policy_url") or "").strip(),
                )
            )
    return targets

def load_seed_dir(seed_dir: str | Path) -> list[Target]:
    """Load every CSV under ``seed_dir``."""
    targets: list[Target] = []
    for p in sorted(Path(seed_dir).glob("*.csv")):
        targets.extend(load_seeds(p))
    return targets

def collect_seeds(
    seeds: list[Target],
    corpus: Corpus,
    force: bool = False,
    delay: float = 0.3,
    limit: int | None = None,
    progress=None,
) -> dict[str, list[CollectedDoc]]:
    """Collect a list of targets."""
    out: dict[str, list[CollectedDoc]] = {}
    for i, t in enumerate(seeds):
        if limit is not None and i >= limit:
            break
        try:
            out[t.id] = collect_target(t, corpus, force=force, delay=delay)
        except Exception as exc:  # noqa: BLE001
            out[t.id] = []
            if progress:
                progress(t, f"ERROR {exc}")
            continue
        if progress:
            ok = sum(1 for d in out[t.id] if d.ok)
            progress(t, f"{ok}/{len(out[t.id])} docs")
    return out


# --------------------------------------------------------------------------- #
# Check usability
# ---------------------------------------------------------------------------
def docset_usable(corpus: Corpus, target_type: str, docs: list[CollectedDoc]) -> bool:
    """Does this document set carry at least one valid disclosure document?

    Uses :func:`tpd.classify.document_class.classify_medium` to predict whether a valid medium will be found.
    """
    for d in docs:
        if not d.ok:
            continue
        html = corpus.read_doc_html(d)
        if d.role in ("store_listing", "play_data_safety") or d.role.endswith(("_json", "ads_txt")): # Common large pages, machine-readable pages recognizable as such in the first few rows.
            html = html[:_USABILITY_MAX_BYTES]
        doc = parse_html(html)
        if classify_medium(doc, role=d.role, target_type=target_type).medium is not None:
            return True
    return False

def usable_target_ids(corpus: Corpus) -> list[str]:
    """Target ids whose document set is usable (:func:`docset_usable`).

    Removes document sets without usable docs.
    """
    ids: list[str] = []
    for tid in corpus.list_targets():
        target, docs = corpus.read_manifest(tid)
        if docset_usable(corpus, target.type, docs):
            ids.append(tid)
    return ids


def collect_stratified(
    seeds: list[Target],
    corpus: Corpus,
    per_type: int,
    workers: int = 8,
    delay: float = 0.3,
    force: bool = False,
    seed: int = 0,
    oversample: int = 8,
    progress=None,
) -> dict[str, list[CollectedDoc]]:
    """Collect a corpus balanced across target types."""
    rng = random.Random(seed)
    out: dict[str, list[CollectedDoc]] = {}

    def _safe(t: Target) -> tuple[Target, list[CollectedDoc]]:
        try:
            return t, collect_target(t, corpus, force=force, delay=delay)
        except Exception as exc:  # noqa: BLE001
            if progress:
                progress(t, f"ERROR {exc}")
            return t, []

    for ttype, candidates in group_by_type(seeds).items():
        pool = candidates[:]
        rng.shuffle(pool)
        max_attempts = min(len(pool), per_type * oversample) if per_type else len(pool)
        usable = 0
        attempted = 0
        idx = 0
        with ThreadPoolExecutor(max_workers=workers) as ex:
            pending: set = set()
            futmap: dict = {}

            def _submit() -> bool:
                nonlocal idx, attempted
                if idx >= max_attempts or usable >= per_type:
                    return False
                t = pool[idx]
                idx += 1
                attempted += 1
                fut = ex.submit(_safe, t)
                pending.add(fut)
                futmap[fut] = t
                return True

            for _ in range(workers):
                if not _submit():
                    break
            while pending:
                done, pending = wait(pending, return_when=FIRST_COMPLETED)
                for fut in done:
                    t = futmap.pop(fut)
                    _, docs = fut.result()
                    out[t.id] = docs
                    good = docset_usable(corpus, t.type, docs)
                    if good:
                        usable += 1
                    if progress:
                        progress(t, f"{'USABLE' if good else 'skip  '} "
                                     f"[{ttype}] {usable}/{per_type} (tried {attempted})")
                if usable < per_type:
                    while len(pending) < workers and _submit():
                        pass
        if progress:
            progress(Target(id="", type=ttype),
                     f"== {ttype}: {usable} usable from {attempted} attempts ==")
    return out