"""Findings page."""

from __future__ import annotations

import html
import json
import statistics
from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from tpd.classify.named_entities import first_party_tokens, load_ner
from tpd.classify.named_relations import NARRATIVE_ROLES
from tpd.classify.prose_entities import scan_document
from tpd.collect.base import Corpus
from tpd.entities import known_to_kb
from tpd.extract import parse_html
from tpd.kb import gvl, tracker_radar
from tpd.sharing_graph import (
    EdgeKind,
    NodeType,
    SharingGraph,
    flow_hops,
    sharing_chains,
)
from tpd.tracks import INVENTORY, PERSONAL_DATA, TRACKS

TEXT_SHARE_FLOOR = 0.05

CHAIN_PARTIES = 4
CHAIN_LIMIT = 2000


def _pct(part: int, whole: int) -> float:
    return round(100.0 * part / whole, 1) if whole else 0.0


def _stats(values) -> dict:
    values = list(values)
    if not values:
        return {"n": 0, "mean": 0.0, "median": 0.0, "max": 0}
    return {
        "n": len(values),
        "mean": round(statistics.mean(values), 1),
        "median": round(statistics.median(values), 1),
        "max": max(values),
    }


# --------------------------------------------------------------------------- #
# Graph
# --------------------------------------------------------------------------- #
def graph_measures(graph: SharingGraph) -> dict:
    """Composition of a walked graph, reported per track and per ring."""
    entities = [n for n in graph.nodes.values() if n.type is NodeType.ENTITY]
    counts = graph.edge_counts()

    origins_by_hop: dict[int, set[str]] = {}
    for node in graph.nodes.values():
        if node.expanded:
            origins_by_hop.setdefault(node.hop_first_seen or 0, set()).add(node.id)

    rings: dict[int, dict] = {}
    for hop, origins in origins_by_hop.items():
        out_degrees, silent, reached = [], 0, set()
        for nid in origins:
            named = set()
            for edge in graph.out_edges(nid):
                if edge.kind is not EdgeKind.DISCLOSES_RELATION_WITH:
                    continue
                dst = graph.nodes.get(edge.dst)
                if dst is not None and dst.type is NodeType.ENTITY:
                    named.add(edge.dst)
            out_degrees.append(len(named))
            if not named:
                silent += 1
            reached |= named
        already = set().union(*(origins_by_hop.get(h, set())
                                for h in range(hop + 1))) if hop >= 0 else set()
        rings[hop] = {
            "origins": len(origins),
            "out_degrees": out_degrees,
            "silent": silent,
            "frontier": reached - already,
        }

    by_ring = {}
    for hop in sorted(rings):
        ring = rings[hop]
        by_ring[hop] = {
            "origins": ring["origins"],
            "named_out_degree": _stats(ring["out_degrees"]),
            "disclosing_nothing_named": ring["silent"],
            "frontier": len(ring["frontier"]),
        }
    hops = sorted(by_ring)
    for i, hop in enumerate(hops[:-1]):
        frontier = rings[hop]["frontier"]
        analysed = frontier & origins_by_hop.get(hops[i + 1], set())
        by_ring[hop]["attrition_pct"] = (
            round(100.0 * (1 - len(analysed) / len(frontier)), 1)
            if frontier else None
        )

    roles = graph.party_roles()
    return {
        "nodes": len(graph.nodes),
        "nodes_by_type": {t.value: sum(1 for n in graph.nodes.values() if n.type is t)
                          for t in NodeType},
        "recipients": len(roles["recipients"]),
        "suppliers": len(roles["suppliers"]),
        "entities": len(entities),
        "entities_grounded": sum(1 for n in entities if n.grounded),
        "entities_ungrounded": sum(1 for n in entities if not n.grounded),
        "entities_with_country": sum(1 for n in entities if n.country),
        "entities_with_domain": sum(1 for n in entities if n.primary_domain),
        "resolution_bases": dict(Counter(n.resolution_basis for n in entities)
                                 .most_common()),
        "edges_by_track": counts,
        "edges_total": len(graph.edges),
        "rings": by_ring,
    }


REACH_HOPS = 8


