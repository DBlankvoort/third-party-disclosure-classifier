"""Small, conventional metrics with explicit denominators and abstentions."""

from __future__ import annotations

import math
import random
from collections import defaultdict
from collections.abc import Callable
from typing import Any

from .schema import CLAIM_FIELDS

POSITIVE = "yes"
SCORABLE = {"yes", "no"}


def prf(tp: int, fp: int, fn: int) -> dict[str, Any]:
    precision = tp / (tp + fp) if tp + fp else None
    recall = tp / (tp + fn) if tp + fn else None
    f1 = (2 * precision * recall / (precision + recall)
          if precision is not None and recall is not None and precision + recall else None)
    return {"tp": tp, "fp": fp, "fn": fn, "precision": precision,
            "recall": recall, "f1": f1, "predicted_positive": tp + fp,
            "gold_positive": tp + fn}


def binary(records: list[dict[str, Any]]) -> dict[str, Any]:
    tp = fp = fn = tn = abstained = unavailable = 0
    for row in records:
        gold = ((row.get("annotations") or {}).get("adjudicated") or {}).get("verdict")
        pred = (row.get("prediction") or {}).get("verdict")
        if row.get("measurement", {}).get("available") is False:
            unavailable += 1
            continue
        if gold not in SCORABLE:
            abstained += 1
            continue
        if pred not in SCORABLE:
            abstained += 1
            continue
        if gold == pred == POSITIVE:
            tp += 1
        elif gold == pred:
            tn += 1
        elif pred == POSITIVE:
            fp += 1
        else:
            fn += 1
    out = prf(tp, fp, fn)
    total = len(records)
    scored = tp + fp + fn + tn
    out.update({"tn": tn, "n": total, "scored": scored, "abstained": abstained,
                "unavailable": unavailable, "coverage": scored / total if total else None,
                "status": "ok" if scored else "not_enough_labeled_data"})
    return out


def claim_tuple(records: list[dict[str, Any]]) -> dict[str, Any]:
    counts = {field: {"correct": 0, "n": 0} for field in CLAIM_FIELDS}
    exact = n = 0
    for row in records:
        final = ((row.get("annotations") or {}).get("adjudicated") or {})
        if final.get("verdict") not in SCORABLE or "values" not in final:
            continue
        prediction, gold = row.get("prediction") or {}, final["values"]
        n += 1
        all_correct = True
        for field in CLAIM_FIELDS:
            if field not in gold:
                all_correct = False
                continue
            counts[field]["n"] += 1
            correct = prediction.get(field) == gold[field]
            counts[field]["correct"] += int(correct)
            all_correct &= correct
        exact += int(all_correct)
    for value in counts.values():
        value["accuracy"] = value["correct"] / value["n"] if value["n"] else None
    return {"n": n, "exact_correct": exact, "exact_accuracy": exact / n if n else None,
            "components": counts, "status": "ok" if n else "not_enough_labeled_data"}


def pairwise_resolution(records: list[dict[str, Any]]) -> dict[str, Any]:
    """Score same-organisation pairs; false merges are false positives."""
    usable = [r for r in records if ((r.get("annotations", {}).get("adjudicated") or {})
                                     .get("verdict") in SCORABLE)]
    return binary(usable)


def document_collection(records: list[dict[str, Any]]) -> dict[str, Any]:
    """Keep discovery separate from downstream fetch/render/parse/role stages."""
    stages = {}
    for stage in ("discovered", "fetched", "rendered", "parsed", "role_correct"):
        projected = []
        for row in records:
            copy = {**row, "prediction": {"verdict": (row.get("prediction") or {}).get(stage)},
                    "annotations": {**(row.get("annotations") or {})}}
            final = ((row.get("annotations") or {}).get("adjudicated") or {})
            copy["annotations"]["adjudicated"] = {
                **final, "verdict": (final.get("values") or {}).get(stage)}
            projected.append(copy)
        stages[stage] = binary(projected)
    return stages


