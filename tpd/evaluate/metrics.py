"""Goal metrics to strive for. """

from __future__ import annotations

import statistics
from dataclasses import dataclass, field

from ..classify.run import CorpusResult
from ..collect.base import Corpus

# Key thresholds
TARGET_RECALL = 0.90
TARGET_COVERAGE = 0.90
TARGET_AGREEMENT = 0.85
LATENCY_SECONDS = 1.0
TARGET_PP_ID_WEBSITE = 0.80
TARGET_PP_ID_APP = 0.70
TARGET_LIST_ID_WEBSITE = 0.75
TARGET_LIST_ID_APP = 0.65
TARGET_NAMING_WEBSITE = 0.50
TARGET_NAMING_APP = 0.40
TARGET_ONTOLOGY_COVERAGE = 0.95
TARGET_PROPAGATION_MIN_N = 30
TARGET_VERIFIED_CHAINS = 5
CHAIN_PARTIES = 4
TARGET_ARRANGEMENT_COVERAGE = 0.90
TARGET_COVERAGE_SAMPLE = 4

APP_TARGET_TYPES = {"play_store_app", "app_store_app"}
_STRUCTURED_LIST_ROLES = {
    "vendor_list", "subprocessor_list", "sellers_json", "vendors_json",
    "app_ads_txt", "ads_txt", "tcf_gvl",
}

NON_NAMING_ROLES = {"store_listing", "play_data_safety", "app_privacy"}


def _pct(x: float) -> str:
    return f"{100 * x:.1f}%"


# --------------------------------------------------------------------------- #
# Document relevance recall
# --------------------------------------------------------------------------- #
@dataclass
class RelevanceReport:
    n_gold_docs: int = 0
    n_disclosing: int = 0
    recall: float = 0.0
    precision: float = 0.0
    n_targets_with_gold: int = 0
    targets_fully_recovered: int = 0
    target_recovery_rate: float = 0.0
    passed: bool = False

    @property
    def summary(self) -> str:
        return (
            f"Relevance recall >= {_pct(TARGET_RECALL)}: "
            f"recall={_pct(self.recall)} on {self.n_disclosing} disclosing docs "
            f"(precision={_pct(self.precision)}); "
            f"per-target all-found={_pct(self.target_recovery_rate)} "
            f"({self.targets_fully_recovered}/{self.n_targets_with_gold}) "
            f"-> {'PASS' if self.passed else 'FAIL'}"
        )


def relevance(
    result: CorpusResult, gold: dict[tuple[str, str], int]
) -> RelevanceReport:
    pred = {
        (tc.target_id, d.doc_id): d.relevant
        for tc in result.targets for d in tc.docs
    }
    tp = fn = fp = 0
    per_target_gold: dict[str, list[bool]] = {}
    for (tid, did), g in gold.items():
        p = bool(pred.get((tid, did), False))
        if g == 1:
            if p:
                tp += 1
            else:
                fn += 1
            per_target_gold.setdefault(tid, []).append(p)
        elif p:
            fp += 1

    n_disclosing = tp + fn
    recall = tp / n_disclosing if n_disclosing else 0.0
    precision = tp / (tp + fp) if (tp + fp) else 0.0
    fully = sum(1 for found in per_target_gold.values() if all(found))
    rate = fully / len(per_target_gold) if per_target_gold else 0.0
    return RelevanceReport(
        n_gold_docs=len(gold),
        n_disclosing=n_disclosing,
        recall=recall,
        precision=precision,
        n_targets_with_gold=len(per_target_gold),
        targets_fully_recovered=fully,
        target_recovery_rate=rate,
        passed=recall >= TARGET_RECALL,
    )


