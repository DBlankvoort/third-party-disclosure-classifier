"""Parties named by a consent dialog."""

from __future__ import annotations

from .classify.named_entities import _is_first_party
from .classify.structured_relations import DOWNSTREAM, purposes_from_tcf
from .entities import resolve_name

# What a consent dialog's vendor list establishes about a party.
_DATA_TYPE = "cookie / device identifiers"

# Vendor-list entries that name the publisher's own tooling or a category.
_NON_VENDOR_NAMES = {
    "necessary", "strictly necessary", "functional", "performance",
    "analytics", "advertising", "targeting", "social media", "preferences",
    "essential", "other", "unclassified", "uncategorised", "uncategorized",
    "first party", "this website", "publisher", "vendor", "vendors",
}

MAX_VENDORS = 2000


def _clean_vendor(entry) -> tuple[str, list[int]]:
    """The name and TCF purpose ids of one vendor-list entry."""
    if isinstance(entry, str):
        return entry.strip(), []
    if not isinstance(entry, dict):
        return "", []
    name = str(entry.get("name") or entry.get("vendor") or "").strip()
    ids = entry.get("purposes") or entry.get("purposeIds") or []
    if isinstance(ids, dict):  # {"1": true, "3": true}
        ids = [int(k) for k, v in ids.items() if v and str(k).isdigit()]
    ids = [i for i in ids if isinstance(i, int)]
    return name, ids


def cmp_vendors(payload: dict | None, first_party: set[str] | None = None) -> list[dict]:
    """The organisations one captured consent dialog names."""
    payload = payload or {}
    entries = payload.get("vendors") or []
    if not isinstance(entries, list):
        return []
    source = str(payload.get("source") or "dom").lower()
    out: dict[str, dict] = {}
    for entry in entries[:MAX_VENDORS]:
        name, ids = _clean_vendor(entry)
        if not name or name.lower() in _NON_VENDOR_NAMES:
            continue
        if _is_first_party(name, first_party):
            continue
        resolved = resolve_name(name)
        if not resolved.key:
            continue
        rec = out.setdefault(resolved.key, {
            "entity": resolved.display,
            "surface": name,
            "basis": resolved.basis,
            "purposes": set(),
            "source": source,
        })
        rec["purposes"].update(purposes_from_tcf(ids))
    return [
        {**rec, "purposes": sorted(rec["purposes"])}
        for rec in sorted(out.values(), key=lambda r: r["entity"].lower())
    ]


def cmp_relations(payload: dict | None, first_party: set[str] | None = None) -> list[dict]:
    """Data-sharing relations declared by a captured consent dialog."""
    payload = payload or {}
    cmp_name = str(payload.get("cmp") or "").strip()
    source = str(payload.get("source") or "dom").lower()
    out: list[dict] = []
    for rec in cmp_vendors(payload, first_party=first_party):
        out.append({
            "entity": rec["entity"].strip().lower(),
            "party": "third",
            "unspecified": False,
            "data_type": _DATA_TYPE,
            "action": "be_shared",
            "negative": False,
            "direction": DOWNSTREAM,
            "purposes": rec["purposes"],
            "examples": [],
            "qualifier": source,
            "sources": ["cmp"],
            "text": (f"consent dialog{f' ({cmp_name})' if cmp_name else ''} "
                     f"lists {rec['surface']}"),
            "doc_ids": [],
        })
    return out
