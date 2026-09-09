from __future__ import annotations

from typing import Any

from .schema import SCHEMA_VERSION, evidence_id


def annotation_item(task: str, split: str, source: dict[str, Any],
                    prediction: dict[str, Any], **extra: Any) -> dict[str, Any]:
    return {"schema_version": SCHEMA_VERSION, "item_id": evidence_id(task, source),
            "task": task, "split": split, "source": source, "prediction": prediction,
            "annotations": {"annotator_1": None, "annotator_2": None,
                            "adjudicated": None}, **extra}
