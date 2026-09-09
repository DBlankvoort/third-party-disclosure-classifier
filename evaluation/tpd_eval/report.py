"""Generate an honest, provenance-first Markdown report."""

from __future__ import annotations

import json
from collections import Counter
from typing import Any

from .metrics import (
    agreement,
    binary,
    chain,
    claim_tuple,
    document_collection,
    macro_micro,
    pairwise_resolution,
)
from .schema import METRIC_VERSION


def score_records(records: list[dict[str, Any]], provenance: dict[str, Any]) -> dict[str, Any]:
    by_task = {task: [r for r in records if r.get("task") == task]
               for task in ("document", "claim", "entity", "traffic", "chain", "communication")}
    return {"metric_version": METRIC_VERSION, "provenance": provenance,
            "counts": dict(Counter(r.get("task") for r in records)),
            "agreement": agreement(records),
            "document": macro_micro(by_task["document"]),
            "document_stages": document_collection(by_task["document"]),
            "claims": claim_tuple(by_task["claim"]),
            "entity_resolution": pairwise_resolution(by_task["entity"]),
            "traffic": macro_micro(by_task["traffic"]),
            "chains": chain(by_task["chain"]),
            "communication": binary(by_task["communication"])}


def markdown(result: dict[str, Any]) -> str:
    def value(x: Any) -> str:
        return "not enough labeled data" if x is None else (f"{100*x:.1f}%" if isinstance(x, float) else str(x))
    p = result["provenance"]
    lines = ["# Evidence correctness evaluation", "", "## Scope and provenance", "",
             f"- Source: `{p.get('source_commit', 'unknown')}` (dirty: `{p.get('dirty_worktree', 'unknown')}`)",
             f"- Corpus snapshot: `{p.get('corpus_snapshot_id', 'unknown')}`",
             f"- Annotation set: `{p.get('annotation_version', 'unknown')}`",
             f"- Configuration/models/cache: `{json.dumps(p.get('configuration', {}), sort_keys=True)}`",
             f"- Browser/locale: `{json.dumps(p.get('browser', {}), sort_keys=True)}`",
             f"- Random seed: `{p.get('random_seed', 'unknown')}`; metric version: `{result['metric_version']}`", "",
             "> A contact, list membership, registration, inventory authorization, written disclosure, and verified personal-data transmission are distinct propositions. No result below converts one into another.", "",
             "## Results", ""]
    mapping = [("Entity resolution", result["entity_resolution"]),
               ("Traffic contact classification", result["traffic"]["micro"]),
               ("User-facing communication", result["communication"])]
    for name, metric in mapping:
        lines.append(f"- {name}: precision {value(metric.get('precision'))}, recall {value(metric.get('recall'))}, F1 {value(metric.get('f1'))} (TP/FP/FN = {metric.get('tp', 0)}/{metric.get('fp', 0)}/{metric.get('fn', 0)}; status: {metric.get('status')})")
    for stage, metric in result["document_stages"].items():
        lines.append(f"- Document {stage}: precision {value(metric.get('precision'))}, recall {value(metric.get('recall'))}, F1 {value(metric.get('f1'))} (TP/FP/FN = {metric.get('tp', 0)}/{metric.get('fp', 0)}/{metric.get('fn', 0)}; status: {metric.get('status')})")
    claims, chains = result["claims"], result["chains"]
    lines += [f"- Claim tuples: exact {value(claims['exact_accuracy'])} ({claims['exact_correct']}/{claims['n']}); status: {claims['status']}",
              f"- Candidate chains: strict precision {value(chains['strict_precision'])} ({chains['strict_correct']}/{chains['strict_n']}); hop precision {value(chains['hop_precision'])}; candidate coverage {value(chains['candidate_coverage'])}; duplicate rate {value(chains['duplicate_rate'])}", "",
              "## Limitations", "",
              "Unavailable or incomplete probes are excluded from negative observations. Unadjudicated, ambiguous, insufficient-evidence, and not-applicable items are reported as abstentions, not silently coerced. Empty held-out annotations produce an explicit insufficient-data state; this report does not infer performance from development labels.", ""]
    return "\n".join(lines)
