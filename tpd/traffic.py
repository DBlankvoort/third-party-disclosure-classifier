"""Third parties named by observed network traffic."""

from __future__ import annotations

from urllib.parse import urlparse

from .entities import canonical_key, entity_for_domain, registrable_domain
from .tracks import PERSONAL_DATA, SITE_VISITOR

_TYPE_DATA = {
    "script": ("cookie / device identifiers", ["services"]),
    "xmlhttprequest": ("cookie / device identifiers", ["services"]),
    "image": ("cookie / device identifiers", ["advertising"]),
    "imageset": ("cookie / device identifiers", ["advertising"]),
    "beacon": ("usage data", ["analytics"]),
    "ping": ("usage data", ["analytics"]),
    "media": ("usage data", ["services"]),
    "font": ("technical data", ["services"]),
    "stylesheet": ("technical data", ["services"]),
    "sub_frame": ("cookie / device identifiers", ["advertising"]),
    "websocket": ("usage data", ["services"]),
}
_DEFAULT_DATA = ("technical data", ["services"])

INFRASTRUCTURE_DOMAINS = {
    "gstatic.com", "jsdelivr.net", "unpkg.com", "bootstrapcdn.com",
    "jquery.com", "cloudflare.com", "akamaized.net", "akamai.net",
    "fastly.net", "cloudfront.net",
}


def _is_first_party(reg: str, origin_reg: str, first_party: set[str] | None) -> bool:
    if not reg:
        return True
    if origin_reg and reg == origin_reg:
        return True
    if first_party:
        label = reg.split(".")[0]
        return label in first_party
    return False


def observed_hosts(
    requests,
    origin: str,
    first_party: set[str] | None = None,
    include_infrastructure: bool = False,
) -> list[dict]:
    """Group observed requests by the third-party organisation contacted."""
    origin_reg = registrable_domain(urlparse(origin).hostname or "")
    by_entity: dict[str, dict] = {}
    for req in requests or ():
        url = (req or {}).get("url") or ""
        host = urlparse(url).hostname or ""
        reg = registrable_domain(host)
        if _is_first_party(reg, origin_reg, first_party):
            continue
        if not include_infrastructure and reg in INFRASTRUCTURE_DOMAINS:
            continue
        name, basis = entity_for_domain(reg)
        if not name:
            continue
        key = canonical_key(name)
        rec = by_entity.setdefault(key, {
            "entity": name,
            "basis": basis,
            "domains": set(),
            "types": set(),
            "requests": 0,
        })
        # A curated attribution outranks one guessed from the domain label.
        if basis == "domain_map":
            rec["basis"] = basis
            rec["entity"] = name
        rec["domains"].add(reg)
        rec["types"].add((req or {}).get("type") or "other")
        rec["requests"] += 1

    out = []
    for rec in by_entity.values():
        out.append({
            "entity": rec["entity"],
            "basis": rec["basis"],
            "domains": sorted(rec["domains"]),
            "types": sorted(rec["types"]),
            "requests": rec["requests"],
        })
    out.sort(key=lambda r: (-r["requests"], r["entity"].lower()))
    return out


def traffic_relations(
    requests,
    origin: str,
    first_party: set[str] | None = None,
    include_infrastructure: bool = False,
) -> list[dict]:
    """Data-sharing relations synthesised from observed requests."""
    from .classify.structured_relations import DOWNSTREAM

    out: list[dict] = []
    for rec in observed_hosts(requests, origin, first_party, include_infrastructure):
        seen: set[str] = set()
        for rtype in rec["types"]:
            data_type, purposes = _TYPE_DATA.get(rtype, _DEFAULT_DATA)
            if data_type in seen:
                continue
            seen.add(data_type)
            out.append({
                "entity": rec["entity"].strip().lower(),
                "party": "third",
                "unspecified": False,
                "data_type": data_type,
                "action": "collect",
                "negative": False,
                "direction": DOWNSTREAM,
                "track": PERSONAL_DATA,
                "subject": SITE_VISITOR,
                "grounded": True,
                "purposes": list(purposes),
                "examples": [],
                "qualifier": rec["basis"],
                "sources": ["traffic"],
                "text": (f"{rec['requests']} request(s) to "
                         f"{', '.join(rec['domains'][:3])}"),
                "doc_ids": [],
            })
    return out
