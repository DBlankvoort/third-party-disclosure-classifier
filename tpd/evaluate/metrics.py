"""Goal metrics to strive for. """

from __future__ import annotations

import statistics
from dataclasses import dataclass, field

from ..classify.run import CorpusResult

# Key thresholds
LATENCY_SECONDS = 1.0


def _pct(x: float) -> str:
    return f"{100 * x:.1f}%"

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
