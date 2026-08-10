"""Extract data-sharing relationships with PoliGraph."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

from .structured_relations import DOWNSTREAM

DEFAULT_ROLES = {
    "privacy_policy", "cookie_policy", "do_not_sell", "dpa",
}

CACHE_NAME = "poligraph.json"

PIPELINE_VERSION = 9

_GRAPHER = None
_IMPORT_ERROR: str | None = None


def poligraph_available() -> bool:
    """Whether the ``poligraph`` package is importable."""
    global _IMPORT_ERROR
    if _IMPORT_ERROR is not None:
        return False
    try:
        from ..poligraph import graph  # noqa: F401
        return True
    except Exception as exc:  # noqa: BLE001
        _IMPORT_ERROR = str(exc)
        return False


def _grapher():
    """Lazily build the PoliGrapher singleton."""
    global _GRAPHER
    if _GRAPHER is None:
        from ..poligraph.poligrapher import PoliGrapher

        _GRAPHER = PoliGrapher()
    return _GRAPHER


def warm_pipeline() -> None:
    """Load the language model up front, outside any latency being measured."""
    if poligraph_available():
        _grapher()


def _doc_hash(html: str) -> str:
    return hashlib.sha1(html.strip().encode("utf-8", "ignore")).hexdigest()


def graphs_for_target(
    corpus,
    target_id: str,
    docs,
    roles: set[str] = DEFAULT_ROLES,
    force: bool = False,
) -> dict[str, object]:
    """Build PoliGraphs for a target's narrative documents."""
    from ..poligraph.graph import PoliGraph
    from ..poligraph.nlp import DEFAULT_MODEL

    cache_path = Path(corpus.root) / target_id / CACHE_NAME
    cache: dict[str, dict] = {}
    if cache_path.exists() and not force:
        try:
            cache = json.loads(cache_path.read_text(encoding="utf-8"))
        except Exception:  # noqa: BLE001
            cache = {}

    graphs: dict[str, PoliGraph] = {}
    dirty = False
    for d in docs:
        if d.role not in roles or not d.ok:
            continue
        html = corpus.read_doc_html(d)
        if not html.strip():
            continue
        h = _doc_hash(html)
        entry = cache.get(d.doc_id)
        if (entry and entry.get("hash") == h
                and entry.get("version") == PIPELINE_VERSION
                and entry.get("model") == DEFAULT_MODEL):
            graphs[d.doc_id] = PoliGraph.from_dict(entry["graph"])
            continue
        graph = _grapher().from_html(html, f"{target_id}/{d.doc_id}").validate()
        graphs[d.doc_id] = graph
        cache[d.doc_id] = {
            "hash": h, "version": PIPELINE_VERSION, "model": DEFAULT_MODEL,
            "graph": graph.to_dict(),
        }
        dirty = True

    if dirty:
        try:
            cache_path.parent.mkdir(parents=True, exist_ok=True)
            cache_path.write_text(json.dumps(cache), encoding="utf-8")
        except Exception:  # noqa: BLE001
            pass
    return graphs


def _is_first_party_entity(entity: str, first_party: set[str] | None) -> bool:
    from ..poligraph.graph import FIRST_PARTY
    from .named_entities import _is_first_party

    if entity == FIRST_PARTY:
        return True
    return _is_first_party(entity, first_party)


def _named_examples(graph, entity: str, limit: int = 5) -> list[str]:
    """Concrete entities the graph says the entity subsumes."""
    from ..poligraph.graph import FIRST_PARTY, UNSPECIFIED_ACTOR, NodeType

    out = [
        n for n in graph.descendants(entity)
        if n not in (entity, FIRST_PARTY, UNSPECIFIED_ACTOR)
        and graph.node_type(n) == NodeType.ENTITY
    ]
    return sorted(out)[:limit]


_NON_ACTOR_TAILS = {
    "act", "law", "laws", "regulation", "regulations", "directive",
    "directives", "statute", "statutes", "legislation",
    "transfer", "transfers", "engineer", "engineers",
}


def _non_actor_entity(entity: str) -> bool:
    words = entity.split()
    return bool(words) and words[-1] in _NON_ACTOR_TAILS


def _clause_subject(text: str, role: str) -> str:
    """Whose data a clause concerns."""
    from ..lexicons import SERVICE_DATA_RE
    from ..tracks import SERVICE_DATA, SERVICE_DATA_ROLES, SITE_VISITOR

    if role in SERVICE_DATA_ROLES or (text and SERVICE_DATA_RE.search(text)):
        return SERVICE_DATA
    return SITE_VISITOR


