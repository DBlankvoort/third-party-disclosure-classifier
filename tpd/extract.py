"""
Turns a raw HTML document into the representations for the project.
"""

from __future__ import annotations

import re
import warnings
from dataclasses import dataclass, field
from enum import Enum
from typing import Iterator, Optional

from bs4 import BeautifulSoup, CData, NavigableString, Tag, XMLParsedAsHTMLWarning

# We only ever read text + tables out of XML, so BeautifulSoup's HTML works.
warnings.filterwarnings("ignore", category=XMLParsedAsHTMLWarning)

_WS_RE = re.compile(r"\s+")
_NL_RE = re.compile(r"\n{3,}")

# Hard cap on HTML parsing size for performance reasons.
MAX_HTML_BYTES = 800_000

# Tags whose text is never policy content.
_DROP_TAGS = ("script", "style", "noscript", "template", "svg", "head")

# Containers whose content is not part of the policy.
_BOILERPLATE_TAGS = frozenset(
    {"nav", "footer", "aside", "header", "form", "button", "iframe"}
)

# Block-level tags we treat as segment boundaries.
_BLOCK_TAGS = ("p", "li", "h1", "h2", "h3", "h4", "h5", "h6", "td", "th", "dd", "dt")
_BLOCK_TAG_SET = frozenset(_BLOCK_TAGS)

_HEADING_LEVELS = {f"h{i}": i for i in range(1, 7)}

# Tags whose own text becomes TEXT nodes.
_CAPTURE_TAGS = frozenset(
    {"body", "main", "section", "article", "blockquote", "p", "div",
     "dd", "dt", "figcaption", "summary", "center"}
)
# Tags that end an element's text region.
_OWN_TEXT_BOUNDARY = _CAPTURE_TAGS | _BOILERPLATE_TAGS | set(_DROP_TAGS) | {
    "ul", "ol", "li", "dl", "table", "thead", "tbody", "tfoot", "tr", "td",
    "th", "h1", "h2", "h3", "h4", "h5", "h6", "figure", "details",
    "fieldset", "pre",
}

# Sentence-ish boundary, used to break up very long unsegmented lines so a sharing
# clause and its third-party reference still land in one chunk.
_SENT_SPLIT_RE = re.compile(r"(?<=[.!?;:])\s+")
# Stricter sentence boundary used for tree TEXT nodes / sentences().
_SENT_RE = re.compile(r"(?<=[.!?])\s+")
_BULLET_RE = re.compile(r"^\s*(?:[-*•·▪◦‣]|\(?\d+[.)]|\(?[a-z][.)])\s+", re.I)

# Below this share of the visible text, block-tag segmentation has missed most
# of the content, so we need to rebuild segments from rendered text.
_BLOCK_COVERAGE_MIN = 0.5
_MAX_SEG_CHARS = 400

# Table cells shorter than this can't carry a subject-verb-object disclosure.
_MIN_CELL_WORDS = 3

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
_IDENTIFIER_RE = re.compile(r"^[._]|[._].*[._]|_|\d.*\d")


class NodeType(str, Enum):
    ROOT = "ROOT"
    HEADING = "HEADING"
    TEXT = "TEXT"
    LISTITEM = "LISTITEM"
    TABLE = "TABLE"


@dataclass
class Table:
    """A flattened HTML table."""

    headers: list[str] = field(default_factory=list)   # lower-cased header cells
    n_rows: int = 0
    n_cols: int = 0
    cell_text: str = ""                                 # all cells joined, for NER
    name_cells: list[str] = field(default_factory=list)  # cells of the vendor/name column
    rows: list[list[str]] = field(default_factory=list)

    @property
    def header_tokens(self) -> set[str]:
        toks: set[str] = set()
        for h in self.headers:
            toks |= set(re.findall(r"[a-z][a-z\-]+", h))
        return toks