def chain(records: list[dict[str, Any]]) -> dict[str, Any]:
    hop_tp = hop_fp = 0
    strict_ok = strict_total = 0
    candidates_covered = candidates_total = 0
    duplicates = paths = 0
    signatures: set[tuple] = set()
    for row in records:
        final = (row.get("annotations", {}).get("adjudicated") or {})
        if final.get("verdict") in SCORABLE:
            strict_total += 1
            strict_ok += final["verdict"] == "yes"
        for hop in final.get("hops", []):
            if hop.get("verdict") == "yes":
                hop_tp += 1
            elif hop.get("verdict") == "no":
                hop_fp += 1
        if row.get("candidate_expected") is not None:
            candidates_total += 1
            candidates_covered += bool(row.get("candidate_expected"))
        signature = tuple(row.get("semantic_path", []))
        if signature:
            paths += 1
            duplicates += signature in signatures
            signatures.add(signature)
    return {"hop_precision": hop_tp / (hop_tp + hop_fp) if hop_tp + hop_fp else None,
            "hop_correct": hop_tp, "hop_incorrect": hop_fp,
            "strict_precision": strict_ok / strict_total if strict_total else None,
            "strict_correct": strict_ok, "strict_n": strict_total,
            "candidate_coverage": candidates_covered / candidates_total if candidates_total else None,
            "candidate_covered": candidates_covered, "candidate_n": candidates_total,
            "duplicate_paths": duplicates, "paths": paths,
            "duplicate_rate": duplicates / paths if paths else None,
            "status": "ok" if strict_total else "not_enough_labeled_data"}


def agreement(records: list[dict[str, Any]]) -> dict[str, Any]:
    """Raw agreement plus Cohen's kappa; ambiguous categories remain categories."""
    pairs = []
    for row in records:
        ann = row.get("annotations") or {}
        a, b = ann.get("annotator_1"), ann.get("annotator_2")
        if a and b and a.get("verdict") and b.get("verdict"):
            pairs.append((a["verdict"], b["verdict"]))
    if not pairs:
        return {"n": 0, "raw_agreement": None, "cohen_kappa": None,
                "status": "not_enough_labeled_data"}
    labels = sorted({x for pair in pairs for x in pair})
    raw = sum(a == b for a, b in pairs) / len(pairs)
    pa = {x: sum(a == x for a, _ in pairs) / len(pairs) for x in labels}
    pb = {x: sum(b == x for _, b in pairs) / len(pairs) for x in labels}
    expected = sum(pa[x] * pb[x] for x in labels)
    kappa = (raw - expected) / (1 - expected) if expected < 1 else None
    return {"n": len(pairs), "raw_agreement": raw, "cohen_kappa": kappa, "status": "ok"}


def bootstrap_ci(values: list[Any], statistic: Callable[[list[Any]], float], *,
                 seed: int, samples: int = 2000) -> dict[str, Any]:
    if len(values) < 20:
        return {"low": None, "high": None, "samples": samples,
                "status": "not_enough_labeled_data"}
    rng = random.Random(seed)
    estimates = sorted(statistic([rng.choice(values) for _ in values]) for _ in range(samples))
    return {"low": estimates[math.floor(.025 * samples)],
            "high": estimates[min(samples - 1, math.ceil(.975 * samples) - 1)],
            "samples": samples, "status": "ok"}


def macro_micro(records: list[dict[str, Any]], group: str = "target_id") -> dict[str, Any]:
    groups: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in records:
        groups[str((row.get("source") or {}).get(group, "unknown"))].append(row)
    scores = {key: binary(rows) for key, rows in groups.items()}
    f1s = [score["f1"] for score in scores.values() if score["f1"] is not None]
    return {"micro": binary(records), "macro_f1": sum(f1s) / len(f1s) if f1s else None,
            "groups": scores}
