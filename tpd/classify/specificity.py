"""Detect specificity of docs."""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from ..extract import Document
from ..lexicons import (
    CATEGORY_RE,
    EXEMPLIFIER_RE,
    GENERIC_RE,
    POINTER_RE,
    clause_window,
    has_first_party_anchor,
    implicit_sale,
    is_negated,
    machine_readable_kind,
    positive_collection,
    positive_sharing,
    registry_named_orgs,
    third_party_collects,
)
from ..typology import Medium, Specificity
from .named_entities import _is_first_party, detect_orgs, gazetteer_orgs
from .platform import parse_platform_label
from .structural import StructuralSignals

# Cap how many segments we scan.
MAX_SEGMENTS = 600
# Number of segments to run through NER.
MAX_NER_SEGMENTS = 30


@dataclass
class SpecScan:
    specificities: set[Specificity] = field(default_factory=set)
    named_orgs: list[str] = field(default_factory=list)
    category_terms: list[str] = field(default_factory=list)
    org_typing: str = ""
    deferred: bool = False          # a deferred pointer ("list available at ...")
    evidence: str = ""


# Fraction of navigation code beyond which to filter out.
_NAV_CAP_FRACTION = 0.6


def _nav_junk(seg: str) -> bool:
    if re.search(r"[.!?]", seg):
        return False
    toks = re.findall(r"[A-Za-z][\w'&-]*", seg)
    if len(toks) < 3:
        return False
    caps = sum(1 for t in toks if t[:1].isupper())
    return caps / len(toks) >= _NAV_CAP_FRACTION


# A short informational heading / link label.
_INFO_HEADING_RE = re.compile(r"^(?:how|what|why|where|when|who|learn)\b[^.!?:]{0,80}$", re.I)

# A disclosure lead-in.
_LEADIN_CARRY = 8

# First-party service-usage naming.
_WE_USE_RE = re.compile(r"\bwe\s+(?:\w+\s+){0,2}?us(?:e|es|ing)\b", re.I)

# Attributive generics pointing to generic references rather than named orgs.
_GEN_POST_STOP = {
    "that", "which", "who", "whom", "for", "to", "in", "of", "and", "or",
    "unless", "except", "without", "with", "on", "at", "if", "when", "the",
    "may", "might", "will", "shall", "can", "could", "such", "under",
}


def _bare_generic(seg: str, orgs: list[str]) -> bool:
    """A generic third-party reference."""
    cat_spans = [m.span() for m in CATEGORY_RE.finditer(seg)]
    for m in GENERIC_RE.finditer(seg):
        if any(a <= m.start() and m.end() <= b for a, b in cat_spans):
            continue
        if is_negated(seg, m.start()):
            continue
        if orgs and re.search(r"\b(?:a|an)\s+$", seg[:m.start()], re.I):
            nxt = re.match(r"\s+([A-Za-z-]+)", seg[m.end():])
            if nxt and nxt.group(1).lower() not in _GEN_POST_STOP:
                continue
        return True
    return False


# Possessive-noun continuations that make the org a qualifier.
_ORG_QUALIFIER_RE = re.compile(
    r"^(?:'s)?\s+(?:data|settings?|accounts?|servers?|api)\b", re.I
)
_ORG_COLLECT_WINDOW = 70

_DOMAIN_ORG_RE = re.compile(
    r"(?<![-.\w])([a-z0-9][a-z0-9-]{1,40}\.(?:com|net|org|io|ai|app|dev))\b", re.I
)
_DOMAIN_LINK_PRE_RE = re.compile(
    r"(?:https?://|www\.|@|(?:at|visit|see|via|from)\s+)$", re.I
)


def _domain_orgs(seg: str, first_party: set[str] | None) -> list[str]:
    """Domain-name org surfaces in ``seg`` that are not links or first party."""
    from .named_entities import _is_first_party

    out = []
    for m in _DOMAIN_ORG_RE.finditer(seg):
        if _DOMAIN_LINK_PRE_RE.search(seg[:m.start()]):
            continue
        d = m.group(1).lower()
        if not _is_first_party(d, first_party):
            out.append(d)
    return out


