from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

from .collect.base import Corpus, Target
from .entities import canonical_key, registrable_domain, resolve_entity_domain, resolve_name
from .lexicons import ADS_TXT_ACCOUNT_RE
from .sharing_graph import (
    DOMAIN_PREFIX,
    EdgeKind,
    Evidence,
    EvidenceSource,
    EvidenceType,
    NodeType,
    SharingGraph,
)
from .tracks import INVENTORY, NOT_APPLICABLE, UNKNOWN

# Reconciliation outcomes for an adjacent pair.
CONFIRMED = "confirmed"
ABSENT = "absent"
CONFIDENTIAL_ONLY = "confidential_only"
NO_SELLERS_JSON = "no_sellers_json"
NOT_COLLECTED = "not_collected"
UNKNOWN_DOMAIN = "unknown_domain"
NO_SELLER_ID = "no_seller_id"
RELATIONSHIP_MISMATCH = "relationship_mismatch"
SELLER_IDENTITY_MISMATCH = "seller_identity_mismatch"

# Seller-entry match methods.
BY_SELLER_ID = "seller_id"
BY_DOMAIN = "domain"
BY_NAME = "name"

_MAX_SELLERS_BYTES = 8_000_000

# Most ad systems publish sellers.json at the domain named in ads.txt.
# Keep known exceptions here.
SELLERS_JSON_HOSTS = {
    "google.com": "realtimebidding.google.com",
}


def sellers_json_host(domain: str) -> str:
    """The host publishing one ad system's sellers.json."""
    host = (domain or "").strip().lower().removeprefix("www.")
    return SELLERS_JSON_HOSTS.get(registrable_domain(host) or host, host)


# --------------------------------------------------------------------------- #
# sellers.json
# --------------------------------------------------------------------------- #
@dataclass
class SellerEntry:
    """One record of a sellers.json ``sellers`` array."""

    seller_id: str = ""
    name: str = ""
    domain: str = ""
    seller_type: str = ""
    confidential: bool = False

    @property
    def key(self) -> str:
        return canonical_key(self.name) if self.name else ""

    def describe(self) -> str:
        parts = [f"seller_id={self.seller_id}" if self.seller_id else "",
                 self.domain, self.name,
                 (self.seller_type or "").upper()]
        return " ".join(p for p in parts if p)


@dataclass
class SellersRecord:
    """A sellers.json file indexed by seller ID, domain and name."""

    entries: list[SellerEntry] = field(default_factory=list)
    by_id: dict[str, SellerEntry] = field(default_factory=dict)
    by_domain: dict[str, SellerEntry] = field(default_factory=dict)
    by_key: dict[str, SellerEntry] = field(default_factory=dict)
    confidential: int = 0

    @property
    def disclosed(self) -> int:
        return len(self.entries)

    def lookup(
        self, domain: str = "", name: str = "", seller_ids=(),
    ) -> tuple[SellerEntry | None, str]:
        """Find an entry, preferring seller ID over domain and name."""
        for sid in seller_ids or ():
            entry = self.by_id.get(str(sid).strip())
            if entry is not None:
                return entry, BY_SELLER_ID
        reg = registrable_domain(domain)
        if reg and reg in self.by_domain:
            return self.by_domain[reg], BY_DOMAIN
        key = resolve_name(name).key or canonical_key(name) if name else ""
        if key and key in self.by_key:
            return self.by_key[key], BY_NAME
        return None, ""


def parse_sellers_json(raw: str) -> SellersRecord | None:
    """Index one sellers.json document, or ``None`` where it is not one."""
    if not raw or len(raw) > _MAX_SELLERS_BYTES:
        return None
    try:
        data = json.loads(raw)
    except (ValueError, TypeError):
        return None
    if not isinstance(data, dict) or not isinstance(data.get("sellers"), list):
        return None
    record = SellersRecord()
    for item in data["sellers"]:
        if not isinstance(item, dict):
            continue
        if item.get("is_confidential"):
            record.confidential += 1
            continue
        entry = SellerEntry(
            seller_id=str(item.get("seller_id") or "").strip(),
            name=str(item.get("name") or "").strip(),
            domain=str(item.get("domain") or "").strip().lower().removeprefix("www."),
            seller_type=str(item.get("seller_type") or "").strip().lower(),
        )
        record.entries.append(entry)
        if entry.seller_id:
            record.by_id.setdefault(entry.seller_id, entry)
        reg = registrable_domain(entry.domain)
        if reg:
            record.by_domain.setdefault(reg, entry)
        if entry.key:
            record.by_key.setdefault(entry.key, entry)
    return record


