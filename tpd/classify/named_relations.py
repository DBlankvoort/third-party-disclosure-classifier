"""Data-sharing relations from organisations named in a document's prose."""

from __future__ import annotations

from ..entities import resolve_name
from ..extract import MAX_HTML_BYTES, parse_html
from ..lexicons import MACHINE_READABLE_ROLES
from ..tracks import PERSONAL_DATA
from .named_entities import first_party_tokens, load_ner  # noqa: F401
from .prose_entities import ADMIT_CONFIDENCE, scan_document
from .structured_relations import DOWNSTREAM

NARRATIVE_ROLES = {
    "privacy_policy", "cookie_policy", "do_not_sell", "dpa", "vendor_list",
    "subprocessor_list", "partners_page", "help_doc",
}

_DATA_TYPE = "personal data"
_ACTION = "be_shared"


def _relation(entity, doc_id: str, text: str, subject: str,
              confidence: float, grounded: bool, signals) -> dict:
    return {
        "entity": entity.strip().lower(),
        "party": "third",
        "unspecified": False,
        "data_type": _DATA_TYPE,
        "action": _ACTION,
        "negative": False,
        "direction": DOWNSTREAM,
        "track": PERSONAL_DATA,
        "subject": subject,
        "confidence": confidence,
        "grounded": grounded,
        "signals": list(signals),
        "purposes": [],
        "examples": [],
        "qualifier": "",
        "sources": ["policy"],
        "text": text[:300],
        "doc_ids": [doc_id] if doc_id else [],
    }


def named_org_relations(
    corpus,
    docs,
    target_type: str = "website",
    first_party: set[str] | None = None,
    roles: set[str] = NARRATIVE_ROLES,
    use_ner: bool = True,
    min_confidence: float = ADMIT_CONFIDENCE,
) -> list[dict]:
    """Relations for the organisations one target's documents name."""
    ner_fn, _ = load_ner(enable=use_ner)
    out: dict[str, dict] = {}
    for d in docs:
        if not d.ok or d.role in MACHINE_READABLE_ROLES or d.role not in roles:
            continue
        html = corpus.read_doc_html(d)
        if not html.strip():
            continue
        scan = scan_document(
            parse_html(html, max_bytes=MAX_HTML_BYTES),
            ner_fn=ner_fn, role=d.role, first_party=first_party,
        )
        for entity in scan.entities:
            if entity.confidence < min_confidence:
                continue
            key = resolve_name(entity.name).key or entity.name.strip().lower()
            if not key:
                continue
            held = out.get(key)
            if held is not None:
                # The reading with the most behind it stands for the party.
                if entity.confidence <= held["confidence"]:
                    continue
            out[key] = _relation(
                entity.name, d.doc_id, entity.evidence, entity.subject,
                entity.confidence, entity.grounded, entity.signals,
            )
    return list(out.values())
