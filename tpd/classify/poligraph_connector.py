"""Extract data-sharing relationships with PoliGraph."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

# Narrative roles worth structural sharing analysis.
DEFAULT_ROLES = {
    "privacy_policy", "cookie_policy", "do_not_sell", "dpa",
    "partners_page", "help_doc",
}

CACHE_NAME = "poligraph.json"

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


def _doc_hash(html: str) -> str:
    return hashlib.sha1(html.strip().encode("utf-8", "ignore")).hexdigest()


def graphs_for_target(
    corpus,
    target_id: str,
    docs,
    roles: set[str] = DEFAULT_ROLES,
    force: bool = False,
) -> dict[str, "object"]:
    """Build PoliGraphs for a target's narrative documents."""
    from ..poligraph.graph import PoliGraph

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
        if entry and entry.get("hash") == h:
            graphs[d.doc_id] = PoliGraph.from_dict(entry["graph"])
            continue
        graph = _grapher().from_html(html, f"{target_id}/{d.doc_id}").validate()
        graphs[d.doc_id] = graph
        cache[d.doc_id] = {"hash": h, "graph": graph.to_dict()}
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
    from ..poligraph.graph import FIRST_PARTY, NodeType, UNSPECIFIED_ACTOR

    out = [
        n for n in graph.descendants(entity)
        if n not in (entity, FIRST_PARTY, UNSPECIFIED_ACTOR)
        and graph.node_type(n) == NodeType.ENTITY
    ]
    return sorted(out)[:limit]


def relations_from_graph(
    graph,
    first_party: set[str] | None = None,
    doc_id: str = "",
) -> list[dict]:
    """Flatten a PoliGraph's COLLECT / NOT_COLLECT edges into relation dicts."""
    from ..poligraph.graph import EdgeType, UNSPECIFIED_ACTOR

    relations: list[dict] = []
    for e in graph.collect_edges(include_negative=True):
        fp = _is_first_party_entity(e.entity, first_party)
        relations.append({
            "entity": e.entity,
            "party": "first" if fp else "third",
            "unspecified": e.entity == UNSPECIFIED_ACTOR,
            "data_type": e.data_type,
            "action": e.action.value,
            "negative": e.edge_type == EdgeType.NOT_COLLECT,
            "purposes": sorted(p.value for p in e.purposes),
            "examples": [] if fp else _named_examples(graph, e.entity),
            "qualifier": "",
            "sources": ["policy"],
            "text": (e.text[0][:300] if e.text else ""),
            "doc_ids": [doc_id] if doc_id else [],
        })
    return relations


def merge_relations(rel_lists) -> list[dict]:
    """Merge per-document relation lists."""
    merged: dict[tuple, dict] = {}
    for rels in rel_lists:
        for r in rels:
            key = (r["entity"], r["data_type"], r["action"], r["negative"])
            if key in merged:
                m = merged[key]
                m["purposes"] = sorted(set(m["purposes"]) | set(r["purposes"]))
                m["examples"] = sorted(set(m["examples"]) | set(r["examples"]))[:5]
                m["doc_ids"] = sorted(set(m["doc_ids"]) | set(r["doc_ids"]))
                m["sources"] = sorted(set(m.get("sources", [])) | set(r.get("sources", [])))
                # A "direct" authorization outranks a "reseller" one.
                if not m.get("qualifier") or r.get("qualifier") == "direct":
                    m["qualifier"] = r.get("qualifier", "") or m.get("qualifier", "")
                if not m["text"]:
                    m["text"] = r["text"]
            else:
                merged[key] = dict(r)
    # Third-party positive edges first, then negatives, then first-party.
    return sorted(
        merged.values(),
        key=lambda r: (r["party"] != "third", r["negative"], r["entity"], r["data_type"]),
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
    return merge_relations(
        relations_from_graph(g, first_party=first_party, doc_id=doc_id)
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
