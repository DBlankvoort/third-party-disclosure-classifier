"""Run the `tpd` tool against a single live URL."""

from __future__ import annotations

import re
import sys
import threading
from pathlib import Path
from urllib.parse import urlparse

from tpd.classify.named_entities import first_party_tokens
from tpd.classify.named_relations import named_org_relations
from tpd.classify.poligraph_connector import (
    merge_relations,
    poligraph_available,
    target_relations,
)
from tpd.classify.run import classify_corpus
from tpd.classify.structured_relations import (
    ALL_REGISTRY_KINDS,
    structured_relations_for_target,
)
from tpd.cmp import cmp_relations, cmp_vendors
from tpd.collect.base import Corpus
from tpd.collect.runner import fetch_target
from tpd.expand import first_party_urls, origin_of, target_for_url
from tpd.extract import parse_html
from tpd.probe import merge_requests
from tpd.reconcile import (
    DEFAULT_LOOKUPS,
    corroborate_relations,
    corroboration_summary,
    tracker_listing,
)
from tpd.site_kind import profile, supports
from tpd.traffic import observed_contacts, observed_hosts
from tpd.typology import media_of

_EMAIL_RE = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9][A-Za-z0-9.-]*\.[A-Za-z]{2,}")
# False positives for e-mails.
_EMAIL_STOP_RE = re.compile(
    r"\.(?:png|jpe?g|gif|svg|webp|css|js)$|@(?:\dx\.|example\.|sentry\.)", re.I,
)
_PRIVACY_LOCAL_RE = re.compile(
    r"^(?:privacy|dpo|data[.-]?protection|legal|compliance|ccpa|gdpr)", re.I,
)
_RIGHTS_LINK_ROLES = ("do_not_sell", "privacy_policy", "cookie_policy", "dpa")
# Relation sources that report a vendor registry rather than the site's own text.
_VENDOR_SOURCES = frozenset({"cmp", "tcf_gvl", "vendors_json", "sellers_json"})
# How long the analysis waits on a probe that has outlived the work beside it.
PROBE_JOIN_TIMEOUT = 90.0


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


def _undisclosed(observed, named_orgs, relations) -> list[dict]:
    """Observed parties that no fetched document names."""
    from tpd.entities import canonical_key

    disclosed = {canonical_key(o) for o in named_orgs}
    for r in relations:
        if "traffic" not in r.get("sources", []):
            disclosed.add(canonical_key(r["entity"]))
    return [o for o in observed if canonical_key(o["entity"]) not in disclosed]


def _empty(origin: str, target_id: str, cached: bool,
           site_profile: dict | None = None) -> dict:
    return {
        "origin": origin,
        "target_id": target_id,
        "target_name": "",
        "cached": cached,
        **(site_profile or profile(origin)),
        "classified": False,
        "usable": False,
        "facets": [],
        "media_present": [],
        "specificities": [],
        "relevant_docs": 0,
        "fetched_docs": 0,
        "failed_urls": [],
        "documents": [],
        "sharing_relations": [],
        "poligraph": False,
        "rights": {"links": {}, "emails": []},
        "observed_parties": [],
        "traffic_contacts": [],
        "probe": {"ran": False, "cached": False, "accepted": "", "available": False},
        "undisclosed_parties": [],
        "cmp_parties": [],
        "corroboration": {"checked": 0, "counts": {}},
    }


class _Probe:
    """A clean-profile capture running alongside collection.

    A probe costs a browser launch and half a minute, which is dead time next
    to the collection and classification the popup is waiting on anyway, so it
    runs in parallel with them and is collected at the end.
    """

    def __init__(self, corpus: Corpus, target, origin: str, force: bool):
        self.result: dict = {"ran": False, "cached": False, "accepted": "",
                             "available": False}
        self.requests: list[dict] = []
        self._thread = threading.Thread(
            target=self._run, args=(corpus, target, origin, force),
            name="probe", daemon=True,
        )
        self._thread.start()

    def _run(self, corpus: Corpus, target, origin: str, force: bool) -> None:
        from tpd.probe import cached_probe

        try:
            record = cached_probe(corpus.root / target.id, origin, force=force)
        except Exception:  # noqa: BLE001
            return
        self.requests = record.get("requests") or []
        self.result = {
            "ran": True, "cached": bool(record.get("cached")),
            "accepted": record.get("accepted") or "",
            "available": bool(record.get("available", True)),
            "requests": len(self.requests),
            "observed_at": record.get("probed_at") or 0,
        }

    def join(self, timeout: float = PROBE_JOIN_TIMEOUT) -> None:
        self._thread.join(timeout)