@dataclass(repr=False)
class Node:
    """One node of the simplified document tree."""

    type: NodeType
    text: str = ""                      # cleaned block text
    level: int = 0                      # heading depth
    table: Optional[Table] = None       # populated for TABLEs
    parent: Optional["Node"] = None
    children: list["Node"] = field(default_factory=list)
    boilerplate: bool = False           # inside nav/footer/aside/...

    def ancestors(self) -> list["Node"]:
        out, cur = [], self.parent
        while cur is not None:
            out.append(cur)
            cur = cur.parent
        return out

    def __repr__(self) -> str:
        t = self.text if len(self.text) <= 40 else self.text[:37] + "..."
        return f"<{self.type.value} {t!r}>"


@dataclass
class Sentence:
    """A candidate sentence handed to the NLP pipeline."""

    text: str
    segment: Node
    # variant index: 0 = the segment alone, higher = more context.
    variant: int = 0


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


def _table_headers_and_rows(tbl) -> tuple[list[str], list[list[str]]]:
    """Split an HTML <table> tag into its (raw, un-lower-cased) header cells and
    cleaned body rows. Shared by every table-walking consumer so the tr/td/th
    traversal and cell cleaning live in exactly one place."""
    rows = tbl.find_all("tr")
    if not rows:
        return [], []
    header_row = [_clean(c.get_text(" ")) for c in rows[0].find_all(["td", "th"])]
    body = [
        [_clean(c.get_text(" ")) for c in r.find_all(["td", "th"])]
        for r in rows[1:]
    ]
    return header_row, body


def _build_table(tbl) -> Optional[Table]:
    """Flatten one <table> tag, or None if it has no rows."""
    header_row, body = _table_headers_and_rows(tbl)
    if not header_row and not body:
        return None
    headers = [h.lower() for h in header_row]
    n_rows = 1 + len(body)
    n_cols = max([len(header_row)] + [len(r) for r in body], default=0)
    all_cells = header_row + [c for row in body for c in row]
    cell_text = " • ".join(c for c in all_cells if c)

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
        for rc in body:
            if name_idx < len(rc):
                v = rc[name_idx]
                if v and not _IDENTIFIER_RE.search(v):
                    name_cells.append(v)

    return Table(
        headers=[h for h in headers if h],
        n_rows=n_rows,
        n_cols=n_cols,
        cell_text=cell_text,
        name_cells=name_cells,
        rows=body,
    )


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