def reach_measures(graph: SharingGraph) -> dict:
    """Distinct parties reachable downstream of the seed."""
    seed = next(
        (nid for nid, n in graph.nodes.items()
         if n.type is NodeType.TARGET and (n.hop_first_seen or 0) == 0),
        None,
    )
    collected = max(
        (n.hop_first_seen or 0 for n in graph.nodes.values() if n.expanded),
        default=0,
    )
    out = {"seed": "", "collected_depth": collected, "hops": REACH_HOPS,
           "by_track": {}}
    if seed is None:
        return out
    out["seed"] = graph.nodes[seed].display_name
    named = {nid for nid, n in graph.nodes.items()
             if n.type in (NodeType.TARGET, NodeType.ENTITY)}
    for track in TRACKS:
        adjacency = flow_hops(graph, track=track)
        seen, frontier, series = {seed}, {seed}, []
        for _ in range(REACH_HOPS):
            nxt = {h.dst for nid in frontier for h in adjacency.get(nid, ())
                   if h.dst in named and h.dst not in seen}
            seen |= nxt
            frontier = nxt
            series.append(len(seen) - 1)
            if not nxt:
                break
        closes = next(
            (i + 1 for i in range(1, len(series)) if series[i] == series[i - 1]),
            None,
        )
        out["by_track"][track] = {
            "by_hop": series,
            "total": series[-1] if series else 0,
            "closes_at": closes,
        }
    return out


# --------------------------------------------------------------------------- #
# Resolvability
# --------------------------------------------------------------------------- #
def resolvability_measures(graph: SharingGraph) -> dict:
    """Why the walk stopped where it did."""
    collected = max(
        (n.hop_first_seen or 0 for n in graph.nodes.values() if n.expanded),
        default=0,
    )
    entities = [n for n in graph.nodes.values() if n.type is NodeType.ENTITY]
    causes: Counter = Counter()
    for node in entities:
        if node.expanded:
            causes["followed"] += 1
        elif (node.hop_first_seen or 0) > collected:
            causes["beyond_budget"] += 1
        elif not node.primary_domain:
            causes["no_site"] += 1
        else:
            causes["not_collected"] += 1

    generics = sorted(
        (n for n in graph.nodes.values() if n.type is NodeType.GENERIC),
        key=lambda n: n.display_name.lower(),
    )
    total = len(entities)
    out = {
        "collected_depth": collected,
        "entities": total,
        "unfollowed": total - causes["followed"],
        "unfollowed_pct": _pct(total - causes["followed"], total),
        "generic": len(generics),
        "generic_examples": [n.display_name for n in generics[:5]],
    }
    for cause in ("followed", "beyond_budget", "no_site", "not_collected"):
        out[cause] = causes[cause]
        out[f"{cause}_pct"] = _pct(causes[cause], total)
    return out


def _source_families(edge) -> set[str]:
    return {e.source.value for e in edge.evidence if not e.negative}


def corroboration(graph: SharingGraph, track: str = PERSONAL_DATA) -> dict:
    """Entity-destination edges supported by more than one source family."""
    total = corroborated = 0
    for edge in graph.edges.values():
        if edge.kind is not EdgeKind.DISCLOSES_RELATION_WITH:
            continue
        dst = graph.nodes.get(edge.dst)
        if dst is None or dst.type is not NodeType.ENTITY:
            continue
        evidence = [e for e in edge.evidence if not e.negative and e.track == track]
        if not evidence:
            continue
        total += 1
        if len({e.source.value for e in evidence}) > 1:
            corroborated += 1
    return {"edges": total, "corroborated": corroborated,
            "pct": _pct(corroborated, total)}


def chain_measures(graph: SharingGraph) -> dict:
    """Chains through four parties, per track and under both subject rules."""
    out = {}
    for track in TRACKS:
        loose = sharing_chains(graph, parties=CHAIN_PARTIES, limit=CHAIN_LIMIT,
                               track=track, subject_strict=False)
        strict = sharing_chains(graph, parties=CHAIN_PARTIES, limit=CHAIN_LIMIT,
                                track=track, subject_strict=True)
        out[track] = {
            "chains": len(loose),
            "subject_stated": len(strict),
            "fully_disclosed": sum(1 for c in loose if c.fully_disclosed),
            "limit": CHAIN_LIMIT,
            "truncated": len(loose) >= CHAIN_LIMIT,
        }
    return out


# --------------------------------------------------------------------------- #
# Corpus
# --------------------------------------------------------------------------- #
@dataclass
class DocMeasure:
    target_id: str
    doc_id: str
    role: str
    bytes: int = 0
    text_chars: int = 0
    named_any: int = 0
    named_grounded: int = 0

    @property
    def text_share(self) -> float:
        return self.text_chars / self.bytes if self.bytes else 0.0


