"""Crawl runner infrastructure."""

from __future__ import annotations

import csv
import time
import random
from collections import OrderedDict
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait
from dataclasses import dataclass, field
from pathlib import Path
import sys
from urllib.parse import urljoin, urlparse

from .. import lexicons
from ..typology import TargetType
from .base import CollectedDoc, Corpus, Target, fetch
from ..classify.document_class import classify_medium
from ..extract import parse_html
from .appstore import collect_app_store_app
from .playstore import collect_play_app
from .web import (
    COMMON_COMPANION_PATHS,
    collect_website,
    _content_key,
    _discover_links,
    _same_site,
)

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
    """Target ids whose document set is usable (:func:`docset_usable`)."""
    ids: list[str] = []
    for tid in corpus.list_targets():
        target, docs = corpus.read_manifest(tid)
        if docset_usable(corpus, target.type, docs):
            ids.append(tid)
    return ids


def group_by_type(seeds: list[Target]) -> "OrderedDict[str, list[Target]]":
    groups: OrderedDict[str, list[Target]] = OrderedDict()
    for t in seeds:
        groups.setdefault(t.type, []).append(t)
    return groups


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
) -> tuple[dict[str, list[CollectedDoc]], set[str]]:
    """Collect a corpus balanced across target types."""
    rng = random.Random(seed)
    out: dict[str, list[CollectedDoc]] = {}
    usable_ids: set[str] = set()

    def _safe(t: Target) -> tuple[Target, list[CollectedDoc]]:
        try:
            return t, fetch_target(t, corpus, force=force, delay=delay)
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
                        usable_ids.add(t.id)
                    if progress:
                        progress(t, f"{'USABLE' if good else 'skip  '} "
                                     f"[{ttype}] {usable}/{per_type} (tried {attempted})")
                if usable < per_type:
                    while len(pending) < workers and _submit():
                        pass
        if progress:
            progress(Target(id="", type=ttype),
                     f"== {ttype}: {usable} usable from {attempted} attempts ==")
    return out, usable_ids

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


def fetch_target(
    target: Target, corpus: Corpus, force: bool = False, delay: float = 0.3
) -> list[CollectedDoc]:
    """Collect a target and immediately back-fill its registry + companion
    disclosure docs, so a single fetch produces the whole document set.
    """
    docs = collect_target(target, corpus, force=force, delay=delay)
    docs = docs + augment_target(corpus, target, docs, force=force, delay=delay)
    docs = docs + augment_disclosure_target(corpus, target, docs, force=force, delay=delay)
    return docs


# Controls that reveal a hidden CMP / cookie vendor list.
_CONSENT_EXPANDERS = [
    "text=/manage (cookies|preferences|settings|options)/i",
    "text=/cookie settings/i",
    "text=/(see|show|view|manage|our) (vendors|partners|third part)/i",
    "text=/vendor(s| list)/i",
    "text=/more (options|information)/i",
    "text=/customi[sz]e/i",
]


class Renderer:
    """Reusable headless-Chromium renderer."""

    def __init__(self, timeout_ms: int = 30000, settle_ms: int = 2500,
                 expand_consent: bool = True):
        self.timeout_ms = timeout_ms
        self.settle_ms = settle_ms
        self.expand_consent = expand_consent
        self._pw = None
        self._browser = None
        self.available = False

    def __enter__(self) -> "Renderer":
        try:
            from playwright.sync_api import sync_playwright

            self._pw = sync_playwright().start()
            self._browser = self._pw.chromium.launch(headless=True)
            self.available = True
        except Exception as exc:  # noqa: BLE001
            print(f"[render] Playwright unavailable ({exc}); static fetch only",
                  file=sys.stderr)
            self.available = False
        return self

    def __exit__(self, *exc) -> None:
        try:
            if self._browser is not None:
                self._browser.close()
        finally:
            if self._pw is not None:
                self._pw.stop()

    def render(self, url: str) -> str | None:
        """Return the post-JS DOM HTML of ``url``, or ``None`` on failure."""
        if not self.available or not url:
            return None
        page = None
        try:
            page = self._browser.new_page()
            page.goto(url, wait_until="networkidle", timeout=self.timeout_ms)
            if self.expand_consent:
                self._expand(page)
            page.wait_for_timeout(self.settle_ms)
            return page.content()
        except Exception:  # noqa: BLE001
            return None
        finally:
            if page is not None:
                try:
                    page.close()
                except Exception:  # noqa: BLE001
                    pass

    def _expand(self, page) -> None:
        for sel in _CONSENT_EXPANDERS:
            try:
                loc = page.locator(sel).first
                if loc.count() and loc.is_visible():
                    loc.click(timeout=2500)
                    page.wait_for_timeout(800)
            except Exception:  # noqa: BLE001
                continue