def ads_txt_accounts(raw: str, ad_system: str) -> list[str]:
    """The accounts an ads.txt declares with one ad system."""
    wanted = registrable_domain(ad_system)
    if not wanted:
        return []
    out: list[str] = []
    for m in ADS_TXT_ACCOUNT_RE.finditer(raw or ""):
        if registrable_domain(m.group(1)) != wanted:
            continue
        account = m.group(2).strip()
        if account and account not in out:
            out.append(account)
    return out


# --------------------------------------------------------------------------- #
# Reading a domain's registries out of the corpus
# --------------------------------------------------------------------------- #
class RegistryStore:

    _ROLES = ("sellers_json", "ads_txt", "app_ads_txt")

    def __init__(self, corpus: Corpus):
        self.corpus = corpus
        self._sellers: dict[str, SellersRecord | None] = {}
        self._ads_txt: dict[str, str | None] = {}
        self._docs: dict[str, dict[str, str] | None] = {}

    def _target_ids(self, domain: str) -> list[str]:
        host = (domain or "").strip().lower().removeprefix("www.")
        if not host:
            return []
        reg = registrable_domain(host) or host
        seen: list[str] = []
        for candidate in (host, reg, f"www.{reg}", sellers_json_host(host)):
            for prefix in ("website__", "data_broker__"):
                tid = f"{prefix}{Target.make_id(candidate)}"
                if tid not in seen:
                    seen.append(tid)
        return seen

    def _registry_docs(self, domain: str) -> dict[str, str] | None:
        host = (domain or "").strip().lower().removeprefix("www.")
        if host in self._docs:
            return self._docs[host]
        found: dict[str, str] | None = None
        for tid in self._target_ids(host):
            if not (self.corpus.root / tid / "manifest.json").exists():
                continue
            try:
                _, docs = self.corpus.read_manifest(tid)
            except (OSError, ValueError, KeyError, TypeError):
                continue
            found = found if found is not None else {}
            for doc in docs:
                if doc.role not in self._ROLES or not doc.ok or doc.role in found:
                    continue
                try:
                    found[doc.role] = self.corpus.read_doc_html(doc)
                except (OSError, ValueError):
                    continue
        self._docs[host] = found
        return found

    def sellers(self, domain: str) -> SellersRecord | None:
        host = (domain or "").strip().lower().removeprefix("www.")
        if host not in self._sellers:
            docs = self._registry_docs(host)
            raw = (docs or {}).get("sellers_json") or ""
            self._sellers[host] = parse_sellers_json(raw) if raw else None
        return self._sellers[host]

    def crawled(self, domain: str) -> bool:
        return self._registry_docs(domain) is not None

    def forget_missing(self) -> None:
        missing = [host for host, docs in self._docs.items() if docs is None]
        for host in missing:
            del self._docs[host]
            self._sellers.pop(host, None)
            self._ads_txt.pop(host, None)

    def add(self, domain: str, raw: str) -> SellersRecord | None:
        host = (domain or "").strip().lower().removeprefix("www.")
        if not host:
            return None
        record = parse_sellers_json(raw)
        self._sellers[host] = record
        self._docs.setdefault(host, {})
        return record

    def accounts(self, domain: str, ad_system: str) -> list[str]:
        host = (domain or "").strip().lower().removeprefix("www.")
        if host not in self._ads_txt:
            docs = self._registry_docs(host) or {}
            self._ads_txt[host] = docs.get("ads_txt") or docs.get("app_ads_txt")
        raw = self._ads_txt[host]
        return ads_txt_accounts(raw, ad_system) if raw else []


# --------------------------------------------------------------------------- #
# The graph pass
# --------------------------------------------------------------------------- #
def node_domain(
    graph: SharingGraph,
    node_id: str,
    domains: dict[str, str] | None = None,
    hints: dict[str, str] | None = None,
    overrides: dict[str, str] | None = None,
) -> str:
    if domains and node_id in domains:
        return registrable_domain(domains[node_id]) or domains[node_id]
    node = graph.nodes.get(node_id)
    if node is None:
        return ""
    if node.type is NodeType.DOMAIN:
        return registrable_domain(node_id.removeprefix(DOMAIN_PREFIX))
    if node.type is not NodeType.ENTITY:
        return ""
    if node.primary_domain:
        return registrable_domain(node.primary_domain) or node.primary_domain
    domain, _ = resolve_entity_domain(node.display_name, hints=hints,
                                      overrides=overrides)
    return registrable_domain(domain) if domain else ""


