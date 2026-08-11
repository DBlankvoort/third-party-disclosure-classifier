"""Corpus storage + HTTP fetching."""

from __future__ import annotations

import codecs
import hashlib
import json
import re
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from dataclasses import asdict, dataclass
from dataclasses import fields as dc_fields
from pathlib import Path

import requests

from .pdf import looks_like_pdf, pdf_to_html

_TRANSIENT_STATUSES = {0, 429, 500, 502, 503, 504}

USER_AGENT = (
    "Mozilla/5.0 (compatible; tpd-research/0.1; +third-party-disclosure-typology) "
    "academic third-party disclosure pattern classifier"
)
DEFAULT_TIMEOUT = (5, 15)
_POOL_SIZE = 64
_SESSION = requests.Session()
_SESSION.headers.update({"User-Agent": USER_AGENT, "Accept-Language": "en-US,en;q=0.9"})
for _scheme in ("http://", "https://"):
    _SESSION.mount(_scheme, requests.adapters.HTTPAdapter(
        pool_connections=_POOL_SIZE, pool_maxsize=_POOL_SIZE))


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
    rendered: int = 0

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
        known = {f.name for f in dc_fields(CollectedDoc)}
        docs = [CollectedDoc(**{k: v for k, v in x.items() if k in known})
                for x in data["docs"]]
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


# charset in a <meta> tag or an XML declaration, within the document head.
_META_CHARSET_RE = re.compile(rb"""charset=["']?\s*([A-Za-z0-9_.:+-]+)""", re.I)
_DECLARED_CHARSET_BYTES = 4096


def _decode(resp) -> str:
    """Decode a response body using the encoding the document itself declares."""
    if "charset=" not in (resp.headers.get("Content-Type") or "").lower():
        resp.encoding = _declared_charset(resp.content[:_DECLARED_CHARSET_BYTES])
    return resp.text


def _declared_charset(head: bytes) -> str | None:
    """The codec a document declares for itself, when Python knows it."""
    m = _META_CHARSET_RE.search(head)
    if not m:
        return None
    name = m.group(1).decode("ascii", "ignore")
    try:
        codecs.lookup(name)
    except LookupError:
        return None
    return name


# --------------------------------------------------------------------------- #
# Per-origin deadline
# --------------------------------------------------------------------------- #
_DEADLINES = threading.local()

# Reported in place of an HTTP error when a request was never attempted.
DEADLINE_ERROR = "origin deadline exceeded"


def remaining_budget() -> float | None:
    """Seconds left in the calling thread's deadline, or None when unbounded."""
    at = getattr(_DEADLINES, "at", None)
    return None if at is None else at - time.monotonic()


@contextmanager
def deadline(seconds: float | None):
    """Bound the wall clock the calling thread's fetches may consume."""
    previous = getattr(_DEADLINES, "at", None)
    _DEADLINES.at = None if not seconds else time.monotonic() + float(seconds)
    try:
        yield
    finally:
        _DEADLINES.at = previous


def _bounded_timeout(timeout):
    """``timeout`` clamped so no single request outlives the origin's budget."""
    left = remaining_budget()
    if left is None:
        return timeout
    if isinstance(timeout, tuple):
        connect, read = timeout
    else:
        connect = read = timeout
    return (min(connect, max(0.1, left)), min(read, max(0.1, left)))


def fetch(
    url: str,
    cache_dir: Path | None = None,
    timeout: int | tuple[int, int] = DEFAULT_TIMEOUT,
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

    left = remaining_budget()
    if left is not None and left <= 0:
        return FetchResult(url=url, status=0, content_type="", text="",
                           error=DEADLINE_ERROR)
    timeout = _bounded_timeout(timeout)

    if delay:
        time.sleep(delay)
    try:
        resp = _SESSION.get(url, timeout=timeout, allow_redirects=True)
        ctype = resp.headers.get("Content-Type", "")
        if looks_like_pdf(ctype, head=resp.content[:5]):
            # PDF policies are captured as minimal HTML.
            text = pdf_to_html(resp.content)
        else:
            # Keep HTML/text and JSON.
            text = _decode(resp) if (
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


PROBE_WORKERS = 6


def warm_cache(urls, cache_dir: Path | None, force: bool = False,
               delay: float = 0.0, timeout: int | tuple[int, int] = DEFAULT_TIMEOUT) -> None:
    """Fetch ``urls`` concurrently so a later sequential read finds them cached."""
    urls = list(dict.fromkeys(u for u in urls if u))
    if not urls or cache_dir is None:
        return

    at = getattr(_DEADLINES, "at", None)

    def one(url: str):
        _DEADLINES.at = at
        return fetch(url, cache_dir=cache_dir, force=force, delay=delay,
                     timeout=timeout)

    if len(urls) == 1:
        one(urls[0])
        return
    with ThreadPoolExecutor(max_workers=PROBE_WORKERS) as pool:
        list(pool.map(one, urls))
