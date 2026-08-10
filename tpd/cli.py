"""Command line interface."""

from __future__ import annotations

import json
import statistics
from collections import Counter
from pathlib import Path

import click

from .classify.run import classify_corpus
from .collect.base import Corpus
from .collect.runner import (
    load_seed_dir,
    load_seeds,
    run_collection,
    usable_target_ids,
)
from .entities import load_entity_domains


def _target_ids(corpus, include_unusable: bool):
    """Target ids a command runs over: the usable corpus by default."""
    return None if include_unusable else usable_target_ids(corpus)

@click.group()
def cli() -> None:
    """Third-party disclosure classifier."""


# --------------------------------------------------------------------------- #
@cli.command()
@click.option("--seeds", "seeds_path", required=True,
              help="seed CSV file or directory of CSVs")
@click.option("--corpus", "corpus_root", required=True, help="corpus output directory")
@click.option("--per-type", type=int, default=10,
              help="collect this many targets per type")
@click.option("--workers", type=int, default=8, show_default=True,
              help="concurrent targets")
@click.option("--oversample", type=int, default=8, show_default=True,
              help="cap attempts per type")
@click.option("--seed", type=int, default=0, show_default=True, help="shuffle seed")
@click.option("--delay", type=float, default=0.3, show_default=True, help="per-request delay (s)")
@click.option("--force", is_flag=True, help="ignore the fetch cache")
@click.option("--render-limit", type=int, default=0, help="cap number of docs re-rendered with JS")
@click.option("--no-render", is_flag=True, help="skip render")
def collect(seeds_path, corpus_root, per_type, workers, oversample, seed, delay, force,
            render_limit, no_render) -> None:
    """Crawl seed targets into a corpus of document sets."""

    def _progress(t, msg):
        name = (t.name or t.id) if t else "?"
        prefix = f"[{t.type:14s}] " if t is not None else ""
        click.echo(f"  {prefix}{name:24s} {msg}")

    p = Path(seeds_path)
    seeds = load_seed_dir(p) if p.is_dir() else load_seeds(p)
    click.echo(f"loaded {len(seeds)} seed targets from {seeds_path}")
    corpus = Corpus(corpus_root)

    report = run_collection(
        seeds, corpus, per_type=per_type, workers=workers, delay=delay,
        force=force, seed=seed, oversample=oversample,
        render=not no_render, render_limit=render_limit, progress=_progress,
    )
    click.echo(f"collected {report.usable} USABLE targets "
                f"(of {report.attempted} attempted) into {corpus_root}")
    click.echo(f"{report.registry_docs} registry document(s), "
               f"{report.disclosure_docs} disclosure document(s) back-filled")

    if no_render:
        return
    click.echo(f"rendered {report.rendered} docs across {len(corpus.list_targets())} targets; "
               f"updated {report.updated}, failed {report.failed}.")

# --------------------------------------------------------------------------- #
@cli.command()
@click.option("--corpus", "corpus_root", required=True)
@click.option("--out", "out_dir", default=None, help="write per-doc/per-target CSV here")
@click.option("--no-ner", is_flag=True, help="disable NER")
@click.option("--polisis", is_flag=True, help="use the POLISIS cache if present")
@click.option("--workers", type=int, default=8, show_default=True,
              help="classify documents in parallel. Ignored with --polisis")
@click.option("--include-unusable", is_flag=True,
              help="classify every attempted target")
@click.option("--quiet", is_flag=True, help="suppress per-target lines")
def classify(corpus_root, out_dir, no_ner, polisis, workers, include_unusable, quiet) -> None:
    """Run relevance + faceted typology classifiers over a corpus."""
    corpus = Corpus(corpus_root)
    cache = None
    if polisis:
        from .classify.polisis_connector import load_cache

        cache = load_cache(corpus_root)
        click.echo(f"polisis cache: {'loaded' if cache else 'not available'}")

    ids = _target_ids(corpus, include_unusable)
    if ids is not None:
        click.echo(f"classifying {len(ids)} usable targets "
                   f"(of {len(corpus.list_targets())} attempted)")
    result = classify_corpus(corpus, use_ner=not no_ner, cache=cache, target_ids=ids,
                             workers=workers)
    if not quiet:
        for tc in result.targets:
            click.echo(
                f"  {tc.target_id:42s} class=[{tc.typology_class}] "
                f"relevant_docs={tc.relevant_docs} classified={int(tc.classified)}"
            )
    _print_distribution(result)
    click.echo("\n" + _timing(result))

    if out_dir:
        _write_outputs(result, out_dir)
        click.echo(f"wrote per-doc + per-target CSVs to {out_dir}")


def _timing(result) -> str:
    """Wall-clock spent per document and per document set."""
    def stat(xs):
        return f"mean {statistics.mean(xs):.2f}s, max {max(xs):.2f}s" if xs else "no samples"

    return (f"timing: {len(result.doc_seconds)} doc(s) {stat(result.doc_seconds)}; "
            f"{len(result.target_seconds)} target(s) {stat(result.target_seconds)}")