@dataclass
class CorpusMeasures:
    targets: int = 0
    documents: int = 0
    documents_ok: int = 0
    by_role: dict = field(default_factory=dict)
    narrative: list[DocMeasure] = field(default_factory=list)

    def to_dict(self) -> dict:
        n = len(self.narrative)
        silent = sum(1 for d in self.narrative if d.named_grounded == 0)
        unnamed = sum(1 for d in self.narrative if d.named_any == 0)
        thin = sum(1 for d in self.narrative if d.text_share < TEXT_SHARE_FLOOR)
        return {
            "targets": self.targets,
            "documents": self.documents,
            "documents_ok": self.documents_ok,
            "by_role": self.by_role,
            "narrative_documents": n,
            "naming_no_grounded_party": silent,
            "naming_no_grounded_party_pct": _pct(silent, n),
            "naming_no_party_at_all": unnamed,
            "naming_no_party_at_all_pct": _pct(unnamed, n),
            "text_under_floor": thin,
            "text_under_floor_pct": _pct(thin, n),
            "text_share_floor": TEXT_SHARE_FLOOR,
            "median_text_share": round(
                statistics.median([d.text_share for d in self.narrative]), 3
            ) if n else 0.0,
        }


def corpus_measures(corpus: Corpus, target_ids=None, use_ner: bool = True,
                    progress=None) -> CorpusMeasures:
    """What the collected documents carry, and what they withhold."""
    ner_fn, _ = load_ner(enable=use_ner)
    ids = list(target_ids if target_ids is not None else corpus.list_targets())
    out = CorpusMeasures(targets=len(ids))
    roles: Counter = Counter()
    for i, tid in enumerate(ids):
        try:
            target, docs = corpus.read_manifest(tid)
        except (OSError, ValueError, KeyError):
            continue
        if progress:
            progress(i + 1, len(ids), tid)
        fp = first_party_tokens(
            [target.seed_policy_url] + [d.url for d in docs], name=target.name,
        )
        for d in docs:
            out.documents += 1
            roles[d.role] += 1
            if not d.ok:
                continue
            out.documents_ok += 1
            if d.role not in NARRATIVE_ROLES:
                continue
            raw = corpus.read_doc_html(d)
            if not raw.strip():
                continue
            doc = parse_html(raw)
            scan = scan_document(doc, ner_fn=ner_fn, role=d.role, first_party=fp)
            admitted = scan.admitted
            out.narrative.append(DocMeasure(
                target_id=tid, doc_id=d.doc_id, role=d.role,
                bytes=len(raw), text_chars=len(doc.text),
                named_any=len(admitted),
                named_grounded=sum(1 for e in admitted if e.grounded),
            ))
    out.by_role = dict(roles.most_common())
    return out


# --------------------------------------------------------------------------- #
# Specificity across the typology
# --------------------------------------------------------------------------- #
def specificity_measures(result) -> dict:
    """How the classified corpus distributes over the specificity facet."""
    from tpd.typology import Specificity

    known = {s.value for s in Specificity}
    per_doc: Counter = Counter()
    per_target: Counter = Counter()
    per_medium: Counter = Counter()
    disclosing_docs = relevant_docs = 0
    for tc in result.targets:
        target_specs: set[str] = set()
        for d in tc.docs:
            specs, media = set(), set()
            for facet in d.facets:
                medium, _, spec = facet.partition(":")
                if spec in known:
                    specs.add(spec)
                    media.add(medium)
            if d.relevant:
                relevant_docs += 1
            if specs:
                disclosing_docs += 1
            for s in specs:
                per_doc[s] += 1
            for m in media:
                per_medium[m] += 1
            target_specs |= specs
        for s in target_specs:
            per_target[s] += 1
    total_docs = sum(len(tc.docs) for tc in result.targets)
    targets_disclosing = sum(
        1 for tc in result.targets
        if any(f.partition(":")[2] in known for d in tc.docs for f in d.facets)
    )
    return {
        "documents": total_docs,
        "documents_relevant": relevant_docs,
        "documents_disclosing": disclosing_docs,
        "documents_disclosing_pct": _pct(disclosing_docs, total_docs),
        "per_document": dict(per_doc.most_common()),
        "per_target": dict(per_target.most_common()),
        "per_medium": dict(per_medium.most_common()),
        "targets": len(result.targets),
        "targets_disclosing": targets_disclosing,
        "targets_disclosing_pct": _pct(targets_disclosing, len(result.targets)),
    }


