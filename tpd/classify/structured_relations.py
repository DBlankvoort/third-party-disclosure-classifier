"""Synthesize data-sharing relations from non-prose disclosure documents."""

from __future__ import annotations

import json
import re

from bs4 import BeautifulSoup

from ..extract import _COOKIE_TABLE_RE, _VENDOR_COL_RE, _clean, _table_headers_and_rows
from ..lexicons import MACHINE_READABLE_ROLES, machine_readable_kind
from ..poligraph.purpose import match_purpose_tags
from .named_entities import _is_first_party

# --------------------------------------------------------------------------- #
# Purpose mapping
# --------------------------------------------------------------------------- #
# IAB-TCF purpose mappings
_TCF_PURPOSES = {
    1: "services",                     # store and/or access information
    2: "advertising", 3: "advertising", 4: "advertising", 7: "advertising",
    5: "other", 6: "other",            # content personalisation
    8: "analytics", 9: "analytics",    # performance / market research
    10: "other",
}


def purposes_from_text(text: str) -> list[str]:
    """Map free purpose text to purpose tags."""
    return sorted(match_purpose_tags(text))


def _relation(
    entity: str,
    data_type: str,
    action: str,
    source: str,
    purposes: list[str] | None = None,
    qualifier: str = "",
    text: str = "",
    doc_id: str = "",
) -> dict:
    return {
        "entity": entity.strip().lower(),
        "party": "third",
        "unspecified": False,
        "data_type": data_type,
        "action": action,
        "negative": False,
        "purposes": purposes or [],
        "examples": [],
        "qualifier": qualifier,
        "sources": [source],
        "text": text[:300],
        "doc_ids": [doc_id] if doc_id else [],
    }


# --------------------------------------------------------------------------- #
# Machine-readable registries
# --------------------------------------------------------------------------- #
_ADS_TXT_ROW_RE = re.compile(
    r"^\s*([A-Za-z0-9.-]+\.[A-Za-z]{2,})\s*,\s*[^,]+,\s*(DIRECT|RESELLER)\b",
    re.I | re.M,
)


def _ads_txt_relations(raw: str, doc_id: str) -> list[dict]:
    """One edge per ads.txt / app-ads.txt row."""
    out: dict[str, dict] = {}
    for m in _ADS_TXT_ROW_RE.finditer(raw or ""):
        domain, kind = m.group(1).lower(), m.group(2).lower()
        if domain in out:
            if kind == "direct":
                out[domain]["qualifier"] = "direct"
            continue
        out[domain] = _relation(
            domain, "advertising bid data", "be_sold",
            source="ads_txt", purposes=["advertising"], qualifier=kind,
            text=m.group(0).strip(), doc_id=doc_id,
        )
    return list(out.values())


def _sellers_json_relations(raw: str, doc_id: str) -> list[dict]:
    """One edge per sellers.json entry."""
    try:
        data = json.loads(raw)
        sellers = data.get("sellers") or []
    except Exception:  # noqa: BLE001
        return []
    out: list[dict] = []
    for s in sellers:
        if not isinstance(s, dict) or s.get("is_confidential"):
            continue
        name = (s.get("name") or s.get("domain") or "").strip()
        if not name:
            continue
        stype = str(s.get("seller_type") or "").lower()
        out.append(_relation(
            name, "advertising bid data", "be_shared",
            source="sellers_json", purposes=["advertising"],
            qualifier=stype,
            text=f"seller_id={s.get('seller_id', '')} seller_type={stype or '?'}",
            doc_id=doc_id,
        ))
    return out


def _gvl_relations(raw: str, doc_id: str, source: str) -> list[dict]:
    """One edge per TCF-GVL / vendors.json vendor, with TCF purpose ids
    mapped onto our purpose vocabulary."""
    try:
        data = json.loads(raw)
    except Exception:  # noqa: BLE001
        return []
    vendors = data.get("vendors") if isinstance(data, dict) else None
    iterable = vendors.values() if isinstance(vendors, dict) else (vendors or [])
    out: list[dict] = []
    for v in iterable:
        if not isinstance(v, dict) or not v.get("name"):
            continue
        ids = v.get("purposes") or []
        purposes = sorted({_TCF_PURPOSES.get(i, "other") for i in ids if isinstance(i, int)})
        out.append(_relation(
            v["name"], "cookie / device identifiers", "be_shared",
            source=source, purposes=purposes,
            text=f"TCF purposes {ids}" if ids else "",
            doc_id=doc_id,
        ))
    return out


def registry_relations(raw: str, doc_id: str = "") -> list[dict]:
    """Relations declared by one machine-readable registry file."""
    kind = machine_readable_kind((raw or "")[:200_000])
    if kind == "ads_txt":
        return _ads_txt_relations(raw, doc_id)
    if kind == "sellers_json":
        return _sellers_json_relations(raw, doc_id)
    if kind in ("tcf_gvl", "vendors_json"):
        return _gvl_relations(raw, doc_id, source=kind)
    return []