def render_html(url: str, timeout_ms: int = 30000, settle_ms: int = 2500,
                expand_consent: bool = True) -> str | None:
    """One-shot convenience: render a single ``url`` (launches/closes a browser)."""
    with Renderer(timeout_ms=timeout_ms, settle_ms=settle_ms,
                  expand_consent=expand_consent) as r:
        return r.render(url)


# Roles benefitting from a JS render.
JS_PRONE_ROLES = {
    "cookie_policy", "vendor_list", "partners_page", "store_listing",
    "play_data_safety", "subprocessor_list", "do_not_sell", "privacy_policy",
}

# Minimal amount of gain required to keep a re-render over the static fetch.
RENDER_MIN_GAIN = 2000
# Per-page render timeout (ms).
RENDER_TIMEOUT_MS = 30000


def render_corpus(
    corpus: Corpus,
    target_ids: list[str] | None = None,
    roles: set[str] | None = None,
    render_limit: int = 0,
    timeout_ms: int = RENDER_TIMEOUT_MS,
    min_gain: int = RENDER_MIN_GAIN,
    progress=None,
) -> tuple[int, int, int]:
    """Re-fetch JS-prone docs with a headless browser.
    """
    role_set = roles if roles is not None else JS_PRONE_ROLES
    tids = target_ids if target_ids is not None else corpus.list_targets()
    rendered = updated = failed = 0
    with Renderer(timeout_ms=timeout_ms) as r:
        if not r.available:
            if progress:
                progress(None, "Playwright/browser unavailable; skipping render step.")
            return rendered, updated, failed
        for tid in tids:
            target, docs = corpus.read_manifest(tid)
            changed = False
            for d in docs:
                if d.role not in role_set or not d.url:
                    continue
                if render_limit and rendered >= render_limit:
                    break
                rendered += 1
                html = r.render(d.url)
                if not html:
                    failed += 1
                    continue
                old = corpus.read_doc_html(d) if d.raw_path else ""
                if len(html) >= len(old) + min_gain:
                    corpus.save_doc(tid, d, html)
                    d.http_status = d.http_status or 200
                    d.error = ""
                    d.fetched_at = time.time()
                    updated += 1
                    changed = True
            if changed:
                corpus.write_manifest(target, docs)
            if progress:
                progress(target, f"rendered, {updated} doc(s) updated so far")
    return rendered, updated, failed


# Roles produced by the per-target augment steps folded into fetch_target.
_REGISTRY_ROLES = {role for _, role in _WEB_PATHS + _APP_PATHS}


@dataclass
class CollectionReport:
    """Outcome of a full :func:`run_collection` pass."""

    collected: dict[str, list[CollectedDoc]] = field(default_factory=dict)
    usable: int = 0
    attempted: int = 0
    registry_docs: int = 0
    disclosure_docs: int = 0
    rendered: int = 0
    updated: int = 0
    failed: int = 0


def run_collection(
    seeds: list[Target],
    corpus: Corpus,
    per_type: int,
    workers: int = 8,
    delay: float = 0.3,
    force: bool = False,
    seed: int = 0,
    oversample: int = 8,
    render: bool = True,
    render_limit: int = 0,
    progress=None,
) -> CollectionReport:
    """Run the full fetcher pipeline: stratified crawl (with registry and
    disclosure back-fill folded into each target's fetch), then an optional
    JS-render pass over the resulting corpus.
    """
    report = CollectionReport()

    report.collected, usable_ids = collect_stratified(
        seeds, corpus, per_type=per_type, workers=workers, delay=delay,
        force=force, seed=seed, oversample=oversample, progress=progress,
    )
    report.attempted = len(report.collected)
    report.usable = len(usable_ids)
    all_docs = [d for docs in report.collected.values() for d in docs]
    report.registry_docs = sum(1 for d in all_docs if d.role in _REGISTRY_ROLES)
    report.disclosure_docs = sum(1 for d in all_docs if d.role in _BACKFILL_ROLES)

    if render:
        report.rendered, report.updated, report.failed = render_corpus(
            corpus, target_ids=None, render_limit=render_limit, progress=progress,
        )
    return report