# --------------------------------------------------------------------------- #
# Vocabulary coverage
# --------------------------------------------------------------------------- #
def vocabulary_measures(graph: SharingGraph, track: str = PERSONAL_DATA) -> dict:
    """How much of the graph's population each reference table recognises."""
    from tpd import gazetteer

    reached = set()
    for edge in graph.edges.values():
        if not any(e.track == track for e in edge.evidence):
            continue
        reached.update((edge.src, edge.dst))
    entities = [n for nid, n in graph.nodes.items()
                if n.type is NodeType.ENTITY and nid in reached]
    names = [n.display_name for n in entities if n.display_name]
    gaz = {*gazetteer.COMPANIES, *gazetteer.SERVICES}
    in_gaz = sum(1 for n in names if n.lower() in gaz)
    in_kb = sum(1 for n in names if known_to_kb(n))
    return {
        "entities": len(names),
        "gazetteer_entries": len(gaz),
        "gazetteer_known": in_gaz,
        "gazetteer_known_pct": _pct(in_gaz, len(names)),
        "kb_entries": len(tracker_radar.index().entities) + len(gvl.vendors()),
        "kb_known": in_kb,
        "kb_known_pct": _pct(in_kb, len(names)),
        "tracker_radar_domains": len(tracker_radar.index().domains),
        "gvl": gvl.version(),
    }


# --------------------------------------------------------------------------- #
# The whole measurement
# --------------------------------------------------------------------------- #
def measure(
    graph: SharingGraph,
    corpus: Corpus | None = None,
    classify_result=None,
    origin: str = "",
    target_ids=None,
    use_ner: bool = True,
    progress=None,
) -> dict:
    """Every figure the findings page states."""
    out = {
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "origin": origin,
        "graph": graph_measures(graph),
        "reach": reach_measures(graph),
        "resolvability": resolvability_measures(graph),
        "corroboration": {t: corroboration(graph, track=t) for t in TRACKS},
        "chains": chain_measures(graph),
        "vocabulary": vocabulary_measures(graph),
    }
    if corpus is not None:
        out["corpus"] = corpus_measures(
            corpus, target_ids=target_ids, use_ner=use_ner, progress=progress,
        ).to_dict()
    if classify_result is not None:
        out["specificity"] = specificity_measures(classify_result)
    return out


# --------------------------------------------------------------------------- #
# The page
# --------------------------------------------------------------------------- #
def _e(value) -> str:
    return html.escape(str(value))


def _fmt(value) -> str:
    if isinstance(value, float):
        return f"{value:,.1f}"
    if isinstance(value, int):
        return f"{value:,}"
    return _e(value)


def _stat_tile(value, label, note: str = "", track: str = "") -> str:
    note_html = f'<span class="note">{_e(note)}</span>' if note else ""
    cls = f" {track}" if track else ""
    return (f'<div class="tile{cls}"><span class="value">{_fmt(value)}</span>'
            f'<span class="label">{_e(label)}</span>{note_html}</div>')


def _table(headers, rows) -> str:
    head = "".join(f"<th>{_e(h)}</th>" for h in headers)
    body = "\n".join(
        "<tr>" + "".join(f"<td>{_fmt(c)}</td>" for c in row) + "</tr>"
        for row in rows
    )
    return (f'<div class="scroll"><table><thead><tr>{head}</tr></thead>'
            f"<tbody>{body}</tbody></table></div>")


