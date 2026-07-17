"""Faceted classification."""

from __future__ import annotations

from dataclasses import dataclass, field

from ..extract import Document
from ..typology import Medium, facet_code
from .document_class import DocClass, classify_medium
from .specificity import SpecScan, specificities_in_doc
from .structural import structural_signals

# Minimum extracted text for a document to support a confident read.
DECISIVE_MIN_TEXT = 1500

# Generic policies
_PLATFORM_POLICIES = [
    ("docs.github.com", "/", "github"),
    ("github.com", "/site-policy", "github"),
    ("policies.google.com", "/", "google"),
    ("www.facebook.com", "/privacy", "facebook"),
    ("www.facebook.com", "/policy", "facebook"),
    ("www.apple.com", "/legal/privacy", "apple"),
    ("automattic.com", "/privacy", "automattic"),
    ("wordpress.com", "/privacy", "wordpress"),
    ("twitter.com", "/privacy", "twitter"),
    ("x.com", "/privacy", "x"),
]


def _generic_platform_policy(url: str, first_party: set[str] | None) -> bool:
    """True iff ``url`` is a platform's own policy and the target is not the platform."""
    from urllib.parse import urlparse

    try:
        parsed = urlparse(url or "")
    except ValueError:
        return False
    host = (parsed.hostname or "").lower()
    path = parsed.path or "/"
    for p_host, p_path, brand in _PLATFORM_POLICIES:
        if host == p_host and path.startswith(p_path):
            return brand not in (first_party or set())
    return False


@dataclass
class DocClassification:
    doc_id: str = ""
    role: str = ""
    url: str = ""
    medium: str = ""
    relevant: bool = False
    facets: list[str] = field(default_factory=list)
    named_orgs: list[str] = field(default_factory=list)
    org_typing: str = ""
    category_terms: list[str] = field(default_factory=list)
    doc_class_reason: str = ""
    structural_fired: list[str] = field(default_factory=list)
    needs_review: bool = False
    review_reason: str = ""
    evidence: str = ""
    decisive: bool = False


@dataclass
class TargetClassification:
    target_id: str = ""
    target_type: str = ""
    relevant_docs: int = 0
    facets: list[str] = field(default_factory=list)
    classified: bool = False
    docs: list[DocClassification] = field(default_factory=list)

    @property
    def typology_class(self) -> str:
        """The target's typology class."""
        return ";".join(self.facets)


def classify_document(
    doc: Document,
    role: str = "",
    target_type: str = "",
    doc_id: str = "",
    url: str = "",
    ner_fn=None,
    backend=None,
    doc_class: DocClass | None = None,
    first_party: set[str] | None = None,
) -> DocClassification:
    """Classify one document."""
    if doc_class is None:
        structural = structural_signals(doc, url=url, role=role)
        doc_class = classify_medium(doc, role=role, target_type=target_type, structural=structural)
    structural = doc_class.structural or structural_signals(doc, url=url, role=role)

    out = DocClassification(
        doc_id=doc_id, role=role, url=url,
        medium=doc_class.medium.value if doc_class.medium else "",
        doc_class_reason=doc_class.reason,
        structural_fired=list(structural.fired),
    )

    if doc_class.reason == "parked_domain":
        out.decisive = True
        return out

    if _generic_platform_policy(url, first_party):
        out.doc_class_reason = "platform_policy"
        out.evidence = "another platform's own generic policy"
        out.decisive = True
        return out

    if doc_class.medium is None:
        # Not a recognisable disclosure document.
        if backend is not None:
            try:
                if backend.has_verdict(doc):
                    out.relevant = backend.is_third_party_sharing(doc)
                    out.decisive = True
                    out.evidence = "polisis_backend"
                    return out
            except Exception:  # noqa: BLE001
                pass
        out.decisive = False
        return out

    scan: SpecScan = specificities_in_doc(
        doc, doc_class.medium, structural, role=role,
        target_type=target_type, ner_fn=ner_fn, first_party=first_party,
    )
    facets = {facet_code(doc_class.medium, s) for s in scan.specificities}

    out.facets = facets
    out.named_orgs = scan.named_orgs
    out.org_typing = scan.org_typing
    out.category_terms = scan.category_terms
    out.relevant = bool(facets)
    out.evidence = scan.evidence or (f"medium={out.medium}" if facets else "")

    out.decisive = bool(facets) or len(doc.text) >= DECISIVE_MIN_TEXT

    # Medium but no disclosure found.
    if doc_class.medium in (Medium.PROSE, Medium.OTHER_DOC) and not facets and backend is not None:
        try:
            if backend.has_verdict(doc):
                out.relevant = backend.is_third_party_sharing(doc)
                out.decisive = True
                out.evidence = "polisis_backend"
        except Exception:  # noqa: BLE001
            pass
    return out


def assemble_target(
    target_type: str,
    target_id: str,
    doc_classifications: list[DocClassification],
) -> TargetClassification:
    """Roll up classified documents into a target's typology class."""
    tc = TargetClassification(target_id=target_id, target_type=target_type)
    union: set[str] = set()
    for dc in doc_classifications:
        tc.docs.append(dc)
        if dc.relevant:
            tc.relevant_docs += 1
        union |= set(dc.facets)

    tc.facets = union
    decisive = any(dc.decisive for dc in tc.docs)
    pending = any(dc.needs_review for dc in tc.docs)
    tc.classified = decisive and not pending
    return tc

def classify_target(
    target_type: str,
    docs: list[tuple[Document, str, str, str]],  # (doc, role, doc_id, url)
    target_id: str = "",
    ner_fn=None,
    backend=None,
    first_party: set[str] | None = None,
) -> TargetClassification:
    """Classify every document in a target's set."""
    doc_classifications = [
        classify_document(
            doc, role=role, target_type=target_type, doc_id=doc_id, url=url,
            ner_fn=ner_fn, backend=backend, first_party=first_party,
        )
        for doc, role, doc_id, url in docs
    ]
    return assemble_target(target_type, target_id, doc_classifications)