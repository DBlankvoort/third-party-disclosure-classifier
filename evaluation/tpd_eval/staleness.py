"""Compatibility checks that prevent accidental reuse of stale annotations."""

from __future__ import annotations

from typing import Any


def stale_reasons(record: dict[str, Any], current: dict[str, Any]) -> list[str]:
    reasons = []
    source = record.get("source") or {}
    for key in ("document_sha256", "passage_sha256", "extraction_input_sha256"):
        if source.get(key) and current.get(key) != source[key]:
            reasons.append(f"changed {key}")
    recorded_version = record.get("extraction_version")
    if recorded_version and current.get("extraction_version") != recorded_version:
        reasons.append("incompatible extraction_version")
    if record.get("task") == "chain":
        old = source.get("hop_evidence_ids")
        if old and current.get("hop_evidence_ids") != old:
            reasons.append("changed hop evidence identity")
    return reasons
