"""Structure detectors over :class:`tpd.extract.Document`s, 
for distinguishing ordinary prose from other doc types.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from .. import lexicons
from ..extract import Document
from .named_entities import detect_orgs

# Min number of rows for a table to be considered "long".
LONG_TABLE_ROWS = 12

# Min number of header tokens for a sub-processor table.
SUBPROC_HEADER_MIN = 2

# Header tokens which suggest subprocessor table status.
SUBPROCESSOR_TABLE_HEADERS = {
    "name", "subprocessor", "sub-processor", "vendor", "company", "entity",
    "purpose", "service", "processing", "location", "country", "region",
    "data", "category",
}

# Header tokens which confirm subprocessor table status.
_SUBPROC_STRONG = {
    "subprocessor", "sub-processor", "vendor", "company", "entity",
    "purpose", "processing", "service",
}

# Min number of distinct names for a structured list of named orgs.
STRUCTURED_ORG_MIN = 5

# Bytes of raw HTML scanned for the CMP fingerprint.
_CMP_SCAN_CAP = 60_000

# Amount of body text to scan for a flat vendor-list's enumerated names.
_LIST_SCAN_CAP = 200_000

# Roles whose purpose is an enumerated third-party list.
_LIST_ROLES = {"vendor_list", "subprocessor_list"}


@dataclass
class StructuralSignals:
    subprocessor_title: bool = False
    subprocessor_table: bool = False
    cmp_fingerprint: bool = False
    cookie_titled: bool = False
    help_doc: bool = False
    partners_page: bool = False
    long_table: bool = False
    table_named_orgs: list[str] = field(default_factory=list)
    n_table_orgs: int = 0
    vendor_list_framed: bool = False        # titled/role'd as an enumerated list
    list_named_orgs: list[str] = field(default_factory=list)  # names in a flat list
    n_list_orgs: int = 0
    fired: list[str] = field(default_factory=list)


def _looks_like_subprocessor_table(doc: Document) -> bool:
    for t in doc.tables:
        if t.n_rows < 3:
            continue
        overlap = t.header_tokens & SUBPROCESSOR_TABLE_HEADERS
        if len(overlap) >= SUBPROC_HEADER_MIN and overlap & _SUBPROC_STRONG:
            return True
    return False


def structural_signals(doc: Document, url: str = "", role: str = "") -> StructuralSignals:
    """Compute structural signals for one document."""
    sig = StructuralSignals()
    title_url = f"{doc.title} {url} {role}"

    sig.subprocessor_title = bool(lexicons.SUBPROCESSOR_TITLE_RE.search(title_url))
    sig.subprocessor_table = _looks_like_subprocessor_table(doc)
    sig.cmp_fingerprint = bool(lexicons.CMP_RE.search(doc.raw[:_CMP_SCAN_CAP]))
    sig.cookie_titled = bool(lexicons.COOKIE_TITLE_RE.search(title_url))
    sig.help_doc = bool(lexicons.HELP_DOC_RE.search(title_url)) or role == "help_doc"
    sig.partners_page = bool(lexicons.PARTNERS_PAGE_RE.search(title_url)) or role == "partners_page"
    sig.long_table = any(t.n_rows >= LONG_TABLE_ROWS for t in doc.tables)

    # Count number of named orgs in the table found using gazetteer.
    orgs: set[str] = set()
    for t in doc.tables:
        if t.cell_text:
            found, _ = detect_orgs(t.cell_text)
            orgs.update(found)
    sig.table_named_orgs = sorted(orgs)
    sig.n_table_orgs = len(orgs)

    # Check for non-<table> tables.
    sig.vendor_list_framed = (
        role in _LIST_ROLES
        or sig.subprocessor_title
        or bool(lexicons.VENDOR_LIST_TITLE_RE.search(f"{doc.title}\n{doc.text[:600]}"))
    )
    if sig.vendor_list_framed and not sig.subprocessor_table:
        body_orgs, _ = detect_orgs(doc.text[:_LIST_SCAN_CAP])
        sig.list_named_orgs = sorted(set(body_orgs))
        sig.n_list_orgs = len(sig.list_named_orgs)

    sig.fired = [
        k
        for k, v in (
            ("subprocessor_title", sig.subprocessor_title),
            ("subprocessor_table", sig.subprocessor_table),
            ("cmp_fingerprint", sig.cmp_fingerprint),
            ("cookie_titled", sig.cookie_titled),
            ("help_doc", sig.help_doc),
            ("partners_page", sig.partners_page),
            ("long_table", sig.long_table),
            ("vendor_list_framed", sig.vendor_list_framed),
        )
        if v
    ]
    return sig
