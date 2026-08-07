"""Outward expansion of the sharing graph from one origin."""

from __future__ import annotations

import threading
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import urlparse

from .classify.named_entities import first_party_tokens
from .classify.poligraph_connector import (
    merge_relations,
    poligraph_available,
    target_relations,
)
from .classify.structured_relations import structured_relations_for_target
from .cmp import cmp_relations
from .collect.base import Corpus, Target
from .collect.runner import fetch_target
from .entities import observed_domain_hints, resolve_entity_domain, resolve_name
from .sharing_graph import (
    NodeType,
    SharingGraph,
    add_target,
    expand_node,
)
from .traffic import observed_hosts, traffic_relations
from .typology import TargetType

FETCH_WORKERS = 8


def origin_of(url: str) -> str:
    """The scheme and host of an http(s) URL."""
    p = urlparse(url.strip())
    if p.scheme not in ("http", "https") or not p.netloc:
        raise ValueError(f"not an http(s) URL: {url!r}")
    return f"{p.scheme}://{p.netloc}"


def target_for_origin(origin: str) -> Target:
    """A website target for one origin."""
    host = urlparse(origin).netloc
    return Target(
        id=f"{TargetType.WEBSITE.value}__{Target.make_id(host)}",
        type=TargetType.WEBSITE.value,
        name=host,
        url=origin,
    )


def relations_for_target(corpus: Corpus, target_id: str, docs, first_party) -> list[dict]:
    """Every sharing relation one target's document set supports."""
    lists = [structured_relations_for_target(corpus, docs, first_party=first_party)]
    if poligraph_available():
        lists.append(target_relations(corpus, target_id, docs, first_party=first_party))
    return merge_relations(lists)


def analyse_origin(
    corpus: Corpus,
    origin: str,
    requests=None,
    force: bool = False,
    delay: float = 0.2,
    fetched: bool = False,
    cmp=None,
) -> tuple[list[dict], list[dict]]:
    """Collect ``origin`` if needed and return its ``(relations, observed)``."""
    target = target_for_origin(origin)
    if not fetched:
        fetch_origin(corpus, origin, force=force, delay=delay)
    _, docs = corpus.read_manifest(target.id)
    fp_urls = [target.seed_policy_url] + [
        d.url for d in docs
        if d.role in ("privacy_policy", "cookie_policy", "do_not_sell")
    ]
    first_party = first_party_tokens(fp_urls, name=target.name)
    relations = merge_relations([
        relations_for_target(corpus, target.id, docs, first_party),
        traffic_relations(requests, origin, first_party=first_party),
        cmp_relations(cmp, first_party=first_party),
    ])
    observed = observed_hosts(requests, origin, first_party=first_party)
    return relations, observed


def fetch_origin(corpus: Corpus, origin: str, force: bool = False,
                 delay: float = 0.2) -> bool:
    """Collect one origin's document set, reusing the corpus when present."""
    target = target_for_origin(origin)
    manifest = corpus.root / target.id / "manifest.json"
    if manifest.exists() and not force:
        return True
    fetch_target(target, corpus, force=force, delay=delay)
    return manifest.exists()


# --------------------------------------------------------------------------- #
# Expansion
# --------------------------------------------------------------------------- #
@dataclass
class Progress:
    """Where an expansion has reached."""

    hop: int = 0
    hops: int = 1
    phase: str = "starting"       # starting / fetching / analysing / done / stopped
    parties_total: int = 0        # parties queued at the current hop
    parties_done: int = 0
    current: str = ""
    crawled: int = 0              # origins collected across the whole run
    unresolved: list[str] = field(default_factory=list)
    error: str = ""

    def to_dict(self) -> dict:
        return {
            "hop": self.hop, "hops": self.hops, "phase": self.phase,
            "parties_total": self.parties_total, "parties_done": self.parties_done,
            "current": self.current, "crawled": self.crawled,
            "unresolved": list(self.unresolved), "error": self.error,
        }


@dataclass
class _Party:
    node_id: str
    name: str
    domain: str
    basis: str


