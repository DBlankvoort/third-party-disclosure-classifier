"""Versioned, evidence-centred records used by the evaluation harness."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Iterable
from pathlib import Path
from typing import Any

SCHEMA_VERSION = "2.0"
METRIC_VERSION = "2.0"
SPLITS = {"development", "validation", "test", "challenge"}
VERDICTS = {"yes", "no", "ambiguous", "insufficient_evidence", "not_applicable"}
TASKS = {"document", "claim", "entity", "traffic", "chain", "communication"}
CLAIM_FIELDS = (
    "source_party", "destination_party", "direction", "polarity",
    "data_category", "purpose", "data_subject", "document_id", "passage_id",
)


class ValidationError(ValueError):
    pass


def canonical_hash(value: Any) -> str:
    encoded = json.dumps(value, ensure_ascii=False, sort_keys=True,
                         separators=(",", ":")).encode()
    return hashlib.sha256(encoded).hexdigest()


def evidence_id(task: str, source: dict[str, Any]) -> str:
    """Stable ID from immutable source evidence, never graph enumeration order."""
    if task not in TASKS:
        raise ValidationError(f"unknown task: {task}")
    identity = {
        "task": task,
        "target_id": source.get("target_id"),
        "document_id": source.get("document_id"),
        "document_sha256": source.get("document_sha256"),
        "passage_sha256": source.get("passage_sha256"),
        "surface": source.get("surface"),
        "contact_domain": source.get("contact_domain"),
        "consent_treatment": source.get("consent_treatment"),
        "hop_evidence_ids": source.get("hop_evidence_ids"),
        "ui_surface": source.get("ui_surface"),
        "proposition_key": source.get("proposition_key"),
    }
    if not any(v not in (None, [], "") for k, v in identity.items() if k != "task"):
        raise ValidationError("source has no stable evidence identity")
    return f"{task}:{canonical_hash(identity)[:24]}"


def validate_manifest(data: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    required = ("schema_version", "corpus_snapshot_id", "annotation_version",
                "created_at", "random_seed", "targets")
    for key in required:
        if data.get(key) in (None, ""):
            errors.append(f"manifest.{key} is required")
    if data.get("schema_version") != SCHEMA_VERSION:
        errors.append(f"schema_version must be {SCHEMA_VERSION}")
    seen: set[str] = set()
    for i, target in enumerate(data.get("targets", [])):
        prefix = f"targets[{i}]"
        for key in ("target_id", "split", "selected_by", "collected_at",
                    "template_group", "corporate_family", "near_duplicate_group"):
            if key not in target:
                errors.append(f"{prefix}.{key} is required")
        if target.get("split") not in SPLITS:
            errors.append(f"{prefix}.split is invalid")
        tid = target.get("target_id")
        if tid in seen:
            errors.append(f"duplicate target_id: {tid}")
        seen.add(tid)
    return errors


def validate_record(record: dict[str, Any], *, allow_unadjudicated: bool = True) -> list[str]:
    errors: list[str] = []
    for key in ("schema_version", "item_id", "task", "split", "source",
                "prediction", "annotations"):
        if key not in record:
            errors.append(f"{key} is required")
    if record.get("schema_version") != SCHEMA_VERSION:
        errors.append(f"schema_version must be {SCHEMA_VERSION}")
    if record.get("task") not in TASKS:
        errors.append("task is invalid")
    if record.get("split") not in SPLITS:
        errors.append("split is invalid")
    source = record.get("source") or {}
    try:
        expected = evidence_id(record.get("task", ""), source)
        if record.get("item_id") != expected:
            errors.append("item_id does not match source evidence")
    except ValidationError as exc:
        errors.append(str(exc))
    annotations = record.get("annotations") or {}
    for name in ("annotator_1", "annotator_2", "adjudicated"):
        value = annotations.get(name)
        if value is None:
            if name == "adjudicated" and allow_unadjudicated:
                continue
            errors.append(f"annotations.{name} is required")
            continue
        if value.get("verdict") not in VERDICTS:
            errors.append(f"annotations.{name}.verdict is invalid")
        confidence = value.get("confidence")
        if confidence is not None and not (0 <= confidence <= 1):
            errors.append(f"annotations.{name}.confidence must be in [0,1]")
    if record.get("task") == "claim":
        for field in CLAIM_FIELDS:
            if field not in (record.get("prediction") or {}):
                errors.append(f"prediction.{field} is required (use 'unstated')")
    return errors


def read_jsonl(path: str | Path) -> list[dict[str, Any]]:
    rows = []
    with Path(path).open(encoding="utf-8") as handle:
        for lineno, line in enumerate(handle, 1):
            if line.strip():
                try:
                    rows.append(json.loads(line))
                except json.JSONDecodeError as exc:
                    raise ValidationError(f"{path}:{lineno}: {exc}") from exc
    return rows


def write_jsonl(path: str | Path, rows: Iterable[dict[str, Any]]) -> None:
    with Path(path).open("w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")
