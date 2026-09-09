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
    contacts = observed_contacts(
        requests, origin, first_party=first_party,
        include_infrastructure=include_infrastructure,
    )
    by_entity: dict[str, dict] = {}
    for contact in contacts:
        if not contact["entity"]:
            continue
        key = canonical_key(contact["entity"])
        rec = by_entity.setdefault(key, {
            "entity": contact["entity"], "basis": contact["basis"],
            "domains": set(), "types": set(), "consent": set(), "requests": 0,
        })
        rec["domains"].add(contact["domain"])
        rec["types"].update(contact["types"])
        rec["consent"].update(contact["consent_states"])
        rec["requests"] += contact["requests"]
    out = [{
        "entity": rec["entity"], "basis": rec["basis"],
        "domains": sorted(rec["domains"]), "types": sorted(rec["types"]),
        "consent": consent_state(rec["consent"]), "requests": rec["requests"],
    } for rec in by_entity.values()]
    out.sort(key=lambda r: (-r["requests"], r["entity"].lower()))
    return out


def observed_contacts(
    requests,
    origin: str,
    first_party: set[str] | None = None,
    include_infrastructure: bool = False,
) -> list[dict]:
    """Group observed requests by contacted registrable domain."""
    origin_reg = registrable_domain(urlparse(origin).hostname or "")
    by_domain: dict[str, dict] = {}
    for req in requests or ():
        url = (req or {}).get("url") or ""
        host = urlparse(url).hostname or ""
        reg = registrable_domain(host)
        if _is_first_party(reg, origin_reg, first_party):
            continue
        if not include_infrastructure and reg in INFRASTRUCTURE_DOMAINS:
            continue
        name, basis = entity_for_domain(reg)
        attributed = basis in {"domain_map", "tracker_radar"}
        rec = by_domain.setdefault(reg, {
            "domain": reg,
            "entity": name if attributed else "",
            "basis": basis,
            "types": set(),
            "consent_states": set(),
            "requests": 0,
            "initiators": set(),
            "redirects": 0,
            "redirect_targets": set(),
        })
        rec["types"].add((req or {}).get("type") or "other")
        rec["consent_states"].add((req or {}).get("consent") or "")
        rec["requests"] += 1
        initiator = (req or {}).get("originUrl") or (req or {}).get("documentUrl") or ""
        if initiator:
            rec["initiators"].add(initiator)
        rec["redirects"] += int(bool((req or {}).get("redirected")))
        redirect_host = urlparse((req or {}).get("redirectUrl") or "").hostname or ""
        redirect_reg = registrable_domain(redirect_host)
        if redirect_reg and redirect_reg != reg:
            rec["redirect_targets"].add(redirect_reg)
    out = [{**rec, "types": sorted(rec["types"]),
            "initiators": sorted(rec["initiators"]),
            "redirect_targets": sorted(rec["redirect_targets"]),
            "consent_states": sorted(rec["consent_states"]),
            "consent": consent_state(rec["consent_states"])}
           for rec in by_domain.values()]
    out.sort(key=lambda r: (-r["requests"], r["domain"]))
    return out