def _org_collects(seg: str, names: set[str]) -> bool:
    """A known org described as itself collecting."""
    from .named_entities import _GAZ_RE

    mentions = [m for m in _GAZ_RE.finditer(seg) if m.group(0).lower() in names]
    mentions += [m for m in _DOMAIN_ORG_RE.finditer(seg)
                 if m.group(1).lower() in names]
    for m in mentions:
        if re.search(r"\bby\s+$", seg[:m.start()], re.I):
            continue
        post = seg[m.end():]
        if _ORG_QUALIFIER_RE.match(post):
            continue
        if positive_collection(clause_window(seg, m.end(), _ORG_COLLECT_WINDOW)):
            return True
    return False


def _org_disclosure_context(seg: str, first_party: set[str] | None = None) -> bool:
    """Disclosure contexts that name third parties without a sharing verb."""
    names = {n for n, _ in gazetteer_orgs(seg)}
    names = set(_party_orgs(seg, sorted(names)))
    names |= {d for d in _domain_orgs(seg, first_party)}
    if not names:
        return False
    if EXEMPLIFIER_RE.search(seg) and (_category_matches(seg, []) or GENERIC_RE.search(seg)):
        return True
    # org must be the (nearby) object of usage.
    for m in _WE_USE_RE.finditer(seg):
        window = seg[m.end():m.end() + 90].lower()
        if any(n in window for n in names):
            return True
    return _org_collects(seg, names)


def _category_matches(seg: str, orgs: list[str]) -> list[str]:
    """Category-term matches that reference a party."""
    lower_orgs = [o.lower() for o in orgs]
    out = []
    for m in CATEGORY_RE.finditer(seg):
        term = m.group(0).lower()
        if is_negated(seg, m.start()):
            continue
        if term in ("partner", "partners") and seg[m.end():].lstrip().lower().startswith("with"):
            continue
        # "Exclude talk about the user's ISP.
        if term.startswith("service provider") and re.search(
            r"\binternet\s+$", seg[:m.start()], re.I
        ):
            continue
        pre = seg[max(0, m.start() - 30):m.start()].lower()
        if lower_orgs and any(o in pre for o in lower_orgs):
            continue
        out.append(term)
    return out


# Filter out corporate data identifiers.
_ORG_IDENTIFIER_RE = re.compile(
    r"^\s+(?:idfas?|advertising[- ]ids?|device[- ]ids?|ids?)\b", re.I
)


def _party_orgs(seg: str, orgs: list[str]) -> list[str]:
    """Filter out org surfaces whose every occurrence is a data-field mention."""
    kept = []
    for o in orgs:
        occurrences = [m.end() for m in re.finditer(re.escape(o), seg, re.I)]
        if occurrences and all(_ORG_IDENTIFIER_RE.match(seg[e:]) for e in occurrences):
            continue
        kept.append(o)
    return kept

_MAX_SEG_LEN = 600
_SENT_SPLIT_RE = re.compile(r"(?<=[.!?])\s+")


def _sentence_segments(segments) -> list[str]:
    out: list[str] = []
    for seg in segments:
        if len(seg) <= _MAX_SEG_LEN:
            out.append(seg)
        else:
            out.extend(p for p in _SENT_SPLIT_RE.split(seg) if p.strip())
    return out


