"""Track how a target's disclosures change between collections."""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path

SNAPSHOT_NAME = "disclosure_snapshot.json"
LOG_NAME = "refresh_log.json"

# HTTP outcomes that indicate collection failed.
_BLOCKED_STATUSES = {401, 403, 404, 407, 410, 429, 500, 502, 503, 504}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _digest(text: str) -> str:
    return hashlib.sha1((text or "").strip().encode("utf-8", "ignore")).hexdigest()[:16]


@dataclass
class DocState:
    doc_id: str
    url: str
    role: str
    http_status: int = 0
    content_hash: str = ""
    ok: bool = False


@dataclass
class Snapshot:
    """What one collection established about a target."""

    target_id: str
    captured_at: str = field(default_factory=_now)
    docs: dict[str, DocState] = field(default_factory=dict)
    named_orgs: list[str] = field(default_factory=list)
    # Relation identities, as "entity|data_type|action|direction".
    relations: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "target_id": self.target_id,
            "captured_at": self.captured_at,
            "docs": {k: asdict(v) for k, v in self.docs.items()},
            "named_orgs": sorted(self.named_orgs),
            "relations": sorted(self.relations),
        }

    @classmethod
    def from_dict(cls, d: dict) -> Snapshot:
        return cls(
            target_id=d["target_id"],
            captured_at=d.get("captured_at", ""),
            docs={k: DocState(**v) for k, v in (d.get("docs") or {}).items()},
            named_orgs=list(d.get("named_orgs") or ()),
            relations=list(d.get("relations") or ()),
        )


def relation_key(rel: dict) -> str:
    return "|".join([
        str(rel.get("entity", "")),
        str(rel.get("data_type", "")),
        str(rel.get("action", "")),
        str(rel.get("direction", "downstream")),
    ])


def snapshot_target(corpus, target_id: str, named_orgs, relations) -> Snapshot:
    """Capture the current state of a collected target."""
    _, docs = corpus.read_manifest(target_id)
    snap = Snapshot(target_id=target_id)
    for d in docs:
        snap.docs[d.doc_id] = DocState(
            doc_id=d.doc_id, url=d.url, role=d.role,
            http_status=d.http_status,
            content_hash=_digest(corpus.read_doc_html(d)) if d.ok else "",
            ok=d.ok,
        )
    snap.named_orgs = sorted({str(o) for o in named_orgs or ()})
    snap.relations = sorted({relation_key(r) for r in relations or ()})
    return snap


@dataclass
class Diff:
    """What changed between two snapshots of one target."""

    target_id: str
    changed_docs: list[str] = field(default_factory=list)
    added_docs: list[str] = field(default_factory=list)
    removed_docs: list[str] = field(default_factory=list)
    broken_docs: list[str] = field(default_factory=list)
    added_parties: list[str] = field(default_factory=list)
    removed_parties: list[str] = field(default_factory=list)
    added_relations: list[str] = field(default_factory=list)
    removed_relations: list[str] = field(default_factory=list)

    @property
    def content_changed(self) -> bool:
        """Whether anything about the disclosures moved."""
        return bool(
            self.changed_docs or self.added_docs or self.removed_docs
            or self.added_parties or self.removed_parties
            or self.added_relations or self.removed_relations
        )

    @property
    def regressed(self) -> bool:
        """Whether the new reading lost ground the old one held."""
        return bool(self.removed_parties or self.removed_relations
                    or self.broken_docs or self.removed_docs)

    def to_dict(self) -> dict:
        d = asdict(self)
        d["content_changed"] = self.content_changed
        d["regressed"] = self.regressed
        return d