# --------------------------------------------------------------------------- #
# Coverage + agreement
# --------------------------------------------------------------------------- #
@dataclass
class AgreementReport:
    n_targets: int = 0
    n_classified: int = 0
    coverage: float = 0.0
    n_gold: int = 0
    exact_agreement: float = 0.0
    mean_jaccard: float = 0.0
    n_gold_docs: int = 0
    doc_agreement: float = 0.0
    coverage_passed: bool = False
    agreement_passed: bool = False

    @property
    def passed(self) -> bool:
        return self.coverage_passed and self.agreement_passed

    @property
    def summary(self) -> str:
        head = (
            f"Coverage >= {_pct(TARGET_COVERAGE)}, agreement >= "
            f"{_pct(TARGET_AGREEMENT)}: "
            f"coverage={_pct(self.coverage)} ({self.n_classified}/{self.n_targets}) "
            f"[{'PASS' if self.coverage_passed else 'FAIL'}]; "
        )
        if not self.n_gold:
            return head + "agreement=n/a"
        tail = ""
        if self.n_gold_docs:
            tail = (
                f"; per-document agreement={_pct(self.doc_agreement)} "
                f"on {self.n_gold_docs} gold docs"
            )
        return head + (
            f"agreement={_pct(self.exact_agreement)} on {self.n_gold} gold "
            f"(mean Jaccard={_pct(self.mean_jaccard)}) "
            f"[{'PASS' if self.agreement_passed else 'FAIL'}]"
        ) + tail


def _jaccard(a: set, b: set) -> float:
    if not a and not b:
        return 1.0
    return len(a & b) / len(a | b) if (a | b) else 1.0


def coverage(result: CorpusResult) -> tuple[int, int, float]:
    n = len(result.targets)
    c = sum(1 for tc in result.targets if tc.classified)
    return c, n, (c / n if n else 0.0)


def agreement(
    result: CorpusResult,
    gold: dict[str, set],
    labeled_docs: dict[str, set] | None = None,
    doc_gold: dict[tuple[str, str], set] | None = None,
) -> AgreementReport:
    """Coverage + agreement calculation."""
    c, n, cov = coverage(result)
    if labeled_docs:
        by_id = {
            tc.target_id: {
                f
                for d in tc.docs if d.doc_id in labeled_docs.get(tc.target_id, set())
                for f in d.facets
            }
            for tc in result.targets
        }
    else:
        by_id = {tc.target_id: set(tc.facets) for tc in result.targets}

    exact = 0
    jaccards: list[float] = []
    n_gold = 0
    for tid, gold_facets in gold.items():
        if tid not in by_id:
            continue
        n_gold += 1
        pred = by_id[tid]
        gf = set(gold_facets)
        if pred == gf:
            exact += 1
        jaccards.append(_jaccard(pred, gf))

    exact_agree = exact / n_gold if n_gold else 0.0
    mean_jacc = statistics.mean(jaccards) if jaccards else 0.0

    n_gold_docs = doc_exact = 0
    if doc_gold:
        by_doc = {
            (tc.target_id, d.doc_id): set(d.facets)
            for tc in result.targets for d in tc.docs
        }
        for key, gf in doc_gold.items():
            if key not in by_doc:
                continue
            n_gold_docs += 1
            doc_exact += by_doc[key] == set(gf)

    return AgreementReport(
        n_targets=n,
        n_classified=c,
        coverage=cov,
        n_gold=n_gold,
        exact_agreement=exact_agree,
        mean_jaccard=mean_jacc,
        n_gold_docs=n_gold_docs,
        doc_agreement=(doc_exact / n_gold_docs) if n_gold_docs else 0.0,
        coverage_passed=cov >= TARGET_COVERAGE,
        agreement_passed=(exact_agree >= TARGET_AGREEMENT) if n_gold else False,
    )

# --------------------------------------------------------------------------- #
# Latency
# --------------------------------------------------------------------------- #
@dataclass
class LatencyReport:
    n_docs: int = 0
    doc_max: float = 0.0
    doc_p95: float = 0.0
    doc_mean: float = 0.0
    doc_under_1s: float = 0.0
    n_targets: int = 0
    target_max: float = 0.0
    target_p95: float = 0.0
    target_mean: float = 0.0
    target_under_1s: float = 0.0
    passed: bool = False

    @property
    def doc_passed(self) -> bool:
        """Doc-level check"""
        return self.doc_max < LATENCY_SECONDS

    @property
    def target_passed(self) -> bool:
        """Set-level check"""
        return self.target_max < LATENCY_SECONDS

    @property
    def summary(self) -> str:
        sec = "" if self.doc_passed else (
            f" [secondary: 1 doc at {self.doc_max*1000:.0f}ms]"
        )
        return (
            f"target-set max={self.target_max*1000:.0f}ms mean={self.target_mean*1000:.0f}ms "
            f"under-1s={_pct(self.target_under_1s)} "
            f"-> {'PASS' if self.passed else 'FAIL'}; "
            f"doc max={self.doc_max*1000:.0f}ms mean={self.doc_mean*1000:.0f}ms "
            f"under-1s={_pct(self.doc_under_1s)}{sec}"
        )


