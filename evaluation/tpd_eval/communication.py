"""Backend semantic guardrails for user-visible propositions."""

from __future__ import annotations

EVIDENCE_KINDS = {"contact", "disclosure", "inventory_authorization", "registration",
                  "compatible_possibility", "verified_transmission"}


def communication_errors(finding: dict) -> list[str]:
    errors = []
    kind = finding.get("evidence_kind")
    if kind not in EVIDENCE_KINDS:
        errors.append("unknown evidence_kind")
    wording = str(finding.get("wording", "")).lower()
    transfer_wording = any(word in wording for word in
                           ("transmitted", "sent your data", "received your data"))
    if transfer_wording and kind != "verified_transmission":
        errors.append("transfer wording requires verified_transmission")
    if kind == "verified_transmission" and not finding.get("typed_transmission_evidence"):
        errors.append("verified_transmission requires typed transmission evidence")
    if finding.get("collection_complete") is not True and not finding.get("shows_incompleteness"):
        errors.append("incomplete collection must be visible")
    if not finding.get("provenance_visible"):
        errors.append("provenance must be visible")
    return errors
