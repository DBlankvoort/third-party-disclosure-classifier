"""Command line interface."""

from __future__ import annotations

import json
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
from .evaluate import (
    relevance,
    agreement,
    latency,
    naming_rate,
    policy_identification,
    structured_list_identification,
    load_relevance_gold,
    load_typology_gold,
    load_typology_gold_docs,
    load_presence_gold,
    write_relevance_sheet,
    write_typology_sheet,
    APP_TARGET_TYPES,
)

# Fetch seed data
DEFAULT_SEEDS = Path(__file__).resolve().parent.parent / "data_sources"


def _eval_ids(corpus, include_unusable: bool):
    """Target ids the metrics run over: the usable corpus by default."""
    return None if include_unusable else usable_target_ids(corpus)

@click.group()
def cli() -> None:
    """Third-party disclosure classifier."""


# --------------------------------------------------------------------------- #
@cli.command()
@click.option("--seeds", "seeds_path", default=str(DEFAULT_SEEDS), show_default=True,
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

    ids = _eval_ids(corpus, include_unusable)
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
    click.echo("\n" + latency(result).summary)

    if out_dir:
        _write_outputs(result, out_dir)
        click.echo(f"wrote per-doc + per-target CSVs to {out_dir}")


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
@cli.command()
@click.option("--corpus", "corpus_root", required=True)
@click.option("--out", "out_dir", required=True, help="directory for labelling sheets")
@click.option("--no-ner", is_flag=True)
@click.option("--workers", type=int, default=8, show_default=True,
              help="documents classified in parallel per target")
@click.option("--include-unusable", is_flag=True)
@click.option("--order-seed", type=int, default=None,
              help="seed for random target ordering")
@click.option("--merge-gold-from", "merge_dir", default=None,
              help="existing dir to merge gold labels from.")
def label(corpus_root, out_dir, no_ner, workers, include_unusable, order_seed, merge_dir) -> None:
    """Emit pre-filled hand-labelling sheets"""
    from .evaluate.labeling import DEFAULT_ORDER_SEED

    seed = DEFAULT_ORDER_SEED if order_seed is None else order_seed
    corpus = Corpus(corpus_root)
    result = classify_corpus(corpus, use_ner=not no_ner, workers=workers,
                             target_ids=_eval_ids(corpus, include_unusable))
    Path(out_dir).mkdir(parents=True, exist_ok=True)
    rel = Path(out_dir) / "relevance_labels.csv"
    typ = Path(out_dir) / "typology_labels.csv"
    prior_rel = Path(merge_dir) / "relevance_labels.csv" if merge_dir else None
    prior_typ = Path(merge_dir) / "typology_labels.csv" if merge_dir else None
    if prior_rel and not prior_rel.exists():
        prior_rel = None
    if prior_typ and not prior_typ.exists():
        prior_typ = None
    n1 = write_relevance_sheet(result, rel, order_seed=seed, prior_path=prior_rel)
    n2 = write_typology_sheet(result, typ, order_seed=seed, prior_path=prior_typ)
    click.echo(f"wrote {n1} relevance rows -> {rel} (random target order, seed={seed})")
    click.echo(f"wrote {n2} typology rows  -> {typ} (random target order, seed={seed})")


# --------------------------------------------------------------------------- #
@cli.command(name="eval")
@click.option("--corpus", "corpus_root", required=True)
@click.option("--relevance-gold", default=None, help="hand-labelled relevance sheet")
@click.option("--typology-gold", default=None, help="hand-labelled typology sheet")
@click.option("--pp-presence-gold", default=None,
              help="sheet with a target_id + gold_pp_present column")
@click.option("--list-presence-gold", default=None,
              help="sheet with a target_id + gold_list_present column")
@click.option("--no-ner", is_flag=True)
@click.option("--polisis", is_flag=True)
@click.option("--workers", type=int, default=8, show_default=True,
              help="documents classified in parallel per target")
@click.option("--include-unusable", is_flag=True)
def eval_(corpus_root, relevance_gold, typology_gold, pp_presence_gold, list_presence_gold,
          no_ner, polisis, workers, include_unusable) -> None:
    """Score key metrics."""
    corpus = Corpus(corpus_root)
    cache = None
    if polisis:
        from .classify.polisis_connector import load_cache

        cache = load_cache(corpus_root)
    ids = _eval_ids(corpus, include_unusable)
    result = classify_corpus(corpus, use_ner=not no_ner, cache=cache,
                             target_ids=ids, workers=workers)

    click.echo(latency(result).summary)

    if relevance_gold:
        gold = load_relevance_gold(relevance_gold)
        if gold:
            click.echo(relevance(result, gold).summary)
        else:
            click.echo("No filled gold_relevant rows found.")

    typology_agreement = agreement(
        result,
        load_typology_gold(typology_gold) if typology_gold else {},
        labeled_docs=load_typology_gold_docs(typology_gold) if typology_gold else None,
    )
    click.echo(typology_agreement.summary)

    # Fetching-documents KPIs (project-goals/KPI.md #8-#13).
    click.echo("")
    for report in naming_rate(result).values():
        click.echo(report.summary)

    pp_gold = load_presence_gold(pp_presence_gold, "gold_pp_present") if pp_presence_gold else {}
    list_gold = load_presence_gold(list_presence_gold, "gold_list_present") if list_presence_gold else {}
    ids_by_group = {"website": [], "app": []}
    for tc in result.targets:
        ids_by_group["app" if tc.target_type in APP_TARGET_TYPES else "website"].append(tc.target_id)
    for group, group_ids in ids_by_group.items():
        click.echo(policy_identification(corpus, pp_gold, group, target_ids=group_ids).summary)
        click.echo(structured_list_identification(corpus, list_gold, group, target_ids=group_ids).summary)


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


if __name__ == "__main__":
    cli()