def _checkable(edge) -> bool:
    if edge.kind is not EdgeKind.AUTHORISES_INVENTORY_SALE:
        return False
    return any(e.evidence_type is EvidenceType.ADS_TXT_AUTHORISATION
               for e in edge.evidence) and not any(
        e.evidence_type is EvidenceType.SELLERS_JSON_CONFIRMATION
        for e in edge.evidence)


def _ads_evidence(edge) -> list[Evidence]:
    return [e for e in edge.evidence
            if e.evidence_type is EvidenceType.ADS_TXT_AUTHORISATION]


def _authorizations(evidence: list[Evidence]) -> list[tuple[str, str]]:
    pairs: list[tuple[str, str]] = []
    for item in evidence:
        records = item.authorizations or [
            {"seller_id": sid, "relationship": item.qualifier}
            for sid in item.publisher_ids
        ]
        for record in records:
            pair = (str(record.get("seller_id") or ""),
                    str(record.get("relationship") or "").lower())
            if pair[0] and pair not in pairs:
                pairs.append(pair)
    return pairs


def _relationship_matches(qualifier: str, seller_type: str) -> bool:
    expected = {
        "direct": {"publisher", "both"},
        "reseller": {"intermediary", "both"},
    }
    return seller_type.lower() in expected.get(qualifier.lower(), set())


def _seller_identity_matches(qualifier: str, source_domain: str,
                             entry: SellerEntry) -> bool:
    """DIRECT accounts must identify the publisher that wrote ads.txt."""
    if qualifier.lower() != "direct":
        return True
    expected = registrable_domain(source_domain)
    actual = registrable_domain(entry.domain)
    return bool(expected and actual and expected == actual)


def reconcile_sellers(
    graph: SharingGraph,
    corpus: Corpus | str | Path,
    domains: dict[str, str] | None = None,
    hints: dict[str, str] | None = None,
    overrides: dict[str, str] | None = None,
    store: RegistryStore | None = None,
    lookups: int = 0,
) -> list[dict]:
    if not isinstance(corpus, Corpus):
        corpus = Corpus(corpus)
    if store is None:
        store = RegistryStore(corpus)
    asked = _look_up_missing(graph, corpus, store, domains, hints, overrides,
                             lookups)
    report: list[dict] = []
    for edge in list(graph.edges.values()):
        if not _checkable(edge):
            continue
        dst_domain = node_domain(graph, edge.dst, domains, hints, overrides)
        src_domain = node_domain(graph, edge.src, domains, hints, overrides)
        row = {
            "kind": edge.kind.value, "src": edge.src, "dst": edge.dst,
            "src_domain": src_domain, "dst_domain": dst_domain,
            "status": UNKNOWN_DOMAIN, "basis": "", "seller_id": "",
            "seller_type": "", "seller_name": "",
        }
        if not dst_domain or not src_domain or src_domain == dst_domain:
            report.append(row)
            continue
        record = store.sellers(dst_domain)
        if record is None:
            row["status"] = (
                NO_SELLERS_JSON
                if dst_domain in asked or store.crawled(dst_domain)
                else NOT_COLLECTED)
            report.append(row)
            continue
        ads = _ads_evidence(edge)
        authorizations = _authorizations(ads)
        if not authorizations:
            authorizations = [
                (sid, next((ev.qualifier for ev in ads if ev.qualifier), ""))
                for sid in store.accounts(src_domain, dst_domain)
            ]
        if not authorizations:
            row["status"] = NO_SELLER_ID
            report.append(row)
            continue
        matches = [(record.by_id[sid], qualifier)
                   for sid, qualifier in authorizations if sid in record.by_id]
        role_valid = [(entry, qualifier) for entry, qualifier in matches
                      if _relationship_matches(qualifier, entry.seller_type)]
        valid = [(entry, qualifier) for entry, qualifier in role_valid
                 if _seller_identity_matches(qualifier, src_domain, entry)]
        entry, qualifier = (valid or role_valid or matches or [(None, "")])[0]
        basis = BY_SELLER_ID if entry is not None else ""
        if entry is None:
            row["status"] = (CONFIDENTIAL_ONLY
                             if not record.disclosed and record.confidential
                             else ABSENT)
            report.append(row)
            continue
        relationship_valid = _relationship_matches(qualifier, entry.seller_type)
        identity_valid = _seller_identity_matches(qualifier, src_domain, entry)
        if not relationship_valid:
            status = RELATIONSHIP_MISMATCH
        elif not identity_valid:
            status = SELLER_IDENTITY_MISMATCH
        else:
            status = CONFIRMED
        row.update({
            "status": status,
            "basis": basis, "seller_id": entry.seller_id,
            "seller_type": entry.seller_type, "seller_name": entry.name,
            "qualifier": qualifier,
            "relationship_valid": relationship_valid,
            "identity_valid": identity_valid,
        })
        report.append(row)
        if status != CONFIRMED:
            continue
        edge.evidence.append(Evidence(
            source=EvidenceSource.REGISTRY,
            evidence_type=EvidenceType.SELLERS_JSON_CONFIRMATION,
            hop=min((e.hop for e in edge.evidence), default=0),
            snippet=(f"{dst_domain} sellers.json names {src_domain or entry.name} "
                     f"({entry.describe()}), matched by {basis}"),
            data_type="advertising bid data",
            purposes=["advertising"],
            track=INVENTORY,
            subject=NOT_APPLICABLE,
            qualifier=qualifier,
            publisher_ids=[entry.seller_id],
            match_basis=basis,
            relationship_valid=True,
        ))
    return report