def _scan_prose(
    doc: Document,
    ner_fn,
    first_party: set[str] | None = None,
    policy_ctx: bool = False,
) -> SpecScan:
    """Scan narrative text for inline disclosures within sharing/collection contexts."""
    scan = SpecScan()
    orgs_all: list[str] = []
    cats_all: list[str] = []
    # Disclosure-context segments only.
    qualifying: list[str] = []
    inherit = 0
    for seg in _sentence_segments(doc.segments[:MAX_SEGMENTS]):
        if _nav_junk(seg):
            continue
        # Do not consider informational headings pointing elsewhere.
        if _INFO_HEADING_RE.match(seg.strip()):
            continue
        q = positive_sharing(seg) or third_party_collects(seg) or implicit_sale(seg)
        if not q and policy_ctx:
            q = _org_disclosure_context(seg, first_party)
        if q and not policy_ctx and not has_first_party_anchor(seg):
            q = False
        if q:
            qualifying.append(seg)
            # Check qualifying lead-ins.
            inherit = _LEADIN_CARRY if seg.rstrip().endswith(":") else 0
        elif inherit > 0:
            qualifying.append(seg)
            inherit -= 1
    ner_segments = qualifying[:MAX_NER_SEGMENTS]
    batch_fn = getattr(ner_fn, "batch", None) if ner_fn else None
    if batch_fn is not None:
        ents_by_seg = batch_fn(ner_segments)
    else:
        single_fn = ner_fn or (lambda t: [])
        ents_by_seg = [single_fn(seg) for seg in ner_segments]
    ents_by_seg += [[]] * (len(qualifying) - len(ents_by_seg))

    for seg, ents in zip(qualifying, ents_by_seg):
        orgs, seg_typing = detect_orgs(
            seg, first_party=first_party, prose_precision=True, ner_ents=ents
        )
        orgs = _party_orgs(seg, orgs)
        for d in _domain_orgs(seg, first_party):
            if d not in orgs and _org_collects(seg, {d}):
                orgs.append(d)
        cats = _category_matches(seg, orgs)
        generic = _bare_generic(seg, orgs)
        # A sale of user data with no stated recipient discloses an unnamed
        # third party.
        if not (orgs or cats or generic) and implicit_sale(seg):
            generic = True
        if orgs:
            scan.specificities.add(Specificity.NAMED)
            orgs_all.extend(orgs)
            if seg_typing and not scan.org_typing:
                scan.org_typing = seg_typing
        if cats:
            scan.specificities.add(Specificity.CATEGORY)
            cats_all.extend(cats)
        if generic:
            scan.specificities.add(Specificity.GENERIC)
        if POINTER_RE.search(seg):
            scan.deferred = True
        if not scan.evidence:
            scan.evidence = seg if len(seg) <= 200 else seg[:197] + "..."
    scan.named_orgs = sorted(set(orgs_all))
    scan.category_terms = sorted(set(cats_all))
    return scan


# Enumeration of named entities for display.
_ENUM_SCAN_CAP = 200_000
_ENUM_ORG_CAP = 200
# A vendor name cell is short.
_MAX_NAME_CELL = 60


def _enumerate_named(doc, structural, first_party) -> list[str]:
    """Clean vendor surfaces from a named structure."""
    from .named_entities import _is_first_party, gazetteer_orgs

    found: list[str] = []
    seen: set[str] = set()

    def add(v: str) -> None:
        v = v.strip(" .,:;-•\t")
        k = v.lower()
        if (v and k not in seen and len(v) <= _MAX_NAME_CELL
                and any(ch.isalpha() for ch in v)
                and not _is_first_party(v, first_party)):
            seen.add(k)
            found.append(v)

    # (a) the vendor/name column of every table
    for t in doc.tables:
        for cell in t.name_cells:
            add(cell)
            if len(found) >= _ENUM_ORG_CAP:
                return found
    # (b) gazetteer-known vendors
    texts = [t.cell_text for t in doc.tables]
    if structural.vendor_list_framed:
        texts.append(doc.text)
    for t in texts:
        for name, _ in gazetteer_orgs((t or "")[:_ENUM_SCAN_CAP]):
            if not _is_first_party(name, first_party):
                add(name)
            if len(found) >= _ENUM_ORG_CAP:
                return found
    return found


def _merge_orgs(existing: list[str], extra: list[str]) -> list[str]:
    """Union two org-surface lists."""
    merged = {o.lower(): o for o in extra}
    for o in existing:
        merged[o.lower()] = o
    return sorted(merged.values(), key=str.lower)[:_ENUM_ORG_CAP]


# Roles whose collection intent is a privacy/disclosure document.
_POLICY_CTX_ROLES = {
    "privacy_policy", "cookie_policy", "dpa", "do_not_sell",
    "subprocessor_list", "vendor_list",
}

_PLATFORM_LABEL_ROLES = {"store_listing", "play_data_safety", "app_privacy"}


