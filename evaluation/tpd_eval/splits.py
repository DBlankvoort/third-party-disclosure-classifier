"""Leakage-resistant corpus split checks."""

from __future__ import annotations

from collections import defaultdict
from typing import Any

LEAKAGE_KEYS = ("target_id", "template_group", "corporate_family", "near_duplicate_group")


def leakage_violations(targets: list[dict[str, Any]]) -> list[dict[str, Any]]:
    violations = []
    for key in LEAKAGE_KEYS:
        groups: dict[str, dict[str, list[str]]] = defaultdict(lambda: defaultdict(list))
        for target in targets:
            value = target.get(key)
            if value not in (None, "", "unknown"):
                groups[str(value)][target.get("split", "missing")].append(target.get("target_id", ""))
        for value, by_split in groups.items():
            if len(by_split) > 1:
                violations.append({"key": key, "value": value,
                                   "splits": dict(sorted(by_split.items()))})
    return violations