def reconcile_supply_chains(
    graph: SharingGraph,
    corpus: Corpus | str | Path,
    domains: dict[str, str] | None = None,
    store: RegistryStore | None = None,
    lookups: int = 0,
) -> list[dict]:
    """Validate captured SupplyChain nodes against each system's sellers.json."""
    if not isinstance(corpus, Corpus):
        corpus = Corpus(corpus)
    store = store or RegistryStore(corpus)
    edges = [edge for edge in graph.edges.values()
             if edge.kind is EdgeKind.DECLARES_SUPPLY_CHAIN
             and not any(item.evidence_type is EvidenceType.SELLERS_JSON_CONFIRMATION
                         for item in edge.evidence)]
    wanted = []
    for edge in edges:
        domain = node_domain(graph, edge.dst, domains)
        if domain and domain not in wanted and store.sellers(domain) is None:
            wanted.append(domain)
    if wanted:
        _fetch_sellers(store, corpus, wanted[:max(0, lookups)])
    report = []
    for edge in edges:
        destination = node_domain(graph, edge.dst, domains)
        source = node_domain(graph, edge.src, domains)
        evidence = next((item for item in edge.evidence
                         if item.evidence_type is EvidenceType.OPENRTB_SUPPLY_CHAIN), None)
        sid = evidence.publisher_ids[0] if evidence and evidence.publisher_ids else ""
        row = {"kind": edge.kind.value, "src": edge.src, "dst": edge.dst,
               "src_domain": source, "dst_domain": destination,
               "seller_id": sid, "status": NOT_COLLECTED, "basis": ""}
        record = store.sellers(destination) if destination else None
        if record is None:
            row["status"] = NO_SELLERS_JSON if destination in wanted else NOT_COLLECTED
        elif not sid:
            row["status"] = NO_SELLER_ID
        elif sid not in record.by_id:
            row["status"] = ABSENT
        else:
            entry = record.by_id[sid]
            expected = registrable_domain(source)
            actual = registrable_domain(entry.domain)
            row["status"] = CONFIRMED if expected and expected == actual \
                else SELLER_IDENTITY_MISMATCH
            row["basis"] = BY_SELLER_ID
            row["seller_name"] = entry.name
            row["seller_type"] = entry.seller_type
            if row["status"] == CONFIRMED:
                edge.evidence.append(Evidence(
                    source=EvidenceSource.REGISTRY,
                    evidence_type=EvidenceType.SELLERS_JSON_CONFIRMATION,
                    snippet=f"{destination} sellers.json identifies {source} as {sid}",
                    track=INVENTORY, subject=NOT_APPLICABLE,
                    publisher_ids=[sid], match_basis=BY_SELLER_ID,
                    relationship_valid=True,
                ))
        report.append(row)
    return report


