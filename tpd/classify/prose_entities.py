"""Recall-tuned extraction of the organisations a narrative document names."""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from ..entities import is_investor_parent, known_to_kb, resolve_name
from ..extract import Document
from ..lexicons import (
    SERVICE_DATA_RE,
    has_first_party_anchor,
    implicit_sale,
    inbound_acquisition,
    positive_sharing,
    third_party_collects,
)
from ..tracks import SERVICE_DATA, SERVICE_DATA_ROLES, SITE_VISITOR, UNKNOWN
from .named_entities import (
    _CORP_SUFFIX_RE,
    _is_first_party,
    clean_ner_org,
    enumeration_runs,
    frame_named_orgs,
    gazetteer_orgs,
    grounded_org,
    in_naming_frame,
)
from .specificity import _domain_orgs, _nav_junk, _sentence_segments
from .structured_relations import DOWNSTREAM, UPSTREAM

MAX_SEGMENTS = 3_000
MAX_NER_CHARS = 400_000
MAX_SEGMENT_CHARS = 2_000

ADMIT_CONFIDENCE = 0.3

SIGNAL_WEIGHTS = {
    "knowledge_base": 0.45,
    "gazetteer": 0.45,
    "host": 0.35,
    "vendor_column": 0.3,
    "corporate_form": 0.25,
    "naming_frame": 0.25,
    "disclosure_context": 0.3,
    "coordinated": 0.3,
    "repeated": 0.1,
}

_HOST_RE = re.compile(r"^[a-z0-9][a-z0-9-]{1,60}(?:\.[a-z0-9-]{2,})+$", re.I)
_MAX_NAME_CELL = 60


@dataclass
class ProseEntity:
    """One organisation a document names with confidence."""

    name: str
    confidence: float = 0.0
    signals: tuple[str, ...] = ()
    grounded: bool = False
    subject: str = UNKNOWN
    direction: str = DOWNSTREAM
    occurrences: int = 1
    evidence: str = ""

    @property
    def admitted(self) -> bool:
        return self.confidence >= ADMIT_CONFIDENCE


@dataclass
class ProseScan:
    """Every organisation one document names."""

    entities: list[ProseEntity] = field(default_factory=list)
    segments_read: int = 0
    segments_total: int = 0
    truncated: bool = False

    @property
    def admitted(self) -> list[ProseEntity]:
        return [e for e in self.entities if e.admitted]


@dataclass
class _Candidate:
    """A surface accumulating the signals read for it."""

    name: str
    signals: set[str] = field(default_factory=set)
    occurrences: int = 0
    subjects: set[str] = field(default_factory=set)
    directions: set[str] = field(default_factory=set)
    evidence: str = ""
    inbound_evidence: str = ""

    def note(self, signal: str) -> None:
        self.signals.add(signal)


def _subject_of(segment: str, role: str) -> str:
    """Whose data the arrangement a segment states concerns."""
    if role in SERVICE_DATA_ROLES or SERVICE_DATA_RE.search(segment):
        return SERVICE_DATA
    if has_first_party_anchor(segment) and (
        positive_sharing(segment) or third_party_collects(segment)
        or implicit_sale(segment)
    ):
        return SITE_VISITOR
    return UNKNOWN


def _resolve_subject(subjects: set[str]) -> str:
    if SERVICE_DATA in subjects:
        return SERVICE_DATA
    if SITE_VISITOR in subjects:
        return SITE_VISITOR
    return UNKNOWN


def _direction_of(segment: str) -> str:
    """Which way a segment moves data"""
    if _in_context(segment):
        return DOWNSTREAM
    return UPSTREAM if inbound_acquisition(segment) else ""


def _resolve_direction(directions: set[str]) -> str:
    """A party reads as a source only where no clause places it downstream."""
    if UPSTREAM in directions and DOWNSTREAM not in directions:
        return UPSTREAM
    return DOWNSTREAM


