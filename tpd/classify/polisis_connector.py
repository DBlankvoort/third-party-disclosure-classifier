"""Run the POLISIS classifier over a corpus and cache verdicts offline."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

from ..extract import Document
from ..polisis.inference import DEFAULT_MODELS_ROOT, ThirdPartyClassifier

CACHE_NAME = "_polisis_thirdparty.json"


def text_hash(text: str) -> str:
    return hashlib.sha1(text.strip().encode("utf-8")).hexdigest()


class PolisisBackend:
    """Reads a precomputed third-party-sharing verdict cache."""

    def __init__(self, cache_path: str | Path):
        self.cache_path = Path(cache_path)
        self._verdicts: dict[str, bool] = {}
        if self.cache_path.exists():
            self._verdicts = json.loads(self.cache_path.read_text(encoding="utf-8"))

    def has_verdict(self, doc: Document) -> bool:
        """Whether POLISIS produced a determination for this document's text."""
        return text_hash(doc.text) in self._verdicts

    def is_third_party_sharing(self, doc: Document) -> bool:
        return bool(self._verdicts.get(text_hash(doc.text), False))

    @property
    def available(self) -> bool:
        return bool(self._verdicts)


def build_cache(
    corpus_root: str | Path,
    models_root: str | Path | None = None,
    roles: tuple[str, ...] | None = None,
) -> Path:
    """Run POLISIS in-process over every usable doc in a corpus (the global doc set).

    ``roles`` restricts which document roles are sent to the classifier; by
    default every document with extractable text is included, since the
    classifier is consulted for any document the structural pipeline could not
    decide on, not just a fixed subset of roles.
    """
    from ..collect.base import Corpus
    from ..extract import parse_html

    corpus = Corpus(corpus_root)
    payload: dict[str, list[str]] = {}
    for tid in corpus.list_targets():
        _, docs = corpus.read_manifest(tid)
        for d in docs:
            if not d.ok or (roles is not None and d.role not in roles):
                continue
            doc = parse_html(corpus.read_doc_html(d))
            if doc.text.strip():
                payload[text_hash(doc.text)] = doc.segments[:400]

    cache_path = Path(corpus_root) / CACHE_NAME
    if not payload:
        cache_path.write_text("{}", encoding="utf-8")
        return cache_path

    clf = ThirdPartyClassifier(models_root=models_root or DEFAULT_MODELS_ROOT)
    out: dict[str, bool] = {}
    for h, segments in payload.items():
        verdict = False
        for ann in clf.classify_policy(segments):
            if ann.get("main_third") == 1:
                verdict = True
                break
        out[h] = verdict

    cache_path.write_text(json.dumps(out), encoding="utf-8")
    return cache_path


def load_cache(corpus_root: str | Path) -> PolisisBackend | None:
    """Load the Polisis cache."""
    cache = Path(corpus_root) / CACHE_NAME
    if cache.exists():
        return PolisisBackend(cache)
    return None