def _look_up_missing(
    graph: SharingGraph,
    corpus: Corpus,
    store: RegistryStore,
    domains, hints, overrides,
    lookups: int,
) -> set[str]:
    if lookups <= 0:
        return set()
    wanted: list[str] = []
    for edge in graph.edges.values():
        if not _checkable(edge):
            continue
        dst = node_domain(graph, edge.dst, domains, hints, overrides)
        if dst and dst not in wanted and store.sellers(dst) is None:
            wanted.append(dst)
    wanted = wanted[:lookups]
    if wanted:
        _fetch_sellers(store, corpus, wanted)
    return set(wanted)


def reconciliation_summary(report: list[dict]) -> dict:
    counts: dict[str, int] = {}
    for row in report or ():
        counts[row["status"]] = counts.get(row["status"], 0) + 1
    return {"checked": len(report or ()), "counts": counts}


# --------------------------------------------------------------------------- #
# One target's own relations
# --------------------------------------------------------------------------- #
DEFAULT_LOOKUPS = 25

_ADS_TXT_SOURCES = frozenset({"ads_txt", "app_ads_txt"})


def _ad_system(relation: dict) -> str:
    return registrable_domain(relation.get("entity") or "")


def corroborate_relations(
    relations,
    corpus: Corpus | str | Path,
    site_domain: str,
    site_name: str = "",
    store: RegistryStore | None = None,
    lookups: int = DEFAULT_LOOKUPS,
    delay: float = 0.0,
) -> list[dict]:
    if isinstance(corpus, (str, Path)):
        corpus = Corpus(corpus)
    if store is None:
        store = RegistryStore(corpus)
    rows = [r for r in relations or ()
            if set(r.get("sources") or ()) & _ADS_TXT_SOURCES and _ad_system(r)]
    if not rows:
        return []
    rows.sort(key=lambda r: r.get("qualifier") != "direct")

    wanted: list[str] = []
    for row in rows:
        domain = _ad_system(row)
        if domain not in wanted and store.sellers(domain) is None:
            wanted.append(domain)
    wanted = wanted[:max(0, lookups)]
    asked = set(wanted)
    if wanted:
        _fetch_sellers(store, corpus, wanted, delay=delay)

    checked: list[dict] = []
    for row in rows:
        domain = _ad_system(row)
        record = store.sellers(domain)
        if record is None:
            # Asking the ad system itself and getting nothing is a different
            # answer from never having asked.
            row["corroboration"] = (
                NO_SELLERS_JSON if domain in asked or store.crawled(domain)
                else NOT_COLLECTED)
            checked.append(row)
            continue
        authorizations = [
            (str(item.get("seller_id") or ""),
             str(item.get("relationship") or "").lower())
            for item in row.get("authorizations") or ()
            if item.get("seller_id")
        ] or [
            (str(sid), str(row.get("qualifier") or "").lower())
            for sid in row.get("publisher_ids") or () if str(sid)
        ]
        if not authorizations:
            row["corroboration"] = NO_SELLER_ID
            checked.append(row)
            continue
        matches = [(record.by_id[sid], qualifier)
                   for sid, qualifier in authorizations if sid in record.by_id]
        role_valid = [
            pair for pair in matches
            if _relationship_matches(pair[1], pair[0].seller_type)
        ]
        valid_matches = [
            pair for pair in role_valid
            if _seller_identity_matches(pair[1], site_domain, pair[0])
        ]
        entry, qualifier = (valid_matches or role_valid or matches or [(None, "")])[0]
        basis = BY_SELLER_ID if entry is not None else ""
        if entry is None:
            row["corroboration"] = (CONFIDENTIAL_ONLY
                                    if not record.disclosed and record.confidential
                                    else ABSENT)
        else:
            role_matches = _relationship_matches(qualifier, entry.seller_type)
            identity_matches = _seller_identity_matches(
                qualifier, site_domain, entry)
            if not role_matches:
                status = RELATIONSHIP_MISMATCH
            elif not identity_matches:
                status = SELLER_IDENTITY_MISMATCH
            else:
                status = CONFIRMED
            row["corroboration"] = status
            row["corroboration_basis"] = basis
            row["corroborated_by"] = domain
            row["relationship_valid"] = role_matches
            row["identity_valid"] = identity_matches
            if entry.seller_id:
                row["seller_id"] = entry.seller_id
            if entry.seller_type:
                row["seller_type"] = entry.seller_type
        checked.append(row)
    return checked