class _TreeBuilder:
    """Build the tree representation."""

    def __init__(self, root: Node):
        self.root = root
        self.heading_stack: list[Node] = [root]
        self.last_block: Node = root
        self.block_texts: list[str] = []   # segment candidates in document order
        self.text_parts: list[str] = []    # rendered strings in document order
        self.tables: list[Table] = []

    def _heading_parent(self, level: int) -> Node:
        while len(self.heading_stack) > 1 and self.heading_stack[-1].level >= level:
            self.heading_stack.pop()
        return self.heading_stack[-1]

    def _own_text(self, el: Tag) -> str:
        parts: list[str] = []

        def collect(tag: Tag) -> None:
            for c in tag.children:
                if type(c) in (NavigableString, CData):
                    parts.append(str(c))
                elif isinstance(c, Tag):
                    if c.name == "br":
                        parts.append(" ")
                    elif c.name not in _OWN_TEXT_BOUNDARY:
                        collect(c)

        collect(el)
        return _clean("".join(parts))

    def _add_text_nodes(self, el: Tag, boiler: bool) -> None:
        own = self._own_text(el)
        if not own:
            return
        if len(own) >= 3:
            self.block_texts.append(own)
        parent = self._heading_parent(7)  # closest heading
        for s in _SENT_RE.split(own):
            s = s.strip()
            if len(s) > 1:
                node = Node(NodeType.TEXT, s, parent=parent, boilerplate=boiler)
                parent.children.append(node)
                self.last_block = node

    def walk(self, el: Tag, tables_by_id: dict[int, Table],
             boiler: bool = False, suppress_nodes: bool = False,
             list_anchor: Optional[Node] = None) -> None:
        name = el.name
        boiler = boiler or name in _BOILERPLATE_TAGS

        if name in _BLOCK_TAG_SET:
            seg = _clean(el.get_text(" "))
            if len(seg) >= 3:
                self.block_texts.append(seg)

        if name in _HEADING_LEVELS:
            if not suppress_nodes:
                txt = _clean(el.get_text(" "))
                if txt:
                    level = _HEADING_LEVELS[name]
                    parent = self._heading_parent(level)
                    node = Node(NodeType.HEADING, txt, level=level,
                                parent=parent, boilerplate=boiler)
                    parent.children.append(node)
                    self.heading_stack.append(node)
                    self.last_block = node
            # Suppress nested nodes so headings are not represented twice.
            suppress_nodes = True
        elif name in ("ul", "ol"):
            list_anchor = self.last_block
        elif name == "li":
            if not suppress_nodes:
                txt = _BULLET_RE.sub("", _clean(el.get_text(" ")))
                if txt:
                    parent = list_anchor if list_anchor is not None else self.last_block
                    node = Node(NodeType.LISTITEM, txt, parent=parent,
                                boilerplate=boiler)
                    parent.children.append(node)
        elif name == "table":
            table = tables_by_id.get(id(el))
            if table is not None and not suppress_nodes:
                parent = self._heading_parent(7)
                node = Node(NodeType.TABLE, "", table=table,
                            parent=parent, boilerplate=boiler)
                parent.children.append(node)
            suppress_nodes = True
        elif name in _CAPTURE_TAGS and not suppress_nodes:
            self._add_text_nodes(el, boiler)

        for child in el.children:
            if type(child) in (NavigableString, CData):
                self.text_parts.append(str(child))
            elif isinstance(child, Tag):
                self.walk(child, tables_by_id, boiler, suppress_nodes, list_anchor)