def _admissible(name: str, first_party: set[str] | None) -> bool:
    """Whether a cleaned surface may stand as a party."""
    if not name or len(name) > _MAX_NAME_CELL:
        return False
    if _is_first_party(name, first_party):
        return False
    return not is_investor_parent(name)


def _segments(doc: Document) -> list[str]:
    out: list[str] = []
    for seg, _ in _sentence_segments(doc.segments, getattr(doc, "list_items", None)):
        if len(seg) > MAX_SEGMENT_CHARS or _nav_junk(seg):
            continue
        out.append(seg)
    return out


def _ner_by_segment(segments: list[str], ner_fn) -> list[list[str]]:
    if ner_fn is None:
        return [[] for _ in segments]
    batch_fn = getattr(ner_fn, "batch", None)
    if batch_fn is not None:
        return list(batch_fn(segments))
    return [ner_fn(seg) for seg in segments]


def _segment_key(segment: str) -> str:
    """A segment's identity across the documents that repeat it."""
    return " ".join(segment.split())


def _read_segment(segment: str, ents, role: str, first_party) -> list[tuple]:
    """Every ``(name, signals, subject, direction)`` one segment supports."""
    subject = _subject_of(segment, role)
    direction = _direction_of(segment)
    context = ["disclosure_context"] if _in_context(segment) else []
    out: list[tuple] = []

    def emit(name: str, signals) -> None:
        out.append((name, tuple(signals), subject, direction))

    for name, _ in gazetteer_orgs(segment):
        emit(name, ["gazetteer", *context])
    framed = frame_named_orgs(segment)
    for name in framed:
        cleaned = clean_ner_org(name)
        if cleaned:
            emit(cleaned, ["naming_frame", *context])
    framed_keys = {f.strip(" .,:;-•").lower() for f in framed}
    for ent in ents:
        cleaned = clean_ner_org(ent, allow_lowercase=True)
        if not cleaned or not _reads_as_a_name(cleaned):
            continue
        signals = list(context)
        if cleaned.lower() in framed_keys or in_naming_frame(segment, cleaned):
            signals.append("naming_frame")
        emit(cleaned, signals)
    for host in _domain_orgs(segment, first_party):
        emit(host, ["host", *context])
    for name in _coordinated_orgs(segment):
        emit(name, ["coordinated", *context])
    return out


def _segment_findings(segments: list[str], ner_fn, role: str, first_party,
                      cache: dict | None):
    """Yield ``(segment, findings)``, reading each distinct segment once."""
    store = cache if cache is not None else {}
    bucket = role in SERVICE_DATA_ROLES
    fresh: dict[tuple, str] = {}
    for segment in segments:
        key = (bucket, _segment_key(segment))
        if key not in store and key not in fresh:
            fresh[key] = segment
    if fresh:
        pending = list(fresh.items())
        texts = [segment for _, segment in pending]
        for (key, segment), ents in zip(pending, _ner_by_segment(texts, ner_fn),
                                        strict=False):
            store[key] = _read_segment(segment, ents, role, first_party)
    for segment in segments:
        yield segment, store.get((bucket, _segment_key(segment)), ())