class Expansion:
    """A single outward walk, observable while it runs."""

    def __init__(
        self,
        corpus_root: str | Path,
        seed_url: str,
        hops: int = 1,
        requests=None,
        force: bool = False,
        delay: float = 0.2,
        overrides: dict[str, str] | None = None,
        workers: int = FETCH_WORKERS,
        cmp=None,
    ) -> None:
        self.corpus = Corpus(corpus_root)
        self.seed_url = seed_url
        self.origin = origin_of(seed_url)
        self.hops = max(1, min(3, int(hops)))
        self.requests = requests or []
        self.force = force
        self.delay = delay
        self.overrides = overrides or {}
        self.workers = max(1, workers)
        self.cmp = cmp or {}
        self.graph = SharingGraph()
        self.progress = Progress(hops=self.hops)
        self.unresolved: dict[str, str] = {}   # canonical key -> display name
        self._lock = threading.Lock()
        self._stop = threading.Event()

    # -- control -------------------------------------------------------- #
    def stop(self) -> None:
        self._stop.set()

    @property
    def stopped(self) -> bool:
        return self._stop.is_set()

    def snapshot(self) -> dict:
        """The graph as built so far, with the current progress."""
        with self._lock:
            return {
                "origin": self.origin,
                "hops": self.hops,
                "graph": self.graph.to_dict(),
                "progress": self.progress.to_dict(),
                "unresolved": sorted(self.unresolved.values(), key=str.lower),
            }

    # -- walk ----------------------------------------------------------- #
    def run(self) -> SharingGraph:
        try:
            self._run()
        except Exception as exc:  # noqa: BLE001
            with self._lock:
                self.progress.error = f"{type(exc).__name__}: {exc}"
                self.progress.phase = "done"
            raise
        return self.graph

    def _run(self) -> SharingGraph:
        self._set(phase="fetching", hop=0, current=self.origin)
        fetch_origin(self.corpus, self.origin, force=self.force, delay=self.delay)
        self._set(phase="analysing", crawled=1)
        relations, observed = analyse_origin(
            self.corpus, self.origin, requests=self.requests,
            force=self.force, delay=self.delay, fetched=True, cmp=self.cmp,
        )
        seed_target = target_for_origin(self.origin)
        with self._lock:
            seed_id = add_target(
                self.graph, seed_target.id, seed_target.name, relations,
                target_type=seed_target.type, observed=observed, hop=0,
            )
        hints = observed_domain_hints(observed)

        expanded: set[str] = {seed_id}
        frontier = self._parties(seed_id, hints)
        for hop in range(1, self.hops):
            if self.stopped or not frontier:
                break
            self._set(hop=hop, parties_total=len(frontier), parties_done=0)
            frontier = self._expand_hop(frontier, hop, expanded, hints)

        self._set(phase="stopped" if self.stopped else "done", current="")
        return self.graph

    def _expand_hop(
        self, parties: list[_Party], hop: int, expanded: set[str],
        hints: dict[str, str],
    ) -> list[_Party]:
        """Collect and analyse one ring, returning the ring beyond it."""
        expanded.update(p.node_id for p in parties)
        self._set(phase="fetching")
        with ThreadPoolExecutor(max_workers=self.workers) as pool:
            list(pool.map(self._fetch_party, parties))
        if self.stopped:
            return []

        self._set(phase="analysing")
        nxt: list[_Party] = []
        for party in parties:
            if self.stopped:
                break
            self._set(current=party.name)
            origin = f"https://{party.domain}"
            analysed = True
            try:
                relations, _ = analyse_origin(
                    self.corpus, origin, delay=self.delay, fetched=True,
                )
            except (FileNotFoundError, ValueError):
                relations, analysed = [], False
            with self._lock:
                expand_node(self.graph, party.node_id, relations, hop=hop,
                            primary_domain=party.domain, expanded=analysed)
                self.progress.parties_done += 1
            for onward in self._parties(party.node_id, hints):
                if onward.node_id not in expanded:
                    nxt.append(onward)
        seen: set[str] = set()
        return [p for p in nxt if not (p.node_id in seen or seen.add(p.node_id))]

    def _fetch_party(self, party: _Party) -> None:
        if self.stopped:
            return
        self._set(current=party.name)
        try:
            fetch_origin(self.corpus, f"https://{party.domain}", delay=self.delay)
        except Exception:  # noqa: BLE001
            return
        with self._lock:
            self.progress.crawled += 1

    def _parties(self, node_id: str, hints: dict[str, str]) -> list[_Party]:
        """The named organisations one node hands data to."""
        out: list[_Party] = []
        with self._lock:
            reached: dict[str, object] = {}
            for edge in self.graph.out_edges(node_id):
                node = self.graph.nodes.get(edge.dst)
                if node is None:
                    continue
                if node.type is NodeType.DOMAIN and node.owner_entity_id:
                    owner = self.graph.nodes.get(node.owner_entity_id)
                    if owner is not None and node.owner_entity_id != node_id:
                        reached[node.owner_entity_id] = owner
                else:
                    reached[edge.dst] = node
            nodes = reached
        for dst, node in nodes.items():
            if node.type is not NodeType.ENTITY:
                continue
            domain, basis = resolve_entity_domain(
                node.display_name, hints=hints, overrides=self.overrides,
            )
            if not domain and node.primary_domain:
                domain, basis = node.primary_domain, "name_domain"
            if not domain:
                with self._lock:
                    self.unresolved[resolve_name(node.display_name).key] = node.display_name
                    self.progress.unresolved = sorted(
                        self.unresolved.values(), key=str.lower
                    )[:50]
                continue
            out.append(_Party(node_id=dst, name=node.display_name,
                              domain=domain, basis=basis))
        return out

    def _set(self, **fields) -> None:
        with self._lock:
            for k, v in fields.items():
                setattr(self.progress, k, v)


def expand(
    corpus_root: str | Path,
    seed_url: str,
    hops: int = 1,
    requests=None,
    force: bool = False,
    delay: float = 0.2,
    overrides: dict[str, str] | None = None,
    cmp=None,
) -> SharingGraph:
    """Walk outward from ``seed_url`` and return the graph reached."""
    return Expansion(
        corpus_root, seed_url, hops=hops, requests=requests, force=force,
        delay=delay, overrides=overrides, cmp=cmp,
    ).run()