def _p95(xs: list[float]) -> float:
    if not xs:
        return 0.0
    s = sorted(xs)
    return s[min(len(s) - 1, int(0.95 * len(s)))]


def latency(result: CorpusResult) -> LatencyReport:
    d, t = result.doc_seconds, result.target_seconds
    r = LatencyReport(
        n_docs=len(d),
        doc_max=max(d) if d else 0.0,
        doc_p95=_p95(d),
        doc_mean=statistics.mean(d) if d else 0.0,
        doc_under_1s=(sum(1 for x in d if x < LATENCY_SECONDS) / len(d)) if d else 1.0,
        n_targets=len(t),
        target_max=max(t) if t else 0.0,
        target_p95=_p95(t),
        target_mean=statistics.mean(t) if t else 0.0,
        target_under_1s=(sum(1 for x in t if x < LATENCY_SECONDS) / len(t)) if t else 1.0,
    )
    r.passed = r.target_max < LATENCY_SECONDS
    return r


# --------------------------------------------------------------------------- #
# Fetching documents
# --------------------------------------------------------------------------- #
@dataclass
class NamingReport:
    group: str = ""
    n_docs: int = 0
    n_named: int = 0
    rate: float = 0.0
    target: float = 0.0
    passed: bool = False

    @property
    def summary(self) -> str:
        return (
            f"{self.group} docs naming a third party >= {_pct(self.target)}: "
            f"rate={_pct(self.rate)} ({self.n_named}/{self.n_docs}) "
            f"-> {'PASS' if self.passed else 'FAIL'}"
        )


def naming_rate(result: CorpusResult) -> dict[str, NamingReport]:
    """Fraction of fetched documents naming a third party, by target group."""
    groups = {
        "website": (lambda t: t not in APP_TARGET_TYPES, TARGET_NAMING_WEBSITE),
        "app": (lambda t: t in APP_TARGET_TYPES, TARGET_NAMING_APP),
    }
    out: dict[str, NamingReport] = {}
    for name, (in_group, target) in groups.items():
        docs = [
            d for tc in result.targets if in_group(tc.target_type)
            for d in tc.docs if d.role not in NON_NAMING_ROLES
        ]
        n = len(docs)
        named = sum(1 for d in docs if d.named_orgs)
        rate = named / n if n else 0.0
        out[name] = NamingReport(
            group=name, n_docs=n, n_named=named, rate=rate,
            target=target, passed=rate >= target,
        )
    return out

@dataclass
class IdentificationReport:
    kind: str = ""    # "privacy_policy" / "structured_list"
    group: str = ""   # "website" / "app"
    n_present: int = 0
    n_identified: int = 0
    rate: float = 0.0
    target: float = 0.0
    passed: bool = False

    @property
    def summary(self) -> str:
        if not self.n_present:
            return f"{self.group} {self.kind} identification: n/a (no gold)"
        return (
            f"{self.group} {self.kind} identified >= {_pct(self.target)} when present: "
            f"rate={_pct(self.rate)} ({self.n_identified}/{self.n_present}) "
            f"-> {'PASS' if self.passed else 'FAIL'}"
        )


def _identification_counts(
    corpus: Corpus, gold: dict[str, bool], roles: set[str],
    target_ids: list[str] | None = None,
    gold_doc_ids: dict[str, set[str]] | None = None,
) -> tuple[int, int]:
    """Targets whose known-present document the crawl actually recovered."""
    ids = target_ids if target_ids is not None else corpus.list_targets()
    present = identified = 0
    for tid in ids:
        if not gold.get(tid):
            continue
        present += 1
        _, docs = corpus.read_manifest(tid)
        wanted = (gold_doc_ids or {}).get(tid)
        if wanted:
            found = any(d.doc_id in wanted and d.ok for d in docs)
        else:
            found = any(d.role in roles and d.ok for d in docs)
        identified += found
    return identified, present


