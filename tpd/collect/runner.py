"""Crawl runner infrastructure."""

from __future__ import annotations

import csv
import threading
import time
import random
from collections import OrderedDict
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait
from dataclasses import dataclass, field
from pathlib import Path
import sys

from ..typology import TargetType
from .base import CollectedDoc, Corpus, Target
from ..classify.document_class import classify_medium
from ..extract import parse_html
from .appstore import collect_app_store_app
from .playstore import collect_play_app
from .registry import REGISTRY_ROLES
from .web import collect_website

_DISPATCH = {
    TargetType.WEBSITE.value: collect_website,
    TargetType.DATA_BROKER.value: collect_website,   # data brokers crawled as websites
    TargetType.PLAY_STORE_APP.value: collect_play_app,
    TargetType.APP_STORE_APP.value: collect_app_store_app,
}

# Bytes of a store/registry shell we bother to parse for usability check.
_USABILITY_MAX_BYTES = 400_000


def fetch_target(
    target: Target, corpus: Corpus, force: bool = False, delay: float = 0.3
) -> list[CollectedDoc]:
    """Collect ``target``'s whole document set."""
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


# --------------------------------------------------------------------------- #
# Rendering during an outward walk
# --------------------------------------------------------------------------- #
THIN_TEXT_SHARE = 0.02
THIN_TEXT_CHARS = 400
WALK_RENDER_TIMEOUT_MS = 12000
WALK_RENDER_SETTLE_MS = 900

_RENDERER_LOCAL = threading.local()
_RENDERERS: list[Renderer] = []
_RENDERERS_LOCK = threading.Lock()


def walk_renderer() -> Renderer | None:
    """A browser this thread may reuse across origins, started on first need."""
    held = getattr(_RENDERER_LOCAL, "renderer", None)
    if held is not None:
        return held if held.available else None
    renderer = Renderer(timeout_ms=WALK_RENDER_TIMEOUT_MS,
                        settle_ms=WALK_RENDER_SETTLE_MS)
    renderer.__enter__()
    _RENDERER_LOCAL.renderer = renderer
    with _RENDERERS_LOCK:
        _RENDERERS.append(renderer)
    return renderer if renderer.available else None


def close_thread_renderer() -> None:
    """Shut the browser this thread holds."""
    renderer = getattr(_RENDERER_LOCAL, "renderer", None)
    _RENDERER_LOCAL.renderer = None
    if renderer is None:
        return
    with _RENDERERS_LOCK:
        if renderer in _RENDERERS:
            _RENDERERS.remove(renderer)
    try:
        renderer.__exit__(None, None, None)
    except Exception:  # noqa: BLE001
        pass


def close_walk_renderers() -> None:
    """Shut any browser still standing once a walk has finished."""
    with _RENDERERS_LOCK:
        held, _RENDERERS[:] = list(_RENDERERS), []
    for renderer in held:
        try:
            renderer.__exit__(None, None, None)
        except Exception:  # noqa: BLE001
            pass
    _RENDERER_LOCAL.renderer = None


def _is_thin(html: str) -> bool:
    """Whether a fetched document's markup arrived without its prose."""
    if not html.strip():
        return True
    try:
        text = parse_html(html, max_bytes=_USABILITY_MAX_BYTES).text
    except Exception:  # noqa: BLE001
        return False
    return len(text) < THIN_TEXT_CHARS or len(text) / len(html) < THIN_TEXT_SHARE


def render_thin_docs(
    corpus: Corpus,
    target: Target,
    docs: list[CollectedDoc],
    roles: set[str] | None = None,
    progress=None,
) -> int:
    """Re-fetch this target's client-rendered documents with a browser."""
    role_set = roles if roles is not None else JS_PRONE_ROLES
    candidates = [d for d in docs
                  if d.role in role_set and d.url and d.ok and not d.rendered]
    if not candidates:
        return 0
    pending = []
    for d in candidates:
        if _is_thin(corpus.read_doc_html(d)):
            pending.append(d)
        else:
            # Recorded so later rings do not re-parse a document already known
            # to have arrived with its prose.
            d.rendered = -1
    updated = 0
    renderer = walk_renderer() if pending else None
    for d in pending if renderer is not None else ():
        html = renderer.render(d.url)
        d.rendered = -1
        if html and not _is_thin(html):
            corpus.save_doc(target.id, d, html)
            d.fetched_at = time.time()
            d.rendered = 1
            updated += 1
            if progress:
                progress(target, f"rendered {d.role}")
    corpus.write_manifest(target, docs)
    return updated


_DISCLOSURE_ROLES = {"vendor_list", "subprocessor_list", "do_not_sell", "partners_page"}


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
    """Run the full fetcher pipeline: a stratified crawl, then an optional
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
    report.registry_docs = sum(1 for d in all_docs if d.role in REGISTRY_ROLES)
    report.disclosure_docs = sum(1 for d in all_docs if d.role in _DISCLOSURE_ROLES)

    if render:
        report.rendered, report.updated, report.failed = render_corpus(
            corpus, target_ids=None, render_limit=render_limit, progress=progress,
        )
    return report
