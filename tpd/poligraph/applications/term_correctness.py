"""Correctness of term definitions."""

from __future__ import annotations

from dataclasses import dataclass, field

from ..graph import NodeType, PoliGraph
from ..ontology import DataOntology, LocalOntology, global_data_ontology

# Terms a policy uses to claim data is not personal / not identifying.
NON_PERSONAL_TERMS = {
    "non-personal information", "non personal information", "nonpersonal information",
    "non-personally identifiable information", "anonymous information",
    "anonymized information", "anonymised information",
    "aggregate / deidentified / pseudonymized information",
    "aggregate information", "aggregated information", "deidentified information",
    "de-identified information", "pseudonymized information", "pseudonymised information",
    "anonymous data", "non-personal data", "non-personal",
}


@dataclass
class TermReport:
    misleading: list[tuple[str, str]] = field(default_factory=list)   # (claimed_term, personal_data)
    non_standard: list[str] = field(default_factory=list)             # hypernyms not in global ontology

    @property
    def is_misleading(self) -> bool:
        return bool(self.misleading)


def check_terms(pg: PoliGraph, data_ont: DataOntology | None = None) -> TermReport:
    data_ont = data_ont or global_data_ontology()
    local = LocalOntology.from_poligraph(pg, NodeType.DATA)
    report = TermReport()

    for hyper, hypo in pg.subsume_edges():
        if pg.node_type(hyper) != NodeType.DATA:
            continue
        if hyper in NON_PERSONAL_TERMS:
            for desc in local.descendants(hyper):
                if desc == hyper:
                    continue
                if data_ont.is_personal(desc):
                    report.misleading.append((hyper, desc))

    for hyper in {h for h, _ in pg.subsume_edges() if pg.node_type(h) == NodeType.DATA}:
        if hyper in NON_PERSONAL_TERMS:
            continue
        if hyper not in data_ont and not _is_special(hyper):
            report.non_standard.append(hyper)

    # de-duplicate
    report.misleading = sorted(set(report.misleading))
    report.non_standard = sorted(set(report.non_standard))
    return report


def _is_special(term: str) -> bool:
    return term in ("unspecified data", "unspecified third party")


def format_report(pg: PoliGraph, report: TermReport) -> str:
    lines = [f"Term-correctness report for {pg.policy_id!r}:"]
    if report.misleading:
        lines.append("  Misleading definitions (declared non-personal, but personal):")
        for claimed, data in report.misleading:
            lines.append(f"    {claimed!r} subsumes personal data {data!r}")
    else:
        lines.append("  No misleading definitions found.")
    if report.non_standard:
        lines.append("  Non-standard terms (not in global ontology): "
                     + ", ".join(repr(t) for t in report.non_standard))
    return "\n".join(lines)
