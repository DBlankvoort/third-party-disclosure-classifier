"""Outward expansion of the sharing graph from one origin."""

from __future__ import annotations

import multiprocessing
import os
import threading
import time
from concurrent.futures import ProcessPoolExecutor, ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import urlparse

from .classify.named_entities import first_party_tokens
from .classify.named_relations import named_org_relations
from .classify.poligraph_connector import (
    merge_relations,
    poligraph_available,
    target_relations,
)
from .classify.structured_relations import structured_relations_for_target
from .cmp import cmp_relations
from .collect.base import Corpus, Target, deadline
from .collect.runner import (
    close_thread_renderer,
    close_walk_renderers,
    fetch_target,
    render_thin_docs,
)
from .corroboration import prune_to_corroborated, pruning_summary
from .entities import observed_domain_hints, resolve_entity_domain, resolve_name
from .probe import cached_probe, merge_requests
from .reconcile import (
    RegistryStore,
    confirm_tracker_domains,
    reconcile_sellers,
    reconcile_supply_chains,
    reconciliation_summary,
    tracker_summary,
)
from .sharing_graph import (
    EdgeKind,
    NodeType,
    SharingGraph,
    add_target,
    attach_schains,
    expand_node,
    target_node_id,
)
from .site_kind import kind_for, profile, store_target, supports
from .tracks import carries_upstream_data
from .traffic import observed_contacts
from .typology import TargetType

FETCH_WORKERS = 8

