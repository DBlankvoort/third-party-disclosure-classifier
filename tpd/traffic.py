"""Third parties named by observed network traffic."""

from __future__ import annotations

from urllib.parse import urlparse

from .entities import canonical_key, entity_for_domain, registrable_domain
from .probe import PRE_CONSENT

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


def consent_state(states) -> str:
    """The consent an organisation's contacts were made under."""
    values = {s for s in (states or ()) if s}
    if not values:
        return ""
    return PRE_CONSENT if PRE_CONSENT in values else sorted(values)[0]


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
            "consent": set(),
            "requests": 0,
        })
        # A curated attribution outranks one guessed from the domain label.
        if basis == "domain_map":
            rec["basis"] = basis
            rec["entity"] = name
        rec["domains"].add(reg)
        rec["types"].add((req or {}).get("type") or "other")
        rec["consent"].add((req or {}).get("consent") or "")
        rec["requests"] += 1

    out = []
    for rec in by_entity.values():
        out.append({
            "entity": rec["entity"],
            "basis": rec["basis"],
            "domains": sorted(rec["domains"]),
            "types": sorted(rec["types"]),
            "consent": consent_state(rec["consent"]),
            "requests": rec["requests"],
        })
    out.sort(key=lambda r: (-r["requests"], r["entity"].lower()))
    return out
