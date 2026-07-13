"""Corpus storage + HTTP fetching."""

from __future__ import annotations

import hashlib
import json
import re
import time
from dataclasses import asdict, dataclass
from pathlib import Path

import requests

_TRANSIENT_STATUSES = {0, 429, 500, 502, 503, 504}

USER_AGENT = (
    "Mozilla/5.0 (compatible; tpd-research/0.1; +third-party-disclosure-typology) "
    "academic third-party disclosure pattern classifier"
)
DEFAULT_TIMEOUT = 15
_SESSION = requests.Session()
_SESSION.headers.update({"User-Agent": USER_AGENT, "Accept-Language": "en-US,en;q=0.9"})


@dataclass
class Target:
    """One target to collect."""

    id: str
    type: str            # tpd.typology.TargetType value
    name: str = ""
    url: str = ""        # homepage / store URL (websites & app listings)
    app_id: str = ""     # store identifier (apps)
    seed_policy_url: str = ""  # optional explicit policy URL from the seed list

    @staticmethod
    def make_id(raw: str) -> str:
        slug = re.sub(r"[^a-z0-9]+", "-", raw.lower()).strip("-")
        return slug[:60] or hashlib.sha1(raw.encode()).hexdigest()[:10]


@dataclass
class CollectedDoc:
    """One fetched document belonging to a target's document set."""

    doc_id: str
    url: str
    role: str            # privacy_policy / subprocessor_list / cookie_policy / help_doc / store_listing / ...
    http_status: int = 0
    content_type: str = ""
    raw_path: str = ""    # path (relative to corpus root) of saved bytes
    fetched_at: float = 0.0
    error: str = ""

    @property
    def ok(self) -> bool:
        return self.http_status == 200 and bool(self.raw_path) and not self.error


@dataclass
class FetchResult:
    url: str
    status: int
    content_type: str
    text: str
    error: str = ""
    final_url: str = ""

    @property
    def ok(self) -> bool:
        return self.status == 200 and not self.error


# --------------------------------------------------------------------------- #
# Corpus
# --------------------------------------------------------------------------- #
class Corpus:
    """Store of collected document sets from targets on the file system."""

    def __init__(self, root: str | Path):
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)
        self.cache_dir = self.root / "_cache"

    def target_dir(self, target_id: str) -> Path:
        """Make a directory for a document set `target_id`."""
        d = self.root / target_id
        (d / "docs").mkdir(parents=True, exist_ok=True)
        return d

    def save_doc(self, target_id: str, doc: CollectedDoc, html: str) -> CollectedDoc:
        """Save document `doc` as part of the specified document set `target_id`."""
        self.target_dir(target_id)
        rel = f"{target_id}/docs/{doc.doc_id}.html"
        (self.root / rel).write_text(html, encoding="utf-8")
        doc.raw_path = rel # Overwrites if `save_doc` is called for the same doc with different `target_id`s
        return doc

    def read_doc_html(self, doc: CollectedDoc) -> str:
        """Read a document if it has been saved."""
        if not doc.raw_path:
            return ""
        p = self.root / doc.raw_path
        return p.read_text(encoding="utf-8") if p.exists() else ""

    def write_manifest(self, target: Target, docs: list[CollectedDoc]) -> None:
        """Write a manifest.json file."""
        d = self.target_dir(target.id)
        manifest = {
            "target": asdict(target),
            "docs": [asdict(x) for x in docs],
            "collected_at": time.time(),
        }
        (d / "manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")

    def read_manifest(self, target_id: str) -> tuple[Target, list[CollectedDoc]]:
        """Read a manifest.json file (sans collected_at since unused)."""
        d = self.root / target_id / "manifest.json"
        data = json.loads(d.read_text(encoding="utf-8"))
        target = Target(**data["target"])
        docs = [CollectedDoc(**x) for x in data["docs"]]
        return target, docs

    def list_targets(self) -> list[str]:
        """List all targets."""
        return sorted(
            p.name for p in self.root.iterdir()
            if p.is_dir() and p.name != "_cache" and (p / "manifest.json").exists()
        )

# --------------------------------------------------------------------------- #
# Fetch
# --------------------------------------------------------------------------- #
def _cache_key(url: str) -> str:
    return hashlib.sha1(url.encode("utf-8")).hexdigest()


def fetch(
    url: str,
    cache_dir: Path | None = None,
    timeout: int = DEFAULT_TIMEOUT,
    force: bool = False,
    delay: float = 0.0,
) -> FetchResult:
    """GET ``url``"""
    cache_file = None
    if cache_dir is not None:
        cache_dir = Path(cache_dir)
        cache_dir.mkdir(parents=True, exist_ok=True)
        cache_file = cache_dir / f"{_cache_key(url)}.json"
        if cache_file.exists() and not force:
            try:
                d = json.loads(cache_file.read_text(encoding="utf-8"))
                return FetchResult(**d)
            except Exception:  # noqa: BLE001
                pass

    if delay:
        time.sleep(delay)
    try:
        resp = _SESSION.get(url, timeout=timeout, allow_redirects=True)
        ctype = resp.headers.get("Content-Type", "")
        # Keep HTML/text and JSON.
        text = resp.text if (
            "html" in ctype or "text" in ctype or "json" in ctype or not ctype
        ) else ""
        result = FetchResult(
            url=url,
            status=resp.status_code,
            content_type=ctype,
            text=text,
            final_url=resp.url,
        )
    except Exception as exc:  # noqa: BLE001
        result = FetchResult(url=url, status=0, content_type="", text="", error=str(exc))

    if cache_file is not None and result.status not in _TRANSIENT_STATUSES:
        try:
            cache_file.write_text(json.dumps(asdict(result)), encoding="utf-8")
        except Exception:  # noqa: BLE001
            pass
    return result