ANALYSIS_WORKERS = max(1, min(4, (os.cpu_count() or 2) // 2))

CHUNK = 96

RENDER_WORKERS = 2

INVENTORY_KINDS = frozenset({"main", "authorises_inventory_sale"})

RECONCILE_DEADLINE = 45.0

TRAFFIC_KINDS = frozenset({"main", "possibility", "traffic_policy",
                           "contacts_domain", "lower_bound"})

ORIGIN_DEADLINE = 120.0
PARTY_DEADLINE = 45.0
RENDER_DEADLINE = 45.0

TIME_LIMIT = 0.0  # 0 leaves the walk unbounded


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


def target_for_url(url: str) -> Target:
    """The target one live URL names"""
    return store_target(url) or target_for_origin(origin_of(url))


POLICY_ROLES = ("privacy_policy", "cookie_policy", "do_not_sell")


def first_party_urls(target: Target, docs) -> list[str]:
    """The URLs a target's own party speaks from."""
    return [target.seed_policy_url] + [
        d.url for d in docs if d.role in POLICY_ROLES
    ]


def first_party_for_target(corpus: Corpus, target: Target) -> set[str]:
    """The brand tokens naming one target's own party."""
    try:
        _, docs = corpus.read_manifest(target.id)
    except (FileNotFoundError, OSError, ValueError, KeyError):
        docs = []
    return first_party_tokens(first_party_urls(target, docs), name=target.name)


def probed_requests(corpus: Corpus, target: Target, origin: str,
                    force: bool = False) -> list[dict]:
    """One clean-profile capture of ``origin``"""
    return cached_probe(corpus.root / target.id, origin, force=force)["requests"]


EDGE_KINDS = {
    "main", "discloses_relation_with", "lists_vendor",
    "authorises_inventory_sale", "contacts_domain",
    "lower_bound", "possibility", "traffic_policy", "schain",
}


def relations_for_target(
    corpus: Corpus, target_id: str, docs, first_party,
    target_type: str = TargetType.WEBSITE.value,
    evidence_kind: str = "main",
    site_kind: str = "",
) -> list[dict]:
    """Every sharing relation one target's document set supports."""
    site_kind = site_kind or target_type
    lists = []
    if evidence_kind in {"main", "possibility", "traffic_policy",
                         "discloses_relation_with"}:
        lists.extend([
            structured_relations_for_target(
                corpus, docs, first_party=first_party, registry_kinds=frozenset(),
            ),
            named_org_relations(corpus, docs, target_type=target_type,
                                first_party=first_party),
        ])
    if evidence_kind in {"main", "possibility", "traffic_policy",
                         "discloses_relation_with"} and poligraph_available():
        lists.append(target_relations(corpus, target_id, docs, first_party=first_party))
    if evidence_kind == "lists_vendor" and supports(site_kind, "lists_vendor"):
        lists.append(structured_relations_for_target(
            corpus, docs, first_party=first_party,
            registry_kinds=frozenset({"tcf_gvl", "vendors_json"}),
        ))
    if (evidence_kind in {"main", "authorises_inventory_sale"}
            and supports(site_kind, "authorises_inventory_sale")):
        lists.append(structured_relations_for_target(
            corpus, docs, first_party=first_party,
            registry_kinds=frozenset({"ads_txt"}),
        ))
    return merge_relations(lists)


def analyse_origin(
    corpus: Corpus,
    origin: str,
    requests=None,
    force: bool = False,
    delay: float = 0.2,
    fetched: bool = False,
    cmp=None,
    probe: bool = False,
    evidence_kind: str = "main",
    target: Target | None = None,
    site_kind: str = "",
) -> tuple[list[dict], list[dict]]:
    """Collect ``origin`` if needed and return its ``(relations, observed)``."""
    target = target or target_for_origin(origin)
    if evidence_kind in {"contacts_domain", "lower_bound", "schain"}:
        if probe:
            requests = merge_requests(
                requests, probed_requests(corpus, target, origin, force=force))
        return [], observed_contacts(
            requests, origin, first_party=first_party_for_target(corpus, target),
        )
    if not fetched:
        fetch_origin(corpus, origin, force=force, delay=delay, target=target)
    if probe:
        requests = merge_requests(
            requests, probed_requests(corpus, target, origin, force=force))
    _, docs = corpus.read_manifest(target.id)
    first_party = first_party_tokens(
        first_party_urls(target, docs), name=target.name)
    kind = site_kind or kind_for(origin, target, docs)
    relations = merge_relations([
        relations_for_target(corpus, target.id, docs, first_party,
                             target_type=target.type, evidence_kind=evidence_kind,
                             site_kind=kind),
        cmp_relations(cmp, first_party=first_party)
        if (evidence_kind == "possibility"
            or (evidence_kind == "lists_vendor" and supports(kind, "lists_vendor")))
        else [],
    ])
    observed = (observed_contacts(requests, origin, first_party=first_party)
                if supports(kind, "contacts_domain") else [])
    return relations, observed


def fetch_origin(corpus: Corpus, origin: str, force: bool = False,
                 delay: float = 0.2, render: bool = True,
                 target: Target | None = None) -> bool:
    """Collect one origin's document set, reusing the corpus when present."""
    target = target or target_for_origin(origin)
    manifest = corpus.root / target.id / "manifest.json"
    if not manifest.exists() or force:
        fetch_target(target, corpus, force=force, delay=delay)
        if not manifest.exists():
            return False
    if render:
        try:
            _, docs = corpus.read_manifest(target.id)
            render_thin_docs(corpus, target, docs)
        except (OSError, ValueError, KeyError):
            pass
    return True


def _init_analysis_worker() -> None:
    """Confine each worker to one core and load the language model once."""
    for var in ("OMP_NUM_THREADS", "OPENBLAS_NUM_THREADS", "MKL_NUM_THREADS"):
        os.environ[var] = "1"
    try:
        import torch

        torch.set_num_threads(1)
    except Exception:  # noqa: BLE001
        pass
    from .classify.named_entities import load_ner
    from .classify.poligraph_connector import warm_pipeline

    warm_pipeline()
    load_ner()


def _close_renderer(gate: threading.Barrier) -> None:
    try:
        gate.wait(timeout=30)
    except threading.BrokenBarrierError:
        pass
    close_thread_renderer()


def _analyse_job(payload: tuple) -> tuple[list[dict], list[dict], bool]:
    """Analyse one already-collected origin in a worker process."""
    corpus_root, origin, delay, probe, evidence_kind = payload
    try:
        relations, observed = analyse_origin(
            Corpus(corpus_root), origin, delay=delay, fetched=True, probe=probe,
            evidence_kind=evidence_kind,
        )
    except (FileNotFoundError, ValueError):
        return [], [], False
    return relations, observed, True


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
    elapsed: float = 0.0          # seconds since the walk began
    time_limit: float = 0.0       # seconds it may run for, 0 for unbounded
    unresolved: list[str] = field(default_factory=list)
    error: str = ""

    def to_dict(self) -> dict:
        return {
            "hop": self.hop, "hops": self.hops, "phase": self.phase,
            "parties_total": self.parties_total, "parties_done": self.parties_done,
            "current": self.current, "crawled": self.crawled,
            "elapsed": round(self.elapsed, 1), "time_limit": self.time_limit,
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
        analysis_workers: int = ANALYSIS_WORKERS,
        origin_deadline: float = ORIGIN_DEADLINE,
        party_deadline: float = PARTY_DEADLINE,
        render: bool = True,
        render_workers: int = RENDER_WORKERS,
        time_limit: float = TIME_LIMIT,
        chunk: int = CHUNK,
        probe: bool | None = None,
        evidence_kind: str = "main",
        lookups: int = 0,
        corroborated_only: bool = False,
        schains=None,
    ) -> None:
        self.corpus = Corpus(corpus_root)
        self.seed_url = seed_url
        self.origin = origin_of(seed_url)
        self.target = target_for_url(seed_url)
        self.site_kind = kind_for(self.origin, self.target)
        self.hops = max(1, int(hops))
        self.origin_deadline = origin_deadline
        self.party_deadline = party_deadline
        self.requests = requests or []
        self.force = force
        self.delay = delay
        self.overrides = overrides or {}
        self.workers = max(1, workers)
        self.analysis_workers = max(1, analysis_workers)
        self.render = render
        self.render_workers = max(1, render_workers)
        self.time_limit = max(0.0, float(time_limit))
        self.chunk = max(1, int(chunk))
        if evidence_kind not in EDGE_KINDS:
            raise ValueError(f"unknown evidence kind: {evidence_kind}")
        if not supports(self.site_kind, evidence_kind):
            raise ValueError(
                f"{evidence_kind} is not evidence about a {self.site_kind}"
            )
        self.evidence_kind = evidence_kind
        self.probe = (
            evidence_kind in {"main", "lower_bound", "possibility",
                              "traffic_policy", "contacts_domain"}
            and supports(self.site_kind, "contacts_domain")
        ) if probe is None else bool(probe)
        self.started = 0.0
        self.cmp = cmp or {}
        self.schains = list(schains or ())
        self._reconciled: dict[tuple, dict] = {}
        self._tracked: dict[tuple, dict] = {}
        self.dropped: list[dict] = []
        self.corroborated_only = bool(corroborated_only) and evidence_kind == "main"
        self._store: RegistryStore | None = None
        self.lookups = max(0, int(lookups))
        self.graph = SharingGraph()
        self.progress = Progress(hops=self.hops, time_limit=self.time_limit)
        self.unresolved: dict[str, str] = {}   # canonical key -> display name
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._timed_out = False
        self._pool: ProcessPoolExecutor | None = None

    # -- control -------------------------------------------------------- #
    def stop(self) -> None:
        self._stop.set()

    @property
    def elapsed(self) -> float:
        return time.monotonic() - self.started if self.started else 0.0

    @property
    def stopped(self) -> bool:
        """A reader's stop and a spent clock end the walk by the same route."""
        if self._stop.is_set():
            return True
        if self.time_limit and self.elapsed >= self.time_limit:
            self._timed_out = True
            self._stop.set()
            return True
        return False

    def apply_edits(self, edits) -> tuple[list, list]:
        """Apply a reader's corrections to the graph as it stands."""
        applied, rejected = [], []
        with self._lock:
            for edit in edits or ():
                if isinstance(edit, dict) and self.graph.apply_edit(edit):
                    applied.append(edit)
                else:
                    rejected.append(edit)
        return applied, rejected

    def snapshot(self) -> dict:
        """The graph as built so far, with the current progress."""
        with self._lock:
            self.progress.elapsed = self.elapsed
            return {
                "origin": self.origin,
                "hops": self.hops,
                **profile(self.origin, self.target),
                "target_id": self.target.id,
                "graph": self.graph.to_dict(),
                "progress": self.progress.to_dict(),
                "unresolved": sorted(self.unresolved.values(), key=str.lower),
                "reconciliation": reconciliation_summary(self.reconciliation),
                "tracker_check": tracker_summary(self.tracker_check),
                "corroborated_only": self.corroborated_only,
                "pruned": pruning_summary(self.dropped),
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
        finally:
            close_walk_renderers()
            if self._pool is not None:
                self._pool.shutdown(wait=not self.stopped, cancel_futures=True)
                self._pool = None
        return self.graph

    def _run(self) -> SharingGraph:
        self.started = time.monotonic()
        self._set(phase="fetching", hop=1, current=self.origin)
        if self.evidence_kind != "contacts_domain":
            with deadline(self.origin_deadline):
                fetch_origin(self.corpus, self.origin, force=self.force,
                             delay=self.delay, render=self.render,
                             target=self.target)
        self._set(phase="analysing", crawled=0 if self.evidence_kind == "contacts_domain" else 1)
        relations, observed = analyse_origin(
            self.corpus, self.origin, requests=self.requests,
            force=self.force, delay=self.delay, fetched=True, cmp=self.cmp,
            probe=self.probe, evidence_kind=self.evidence_kind,
            target=self.target, site_kind=self.site_kind,
        )
        if self.evidence_kind == "contacts_domain":
            self._set(crawled=1)
        seed_target = self.target
        with self._lock:
            seed_id = add_target(
                self.graph, seed_target.id, seed_target.name, relations,
                target_type=seed_target.type, observed=observed, hop=0,
            )
            if self.evidence_kind == "schain":
                attach_schains(self.graph, seed_id, self.schains)
        hints = observed_domain_hints(observed)
        self._verify(hop=1)

        expanded: set[str] = {seed_id}
        frontier = self._parties(seed_id, hints)
        # An ad system's own ads.txt concerns its inventory, not onward sale of
        # the seed publisher's inventory. Keep this view to the seed's records.
        if self.evidence_kind in {
            "authorises_inventory_sale", "contacts_domain", "lower_bound", "schain",
        }:
            frontier = []
        for hop in range(1, self.hops):
            if self.stopped or not frontier:
                break
            frontier = self._ranked(frontier)
            self._set(hop=hop + 1, parties_total=len(frontier), parties_done=0)
            frontier = self._expand_hop(frontier, hop, expanded, hints)
            self._verify(hop=hop + 1)
            frontier = [p for p in frontier if p.node_id in self.graph.nodes]

        self._verify(hop=None)

        phase = "done"
        if self._timed_out:
            phase = "timed out"
        elif self.stopped:
            phase = "stopped"
        completed = {"hop": self.hops,
                     "parties_done": self.progress.parties_total} \
            if phase == "done" else {}
        self._set(phase=phase, current="", elapsed=self.elapsed, **completed)
        return self.graph

    # -- cross-checks --------------------------------------------------- #
    def _verify(self, hop: int | None) -> None:
        """Verify a newly added ring, or run a final pass without pruning."""
        if self.evidence_kind in TRAFFIC_KINDS:
            self._confirm_trackers()
        if self.evidence_kind in INVENTORY_KINDS:
            self._reconcile()
        if self.evidence_kind == "schain" and not self._reconciled:
            self._reconcile_schains()
        if self.corroborated_only and hop is not None:
            with self._lock:
                self.dropped.extend(prune_to_corroborated(self.graph, hop))

    @property
    def reconciliation(self) -> list[dict]:
        """Return all inventory arrangements checked during the crawl."""
        return list(self._reconciled.values())

    @property
    def tracker_check(self) -> list[dict]:
        """Return all observed contacts checked during the crawl."""
        return list(self._tracked.values())

    def _confirm_trackers(self) -> None:
        """Mark observed contacts recognized by an independent tracker list."""
        try:
            with self._lock:
                for row in confirm_tracker_domains(self.graph):
                    self._tracked[(row["src"], row["dst"])] = row
        except Exception as exc:  # noqa: BLE001
            self._set(error=self.progress.error or f"tracker list: {exc}")

    def _reconcile(self) -> None:
        """Check graph edges against each receiving party's sellers.json."""
        self._set(phase="verifying", current="")
        if self._store is None:
            self._store = RegistryStore(self.corpus)
        else:
            self._store.forget_missing()
        seed_host = urlparse(self.origin).netloc.removeprefix("www.")
        try:
            with deadline(RECONCILE_DEADLINE):
                report = reconcile_sellers(
                    self.graph, self.corpus,
                    domains={target_node_id(self.target.id): seed_host},
                    overrides=self.overrides,
                    store=self._store,
                    lookups=self.lookups,
                )
            with self._lock:
                for row in report:
                    self._reconciled[(row["kind"], row["src"], row["dst"])] = row
        except Exception as exc:  # noqa: BLE001
            self._set(error=self.progress.error or f"reconcile: {exc}")

    def _reconcile_schains(self) -> None:
        """Validate captured supply-chain accounts against sellers.json."""
        self._set(phase="verifying", current="")
        if self._store is None:
            self._store = RegistryStore(self.corpus)
        seed_host = urlparse(self.origin).netloc.removeprefix("www.")
        try:
            report = reconcile_supply_chains(
                self.graph, self.corpus,
                domains={target_node_id(self.target.id): seed_host},
                store=self._store, lookups=self.lookups,
            )
            with self._lock:
                for row in report:
                    self._reconciled[(row["kind"], row["src"], row["dst"])] = row
        except Exception as exc:  # noqa: BLE001
            self._set(error=self.progress.error or f"schain: {exc}")

    def _expand_hop(
        self, parties: list[_Party], hop: int, expanded: set[str],
        hints: dict[str, str],
    ) -> list[_Party]:
        """Collect and analyse one ring, returning the ring beyond it."""
        expanded.update(p.node_id for p in parties)
        nxt: list[_Party] = []
        for start in range(0, len(parties), self.chunk):
            if self.stopped:
                break
            batch = parties[start:start + self.chunk]
            self._set(phase="fetching")
            with ThreadPoolExecutor(max_workers=self.workers) as pool:
                reached = [p for p, ok in zip(
                    batch, pool.map(self._fetch_party, batch), strict=True,
                )
                           if ok]
            if self.stopped:
                break
            if not reached:
                continue
            self._render_ring(reached)
            if self.stopped:
                break

            self._set(phase="analysing")
            for party, (relations, observed, analysed) in self._analyse_ring(reached):
                with self._lock:
                    expand_node(self.graph, party.node_id, relations,
                                observed=observed, hop=hop,
                                primary_domain=party.domain, expanded=analysed)
                    if self.evidence_kind == "contacts_domain" and analysed:
                        self.progress.crawled += 1
                    self.progress.parties_done += 1
                    self.progress.current = party.name
                for onward in self._parties(party.node_id, hints):
                    if onward.node_id not in expanded:
                        nxt.append(onward)
        seen: set[str] = set()
        return [p for p in nxt if not (p.node_id in seen or seen.add(p.node_id))]

    def _ranked(self, parties: list[_Party]) -> list[_Party]:
        """A ring ordered by evidence."""
        with self._lock:
            return sorted(
                parties,
                key=lambda p: (-len(self.graph.in_edges(p.node_id)),
                               -self._top_confidence(p.node_id), p.name.lower()),
            )

    def _top_confidence(self, node_id: str) -> float:
        return max(
            (ev.confidence for edge in self.graph.in_edges(node_id)
             for ev in edge.evidence),
            default=0.0,
        )

    def _analyse_ring(self, parties: list[_Party]):
        """Yield ``(party, (relations, observed, analysed))`` for one batch."""
        def payload(p: _Party) -> tuple:
            return (str(self.corpus.root), f"https://{p.domain}", self.delay,
                    False, self.evidence_kind)

        if self.analysis_workers <= 1 or len(parties) == 1:
            for party in parties:
                if self.stopped:
                    return
                self._set(current=party.name)
                yield party, _analyse_job(payload(party))
            return
        pool = self._analysis_pool()
        futures = {pool.submit(_analyse_job, payload(p)): p for p in parties}
        for future in as_completed(futures):
            if self.stopped:
                for pending in futures:
                    pending.cancel()
                return
            yield futures[future], future.result()

    def _analysis_pool(self) -> ProcessPoolExecutor:
        if self._pool is None:
            ctx = multiprocessing.get_context("spawn")
            kwargs = {
                "max_workers": self.analysis_workers,
                "mp_context": ctx,
            }
            if self.evidence_kind in {"main", "discloses_relation_with"}:
                kwargs["initializer"] = _init_analysis_worker
            self._pool = ProcessPoolExecutor(**kwargs)
        return self._pool

    def _fetch_party(self, party: _Party) -> bool:
        """Collect one party's origin, reporting whether it can be analysed."""
        if self.stopped:
            return False
        self._set(current=party.name)
        if self.evidence_kind == "contacts_domain":
            return True
        try:
            with deadline(self.party_deadline):
                fetch_origin(self.corpus, f"https://{party.domain}",
                             delay=self.delay, render=False)
        except Exception:  # noqa: BLE001
            return False
        with self._lock:
            self.progress.crawled += 1
        return True

    def _render_ring(self, parties: list[_Party]) -> None:
        """Re-fetch the ring's client-rendered documents with a browser."""
        if not self.render or self.evidence_kind == "contacts_domain":
            return
        seen: set[str] = set()
        sites = [p for p in parties
                 if not (p.domain in seen or seen.add(p.domain))]
        if not sites:
            return
        self._set(phase="rendering")
        workers = min(self.render_workers, len(sites))
        with ThreadPoolExecutor(max_workers=workers) as pool:
            list(pool.map(self._render_party, sites))
            gate = threading.Barrier(workers)
            list(pool.map(lambda _: _close_renderer(gate), range(workers)))

    def _render_party(self, party: _Party) -> None:
        if self.stopped:
            return
        target = target_for_origin(f"https://{party.domain}")
        try:
            with deadline(RENDER_DEADLINE):
                _, docs = self.corpus.read_manifest(target.id)
                render_thin_docs(self.corpus, target, docs)
        except Exception:  # noqa: BLE001
            return

    def _parties(self, node_id: str, hints: dict[str, str]) -> list[_Party]:
        """The named organisations one node hands data to."""
        out: list[_Party] = []
        with self._lock:
            reached: dict[str, object] = {}
            for edge in self.graph.out_edges(node_id):
                source = self.graph.nodes.get(node_id)
                if self.evidence_kind == "traffic_policy":
                    at_seed = source is not None and source.hop_first_seen == 0
                    if at_seed and edge.kind is not EdgeKind.CONTACTS_DOMAIN:
                        continue
                if (self.evidence_kind == "possibility" and source is not None
                        and source.hop_first_seen > 0
                        and edge.kind is EdgeKind.DISCLOSES_RELATION_WITH
                        and not any(not ev.negative and carries_upstream_data(ev.subject)
                                    for ev in edge.evidence)):
                    continue
                    if not at_seed and edge.kind is not EdgeKind.DISCLOSES_RELATION_WITH:
                        continue
                    if not at_seed and not any(
                        not ev.negative and carries_upstream_data(ev.subject)
                        for ev in edge.evidence
                    ):
                        continue
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
    origin_deadline: float = ORIGIN_DEADLINE,
) -> SharingGraph:
    """Walk outward from ``seed_url`` and return the graph reached."""
    return Expansion(
        corpus_root, seed_url, hops=hops, requests=requests, force=force,
        delay=delay, overrides=overrides, cmp=cmp,
        origin_deadline=origin_deadline,
    ).run()