def policy_identification(
    corpus: Corpus, gold: dict[str, bool], group: str,
    target_ids: list[str] | None = None,
    gold_doc_ids: dict[str, set[str]] | None = None,
) -> IdentificationReport:
    """Whether a fetched privacy policy was found for targets known to have one."""
    identified, present = _identification_counts(
        corpus, gold, {"privacy_policy"}, target_ids, gold_doc_ids
    )
    target = TARGET_PP_ID_WEBSITE if group == "website" else TARGET_PP_ID_APP
    rate = identified / present if present else 0.0
    return IdentificationReport(
        kind="privacy_policy", group=group, n_present=present, n_identified=identified,
        rate=rate, target=target, passed=(rate >= target) if present else False,
    )


def structured_list_identification(
    corpus: Corpus, gold: dict[str, bool], group: str,
    target_ids: list[str] | None = None,
    gold_doc_ids: dict[str, set[str]] | None = None,
) -> IdentificationReport:
    """Whether a fetched structured third-party list was found for targets known to have one."""
    identified, present = _identification_counts(
        corpus, gold, _STRUCTURED_LIST_ROLES, target_ids, gold_doc_ids
    )
    target = TARGET_LIST_ID_WEBSITE if group == "website" else TARGET_LIST_ID_APP
    rate = identified / present if present else 0.0
    return IdentificationReport(
        kind="structured_list", group=group, n_present=present, n_identified=identified,
        rate=rate, target=target, passed=(rate >= target) if present else False,
    )


# --------------------------------------------------------------------------- #
# Data-sharing ontologies
# --------------------------------------------------------------------------- #
@dataclass
class OntologyAccommodationReport:
    n_terms: int = 0
    n_accommodated: int = 0
    rate: float = 0.0
    unaccommodated: list[str] = field(default_factory=list)
    passed: bool = False

    @property
    def summary(self) -> str:
        return (
            f"Data ontology accommodates >= {_pct(TARGET_ONTOLOGY_COVERAGE)} of distinct "
            f"data-type clauses: rate={_pct(self.rate)} "
            f"({self.n_accommodated}/{self.n_terms}) -> {'PASS' if self.passed else 'FAIL'}"
        )


def ontology_accommodation(
    relations_by_target: dict[str, list[dict]],
    ontology=None,
) -> OntologyAccommodationReport:
    """Fraction of extracted data-type terms the global ontology recognises."""
    if ontology is None:
        from ..poligraph.ontology import global_data_ontology

        ontology = global_data_ontology()

    terms = {
        r["data_type"].strip().lower()
        for rels in relations_by_target.values() for r in rels
        if r.get("data_type")
    }
    accommodated = {t for t in terms if t in ontology}
    n = len(terms)
    n_ok = len(accommodated)
    return OntologyAccommodationReport(
        n_terms=n,
        n_accommodated=n_ok,
        rate=(n_ok / n) if n else 0.0,
        unaccommodated=sorted(terms - accommodated),
        passed=(n_ok / n >= TARGET_ONTOLOGY_COVERAGE) if n else False,
    )


@dataclass
class PropagationReport:
    n_reviewed: int = 0
    n_false: int = 0
    false_rate: float = 0.0
    n_stale: int = 0
    passed: bool = False

    @property
    def summary(self) -> str:
        stale = (
            f" ({self.n_stale} reviewed clause(s) no longer extracted)"
            if self.n_stale else ""
        )
        if self.n_reviewed < TARGET_PROPAGATION_MIN_N:
            return (
                f"Data-type propagation review: only {self.n_reviewed}/"
                f"{TARGET_PROPAGATION_MIN_N} distinct clauses reviewed so far -> FAIL"
                f"{stale}"
            )
        return (
            f"Data-type propagation review (>= {TARGET_PROPAGATION_MIN_N} distinct clauses, "
            f"0 false propagations expected): {self.n_false} false / {self.n_reviewed} "
            f"reviewed -> {'PASS' if self.passed else 'FAIL'}{stale}"
        )


def propagation(
    gold: dict[str, bool], clause_ids: set[str] | None = None
) -> PropagationReport:
    """Propagation tests."""
    reviewed = (
        {cid: v for cid, v in gold.items() if cid in clause_ids}
        if clause_ids is not None else dict(gold)
    )
    n = len(reviewed)
    n_false = sum(1 for correct in reviewed.values() if not correct)
    return PropagationReport(
        n_reviewed=n,
        n_false=n_false,
        false_rate=(n_false / n) if n else 0.0,
        n_stale=len(gold) - n,
        passed=(n >= TARGET_PROPAGATION_MIN_N and n_false == 0),
    )