def render_html(m: dict, title: str = "Disclosure graph findings") -> str:
    """The findings page."""
    g = m["graph"]
    corpus = m.get("corpus") or {}
    spec = m.get("specificity") or {}
    vocab = m["vocabulary"]
    chains = m["chains"]
    corr = m["corroboration"][PERSONAL_DATA]

    ring_rows = [
        [f"ring {hop}", r["origins"], r["named_out_degree"]["mean"],
         r["named_out_degree"]["median"], r["named_out_degree"]["max"],
         r["disclosing_nothing_named"], r["frontier"],
         "—" if r.get("attrition_pct") is None else f"{r['attrition_pct']}%"]
        for hop, r in sorted(g["rings"].items())
    ]

    spec_rows = [[k, v, _pct(v, spec.get("documents", 0))]
                 for k, v in (spec.get("per_document") or {}).items()]

    basis_rows = [[k or "(none)", v, _pct(v, g["entities"])]
                  for k, v in list(g["resolution_bases"].items())[:10]]

    role_rows = [[k, v] for k, v in list((corpus.get("by_role") or {}).items())[:14]]

    return f"""<title>{_e(title)}</title>
<style>
{_CSS}
</style>
<article>
  <header>
    <p class="kicker">Draft findings · generated {_e(m['generated_at'])}</p>
    <h1>{_e(title)}</h1>
    <p class="lede">
        Reports key findings from crawling <code>{_e(m.get('origin') or 'one seed origin')}</code>.
    </p>
  </header>

  <section>
    <h2>What the walk reaches</h2>
    <div class="tiles">
      {_stat_tile(g['recipients'], 'parties data reaches',
                  'a disclosure names them as a recipient')}
      {_stat_tile(g['suppliers'], 'parties that supply',
                  'a disclosure names them as a source')}
      {_stat_tile(g['edges_by_track'][PERSONAL_DATA], 'personal-data arrangements',
                  track="pd")}
      {_stat_tile(g['edges_by_track'][INVENTORY], 'inventory authorisations',
                  track="inv")}
    </div>
    <p>
      The graph holds {_fmt(g['nodes'])} nodes and {_fmt(g['entities'])} named
      organisations. Of its arrangements,
      <strong>{_pct(g['edges_by_track'][INVENTORY], g['edges_total'])}%</strong>
      are inventory authorisations.
    </p>
    {_table(
        ["", "origins analysed", "named out-degree (mean)", "median", "max",
         "disclosing nothing named", "next frontier", "attrition"],
        ring_rows,
    )}
    {_attrition_note(g['rings'])}
  </section>

  <section>
    <h2>How far the data travels</h2>
    {_reach_section(m.get('reach') or {})}
  </section>

  <section>
    <h2>How far the parties could be followed</h2>
    {_resolvability_section(m.get('resolvability') or {})}
  </section>

  <section>
    <h2>How far the disclosures agree with each other</h2>
    <p>
      Of <strong>{_fmt(corr['edges'])}</strong> personal-data arrangements
      whose recipient is a named organisation,
      <strong>{_fmt(corr['corroborated'])}</strong>
      ({corr['pct']}%) are supported by more than one family of source.
    </p>
  </section>

  <section>
    <h2>Opaque pages</h2>
    {_opacity_section(corpus)}
  </section>

  <section>
    <h2>Specificity across the typology</h2>
    {_specificity_section(spec, spec_rows)}
  </section>

  <section>
    <h2>Chains through four parties</h2>
    {_chain_section(chains)}
  </section>

  <section>
    <h2>Graph naming</h2>
    <div class="tiles">
      {_stat_tile(g['entities_grounded'], 'recognised by a register')}
      {_stat_tile(g['entities_ungrounded'], 'named only by a document')}
      {_stat_tile(g['entities_with_domain'], 'resolved to a site')}
      {_stat_tile(g['entities_with_country'], 'with a headquarters country')}
    </div>
    <p>
      Of the {_fmt(vocab['entities'])} organisations the personal-data track
      reaches, the curated gazetteer's {_fmt(vocab['gazetteer_entries'])}
      entries recognise {vocab['gazetteer_known_pct']}%. Tracker Radar and the
      Global Vendor List together hold {_fmt(vocab['kb_entries'])} entries and
      recognise {vocab['kb_known_pct']}%.
    </p>
    {_table(["resolution basis", "organisations", "share (%)"], basis_rows)}
  </section>

  <section>
    <h2>Corpus composition</h2>
    {_table(["document role", "collected"], role_rows) if role_rows
     else '<p class="aside">No corpus was measured for this page.</p>'}
  </section>
</article>
"""


def _attrition_note(rings: dict) -> str:
    series = [(hop, r["attrition_pct"]) for hop, r in sorted(rings.items())
              if r.get("attrition_pct") is not None]
    if not series:
        return ""
    stated = ", ".join(f"ring {hop} → {pct}%" for hop, pct in series)
    if len(series) < 2:
        trend = (
            "Not enough data to comment on attrition."
        )
    else:
        first, last = series[0][1], series[-1][1]
        if last > first + 5:
            trend = (
                "Attrition rises with depth over the rings measured. If it "
                "keeps rising the graph approaches a limit; if it levels off "
                "the graph keeps growing."
            )
        elif last < first - 5:
            trend = (
                "Attrition falls with depth over the rings measured, which "
                "would imply continued growth."
            )
        else:
            trend = (
                "Attrition holds roughly level across the rings measured, "
                "which is consistent with a graph that continues to grow with "
                "depth."
            )
    return f"<p>Ring attrition: {stated}. {trend}</p>"


def _rings(n: int) -> str:
    return f"{n:,} ring" + ("" if n == 1 else "s")