# --------------------------------------------------------------------------- #
# Structured tables
# --------------------------------------------------------------------------- #
_ENTITY_COL_RE = re.compile(
    r"\b(host|domain|provider|vendors?|compan(?:y|ies)|organi[sz]ations?|"
    r"sub[- ]?processors?|processors?|partners?|recipients?|suppliers?|"
    r"third[- ]part|set by|source|owner|supplier)\b", re.I,
)
_PURPOSE_COL_RE = re.compile(
    r"\b(purpose|category|type|description|function|used for|use)\b", re.I,
)
# Cell values that are never a third party.
_ENTITY_CELL_STOP_RE = re.compile(
    r"^(?:this (?:web)?site|first[- ]party|we|us|ourselves|n/?a|-+|none|"
    r"various|see |recipients?)\b", re.I,
)
_MAX_ENTITY_CELL = 60


def _clean_entity_cell(v: str) -> str:
    v = _clean(v).strip(" .,:;-•*\t")
    v = v.lstrip(".")  # ".doubleclick.net" -> "doubleclick.net"
    if (not v or len(v) > _MAX_ENTITY_CELL
            or not any(c.isalpha() for c in v)
            or _ENTITY_CELL_STOP_RE.match(v)):
        return ""
    return v


def table_relations(
    raw_html: str,
    role: str = "",
    first_party: set[str] | None = None,
    doc_id: str = "",
) -> list[dict]:
    """Per-row relations from cookie / vendor / sub-processor tables."""
    if not raw_html:
        return []
    soup = BeautifulSoup(raw_html, "lxml")
    out: dict[str, dict] = {}
    for tbl in soup.find_all("table"):
        header_row, body = _table_headers_and_rows(tbl)
        if not header_row or not body:
            continue
        headers = [h.lower() for h in header_row]
        ent_idx = next(
            (i for i, h in enumerate(headers) if h and _ENTITY_COL_RE.search(h)), None
        )
        if ent_idx is None:
            continue
        purp_idx = next(
            (i for i, h in enumerate(headers)
             if i != ent_idx and h and _PURPOSE_COL_RE.search(h)),
            None,
        )
        cookie_table = (
            role == "cookie_policy"
            or any(_COOKIE_TABLE_RE.search(h) for h in headers)
        )
        vendor_table = any(_VENDOR_COL_RE.search(h) for h in headers if h)
        if cookie_table:
            data_type, action, default_purposes = (
                "cookie / device identifiers", "collect", [])
        elif vendor_table or role in ("subprocessor_list", "dpa"):
            data_type, action, default_purposes = (
                "personal data", "be_shared",
                ["services"] if role in ("subprocessor_list", "dpa") else [])
        else:
            continue

        source = "cookie_table" if cookie_table else "vendor_table"
        for cells in body:
            if ent_idx >= len(cells):
                continue
            entity = _clean_entity_cell(cells[ent_idx])
            if not entity or _is_first_party(entity, first_party):
                continue
            purpose_text = cells[purp_idx] if (
                purp_idx is not None and purp_idx < len(cells)) else ""
            purposes = purposes_from_text(purpose_text) or list(default_purposes)
            key = entity.lower()
            if key in out:
                out[key]["purposes"] = sorted(set(out[key]["purposes"]) | set(purposes))
                continue
            out[key] = _relation(
                entity, data_type, action, source=source,
                purposes=purposes, text=purpose_text, doc_id=doc_id,
            )
    return list(out.values())


# --------------------------------------------------------------------------- #
# Per-target entry point
# --------------------------------------------------------------------------- #
# Roles whose HTML is worth scanning for disclosure tables.
_TABLE_ROLES = {
    "privacy_policy", "cookie_policy", "subprocessor_list", "vendor_list",
    "dpa", "do_not_sell", "partners_page",
}


def structured_relations_for_target(
    corpus,
    docs,
    first_party: set[str] | None = None,
) -> list[dict]:
    """Relations synthesized from every registry / table doc of one target."""
    out: list[dict] = []
    for d in docs:
        if not d.ok:
            continue
        if d.role in MACHINE_READABLE_ROLES:
            raw = corpus.read_doc_html(d)
            rels = registry_relations(raw, doc_id=d.doc_id)
            if first_party:
                rels = [r for r in rels if not _is_first_party(r["entity"], first_party)]
            out.extend(rels)
        elif d.role in _TABLE_ROLES:
            raw = corpus.read_doc_html(d)
            out.extend(table_relations(
                raw, role=d.role, first_party=first_party, doc_id=d.doc_id,
            ))
    return out