def scan_document(
    doc: Document,
    ner_fn=None,
    role: str = "",
    first_party: set[str] | None = None,
    cache: dict | None = None,
) -> ProseScan:
    """Every organisation ``doc`` names."""
    segments = _segments(doc)
    scan = ProseScan(segments_total=len(segments))

    read: list[str] = []
    budget = MAX_NER_CHARS
    for seg in segments[:MAX_SEGMENTS]:
        if budget <= 0:
            break
        read.append(seg)
        budget -= len(seg)
    scan.segments_read = len(read)
    scan.truncated = len(read) < len(segments)

    candidates: dict[str, _Candidate] = {}

    def record(name: str, segment: str, signals, subject: str,
               direction: str = "") -> None:
        if not _admissible(name, first_party):
            return
        key = name.lower()
        cand = candidates.get(key)
        if cand is None:
            cand = candidates[key] = _Candidate(name=name)
        cand.occurrences += 1
        cand.subjects.add(subject)
        if direction:
            cand.directions.add(direction)
        for s in signals:
            cand.note(s)
        if known_to_kb(name):
            cand.note("knowledge_base")
        if _CORP_SUFFIX_RE.search(name):
            cand.note("corporate_form")
        if _HOST_RE.match(name):
            cand.note("host")
        clipped = (segment if len(segment) <= 200 else segment[:197] + "...") if segment else ""
        if not cand.evidence:
            cand.evidence = clipped
        if direction == UPSTREAM and not cand.inbound_evidence:
            cand.inbound_evidence = clipped

    for segment, findings in _segment_findings(read, ner_fn, role, first_party,
                                               cache):
        for name, signals, subject, direction in findings:
            record(name, segment, signals, subject, direction)

    _record_tables(doc, role, first_party, record)

    scan.entities = sorted(
        (_finalise(c) for c in _merge_surfaces(candidates.values())),
        key=lambda e: (-e.confidence, e.name.lower()),
    )
    return scan


def _merge_surfaces(candidates) -> list[_Candidate]:
    """Fold the several ways a document writes one organisation into one party."""
    merged: dict[str, _Candidate] = {}
    for cand in candidates:
        key = resolve_name(cand.name).key or cand.name.lower()
        held = merged.get(key)
        if held is None:
            merged[key] = cand
            continue
        held.signals |= cand.signals
        held.occurrences += cand.occurrences
        held.subjects |= cand.subjects
        held.directions |= cand.directions
        held.evidence = held.evidence or cand.evidence
        held.inbound_evidence = held.inbound_evidence or cand.inbound_evidence
        if len(cand.name) > len(held.name):
            held.name = cand.name
    return list(merged.values())


def _reads_as_a_name(surface: str) -> bool:
    if any(c.isupper() for c in surface):
        return True
    return bool(
        any(c.isdigit() for c in surface)
        or _HOST_RE.match(surface)
        or known_to_kb(surface)
    )


def _coordinated_orgs(segment: str) -> list[str]:
    """Members of an enumeration that a register recognises one member of."""
    out: list[str] = []
    for run in enumeration_runs(segment):
        cleaned = [clean_ner_org(name, allow_lowercase=True) or name for name in run]
        if not any(known_to_kb(name) for name in cleaned):
            continue
        out.extend(name for name in cleaned if name)
    return out


def _in_context(segment: str) -> bool:
    return bool(
        positive_sharing(segment) or third_party_collects(segment)
        or implicit_sale(segment)
    )


def _record_tables(doc: Document, role: str, first_party, record) -> None:
    """Surfaces held in a table's vendor, provider or recipient column."""
    subject = SERVICE_DATA if role in SERVICE_DATA_ROLES else SITE_VISITOR
    for table in doc.tables:
        for cell in table.name_cells:
            name = cell.strip(" .,:;-•\t")
            if not name:
                continue
            record(name, cell, ["vendor_column"], subject, DOWNSTREAM)
            for known, _ in gazetteer_orgs(name):
                if known.lower() != name.lower():
                    record(known, cell, ["vendor_column", "gazetteer"], subject,
                           DOWNSTREAM)


def _finalise(cand: _Candidate) -> ProseEntity:
    signals = set(cand.signals)
    if cand.occurrences >= 3:
        signals.add("repeated")
    confidence = min(1.0, sum(SIGNAL_WEIGHTS.get(s, 0.0) for s in signals))
    direction = _resolve_direction(cand.directions)
    return ProseEntity(
        name=cand.name,
        confidence=round(confidence, 3),
        signals=tuple(sorted(signals)),
        grounded=grounded_org(cand.name),
        subject=_resolve_subject(cand.subjects),
        direction=direction,
        occurrences=cand.occurrences,
        evidence=(cand.inbound_evidence or cand.evidence) if direction == UPSTREAM
        else cand.evidence,
    )