def _reach_section(reach: dict) -> str:
    """Downstream reach by hop against depth."""
    tracks = reach.get("by_track") or {}
    if not reach.get("seed") or not tracks:
        return ('<p class="aside">No seed target was walked for this page.</p>')
    pd = tracks.get(PERSONAL_DATA, {})
    inv = tracks.get(INVENTORY, {})
    depth = max(len(t.get("by_hop") or ()) for t in tracks.values())
    rows = [
        [f"{hop} hop" + ("" if hop == 1 else "s"),
         (pd.get("by_hop") or [None] * depth)[hop - 1]
         if hop <= len(pd.get("by_hop") or ()) else "—",
         (inv.get("by_hop") or [None] * depth)[hop - 1]
         if hop <= len(inv.get("by_hop") or ()) else "—"]
        for hop in range(1, depth + 1)
    ]
    collected = reach.get("collected_depth") or 0
    closes = pd.get("closes_at")
    if closes is None:
        settles = (
            "The personal-data reachable set is still growing at the last hop "
            "measured."
        )
    elif closes > collected:
        settles = (
            f"The personal-data reachable set settles at hop {closes}. The walk "
            f"collected {_rings(collected)}, and a party outside those carries "
            f"no onward arrangements, so hop {closes} is the depth at which the "
            "collection ran out. A claim about where the sharing itself ends "
            "needs a walk collected at least that deep."
        )
    else:
        settles = (
            f"The personal-data reachable set settles at hop {closes}, within "
            f"the {_rings(collected)} the walk collected, so the settling "
            "follows from the arrangements read."
        )
    return f"""
    <div class="tiles">
      {_stat_tile(pd.get('total', 0), 'parties reached downstream', track="pd")}
      {_stat_tile(inv.get('total', 0), 'reached by inventory authorisation',
                  track="inv")}
      {_stat_tile(collected, 'rings collected')}
    </div>
    <p>
      Distinct parties reachable from <code>{_e(reach['seed'])}</code> along
      arrangements followed in the direction the data moves, counted once each
      and restricted to arrangements whose evidence sits on the track measured.
    </p>
    {_table(["", "personal data", "ad inventory"], rows)}
    <p>{settles}</p>"""


def _resolvability_section(res: dict) -> str:
    if not res or not res["entities"]:
        return ('<p class="aside">No organisations were reached, so there is '
                'nothing to report on how far they could be followed.</p>')
    generic = ""
    if res["generic"]:
        examples = ", ".join(res["generic_examples"])
        plural = "recipient is" if res["generic"] == 1 else "recipients are"
        generic = f"""
    <p>
      A further {_fmt(res['generic'])} {plural} disclosed only as a category
      ({_e(examples)}), standing for an unstated number of organisations.
    </p>"""
    return f"""
    <div class="tiles">
      {_stat_tile(res['followed'], 'followed onward',
                  'their own disclosures were read')}
      {_stat_tile(res['beyond_budget'], 'beyond the hop budget',
                  'the walk stopped first')}
      {_stat_tile(res['no_site'], 'resolved to no site',
                  'named, with nowhere to collect')}
      {_stat_tile(res['not_collected'], 'site yielded nothing',
                  'fetch or analysis returned no documents')}
    </div>
    <p>
      The walk collected {_rings(res['collected_depth'])} and named
      {_fmt(res['entities'])} organisations, of which
      {_fmt(res['unfollowed'])} ({res['unfollowed_pct']}%) were named but not
      followed.
    </p>{generic}"""


def _opacity_section(corpus: dict) -> str:
    if not corpus:
        return '<p class="aside">No corpus was measured for this page.</p>'
    return f"""
    <div class="tiles">
      {_stat_tile(corpus['narrative_documents'], 'narrative documents read')}
      {_stat_tile(str(corpus['naming_no_grounded_party_pct']) + '%',
                  'name no party a register knows')}
      {_stat_tile(str(corpus['naming_no_party_at_all_pct']) + '%',
                  'name no party at all')}
      {_stat_tile(str(corpus['text_under_floor_pct']) + '%',
                  'under 5% visible text',
                  'markup arrived, prose did not')}
    </div>
    <p>
      Of {_fmt(corpus['narrative_documents'])} policies, cookie notices,
      sub-processor pages and vendor lists collected,
      {_fmt(corpus['naming_no_party_at_all'])} name no organisation this
      pipeline can read. {_fmt(corpus['text_under_floor'])} carry visible
      text amounting to under
      {int(corpus['text_share_floor'] * 100)}% of their bytes. The median
      document is {corpus['median_text_share'] * 100:.1f}% text.
    </p>"""


def _specificity_section(spec: dict, rows) -> str:
    if not spec:
        return ('<p class="aside">The typology classifier was not run for this '
                'page, so the specificity distribution is absent rather than '
                'estimated.</p>')
    return f"""
    <p>
      Across {_fmt(spec['documents'])} collected documents,
      {_fmt(spec['documents_disclosing'])}
      ({spec['documents_disclosing_pct']}%) carry a disclosure at some level of
      specificity, and {_fmt(spec['targets_disclosing'])} of
      {_fmt(spec['targets'])} document sets ({spec['targets_disclosing_pct']}%)
      carry at least one.
    </p>
    {_table(["specificity", "documents", "share (%)"], rows)}"""


