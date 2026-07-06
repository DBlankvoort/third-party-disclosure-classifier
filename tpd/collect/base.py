"""Corpus storage + HTTP fetching."""

from __future__ import annotations

import hashlib
import json
import re
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path

import requests

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
        d = self.target_dir(target_id)
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