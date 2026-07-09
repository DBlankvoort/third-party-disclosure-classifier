"""Medium classification."""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from .. import lexicons
from ..extract import Document
from ..typology import Medium, TargetType
from .platform import parse_platform_label
from .structural import STRUCTURED_ORG_MIN, StructuralSignals, structural_signals

_APP_TYPES = {TargetType.PLAY_STORE_APP.value, TargetType.APP_STORE_APP.value}
_WEB_TYPES = {TargetType.WEBSITE.value, TargetType.DATA_BROKER.value}

# Document roles whose collection intent is a narrative privacy/disclosure policy.
# A document fetched under one of these roles that reads like a policy is prose,
_POLICY_ROLES = {"privacy_policy", "cookie_policy", "do_not_sell", "dpa"}

# Title framing that marks a document as 100% a privacy/disclosure document, so
# a "Privacy & Terms" page is not discarded by the Terms-of-Use title guard.
_PRIVACY_TITLE_RE = re.compile(
    r"\b(privacy|cookies?|data protection|gdpr|ccpa|do not sell|"
    r"your privacy choices|sub[- ]?processors?|data processing)\b",
    re.I,
)

# Machine-readable registry roles allowed for each target type.
MR_ROLES_BY_TYPE = {
    TargetType.WEBSITE.value: {"ads_txt", "sellers_json", "vendors_json", "tcf_gvl"},
    TargetType.DATA_BROKER.value: {"ads_txt", "sellers_json", "vendors_json", "tcf_gvl"},
    TargetType.PLAY_STORE_APP.value: {"app_ads_txt", "sellers_json", "vendors_json", "tcf_gvl"},
    TargetType.APP_STORE_APP.value: {"app_ads_txt", "sellers_json", "vendors_json", "tcf_gvl"},
}


# Minimum number of cues required for lenient classification as a prose policy.
MIN_POLICY_CUES_SHORT = 2

# Minimum number of cues required for strict classification as a prose policy.
MIN_POLICY_CUES = 4


# Minimum visible text for a confident prose read.
MIN_PROSE_TEXT = 600

@dataclass
class DocClass:
    """The medium of a document (or ``None``) + evidence."""

    medium: Medium | None = None
    reason: str = ""
    structural: StructuralSignals | None = None
    mr_kind: str = ""              # the machine_readable_kind, if any
    has_platform_label: bool = False
    fired: list[str] = field(default_factory=list)

# Minimum share of a document's text that must sit
# in tables for CMP / cookie vendor-list classes.
_CMP_TABLE_DOMINANCE = 0.08


def _is_structured(
    doc: Document, structural: StructuralSignals, role: str, target_type: str
) -> tuple[bool, str]:
    """Classify structured property."""

    if target_type in _APP_TYPES:
        label = parse_platform_label(doc.text, role=role)
        if label.has_label and label.shares:
            return True, "platform_label"
    if structural.subprocessor_table:
        return True, "subprocessor_table"
    if structural.subprocessor_title and (structural.long_table or structural.n_table_orgs >= STRUCTURED_ORG_MIN):
        return True, "subprocessor_list"
    # A non-<table> third-party list
    if structural.vendor_list_framed and structural.n_list_orgs >= STRUCTURED_ORG_MIN:
        return True, "vendor_list"
    # A CMP / cookie vendor list (check first for evidence of a
    # cookie page and then for evidence that tables dominate). 
    strong_list = structural.cookie_titled or role in ("cookie_policy", "do_not_sell", "vendor_list")
    cmp_listish = (
        structural.cmp_fingerprint and (strong_list or structural.long_table)
    ) or (structural.cookie_titled and structural.long_table)
    table_chars = sum(len(t.cell_text) for t in doc.tables)
    tables_dominate = table_chars >= _CMP_TABLE_DOMINANCE * max(1, len(doc.text))
    if cmp_listish and tables_dominate and (
        structural.n_table_orgs >= 1 or (strong_list and structural.long_table)
    ):
        return True, "cmp_vendor_list"
    # Any other long table enumerating named orgs is a structured org list.
    if structural.long_table and structural.n_table_orgs >= STRUCTURED_ORG_MIN:
        return True, "vendor_table"
    return False, ""


def _has_disclosure_clause(doc: Document) -> bool:
    """Check if document has a disclosure clause early on."""
    for seg in doc.segments[:200]:
        if lexicons.positive_sharing(seg) or lexicons.third_party_collects(seg):
            return True
    return False


def _policy_prose(text: str, role: str, doc: Document) -> tuple[bool, str]:
    """Preliminary check for whether a doc is a prose policy."""
    if len(text) < MIN_PROSE_TEXT:
        return False, ""
    cues = {m.group(0).lower() for m in lexicons.POLICY_CUES_RE.finditer(text)}
    if len(cues) >= MIN_POLICY_CUES:
        return True, "policy_prose"
    if role in _POLICY_ROLES:
        if len(cues) >= MIN_POLICY_CUES_SHORT and _has_disclosure_clause(doc):
            return True, "policy_prose(short)"
    return False, ""


def classify_medium(
    doc: Document,
    role: str = "",
    target_type: str = "",
    structural: StructuralSignals | None = None,
) -> DocClass:
    """Assign a document :class:`Medium`"""
    if structural is None:
        structural = structural_signals(doc, role=role)
    dc = DocClass(structural=structural)

    # 1. machine_readable
    # Kinds sit at the top, so a prefix is sufficient.
    kind = lexicons.machine_readable_kind((doc.raw or doc.text)[:200_000])
    if kind:
        allowed = MR_ROLES_BY_TYPE.get(target_type, lexicons.MACHINE_READABLE_ROLES)
        # Check for role first to disambiguate ads.txt and app_ads.txt, then decide by kind.abs
        role_ok = (role in allowed) if role else (kind in allowed)
        if role_ok:
            dc.medium, dc.reason, dc.mr_kind = Medium.MACHINE_READABLE, f"registry:{kind}", kind
            dc.fired = [f"machine_readable:{kind}"]
            return dc

    # 2. structured
    is_struct, why = _is_structured(doc, structural, role, target_type)
    if is_struct:
        dc.medium, dc.reason = Medium.STRUCTURED, why
        dc.has_platform_label = why == "platform_label"
        dc.fired = list(structural.fired) + [why]
        return dc

    # Filter out prose policies to prevent classification as other docs.
    text = doc.text or ""
    is_prose, prose_reason = _policy_prose(text, role, doc)

    # 3. other_doc
    if (structural.help_doc or structural.partners_page) and not (
        role in _POLICY_ROLES and is_prose
    ):
        dc.medium = Medium.OTHER_DOC
        dc.reason = "help_doc" if structural.help_doc else "partners_page"
        dc.fired = list(structural.fired)
        return dc

    # Handle ToU/ToS/EULA/etc. being logged as PP.
    title = doc.title or ""
    if (
        is_prose
        and lexicons.TOS_TITLE_RE.search(title)
        and not _PRIVACY_TITLE_RE.search(title)
    ):
        dc.reason = "tos_page"
        return dc  # medium stays None

    # 4. prose
    if is_prose:
        dc.medium, dc.reason = Medium.PROSE, prose_reason
        dc.fired = ["policy_prose"]
        if lexicons.TOS_TITLE_RE.search(f"{doc.title} {role}"):
            dc.reason = "policy_prose(tos_titled)"  # privacy content on a terms page
        return dc

    # 5. None
    dc.reason = "unrecognised"
    return dc