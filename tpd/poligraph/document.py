"""Pre-process HTML into a simplified document tree."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from enum import Enum
from typing import Optional

try:
    from bs4 import BeautifulSoup, NavigableString, Tag
    _HAVE_BS4 = True
except Exception:
    _HAVE_BS4 = False


class SegType(str, Enum):
    HEADING = "HEADING"
    LISTITEM = "LISTITEM"
    TEXT = "TEXT"


@dataclass
class Segment:
    seg_type: SegType
    text: str
    parent: Optional["Segment"] = None
    children: list["Segment"] = field(default_factory=list)
    level: int = 0

    def ancestors(self) -> list["Segment"]:
        out, cur = [], self.parent
        while cur is not None:
            out.append(cur)
            cur = cur.parent
        return out

    def __repr__(self) -> str:
        t = self.text if len(self.text) <= 40 else self.text[:37] + "..."
        return f"<{self.seg_type.value} {t!r}>"


@dataclass
class Sentence:
    """A candidate sentence handed to the NLP pipeline."""

    text: str
    segment: Segment
    # variant index: 0 = the segment alone, higher = more context.
    variant: int = 0


_WS = re.compile(r"\s+")
_BULLET = re.compile(r"^\s*(?:[-*•·▪◦‣]|\(?\d+[.)]|\(?[a-z][.)])\s+", re.I)
_SKIP_TAGS = {"script", "style", "noscript", "nav", "footer", "aside", "header",
              "form", "button", "svg", "iframe"}


def _clean(text: str) -> str:
    return _WS.sub(" ", text).strip()


class DocumentTree:
    """A simplified document tree."""

    def __init__(self, root: Segment):
        self.root = root

    # -------------------------------------------------------------- builders
    @classmethod
    def from_html(cls, html: str) -> "DocumentTree":
        if not _HAVE_BS4:
            return cls.from_text(re.sub(r"<[^>]+>", " ", html))
        soup = BeautifulSoup(html, "lxml" if _lxml() else "html.parser")
        for t in soup(list(_SKIP_TAGS)):
            t.decompose()
        body = soup.body or soup
        root = Segment(SegType.HEADING, "", level=0)
        builder = _TreeBuilder(root)
        builder.walk(body)
        return cls(root)

    @classmethod
    def from_text(cls, text: str) -> "DocumentTree":
        root = Segment(SegType.HEADING, "", level=0)
        for block in re.split(r"\n\s*\n", text):
            block = block.strip()
            if not block:
                continue
            for line in block.splitlines():
                line = _clean(line)
                if not line:
                    continue
                if _BULLET.match(line):
                    seg = Segment(SegType.LISTITEM, _BULLET.sub("", line), parent=root)
                else:
                    seg = Segment(SegType.TEXT, line, parent=root)
                root.children.append(seg)
        return cls(root)

    # ---------------------------------------------------------- traversal
    def walk(self):
        stack = [self.root]
        while stack:
            seg = stack.pop()
            if seg.seg_type is not SegType.HEADING or seg.text:
                yield seg
            stack.extend(reversed(seg.children))

    def segments(self, seg_type: Optional[SegType] = None) -> list[Segment]:
        return [s for s in self.walk() if seg_type is None or s.seg_type == seg_type]

    # ---------------------------------------------------------- sentences
    def sentences(self) -> list[Sentence]:
        """Generate candidate complete sentences."""
        out: list[Sentence] = []
        for seg in self.walk():
            if seg.seg_type is SegType.TEXT:
                out.append(Sentence(seg.text, seg, 0))
            elif seg.seg_type is SegType.HEADING:
                if seg.text:
                    out.append(Sentence(seg.text, seg, 0))
            elif seg.seg_type is SegType.LISTITEM:
                anc = seg.ancestors()
                out.append(Sentence(seg.text, seg, 0))
                if anc:
                    p = anc[0]
                    joined = _join(p.text, seg.text)
                    out.append(Sentence(joined, seg, 1))
                if len(anc) >= 2:
                    joined = _join(anc[1].text, _join(anc[0].text, seg.text))
                    out.append(Sentence(joined, seg, 2))
        return out


def _join(prefix: str, item: str) -> str:
    prefix = prefix.rstrip()
    if not prefix:
        return item
    # If the preceding text ends with a colon, the list completes it directly.
    if prefix.endswith(":"):
        return f"{prefix} {item}"
    if prefix.endswith((".", ";")):
        return f"{prefix} {item}"
    return f"{prefix}: {item}"


def _lxml() -> bool:
    try:
        import lxml  # noqa: F401
        return True
    except Exception:
        return False


class _TreeBuilder:
    """Walks an HTML body and attaches segments under the right parents."""

    HEADING_TAGS = {f"h{i}": i for i in range(1, 7)}

    def __init__(self, root: Segment):
        self.root = root
        self.last_block: Segment = root
        self.heading_stack: list[Segment] = [root]

    def _heading_parent(self, level: int) -> Segment:
        while len(self.heading_stack) > 1 and self.heading_stack[-1].level >= level:
            self.heading_stack.pop()
        return self.heading_stack[-1]

    def walk(self, node) -> None:
        for child in getattr(node, "children", []):
            if not isinstance(child, Tag):
                continue
            name = child.name
            if name in self.HEADING_TAGS:
                level = self.HEADING_TAGS[name]
                txt = _clean(child.get_text(" "))
                if txt:
                    parent = self._heading_parent(level)
                    seg = Segment(SegType.HEADING, txt, parent=parent, level=level)
                    parent.children.append(seg)
                    self.heading_stack.append(seg)
                    self.last_block = seg
            elif name in ("ul", "ol"):
                parent = self.last_block
                for li in child.find_all("li", recursive=False):
                    txt = _clean(li.get_text(" "))
                    txt = _BULLET.sub("", txt)
                    if txt:
                        seg = Segment(SegType.LISTITEM, txt, parent=parent)
                        parent.children.append(seg)
                    self.walk(li)
            elif name in ("p", "div", "section", "article", "td", "li", "span", "main", "body"):
                direct = _clean("".join(
                    c for c in child.strings
                    if isinstance(c, NavigableString) and c.parent is child
                ))
                if direct and child.name in ("p", "div", "td", "span"):
                    parent = self._heading_parent(7)  # closest heading
                    for s in re.split(r"(?<=[.!?])\s+", direct):
                        s = s.strip()
                        if len(s) > 1:
                            seg = Segment(SegType.TEXT, s, parent=parent)
                            parent.children.append(seg)
                            self.last_block = seg
                self.walk(child)
