"""
Turns a raw HTML document into the representation the classifiers consumes.
"""

from __future__ import annotations

import re
import warnings
from dataclasses import dataclass, field

from bs4 import BeautifulSoup, XMLParsedAsHTMLWarning

# We only ever read text + tables out of XML, so BeautifulSoup's HTML works.
warnings.filterwarnings("ignore", category=XMLParsedAsHTMLWarning)

_WS_RE = re.compile(r"\s+")
_NL_RE = re.compile(r"\n{3,}")

# Hard cap on HTML parsing size for performance reasons.
MAX_HTML_BYTES = 800_000

# Tags whose text is never policy content.
_DROP_TAGS = ("script", "style", "noscript", "template", "svg", "head")

# Block-level tags we treat as segment boundaries.
_BLOCK_TAGS = ("p", "li", "h1", "h2", "h3", "h4", "h5", "h6", "td", "th", "dd", "dt")

# Sentence-ish boundary, used to break up very long unsegmented lines so a sharing
# clause and its third-party reference still land in one chunk.
_SENT_SPLIT_RE = re.compile(r"(?<=[.!?;:])\s+")

# Below this share of the visible text, block-tag segmentation has missed most
# of the content, so we need to rebuild segments from rendered text.
_BLOCK_COVERAGE_MIN = 0.5
_MAX_SEG_CHARS = 400

# Check whether the table (likely) names the entity in each row.
_VENDOR_COL_RE = re.compile(
    r"\b(vendors?|compan(?:y|ies)|organi[sz]ations?|providers?|sub[- ]?processors?|"
    r"processors?|partners?|recipients?|suppliers?|controllers?|third[- ]part|"
    r"data importers?)\b",
    re.I,
)
_WEAK_NAME_COL_RE = re.compile(r"\b(name|entity)\b", re.I)
# Check cookie-table signature.
_COOKIE_TABLE_RE = re.compile(
    r"\bcookies?\b|\bduration\b|\bexpir|\blifespan\b|\bretention\b|\bmax(?:imum)? ?age\b",
    re.I,
)
# Check for technical identifier.
_IDENTIFIER_RE = re.compile(r"^[._]|[._].*[._]|_|^[A-Za-z]+\d|\d[A-Za-z]*$")


@dataclass
class Table:
    """A flattened HTML table."""

    headers: list[str] = field(default_factory=list)   # lower-cased header cells
    n_rows: int = 0
    n_cols: int = 0
    cell_text: str = ""                                 # all cells joined, for NER
    name_cells: list[str] = field(default_factory=list)  # cells of the vendor/name column

    @property
    def header_tokens(self) -> set[str]:
        toks: set[str] = set()
        for h in self.headers:
            toks |= set(re.findall(r"[a-z][a-z\-]+", h))
        return toks

@dataclass
class Document:
    """Structural + textual view of one HTML document."""

    title: str = ""
    text: str = ""
    segments: list[str] = field(default_factory=list)
    tables: list[Table] = field(default_factory=list)
    links: list[tuple[str, str]] = field(default_factory=list)   # (anchor_text, href)
    raw: str = ""


def _clean(s: str) -> str:
    s = _WS_RE.sub(" ", s or "")
    return s.strip()


def _extract_tables(soup: BeautifulSoup) -> list[Table]:
    tables: list[Table] = []
    for tbl in soup.find_all("table"):
        rows = tbl.find_all("tr")
        if not rows:
            continue
        # header: first row's th, else first row's td
        first = rows[0]
        headers = [_clean(c.get_text(" ")).lower() for c in first.find_all(["td", "th"])]
        cells = [_clean(c.get_text(" ")) for c in tbl.find_all(["td", "th"])]
        n_cols = max((len(r.find_all(["td", "th"])) for r in rows), default=0)

        # Identify a vendor/name column and read its cells as the row entities.
        cookie_table = any(_COOKIE_TABLE_RE.search(h) for h in headers)
        name_idx = next(
            (i for i, h in enumerate(headers) if h and _VENDOR_COL_RE.search(h)), None
        )
        if name_idx is None and not cookie_table:
            name_idx = next(
                (i for i, h in enumerate(headers) if h and _WEAK_NAME_COL_RE.search(h)),
                None,
            )
        name_cells: list[str] = []
        if name_idx is not None:
            for r in rows[1:]:
                rc = r.find_all(["td", "th"])
                if name_idx < len(rc):
                    v = _clean(rc[name_idx].get_text(" "))
                    if v and not _IDENTIFIER_RE.search(v):
                        name_cells.append(v)

        tables.append(
            Table(
                headers=[h for h in headers if h],
                n_rows=len(rows),
                n_cols=n_cols,
                cell_text=" • ".join(c for c in cells if c),
                name_cells=name_cells,
            )
        )
    return tables


def _segments_from_text(text: str) -> list[str]:
    """Alternative de-segmentation flow."""
    reflowed: list[str] = []
    buf = ""
    for raw in text.split("\n"):
        line = _clean(raw)
        if not line:
            if buf:
                reflowed.append(buf)
                buf = ""
            continue
        buf = f"{buf} {line}".strip() if buf else line
        # flush at a sentence end (or when the buffer is already long)
        if buf[-1:] in ".!?" or len(buf) >= _MAX_SEG_CHARS:
            reflowed.append(buf)
            buf = ""
    if buf:
        reflowed.append(buf)

    segs: list[str] = []
    for line in reflowed:
        if len(line) < 3:
            continue
        if len(line) <= _MAX_SEG_CHARS:
            segs.append(line)
            continue
        for part in _SENT_SPLIT_RE.split(line):
            part = part.strip()
            if len(part) >= 3:
                segs.append(part)
    return segs


def parse_html(html: str, max_bytes: int = MAX_HTML_BYTES) -> Document:
    """Parse raw HTML into a :class:`Document`."""
    html = html or ""
    if len(html) > max_bytes:
        html = html[:max_bytes]
    soup = BeautifulSoup(html, "lxml")

    title = _clean(soup.title.get_text()) if soup.title else ""

    # Extract tables
    tables = _extract_tables(soup)

    # Extract links for possible companion docs
    links: list[tuple[str, str]] = []
    for a in soup.find_all("a", href=True):
        txt = _clean(a.get_text(" "))
        href = a["href"].strip()
        if href:
            links.append((txt, href))

    raw = html or ""

    # Strip non-content tags and pull segments.
    for tag in soup(list(_DROP_TAGS)):
        tag.decompose()

    segments: list[str] = []
    for el in soup.find_all(_BLOCK_TAGS):
        seg = _clean(el.get_text(" "))
        if len(seg) >= 3:
            segments.append(seg)
    # de-dup adjacent repeats
    deduped: list[str] = []
    for s in segments:
        if not deduped or deduped[-1] != s:
            deduped.append(s)
    segments = deduped

    body = soup.body or soup
    text = body.get_text("\n") if body else ""
    text = _NL_RE.sub("\n\n", "\n".join(_clean(line) for line in text.splitlines()))
    text = text.strip()

    # If block tags carved out little text 
    # (e.g. a policy laid out in <div>/<br>)
    # rebuild segments from the rendered text.
    block_chars = sum(len(s) for s in segments)
    if text and block_chars < _BLOCK_COVERAGE_MIN * len(text):
        from_text = _segments_from_text(text)
        if len(from_text) > len(segments):
            segments = from_text

    return Document(
        title=title,
        text=text.strip(),
        segments=segments,
        tables=tables,
        links=links,
        raw=raw,
    )