def _fetch_sellers(store: RegistryStore, corpus: Corpus, domains,
                   delay: float = 0.0) -> None:
    """Read the sellers.json of ad systems the corpus has not collected."""
    from .collect.base import fetch, warm_cache

    urls = [f"https://{sellers_json_host(d)}/sellers.json" for d in domains]
    try:
        warm_cache(urls, corpus.cache_dir, delay=delay)
    except Exception:  # noqa: BLE001
        pass
    for domain, url in zip(domains, urls, strict=True):
        try:
            result = fetch(url, cache_dir=corpus.cache_dir, delay=delay)
        except Exception:  # noqa: BLE001
            continue
        if result.ok and result.text:
            store.add(domain, result.text)


def corroboration_summary(relations) -> dict:
    """Counts per status, over the relations a check reached."""
    counts: dict[str, int] = {}
    for row in relations or ():
        status = row.get("corroboration")
        if status:
            counts[status] = counts.get(status, 0) + 1
    return {"checked": sum(counts.values()), "counts": counts}


# --------------------------------------------------------------------------- #
# Observed traffic against an independent tracker list
# --------------------------------------------------------------------------- #
TRACKING_CATEGORIES = frozenset({
    "Advertising", "Ad Motivated Tracking", "Audience Measurement",
    "Third-Party Analytics Marketing", "Action Pixels", "Session Replay",
    "Ad Fraud", "Analytics", "Social - Share", "Unknown High Risk Behavior",
    "Malware",
})

# Statuses one contacted domain can be checked to.
KNOWN_NOT_TRACKING = "known_not_tracking"
UNLISTED = "unlisted"

# How a domain was recognised.
BY_TRACKER_RADAR = "tracker_radar"


def tracker_listing(domain: str) -> tuple[str, str, list[str]]:
    """``(status, basis, categories)`` for one contacted domain."""
    from .kb import tracker_radar
    host = (domain or "").strip().lower().removeprefix("www.")
    if not host:
        return UNLISTED, "", []
    reg = registrable_domain(host)
    index = tracker_radar.index()
    record = index.lookup_domain(host) or (index.lookup_domain(reg) if reg else None)
    categories = sorted(record.categories) if record is not None else []
    if categories and set(categories) & TRACKING_CATEGORIES:
        return CONFIRMED, BY_TRACKER_RADAR, categories
    if record is not None:
        return KNOWN_NOT_TRACKING, BY_TRACKER_RADAR, categories
    return UNLISTED, "", []


def confirm_tracker_domains(graph: SharingGraph) -> list[dict]:
    """Mark each observed contact whose domain a tracker list recognises."""
    report: list[dict] = []
    for edge in list(graph.edges.values()):
        if edge.kind is not EdgeKind.CONTACTS_DOMAIN:
            continue
        if any(e.evidence_type is EvidenceType.TRACKER_LIST_CONFIRMATION
               for e in edge.evidence):
            continue
        node = graph.nodes.get(edge.dst)
        domain = (node.display_name or edge.dst.removeprefix(DOMAIN_PREFIX)
                  ) if node is not None else ""
        status, basis, categories = tracker_listing(domain)
        report.append({
            "src": edge.src, "dst": edge.dst, "domain": domain,
            "status": status, "basis": basis, "categories": categories,
        })
        if status != CONFIRMED:
            continue
        listed = ", ".join(categories) if categories else "registered vendor"
        edge.evidence.append(Evidence(
            source=EvidenceSource.REGISTRY,
            evidence_type=EvidenceType.TRACKER_LIST_CONFIRMATION,
            hop=min((e.hop for e in edge.evidence), default=0),
            snippet=f"{basis.replace('_', ' ')} lists {domain} as {listed}",
            track="",
            subject=UNKNOWN,
        ))
    return report


def tracker_summary(report: list[dict]) -> dict:
    """Counts per status."""
    counts: dict[str, int] = {}
    for row in report or ():
        counts[row["status"]] = counts.get(row["status"], 0) + 1
    return {"checked": len(report or ()), "counts": counts}