def diff_snapshots(old: Snapshot, new: Snapshot) -> Diff:
    """Compare two snapshots of the same target."""
    diff = Diff(target_id=new.target_id)

    old_docs, new_docs = old.docs, new.docs
    diff.added_docs = sorted(set(new_docs) - set(old_docs))
    diff.removed_docs = sorted(set(old_docs) - set(new_docs))
    for doc_id in sorted(set(old_docs) & set(new_docs)):
        o, n = old_docs[doc_id], new_docs[doc_id]
        # A document that fetched before and fails now is a collection
        # failure, not a revision, however much its content appears to differ.
        if o.ok and not n.ok:
            diff.broken_docs.append(doc_id)
        elif o.ok and n.ok and o.content_hash != n.content_hash:
            diff.changed_docs.append(doc_id)

    old_p, new_p = set(old.named_orgs), set(new.named_orgs)
    diff.added_parties = sorted(new_p - old_p)
    diff.removed_parties = sorted(old_p - new_p)

    old_r, new_r = set(old.relations), set(new.relations)
    diff.added_relations = sorted(new_r - old_r)
    diff.removed_relations = sorted(old_r - new_r)
    return diff


def collection_degraded(old: Snapshot, new: Snapshot) -> bool:
    """Whether the new collection fetched strictly worse than the old one."""
    for doc_id, o in old.docs.items():
        n = new.docs.get(doc_id)
        if o.ok and (n is None or not n.ok
                     or n.http_status in _BLOCKED_STATUSES):
            return True
    return False


def merge_snapshots(old: Snapshot, new: Snapshot) -> tuple[Snapshot, Diff]:
    """Fold ``new`` into ``old`` without discarding established parties."""
    diff = diff_snapshots(old, new)
    merged = Snapshot(
        target_id=new.target_id,
        captured_at=new.captured_at,
        docs=dict(new.docs),
    )
    if collection_degraded(old, new):
        # Keep the prior reading of the parties, and the documents that still
        # carry evidence for them.
        merged.named_orgs = sorted(set(old.named_orgs) | set(new.named_orgs))
        merged.relations = sorted(set(old.relations) | set(new.relations))
        for doc_id, o in old.docs.items():
            n = merged.docs.get(doc_id)
            if o.ok and (n is None or not n.ok):
                merged.docs[doc_id] = o
    else:
        merged.named_orgs = sorted(new.named_orgs)
        merged.relations = sorted(new.relations)
    return merged, diff


# --------------------------------------------------------------------------- #
# Persistence
# --------------------------------------------------------------------------- #
def snapshot_path(corpus, target_id: str) -> Path:
    return Path(corpus.root) / target_id / SNAPSHOT_NAME


def load_snapshot(corpus, target_id: str) -> Snapshot | None:
    p = snapshot_path(corpus, target_id)
    if not p.exists():
        return None
    try:
        return Snapshot.from_dict(json.loads(p.read_text(encoding="utf-8")))
    except Exception:  # noqa: BLE001
        return None


def save_snapshot(corpus, snap: Snapshot) -> None:
    p = snapshot_path(corpus, snap.target_id)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(snap.to_dict(), indent=1), encoding="utf-8")


def record_refresh(
    corpus,
    target_id: str,
    named_orgs,
    relations,
) -> Diff | None:
    """Snapshot a freshly-collected target against its previous state."""
    new = snapshot_target(corpus, target_id, named_orgs, relations)
    old = load_snapshot(corpus, target_id)
    if old is None:
        save_snapshot(corpus, new)
        return None
    merged, diff = merge_snapshots(old, new)
    save_snapshot(corpus, merged)
    return diff


def append_log(corpus, diffs) -> Path:
    """Write a session log of the diffs from one refresh run."""
    path = Path(corpus.root) / LOG_NAME
    entries = []
    if path.exists():
        try:
            entries = json.loads(path.read_text(encoding="utf-8")) or []
        except Exception:  # noqa: BLE001
            entries = []
    changed = [d for d in diffs if d is not None and d.content_changed]
    entries.append({
        "ran_at": _now(),
        "targets": len(list(diffs)),
        "changed": len(changed),
        "regressed": sum(1 for d in changed if d.regressed),
        "diffs": [d.to_dict() for d in changed],
    })
    path.write_text(json.dumps(entries, indent=1), encoding="utf-8")
    return path
