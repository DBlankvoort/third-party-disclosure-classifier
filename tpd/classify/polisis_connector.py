"""Reuse the Polisis backend and cache offline."""

from __future__ import annotations

import hashlib
import json
import subprocess
import sys
from pathlib import Path

from ..extract import Document

DEFAULT_POLISIS_ROOT = "../polisis"
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

    def is_third_party_sharing(self, doc: Document) -> bool:
        return bool(self._verdicts.get(text_hash(doc.text), False))

    @property
    def available(self) -> bool:
        return bool(self._verdicts)


# The worker script executed inside the polisis venv.
# Reads {hash: [segments]} from argv[1] and writes {hash: bool} to argv[2].
_WORKER = r"""
import json, sys
from pathlib import Path
sys.path.insert(0, sys.argv[3])  # polisis root
from polisis.inference import HierarchicalClassifier

payload = json.loads(Path(sys.argv[1]).read_text())
clf = HierarchicalClassifier()
out = {}
for h, segments in payload.items():
    verdict = False
    for ann in clf.classify_policy(segments):
        if ann.get("main_third") == 1:
            verdict = True
            break
    out[h] = verdict
Path(sys.argv[2]).write_text(json.dumps(out))
"""


def build_cache(
    corpus_root: str | Path,
    polisis_root: str | Path = DEFAULT_POLISIS_ROOT,
    python_exe: str | None = None,
    roles: tuple[str, ...] = ("privacy_policy", "help_doc", "cookie_policy", "do_not_sell"),
) -> Path:
    """Run POLISIS over the prose docs of a corpus."""
    from ..collect.base import Corpus
    from ..extract import parse_html

    polisis_root = Path(polisis_root)
    if python_exe is None:
        venv_py = polisis_root / ".venv" / "bin" / "python"
        python_exe = str(venv_py) if venv_py.exists() else sys.executable

    corpus = Corpus(corpus_root)
    payload: dict[str, list[str]] = {}
    for tid in corpus.list_targets():
        _, docs = corpus.read_manifest(tid)
        for d in docs:
            if d.role not in roles or not d.ok:
                continue
            doc = parse_html(corpus.read_doc_html(d))
            if doc.text.strip():
                payload[text_hash(doc.text)] = doc.segments[:400]

    cache_path = Path(corpus_root) / CACHE_NAME
    if not payload:
        cache_path.write_text("{}", encoding="utf-8")
        return cache_path

    import tempfile

    with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as fin:
        json.dump(payload, fin)
        in_path = fin.name
    out_path = str(cache_path)
    subprocess.run(
        [python_exe, "-c", _WORKER, in_path, out_path, str(polisis_root)],
        check=True,
    )
    return cache_path


def load_cache(corpus_root: str | Path) -> PolisisBackend | None:
    """Load the Polisis cache."""
    cache = Path(corpus_root) / CACHE_NAME
    if cache.exists():
        return PolisisBackend(cache)
    return None
