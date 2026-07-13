"""The intermediate phrase graph."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Optional


class PhraseLabel(str, Enum):
    DATA = "DATA"
    ENTITY = "ENTITY"
    PURPOSE = "PURPOSE"


class PhraseEdge(str, Enum):
    COLLECT = "COLLECT"
    NOT_COLLECT = "NOT_COLLECT"
    SUBSUME = "SUBSUME"
    COREF = "COREF" 
    PURPOSE = "PURPOSE"


@dataclass
class Phrase:
    key: str
    text: str
    label: PhraseLabel
    sent_id: int = 0
    meta: dict = field(default_factory=dict)


@dataclass
class Edge:
    src: str
    dst: str
    etype: PhraseEdge
    attrs: dict = field(default_factory=dict)


class PhraseGraph:
    def __init__(self) -> None:
        self.phrases: dict[str, Phrase] = {}
        self.edges: list[Edge] = []

    def add_phrase(self, key: str, text: str, label: PhraseLabel,
                   sent_id: int = 0) -> str:
        if key not in self.phrases:
            self.phrases[key] = Phrase(key, text, label, sent_id)
        return key

    def add_edge(self, src: str, dst: str, etype: PhraseEdge, **attrs) -> None:
        if src in self.phrases and dst in self.phrases:
            self.edges.append(Edge(src, dst, etype, attrs))

    def edges_of(self, etype: PhraseEdge) -> list[Edge]:
        return [e for e in self.edges if e.etype == etype]

    def __repr__(self) -> str:
        from collections import Counter
        c = Counter(e.etype.value for e in self.edges)
        return f"<PhraseGraph {len(self.phrases)} phrases, edges={dict(c)}>"