def _print_distribution(result) -> None:
    media = Counter()
    facets = Counter()
    classes = Counter()
    covered = 0
    for tc in result.targets:
        covered += int(tc.classified)
        classes[tc.typology_class or "(none)"] += 1
        for f in tc.facets:
            facets[f] += 1
            media[f.split(":")[0]] += 1
    n = len(result.targets)
    click.echo(f"\ncoverage: {covered}/{n} classified")
    click.echo("medium frequency (targets):  " +
               ", ".join(f"{m}={c}" for m, c in media.most_common()))
    click.echo("facet frequency (targets):   " +
               ", ".join(f"{f}={c}" for f, c in facets.most_common()))
    click.echo("most common typology classes:")
    for cls, c in classes.most_common(8):
        click.echo(f"    {c:4d}  {cls}")

def _write_outputs(result, out_dir: str) -> None:
    import csv

    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    with open(out / "documents.csv", "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["target_id", "target_type", "doc_id", "role", "url", "medium",
                    "relevant", "facets", "named_orgs", "org_typing", "category_terms",
                    "doc_class_reason", "structural_fired", "needs_review", "review_reason"])
        for tc in result.targets:
            for d in tc.docs:
                w.writerow([tc.target_id, tc.target_type, d.doc_id, d.role, d.url, d.medium,
                            int(d.relevant), ";".join(d.facets), ";".join(d.named_orgs),
                            d.org_typing, ";".join(d.category_terms), d.doc_class_reason,
                            ";".join(d.structural_fired), int(d.needs_review), d.review_reason])
    with open(out / "targets.csv", "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["target_id", "target_type", "relevant_docs", "typology_class",
                    "facets", "classified"])
        for tc in result.targets:
            w.writerow([tc.target_id, tc.target_type, tc.relevant_docs,
                        tc.typology_class, ";".join(tc.facets), int(tc.classified)])
    (out / "summary.json").write_text(json.dumps({
        "n_targets": len(result.targets),
        "classified": sum(1 for tc in result.targets if tc.classified),
    }, indent=2), encoding="utf-8")


# --------------------------------------------------------------------------- #
@cli.command(name="polisis-cache")
@click.option("--corpus", "corpus_root", required=True)
@click.option("--models-root", default=None, help="path to POLISIS models")
def polisis_cache(corpus_root, models_root) -> None:
    """Build the POLISIS cache over every usable document in the corpus."""
    from .classify.polisis_connector import build_cache

    click.echo("running POLISIS over the full corpus doc set ...")
    path = build_cache(corpus_root, models_root=models_root)
    click.echo(f"wrote verdict cache -> {path}")


# --------------------------------------------------------------------------- #
def _target_relations(corpus, target_id, docs, first_party):
    """Every relation one target's documents support."""
    from .expand import relations_for_target

    return relations_for_target(corpus, target_id, docs, first_party)


@cli.command(name="graph")
@click.option("--corpus", "corpus_root", required=True)
@click.option("--out", "out_path", required=True, help="graph JSON output path")
@click.option("--no-ner", is_flag=True, help="disable NER")
@click.option("--include-unusable", is_flag=True)
def graph_cmd(corpus_root, out_path, no_ner, include_unusable) -> None:
    """Build the cross-target data-sharing graph from a corpus."""
    from .classify.named_entities import first_party_tokens
    from .sharing_graph import SharingGraph, add_target

    corpus = Corpus(corpus_root)
    ids = _target_ids(corpus, include_unusable) or corpus.list_targets()
    result = classify_corpus(corpus, use_ner=not no_ner, target_ids=list(ids))
    by_target = {tc.target_id: tc for tc in result.targets}

    g = SharingGraph()
    for tid in ids:
        target, docs = corpus.read_manifest(tid)
        fp_urls = [target.seed_policy_url] + [
            d.url for d in docs
            if d.role in ("privacy_policy", "cookie_policy", "do_not_sell")
        ]
        first_party = first_party_tokens(fp_urls, name=target.name)
        rels = _target_relations(corpus, tid, docs, first_party)
        add_target(g, tid, target.name, rels, target_type=target.type)
        tc = by_target.get(tid)
        if tc:
            for doc in tc.docs:
                for org in doc.named_orgs:
                    add_target(g, tid, target.name,
                               [{"entity": org, "party": "third",
                                 "unspecified": False, "data_type": "personal data",
                                 "action": "be_shared", "negative": False,
                                 "direction": "downstream", "purposes": [],
                                 "sources": ["policy"], "text": "",
                                 "doc_ids": [doc.doc_id]}],
                               target_type=target.type)

    g.save(out_path)
    entities = sum(1 for n in g.nodes.values() if n.type.value == "entity")
    unexpanded = sum(1 for nid in g.nodes if g.termination(nid) == "unexpanded")
    click.echo(f"graph: {len(g.nodes)} nodes ({entities} entities), "
               f"{len(g.edges)} edges, {unexpanded} unexpanded leaves")
    click.echo(f"wrote {out_path}")