# --------------------------------------------------------------------------- #
# Graph: onward-sharing chains
# --------------------------------------------------------------------------- #
@dataclass
class ChainReport:
    parties: int = CHAIN_PARTIES
    n_found: int = 0
    n_reviewed: int = 0
    n_verified: int = 0
    n_rejected: int = 0
    n_verified_fully_disclosed: int = 0
    n_stale: int = 0
    passed: bool = False

    @property
    def summary(self) -> str:
        stale = (
            f" ({self.n_stale} reviewed chain(s) no longer extracted)"
            if self.n_stale else ""
        )
        return (
            f">= {TARGET_VERIFIED_CHAINS} verified {self.parties}-party sharing chains: "
            f"{self.n_verified} verified of {self.n_reviewed} reviewed "
            f"({self.n_found} extracted, {self.n_rejected} rejected, "
            f"{self.n_verified_fully_disclosed} resting wholly on written "
            f"disclosure) -> {'PASS' if self.passed else 'FAIL'}{stale}"
        )


def chain_verification(chains, gold: dict[str, bool]) -> ChainReport:
    """Whether enough multi-party chains have been confirmed by hand."""
    by_id = {c.id: c for c in chains}
    reviewed = {cid: v for cid, v in gold.items() if cid in by_id}
    verified = [cid for cid, ok in reviewed.items() if ok]
    disclosed = sum(1 for cid in verified if by_id[cid].fully_disclosed)
    lengths = {len(c.parties) for c in chains}
    return ChainReport(
        parties=lengths.pop() if len(lengths) == 1 else CHAIN_PARTIES,
        n_found=len(by_id),
        n_reviewed=len(reviewed),
        n_verified=len(verified),
        n_rejected=len(reviewed) - len(verified),
        n_verified_fully_disclosed=disclosed,
        n_stale=len(gold) - len(reviewed),
        passed=len(verified) >= TARGET_VERIFIED_CHAINS,
    )


# --------------------------------------------------------------------------- #
# Graph: arrangement coverage
# --------------------------------------------------------------------------- #
@dataclass
class ArrangementCoverageReport:
    n_targets: int = 0
    n_gold: int = 0
    n_recovered: int = 0
    recall: float = 0.0
    missed: list[str] = field(default_factory=list)
    n_false: int = 0
    sample_passed: bool = False
    passed: bool = False

    @property
    def summary(self) -> str:
        if not self.n_gold:
            return "Arrangement coverage: n/a (no labelled arrangements)"
        sample = (
            "" if self.sample_passed else
            f" [only {self.n_targets}/{TARGET_COVERAGE_SAMPLE} targets labelled]"
        )
        return (
            f"Arrangement coverage >= {_pct(TARGET_ARRANGEMENT_COVERAGE)} on "
            f"{TARGET_COVERAGE_SAMPLE} targets: recall={_pct(self.recall)} "
            f"({self.n_recovered}/{self.n_gold} arrangements, "
            f"{self.n_false} detected and rejected) "
            f"-> {'PASS' if self.passed else 'FAIL'}{sample}"
        )


def arrangement_coverage(
    gold: dict[str, dict], detected_ids: set[str]
) -> ArrangementCoverageReport:
    """Fraction of hand-labelled sharing arrangements."""
    real = {aid: rec for aid, rec in gold.items() if rec.get("gold")}
    recovered = [aid for aid in real if aid in detected_ids]
    missed = sorted(set(real) - set(recovered))
    targets = {rec["target_id"] for rec in real.values() if rec.get("target_id")}
    n = len(real)
    recall = (len(recovered) / n) if n else 0.0
    enough = len(targets) >= TARGET_COVERAGE_SAMPLE
    return ArrangementCoverageReport(
        n_targets=len(targets),
        n_gold=n,
        n_recovered=len(recovered),
        recall=recall,
        missed=missed,
        n_false=sum(1 for aid, rec in gold.items()
                    if not rec.get("gold") and aid in detected_ids),
        sample_passed=enough,
        passed=bool(n) and enough and recall >= TARGET_ARRANGEMENT_COVERAGE,
    )
