"""Collect a website's (or data broker's) document set from the web."""

from __future__ import annotations

import hashlib
import time
from urllib.parse import urljoin, urlparse

from .. import lexicons
from ..extract import parse_html
from .base import CollectedDoc, Corpus, Target, fetch


def _content_key(text: str) -> str:
    """Hash a fetched body."""
    return hashlib.sha1(parse_html(text).text.strip().encode("utf-8", "ignore")).hexdigest()

# Policy paths tried when no link is discovered.
COMMON_POLICY_PATHS = [
    "/privacy", "/privacy-policy", "/privacy-policy/", "/legal/privacy",
    "/privacy-notice", "/policies/privacy", "/about/privacy", "/privacy.html",
]
# Companion-doc paths tried when no link is discovered.
COMMON_COMPANION_PATHS = [
    ("subprocessor_list", ["/subprocessors", "/sub-processors", "/legal/subprocessors",
                            "/trust/subprocessors", "/subprocessor-list"]),
    ("cookie_policy", ["/cookie-policy", "/cookies", "/legal/cookies", "/cookie-notice"]),
    ("do_not_sell", ["/do-not-sell", "/do-not-sell-my-info", "/privacy/do-not-sell",
                     "/your-privacy-choices", "/ccpa"]),
    ("dpa", ["/dpa", "/legal/dpa", "/data-processing-agreement"]),
    ("partners_page", ["/partners", "/our-partners", "/integrations"]),
    ("vendor_list", ["/third-party-data", "/privacy-center/third-party-data",
                     "/privacy-centre/third-party-data", "/vendor-list", "/vendors",
                     "/legal/vendors", "/third-parties", "/who-we-share-data-with"]),
]
# Companion roles we keep (a discovered link's suggested role).
_COMPANION_ROLES = {
    "subprocessor_list", "dpa", "cookie_policy", "vendor_list",
    "do_not_sell", "partners_page", "help_doc",
}


def _registrable(host: str) -> str:
    """Approximate registrable domain."""
    parts = host.lower().split(".")
    return ".".join(parts[-2:]) if len(parts) >= 2 else host


def _same_site(url: str, *base_hosts: str) -> bool:
    """True if ``url`` is on (the registrable domain of) any of ``base_hosts``."""
    host = urlparse(url).netloc.lower()
    if not host:
        return True  # relative URL, already joined
    reg = _registrable(host)
    return any(reg == _registrable(h) for h in base_hosts if h)


def _discover_links(doc_html: str, base_url: str) -> dict[str, list[str]]:
    """Return {role: absolute_url} for companion docs discovered in ``doc_html``."""
    parsed = parse_html(doc_html)
    found: dict[str, list[str]] = {}
    for text, href in parsed.links:
        if href.startswith(("javascript:", "mailto:", "#")):
            continue
        absu = urljoin(base_url, href)
        hay = f"{text} {href}"
        for role, pat in lexicons.LINK_DISCOVERY:
            if pat.search(hay):
                if role in found:
                    found[role].append(absu)
                else:
                    found[role] = [absu]
    return found


def collect_website(
    target: Target,
    corpus: Corpus,
    force: bool = False,
    delay: float = 0.3,
) -> list[CollectedDoc]:
    """Collect ``target``'s document set."""
    docs: list[CollectedDoc] = []
    cache = corpus.cache_dir
    home = target.url.rstrip("/")
    base_host = urlparse(home).netloc or home
    seen_hashes: set[str] = set()

    def _save(url: str, role: str, dedup: bool = False) -> CollectedDoc | None:
        res = fetch(url, cache_dir=cache, force=force, delay=delay)
        # Content de-dup
        if dedup and res.ok and res.text:
            key = _content_key(res.text)
            if key in seen_hashes:
                return None
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
            seen_hashes.add(_content_key(res.text))
            docs.append(doc)
        return doc if res.ok else None

    # 1. homepage ----------------------------------------------------------- #
    home_res = fetch(home, cache_dir=cache, force=force, delay=delay) if home else None
    discovered: dict[str, list[str]] = {}
    if home_res and home_res.ok and home_res.text:
        discovered = _discover_links(home_res.text, home_res.final_url or home)

    # 2. privacy policy ----------------------------------------------------- #
    policy_url = target.seed_policy_url or discovered.get("privacy_policy", "")
    policy_doc = None
    if policy_url:
        policy_doc = _save(policy_url, "privacy_policy")
    if policy_doc is None and home:
        for path in COMMON_POLICY_PATHS:
            cand = urljoin(home + "/", path.lstrip("/"))
            policy_doc = _save(cand, "privacy_policy")
            if policy_doc is not None:
                break

    # 3. discover companion docs from the policy too ------------------------ #
    policy_host = ""
    if policy_doc is not None:
        policy_host = urlparse(policy_doc.url).netloc
        policy_html = corpus.read_doc_html(policy_doc)
        for role, urls in _discover_links(policy_html, policy_doc.url).items():
            for url in urls:
                discovered.setdefault(role, url)

    # 4. fetch discovered companion docs ------------------------------------ #
    seen_urls = {d.url for d in docs}
    for role, urls in discovered.items():
        for url in urls:
            if role == "privacy_policy" or role not in _COMPANION_ROLES:
                continue
            if url in seen_urls:
                continue
            if not _same_site(url, base_host, policy_host):
                continue
            _save(url, role, dedup=True)
            seen_urls.add(url)

    # 5. try conventional companion paths for high-value surfaces not yet found.
    roots = []
    for h in (base_host, policy_host):
        if h:
            r = f"{urlparse(home).scheme or 'https'}://{h}"
            if r not in roots:
                roots.append(r)
    for role, paths in COMMON_COMPANION_PATHS:
        done = False
        for root in roots:
            for path in paths:
                cand = urljoin(root + "/", path.lstrip("/"))
                if cand in seen_urls:
                    continue
                got = _save(cand, role, dedup=True)
                seen_urls.add(cand)
                if got is not None:
                    done = True
                    break
            if done:
                break

    corpus.write_manifest(target, docs)
    return docs