class DocTree:
    """A simplified tree with derived flat views."""

    def __init__(self, root: Node, title: str = "", raw: str = "",
                 links: Optional[list[tuple[str, str]]] = None,
                 tables: Optional[list[Table]] = None,
                 text: str = "", block_texts: Optional[list[str]] = None):
        self.root = root
        self.title = title
        self.raw = raw
        self.links = links or []
        self._tables = tables or []
        self._text = text
        self._block_texts = block_texts or []
        self._segments: Optional[list[str]] = None

    # -------------------------------------------------------------- builders
    @classmethod
    def from_html(cls, html: str, max_bytes: int = MAX_HTML_BYTES) -> "DocTree":
        html = html or ""
        if max_bytes and len(html) > max_bytes:
            html = html[:max_bytes]
        soup = BeautifulSoup(html, "lxml")

        title_tag = soup.find("title")
        title = _clean(title_tag.get_text()) if title_tag else ""

        # Read tables and links before stripping non-content tags.
        tables: list[Table] = []
        tables_by_id: dict[int, Table] = {}
        for tbl in soup.find_all("table"):
            table = _build_table(tbl)
            if table is not None:
                tables.append(table)
                tables_by_id[id(tbl)] = table

        links: list[tuple[str, str]] = []
        for a in soup.find_all("a", href=True):
            txt = _clean(a.get_text(" "))
            href = a["href"].strip()
            if href:
                links.append((txt, href))

        for tag in soup(list(_DROP_TAGS)):
            tag.decompose()

        root = Node(NodeType.ROOT)
        builder = _TreeBuilder(root)
        body = soup.body or soup
        if isinstance(body, Tag):
            builder.walk(body, tables_by_id)

        text = "\n".join(builder.text_parts)
        text = _NL_RE.sub("\n\n", "\n".join(_clean(line) for line in text.splitlines()))

        return cls(root, title=title, raw=html, links=links, tables=tables,
                   text=text.strip(), block_texts=builder.block_texts)

    @classmethod
    def from_text(cls, text: str) -> "DocTree":
        root = Node(NodeType.ROOT)
        for block in re.split(r"\n\s*\n", text or ""):
            block = block.strip()
            if not block:
                continue
            for line in block.splitlines():
                line = _clean(line)
                if not line:
                    continue
                if _BULLET_RE.match(line):
                    node = Node(NodeType.LISTITEM, _BULLET_RE.sub("", line), parent=root)
                    root.children.append(node)
                else:
                    for sent in _SENT_RE.split(line):
                        sent = sent.strip()
                        if sent:
                            root.children.append(
                                Node(NodeType.TEXT, sent, parent=root)
                            )
        segs = [n.text for n in root.children]
        return cls(root, text=_clean(text or ""), block_texts=segs)

    # ---------------------------------------------------------- traversal
    def walk(self) -> Iterator[Node]:
        stack = [self.root]
        while stack:
            node = stack.pop()
            if node.type is not NodeType.ROOT and (
                    node.type is not NodeType.HEADING or node.text):
                yield node
            stack.extend(reversed(node.children))

    # -------------------------------------------------------- derived views
    @property
    def text(self) -> str:
        return self._text

    def tables(self) -> list[Table]:
        return self._tables

    def segments(self, node_type: Optional[NodeType] = None) -> list:
        if node_type is not None:
            return [n for n in self.walk() if n.type == node_type]
        if self._segments is None:
            deduped: list[str] = []
            for s in self._block_texts:
                if not deduped or deduped[-1] != s:
                    deduped.append(s)
            # If block tags carved out little text
            # (e.g. a policy laid out in <div>/<br>)
            # rebuild segments from the rendered text.
            block_chars = sum(len(s) for s in deduped)
            if self._text and block_chars < _BLOCK_COVERAGE_MIN * len(self._text):
                from_text = _segments_from_text(self._text)
                if len(from_text) > len(deduped):
                    deduped = from_text
            self._segments = deduped
        return self._segments

    # ---------------------------------------------------------- sentences
    def sentences(self) -> list[Sentence]:
        """Generate candidate complete sentences."""
        out: list[Sentence] = []
        for node in self.walk():
            if node.boilerplate:
                continue
            if node.type is NodeType.TEXT or node.type is NodeType.HEADING:
                out.append(Sentence(node.text, node, 0))
            elif node.type is NodeType.LISTITEM:
                anc = node.ancestors()
                out.append(Sentence(node.text, node, 0))
                if anc:
                    p = anc[0]
                    joined = _join(p.text, node.text)
                    out.append(Sentence(joined, node, 1))
                if len(anc) >= 2:
                    joined = _join(anc[1].text, _join(anc[0].text, node.text))
                    out.append(Sentence(joined, node, 2))
            elif node.type is NodeType.TABLE and node.table is not None:
                for row in node.table.rows:
                    for cell in row:
                        if len(cell.split()) < _MIN_CELL_WORDS:
                            continue
                        for s in _SENT_RE.split(cell):
                            s = s.strip()
                            if len(s) > 1:
                                out.append(Sentence(s, node, 0))
        return out

    def as_document(self) -> Document:
        """Drop-in :class:`Document`."""
        return Document(
            title=self.title,
            text=self._text,
            segments=self.segments(),
            tables=self._tables,
            links=self.links,
            raw=self.raw,
        )


def _join(prefix: str, item: str) -> str:
    prefix = prefix.rstrip()
    if not prefix:
        return item
    if prefix.endswith(":"):
        return f"{prefix} {item}"
    if prefix.endswith((".", ";")):
        return f"{prefix} {item}"
    return f"{prefix}: {item}"


def parse_html(html: str, max_bytes: int = MAX_HTML_BYTES) -> Document:
    """Parse raw HTML into a :class:`Document`."""
    return DocTree.from_html(html, max_bytes=max_bytes).as_document()