def _chain_section(chains: dict) -> str:
    pd = chains[PERSONAL_DATA]
    inv = chains[INVENTORY]
    return f"""
    <div class="tiles">
      {_stat_tile(pd['chains'], 'personal-data chains', track="pd")}
      {_stat_tile(pd['subject_stated'], 'with every onward hop stating its subject',
                  track="pd")}
      {_stat_tile(inv['chains'], 'inventory chains', track="inv")}
    </div>
    <p>
      A 'chain' passes through four organisations within a single track. For the
      second figure, we also require that data sharing arrangements explicitly
      cover data obtained from third parties.
    </p>"""


_CSS = """
:root {
  --ground: #f2f4f2;
  --surface: #ffffff;
  --ink: #12181a;
  --dim: #566165;
  --rule: #d5dbd7;
  --rule-strong: #12181a;
  /* Arrangements over people. */
  --pd: #14665a;
  --pd-wash: #e2efeb;
  /* Authorisation to resell advertising inventory. */
  --inv: #8a5712;
  --inv-wash: #f4ece0;
}
@media (prefers-color-scheme: dark) {
  :root:not([data-theme="light"]) {
    --ground: #0e1214;
    --surface: #161c1f;
    --ink: #e3e9e7;
    --dim: #8d9b9b;
    --rule: #27302f;
    --rule-strong: #576461;
    --pd: #58c0a6;
    --pd-wash: #14241f;
    --inv: #d6a355;
    --inv-wash: #241d13;
  }
}
:root[data-theme="dark"] {
  --ground: #0e1214;
  --surface: #161c1f;
  --ink: #e3e9e7;
  --dim: #8d9b9b;
  --rule: #27302f;
  --rule-strong: #576461;
  --pd: #58c0a6;
  --pd-wash: #14241f;
  --inv: #d6a355;
  --inv-wash: #241d13;
}

:root {
  --serif: ui-serif, "Iowan Old Style", "Palatino Linotype", Palatino,
    "Book Antiqua", "Source Serif 4", Cambria, serif;
  --mono: ui-monospace, "SF Mono", "JetBrains Mono", "IBM Plex Mono",
    "Roboto Mono", Menlo, Consolas, monospace;
}

* { box-sizing: border-box; }
body {
  margin: 0;
  background: var(--ground);
  color: var(--ink);
  font: 1rem/1.7 var(--serif);
  -webkit-font-smoothing: antialiased;
}
article {
  max-width: 62rem;
  margin: 0 auto;
  padding: clamp(2rem, 6vw, 4.5rem) 1.25rem 5rem;
  display: flex;
  flex-direction: column;
  gap: 3.25rem;
}

/* A margin rail carries the section's label, the way an audit report annotates
   its own findings. It folds into the flow when there is no room for it. */
section, header, footer {
  display: grid;
  grid-template-columns: 11rem minmax(0, 1fr);
  gap: 0 2.5rem;
  align-items: start;
}
section > :not(h2), header > *, footer > * { grid-column: 2; }
@media (max-width: 52rem) {
  section, header, footer { grid-template-columns: minmax(0, 1fr); gap: 0; }
  section > :not(h2), header > *, footer > * { grid-column: 1; }
}

header { border-bottom: 2px solid var(--rule-strong); padding-bottom: 2rem; }
.kicker {
  grid-column: 1 / -1;
  font: 0.7rem/1.5 var(--mono);
  letter-spacing: 0.08em;
  text-transform: uppercase;
  color: var(--pd);
  margin: 0 0 1rem;
}
h1 {
  grid-column: 1 / -1;
  font: 500 clamp(2rem, 5.5vw, 3.2rem)/1.08 var(--mono);
  letter-spacing: -0.03em;
  text-wrap: balance;
  margin: 0 0 1.25rem;
}
.lede {
  grid-column: 1 / -1;
  max-width: 42rem;
  font-size: 1.1rem;
  color: var(--dim);
  margin: 0;
}
h2 {
  grid-column: 1;
  position: sticky;
  top: 1.5rem;
  margin: 0.35rem 0 1rem;
  font: 0.72rem/1.45 var(--mono);
  letter-spacing: 0.06em;
  text-transform: uppercase;
  color: var(--dim);
  text-align: right;
}
@media (max-width: 52rem) {
  h2 {
    position: static; text-align: left;
    padding-bottom: 0.5rem; border-bottom: 1px solid var(--rule);
  }
}
p { margin: 0 0 1rem; max-width: 42rem; }
p:last-child { margin-bottom: 0; }
strong { font-weight: 600; }
code {
  font: 0.85em var(--mono);
  background: var(--surface);
  border: 1px solid var(--rule);
  padding: 0.05em 0.35em;
  border-radius: 2px;
}

/* Each limit is named rather than numbered: ten limits are a set, not a
   sequence, and the name says which one a reader has reached. */
dl.limits { margin: 0; display: flex; flex-direction: column; }
.limit {
  display: grid;
  grid-template-columns: 13rem minmax(0, 1fr);
  gap: 0.2rem 1.5rem;
  padding: 0.7rem 0;
  border-bottom: 1px solid var(--rule);
}
.limit:last-child { border-bottom: none; }
.limit dt {
  font: 0.72rem/1.6 var(--mono);
  letter-spacing: 0.02em;
  color: var(--pd);
}
.limit dd { margin: 0; font-size: 0.95rem; }
@media (max-width: 40rem) {
  .limit { grid-template-columns: minmax(0, 1fr); }
}

.tiles {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(10rem, 1fr));
  gap: 1px;
  background: var(--rule);
  border: 1px solid var(--rule);
  margin: 0 0 1.5rem;
}
.tile {
  background: var(--surface);
  padding: 1rem 0.95rem;
  display: flex;
  flex-direction: column;
  gap: 0.2rem;
  border-top: 2px solid transparent;
}
.tile.pd { border-top-color: var(--pd); background: var(--pd-wash); }
.tile.inv { border-top-color: var(--inv); background: var(--inv-wash); }
.tile .value {
  font: 500 1.75rem/1 var(--mono);
  letter-spacing: -0.02em;
  font-variant-numeric: tabular-nums;
}
.tile.pd .value { color: var(--pd); }
.tile.inv .value { color: var(--inv); }
.tile .label { font: 0.75rem/1.4 var(--mono); color: var(--dim); }
.tile .note { font: italic 0.78rem/1.4 var(--serif); color: var(--dim); }

.scroll { overflow-x: auto; margin: 0 0 1.25rem; }
table {
  border-collapse: collapse;
  width: 100%;
  min-width: 34rem;
  font: 0.8rem/1.55 var(--mono);
  font-variant-numeric: tabular-nums;
  background: var(--surface);
}
th, td {
  text-align: right;
  padding: 0.5rem 0.7rem;
  border-bottom: 1px solid var(--rule);
}
th:first-child, td:first-child { text-align: left; }
th {
  font-weight: 500;
  color: var(--dim);
  border-bottom: 1px solid var(--rule-strong);
  font-size: 0.68rem;
  text-transform: uppercase;
  letter-spacing: 0.06em;
}
tbody tr:last-child td { border-bottom: none; }

ul.declined { list-style: none; padding: 0; margin: 0 0 1.25rem; }
ul.declined li {
  padding: 0.7rem 0 0.7rem 1.6rem;
  border-bottom: 1px solid var(--rule);
  position: relative;
  font-size: 0.95rem;
  max-width: 42rem;
}
ul.declined li:last-child { border-bottom: none; }
ul.declined li::before {
  content: "\\2715";
  position: absolute;
  left: 0;
  top: 0.85rem;
  font: 0.7rem/1 var(--mono);
  color: var(--inv);
}

.aside {
  background: var(--surface);
  border: 1px solid var(--rule);
  border-left: 2px solid var(--dim);
  padding: 1rem 1.15rem;
  font-size: 0.92rem;
  color: var(--dim);
  margin: 1.5rem 0 0;
  max-width: 42rem;
}
footer {
  border-top: 1px solid var(--rule);
  padding-top: 1.5rem;
  font: 0.82rem/1.65 var(--mono);
  color: var(--dim);
}
:focus-visible { outline: 2px solid var(--pd); outline-offset: 2px; }
"""


def write_findings(
    measurements: dict, out_path: str | Path, title: str | None = None,
) -> tuple[Path, Path]:
    """Write the page and the measurements it was generated from."""
    page = Path(out_path)
    page.parent.mkdir(parents=True, exist_ok=True)
    page.write_text(
        render_html(measurements, title=title) if title
        else render_html(measurements),
        encoding="utf-8",
    )
    data = page.with_suffix(".json")
    data.write_text(json.dumps(measurements, indent=1), encoding="utf-8")
    return page, data