def relations_from_graph(
    graph,
    first_party: set[str] | None = None,
    doc_id: str = "",
    role: str = "",
) -> list[dict]:
    """Flatten a PoliGraph's COLLECT / NOT_COLLECT edges into relation dicts."""
    from ..poligraph.graph import UNSPECIFIED_ACTOR, EdgeType
    from ..tracks import PERSONAL_DATA
    from .named_entities import grounded_org

    relations: list[dict] = []
    for e in graph.collect_edges(include_negative=True):
        if _non_actor_entity(e.entity):
            continue
        fp = _is_first_party_entity(e.entity, first_party)
        text = e.text[0][:300] if e.text else ""
        relations.append({
            "entity": e.entity,
            "party": "first" if fp else "third",
            "unspecified": e.entity == UNSPECIFIED_ACTOR or not (
                fp or grounded_org(e.entity)),
            "data_type": e.data_type,
            "action": e.action.value,
            "negative": e.edge_type == EdgeType.NOT_COLLECT,
            "direction": DOWNSTREAM,
            "track": PERSONAL_DATA,
            "subject": _clause_subject(text, role),
            "grounded": fp or grounded_org(e.entity),
            "purposes": sorted(p.value for p in e.purposes),
            "examples": [] if fp else _named_examples(graph, e.entity),
            "qualifier": "",
            "sources": ["policy"],
            "text": text,
            "doc_ids": [doc_id] if doc_id else [],
        })
    return relations


def merge_relations(rel_lists) -> list[dict]:
    """Merge per-document relation lists."""
    from ..tracks import PERSONAL_DATA, UNKNOWN

    merged: dict[tuple, dict] = {}
    for rels in rel_lists:
        for r in rels:
            key = (r["entity"], r["data_type"], r["action"], r["negative"],
                   r.get("direction", DOWNSTREAM),
                   r.get("track", PERSONAL_DATA), r.get("subject", UNKNOWN))
            if key in merged:
                m = merged[key]
                m["purposes"] = sorted(set(m["purposes"]) | set(r["purposes"]))
                m["examples"] = sorted(set(m["examples"]) | set(r["examples"]))[:5]
                m["doc_ids"] = sorted(set(m["doc_ids"]) | set(r["doc_ids"]))
                m["sources"] = sorted(set(m.get("sources", [])) | set(r.get("sources", [])))
                m["signals"] = sorted(set(m.get("signals", [])) | set(r.get("signals", [])))
                m["confidence"] = max(m.get("confidence", 0.0), r.get("confidence", 0.0))
                m["grounded"] = bool(m.get("grounded")) or bool(r.get("grounded"))
                # A "direct" authorization outranks a "reseller" one.
                if not m.get("qualifier") or r.get("qualifier") == "direct":
                    m["qualifier"] = r.get("qualifier", "") or m.get("qualifier", "")
                if not m["text"]:
                    m["text"] = r["text"]
            else:
                merged[key] = dict(r)
    return sorted(
        merged.values(),
        key=lambda r: (r["party"] != "third", r["negative"],
                       r.get("direction", DOWNSTREAM) != DOWNSTREAM,
                       r.get("track", PERSONAL_DATA) != PERSONAL_DATA,
                       r["entity"], r["data_type"]),
    )


def target_relations(
    corpus,
    target_id: str,
    docs,
    first_party: set[str] | None = None,
    roles: set[str] = DEFAULT_ROLES,
    force: bool = False,
) -> list[dict]:
    """The merged sharing-relation list for one target's document set."""
    graphs = graphs_for_target(corpus, target_id, docs, roles=roles, force=force)
    role_by_doc = {d.doc_id: d.role for d in docs}
    return merge_relations(
        relations_from_graph(g, first_party=first_party, doc_id=doc_id,
                             role=role_by_doc.get(doc_id, ""))
        for doc_id, g in graphs.items()
    )


def corpus_relations(
    corpus,
    target_ids: list[str] | None = None,
    roles: set[str] = DEFAULT_ROLES,
    force: bool = False,
) -> dict[str, list[dict]]:
    """The merged sharing-relation list for every target in the corpus."""
    from .named_entities import first_party_tokens

    ids = target_ids if target_ids is not None else corpus.list_targets()
    out: dict[str, list[dict]] = {}
    for tid in ids:
        target, docs = corpus.read_manifest(tid)
        fp_urls = [target.seed_policy_url] + [
            d.url for d in docs if d.role in ("privacy_policy", "cookie_policy", "do_not_sell")
        ]
        first_party = first_party_tokens(fp_urls, name=target.name)
        rels = target_relations(corpus, tid, docs, first_party=first_party,
                                roles=roles, force=force)
        if rels:
            out[tid] = rels
    return out