def _policy_table_orgs(doc: Document, first_party: set[str] | None) -> list[str]:
    """Named third parties from vendor/provider tables embedded in a prose policy."""
    from ..extract import _VENDOR_COL_RE
    from .named_entities import _CORP_SUFFIX_RE

    found: list[str] = []
    for t in doc.tables:
        for cell in t.name_cells:
            cell = cell.strip(" .,:;-•\t")
            if not cell or len(cell) > _MAX_NAME_CELL:
                continue
            if _is_first_party(cell, first_party):
                continue
            gaz = gazetteer_orgs(cell)
            if gaz:
                found.extend(name for name, _ in gaz
                             if not _is_first_party(name, first_party))
            elif _CORP_SUFFIX_RE.search(cell):
                found.append(cell)
        if not t.name_cells and t.cell_text and any(
            _VENDOR_COL_RE.search(h) for h in t.headers
        ):
            found.extend(
                name for name, _ in gazetteer_orgs(t.cell_text[:_ENUM_SCAN_CAP])
                if not _is_first_party(name, first_party)
            )
    return sorted({o.lower(): o for o in found}.values(), key=str.lower)


def specificities_in_doc(
    doc: Document,
    medium: Medium,
    structural: StructuralSignals,
    role: str = "",
    target_type: str = "",
    ner_fn=None,
    first_party: set[str] | None = None,
) -> SpecScan:
    """Specificity set + evidence for ``doc``, given its classified ``medium``."""
    if medium is Medium.MACHINE_READABLE:
        # Return machine-readable orgs.
        raw = doc.raw or doc.text or ""
        kind = machine_readable_kind(raw[:200_000])
        orgs = registry_named_orgs(raw, kind)
        if first_party:
            orgs = [o for o in orgs if not _is_first_party(o, first_party)]
        return SpecScan(
            specificities={Specificity.NAMED},
            named_orgs=sorted(set(orgs), key=str.lower),
            org_typing="company",
            evidence=(f"registry enumerates {len(orgs)} named vendor(s)"
                      if orgs else "registry enumerates named vendors"),
        )

    policy_ctx = role in _POLICY_CTX_ROLES

    if medium is Medium.STRUCTURED:
        label = parse_platform_label(doc.text, role=role)
        if label.has_label and role in _PLATFORM_LABEL_ROLES:
            scan = SpecScan()
        else:
            scan = _scan_prose(doc, ner_fn, first_party=first_party, policy_ctx=policy_ctx)
        # Named orgs in the structure's tables, or enumerated in a flat vendor list.
        struct_orgs = set(structural.table_named_orgs) | set(structural.list_named_orgs)
        if struct_orgs:
            scan.specificities.add(Specificity.NAMED)
            scan.named_orgs = sorted(set(scan.named_orgs) | struct_orgs)
        # Generic data sharing clauses in app stores.
        if label.has_label and label.shares and not scan.specificities:
            scan.specificities.add(Specificity.GENERIC)
            if not scan.evidence:
                scan.evidence = f"platform label ({label.kind}) declares sharing"
        # A subprocessor/vendor table automatically qualifies for named.
        if not scan.specificities and (structural.subprocessor_table
                                       or structural.long_table
                                       or structural.vendor_list_framed):
            scan.specificities.add(Specificity.NAMED)
            scan.evidence = scan.evidence or "structured vendor/sub-processor list"
        # Enrich the displayed vendor list.
        if Specificity.NAMED in scan.specificities:
            scan.named_orgs = _merge_orgs(
                scan.named_orgs, _enumerate_named(doc, structural, first_party)
            )
        return scan

    if medium is Medium.OTHER_DOC:
        # Help / partners / integrations page emits facet only from disclosures in context.
        scan = _scan_prose(doc, ner_fn, first_party=first_party)
        # Enrich names
        if Specificity.NAMED in scan.specificities:
            scan.named_orgs = _merge_orgs(
                scan.named_orgs, _enumerate_named(doc, structural, first_party)
            )
        return scan

    # prose
    scan = _scan_prose(doc, ner_fn, first_party=first_party, policy_ctx=policy_ctx)
    # Check for prose embedded vendor table.
    if policy_ctx:
        table_orgs = _policy_table_orgs(doc, first_party)
        if table_orgs:
            scan.specificities.add(Specificity.NAMED)
            scan.named_orgs = _merge_orgs(scan.named_orgs, table_orgs)
            if not scan.org_typing:
                scan.org_typing = "company"
            if not scan.evidence:
                scan.evidence = f"embedded vendor table names {len(table_orgs)} org(s)"
    return scan