def analyze_url(
    url: str,
    corpus_root: str | Path,
    use_ner: bool = True,
    use_poligraph: bool = True,
    force: bool = False,
    delay: float = 0.2,
    requests: list[dict] | None = None,
    cmp: dict | None = None,
    probe: bool = True,
    corroborate: int = DEFAULT_LOOKUPS,
) -> dict:
    """Collect + classify the origin of a URL."""
    origin = origin_of(url)
    corpus = Corpus(corpus_root)
    # A store listing is analysed as the app it names: the store's own traffic
    # and advertising registry are Google's or Apple's, never the developer's.
    target = target_for_url(url)
    # Started before collection so the browser launch overlaps it rather than
    # following it; the popup and the graph read the same stored capture.
    probing = _Probe(corpus, target, origin, force) if probe else None

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
    poligraph_on = use_poligraph and poligraph_available()
    ner_nlp = None
    if poligraph_on:
        from tpd.poligraph.nlp import get_nlp as get_poligraph_nlp
        ner_nlp = get_poligraph_nlp().nlp
    result = classify_corpus(
        corpus, use_ner=use_ner, target_ids=[target.id], ner_nlp=ner_nlp,
    )
    if not result.targets:
        if probing is not None:
            probing.join()
        return _empty(origin, target.id, cached, profile(origin, target))
    tc = result.targets[0]

    _, raw_docs = corpus.read_manifest(target.id)
    site_profile = profile(origin, target, raw_docs)
    site_kind = site_profile["site_kind"]
    usable = any(d.medium for d in tc.docs)
    fetched = len(raw_docs)
    failed = [d.url for d in raw_docs if not d.ok]

    # Run PoliGraph to capture sharing relationships.
    first_party = first_party_tokens(
        first_party_urls(target, raw_docs), name=target.name)
    prose_rels = target_relations(
        corpus, target.id, raw_docs, first_party=first_party, force=force,
    ) if poligraph_on else []
    structured_rels = structured_relations_for_target(
        corpus, raw_docs, first_party=first_party,
        registry_kinds=ALL_REGISTRY_KINDS,
    )
    named_rels = named_org_relations(
        corpus,
        [d for d in raw_docs if d.doc_id in {
            item.doc_id for item in tc.docs if item.relevant
        }],
        target_type=target.type, first_party=first_party,
        use_ner=use_ner,
    )
    # Observed requests name parties the documents may omit entirely, but only
    # where the requests belong to the target: a listing's are the store's.
    traffic_on = supports(site_kind, "contacts_domain")
    probe_info = {"ran": False, "cached": False, "accepted": "", "available": False}
    if probing is not None:
        probing.join()
        probe_info = probing.result
        # The reader's own session misses whatever their consent state, cache,
        # or content blocking suppressed; the clean-profile load supplies it.
        requests = merge_requests(requests, probing.requests)
    contacts = (observed_contacts(requests, origin, first_party=first_party)
                if traffic_on else [])
    # Add independent tracker-list classifications to observed contacts.
    for contact in contacts:
        status, basis, categories = tracker_listing(contact.get("domain") or "")
        contact["tracker"] = status
        contact["tracker_basis"] = basis
        contact["tracker_categories"] = categories
    observed = (observed_hosts(requests, origin, first_party=first_party)
                if traffic_on else [])
    # A vendor registry read off a publisher's origin names the registry rather
    # than the publisher, so vendor listings stand only for a vendor-side site.
    vendors_on = supports(site_kind, "lists_vendor")
    # The consent dialog names parties the crawled documents never render.
    cmp_parties = cmp_vendors(cmp, first_party=first_party) if vendors_on else []
    cmp_rels = cmp_relations(cmp, first_party=first_party) if vendors_on else []
    if not vendors_on:
        structured_rels = [r for r in structured_rels
                           if not _VENDOR_SOURCES & set(r.get("sources") or ())]
    sharing = merge_relations(
        [prose_rels, structured_rels, named_rels, cmp_rels]
    )
    # Check ads.txt authorisations against each ad system's sellers.json.
    corroboration = {"checked": 0, "counts": {}}
    if supports(site_kind, "authorises_inventory_sale"):
        try:
            checked = corroborate_relations(
                sharing, corpus,
                site_domain=urlparse(origin).hostname or "",
                site_name=target.name,
                lookups=corroborate, delay=delay,
            )
            corroboration = corroboration_summary(checked)
        except Exception as exc:  # noqa: BLE001
            print(f"[analyze] corroboration skipped: "
                  f"{type(exc).__name__}: {exc}", file=sys.stderr)

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
    named = {o for d in tc.docs for o in d.named_orgs}
    specificities = sorted({f.split(":", 1)[1] for f in tc.facets if ":" in f})

    return {
        "origin": origin,
        "target_id": target.id,
        "target_name": target.name,
        "cached": cached,
        **site_profile,
        "classified": bool(tc.classified),
        "usable": bool(usable),
        "facets": list(tc.facets),
        "media_present": media_present,
        "specificities": specificities,
        "relevant_docs": tc.relevant_docs,
        "fetched_docs": fetched,
        "failed_urls": failed,
        "documents": documents,
        "sharing_relations": sharing,
        "poligraph": poligraph_on,
        "rights": _rights_info(corpus, raw_docs),
        "observed_parties": observed,
        "traffic_contacts": contacts,
        "probe": probe_info,
        "undisclosed_parties": _undisclosed(observed, named, sharing),
        "cmp_parties": cmp_parties,
        "corroboration": corroboration,
        "interpretation": {
            "traffic": "Observed network contact; payload and purpose were not inspected.",
            "policy": "A statement in a collected document, not proof that the practice occurred.",
            "vendor": "Registry or consent-interface membership, not proof of a transaction.",
            "inventory": "Permission to sell advertising inventory, not personal-data transfer.",
        },
        "coverage": {
            "probe_available": bool(probe_info.get("available")),
            "probe_cached": bool(probe_info.get("cached")),
            "probe_observed_at": probe_info.get("observed_at") or 0,
            "probe_treatment": "fresh no-action baseline followed by accept-all and reject-all treatments when controls are available",
            "probe_limitations": [
                "one clean browser profile",
                "one short page-load observation",
                "reject-all is measured only when directly exposed and recognised",
                "request destinations only; no payload inspection",
            ],
            "documents_failed": len(failed),
            "documents_fetched": fetched,
            "poligraph_available": poligraph_on,
        },
    }