@cli.command(name="expand")
@click.option("--url", required=True, help="origin to walk outward from")
@click.option("--corpus", "corpus_root", required=True)
@click.option("--out", "out_path", required=True, help="graph JSON output path")
@click.option("--hops", type=int, default=1, show_default=True,
              help="how many rings of onward sharing to collect (1-3)")
@click.option("--entity-domains", "domains_path", default=None,
              help="hand-filled entity_resolution.csv supplying organisation domains")
@click.option("--delay", type=float, default=0.2, show_default=True,
              help="polite per-request delay (s)")
@click.option("--force", is_flag=True, help="ignore the fetch cache")
def expand_cmd(url, corpus_root, out_path, hops, domains_path, delay, force) -> None:
    """Walk outward from one URL, collecting each party it shares with."""
    from .expand import Expansion

    overrides = load_entity_domains(domains_path) if domains_path else {}
    exp = Expansion(corpus_root, url, hops=hops, delay=delay, force=force,
                    overrides=overrides)
    click.echo(f"walking {exp.origin} to {exp.hops} hop(s) ...")
    exp.run()
    exp.graph.save(out_path)
    entities = sum(1 for n in exp.graph.nodes.values() if n.type.value == "entity")
    click.echo(f"graph: {len(exp.graph.nodes)} nodes ({entities} entities), "
               f"{len(exp.graph.edges)} edges, {exp.progress.crawled} origin(s) collected")
    if exp.unresolved:
        click.echo(f"{len(exp.unresolved)} party name(s) resolved to no site")
    click.echo(f"wrote {out_path}")


@cli.command(name="chains")
@click.option("--graph", "graph_path", required=True, help="graph JSON to read")
@click.option("--parties", type=int, default=4, show_default=True,
              help="organisations a chain must pass through")
@click.option("--limit", type=int, default=1000, show_default=True,
              help="stop enumerating after this many chains")
def chains_cmd(graph_path, parties, limit) -> None:
    """List the onward-sharing chains a graph contains."""
    from .sharing_graph import SharingGraph, sharing_chains

    g = SharingGraph.load(graph_path)
    found = sharing_chains(g, parties=parties, limit=limit)

    def label(nid):
        node = g.nodes.get(nid)
        return node.display_name if node and node.display_name else nid

    for chain in found[:40]:
        marks = "".join("~" if h.traffic_only else "-" for h in chain.hops)
        click.echo(f"  [{marks}] " + " -> ".join(label(p) for p in chain.parties))
    disclosed = sum(1 for c in found if c.fully_disclosed)
    click.echo(f"\n{len(found)} chain(s) through {parties} parties; "
               f"{disclosed} rest wholly on written disclosure "
               f"(~ marks a hop evidenced only by observed traffic)")


@cli.command(name="refresh")
@click.option("--corpus", "corpus_root", required=True)
@click.option("--no-ner", is_flag=True, help="disable NER")
@click.option("--include-unusable", is_flag=True)
def refresh_cmd(corpus_root, no_ner, include_unusable) -> None:
    """Snapshot the corpus and report what changed since the last run."""
    from .classify.named_entities import first_party_tokens
    from .refresh import append_log, record_refresh

    corpus = Corpus(corpus_root)
    ids = _target_ids(corpus, include_unusable) or corpus.list_targets()
    result = classify_corpus(corpus, use_ner=not no_ner, target_ids=list(ids))
    by_target = {tc.target_id: tc for tc in result.targets}

    diffs = []
    for tid in ids:
        target, docs = corpus.read_manifest(tid)
        fp_urls = [target.seed_policy_url] + [
            d.url for d in docs
            if d.role in ("privacy_policy", "cookie_policy", "do_not_sell")
        ]
        first_party = first_party_tokens(fp_urls, name=target.name)
        rels = _target_relations(corpus, tid, docs, first_party)
        tc = by_target.get(tid)
        orgs = sorted({o for d in tc.docs for o in d.named_orgs}) if tc else []
        diff = record_refresh(corpus, tid, orgs, rels)
        diffs.append(diff)
        if diff is not None and diff.content_changed:
            flag = "REGRESSED" if diff.regressed else "changed"
            click.echo(
                f"  {tid:42s} {flag}: "
                f"+{len(diff.added_parties)}/-{len(diff.removed_parties)} parties, "
                f"{len(diff.changed_docs)} doc(s) revised, "
                f"{len(diff.broken_docs)} doc(s) now failing"
            )

    baselines = sum(1 for d in diffs if d is None)
    changed = [d for d in diffs if d is not None and d.content_changed]
    click.echo(f"\n{len(diffs)} target(s): {baselines} new baseline(s), "
               f"{len(changed)} changed, "
               f"{sum(1 for d in changed if d.regressed)} regressed")
    click.echo(f"wrote {append_log(corpus, diffs)}")


if __name__ == "__main__":
    cli()