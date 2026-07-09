"""Command line interface."""

from __future__ import annotations

import json
from collections import Counter
from pathlib import Path

import click

from .classify.run import classify_corpus
from .collect.base import Corpus
from .collect.runner import (
    collect_seeds,
    collect_stratified,
    docset_usable,
    load_seed_dir,
    load_seeds,
    usable_target_ids,
)
from .evaluate import (
    relevance,
    agreement,
    latency,
    load_relevance_gold,
    load_typology_gold,
    load_typology_gold_docs,
    write_relevance_sheet,
    write_typology_sheet,
)

# Fetch seed data
DEFAULT_SEEDS = Path(__file__).resolve().parent.parent / "data_sources"

# Roles benefitting from a JS render.
_JS_PRONE_ROLES = {
    "cookie_policy", "vendor_list", "partners_page", "store_listing",
    "play_data_safety", "subprocessor_list", "do_not_sell", "privacy_policy",
}

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
def collect(seeds_path, corpus_root, limit, per_type, workers, oversample, seed, delay, force) -> None:
    """Crawl seed targets into a corpus of document sets."""
    from .collect.disclosure import augment_disclosure_corpus

    p = Path(seeds_path)
    seeds = load_seed_dir(p) if p.is_dir() else load_seeds(p)
    click.echo(f"loaded {len(seeds)} seed targets from {seeds_path}")
    corpus = Corpus(corpus_root)

    def _progress(t, msg):
        click.echo(f"  [{t.type:14s}] {t.name or t.id:24s} {msg}")

    out = collect_stratified(
        seeds, corpus, per_type=per_type, workers=workers, delay=delay,
        force=force, seed=seed, oversample=oversample, progress=_progress,
    )
    seed_type = {s.id: s.type for s in seeds}
    usable = sum(1 for tid, docs in out.items()
                    if docset_usable(corpus, seed_type.get(tid, tid.split("__")[0]), docs))
    click.echo(f"collected {usable} USABLE targets "
                f"(of {len(out)} attempted) into {corpus_root}")

    def _progress(t, msg):
        click.echo(f"  {(t.name or t.id) if t else '?':30s} {msg}")

    ids = _eval_ids(corpus, True)
    added = augment_disclosure_corpus(corpus, target_ids=ids, force=force,
                                      delay=delay, workers=workers, progress=_progress)
    n_docs = sum(len(v) for v in added.values())
    click.echo(f"added {n_docs} disclosure document(s) across {len(added)} target(s)")


# --------------------------------------------------------------------------- #
@cli.command()
@click.option("--corpus", "corpus_root", required=True, help="existing corpus directory")
@click.option("--workers", type=int, default=8, show_default=True)
@click.option("--delay", type=float, default=0.3, show_default=True, help="per-request delay (s)")
@click.option("--force", is_flag=True, help="ignore the fetch cache")
@click.option("--include-unusable", is_flag=True, help="augment every target")
def augment(corpus_root, workers, delay, force, include_unusable) -> None:
    """Add machine-readable registries to an existing corpus."""
    from .collect.registry import augment_corpus

    corpus = Corpus(corpus_root)

    def _progress(t, msg):
        name = (t.name or t.id) if t else "?"
        click.echo(f"  {name:30s} {msg}")

    ids = _eval_ids(corpus, include_unusable)
    added = augment_corpus(corpus, target_ids=ids, force=force, delay=delay,
                           workers=workers, progress=_progress)
    n_docs = sum(len(v) for v in added.values())
    click.echo(f"added {n_docs} registry document(s) across {len(added)} target(s)")


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
    if merge_dir:
        click.echo(f"carried over hand labels + order from {merge_dir}; "
                   "fill the new blank rows, then `python -m tpd eval`.")
    else:
        click.echo("Label the first ~50 targets by label_order, fill gold_*, then "
                   "`python -m tpd eval`.")


# --------------------------------------------------------------------------- #
@cli.command(name="eval")
@click.option("--corpus", "corpus_root", required=True)
@click.option("--relevance-gold", default=None, help="hand-labelled relevance sheet")
@click.option("--typology-gold", default=None, help="hand-labelled typology sheet")
@click.option("--no-ner", is_flag=True)
@click.option("--polisis", is_flag=True)
@click.option("--workers", type=int, default=8, show_default=True,
              help="documents classified in parallel per target")
@click.option("--include-unusable", is_flag=True)
def eval_(corpus_root, relevance_gold, typology_gold, no_ner, polisis, workers,
          include_unusable) -> None:
    """Score key metrics."""
    corpus = Corpus(corpus_root)
    cache = None
    if polisis:
        from .classify.polisis_connector import load_cache

        cache = load_cache(corpus_root)
    result = classify_corpus(corpus, use_ner=not no_ner, cache=cache,
                             target_ids=_eval_ids(corpus, include_unusable), workers=workers)

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


# --------------------------------------------------------------------------- #
@cli.command(name="render")
@click.option("--corpus", "corpus_root", required=True)
@click.option("--targets", default="gold",
              help="'gold', 'all', or a CSV of target ids")
@click.option("--typology-gold", default="labels/typology_labels.csv",
              help="sheet defining the gold set")
@click.option("--roles", default=None,
              help="roles to render")
@click.option("--min-gain", type=int, default=2000,
              help="only overwrite document when the render adds at least this many chars")
@click.option("--timeout", type=int, default=30000, help="per-page render timeout (ms)")
@click.option("--limit", type=int, default=0, help="cap number of docs rendered (0 = no cap)")
def render_cmd(corpus_root, targets, typology_gold, roles, min_gain, timeout, limit) -> None:
    """Re-fetch JS-prone documents with headless Chromium."""
    from .collect.render import Renderer

    corpus = Corpus(corpus_root)
    role_set = (
        {r.strip() for r in roles.split(",") if r.strip()} if roles else _JS_PRONE_ROLES
    )
    if targets == "all":
        tids = corpus.list_targets()
    elif targets == "gold":
        tids = sorted(load_typology_gold_docs(typology_gold)) if Path(typology_gold).exists() else []
    else:
        tids = [t.strip() for t in targets.split(",") if t.strip()]
    if not tids:
        click.echo("no targets selected.")
        return

    rendered = updated = failed = 0
    with Renderer(timeout_ms=timeout) as r:
        if not r.available:
            click.echo("Playwright/browser unavailable.")
            return
        for tid in tids:
            target, docs = corpus.read_manifest(tid)
            changed = False
            for d in docs:
                if d.role not in role_set or not d.url:
                    continue
                if limit and rendered >= limit:
                    break
                rendered += 1
                html = r.render(d.url)
                if not html:
                    failed += 1
                    continue
                old = corpus.read_doc_html(d) if d.raw_path else ""
                if len(html) >= len(old) + min_gain:
                    corpus.save_doc(tid, d, html)
                    d.http_status = d.http_status or 200
                    d.error = ""
                    d.fetched_at = __import__("time").time()
                    updated += 1
                    changed = True
            if changed:
                corpus.write_manifest(target, docs)
            click.echo(f"  {tid}: rendered, {updated} doc(s) updated so far", err=True)
    click.echo(f"rendered {rendered} docs across {len(tids)} targets; "
               f"updated {updated}, failed {failed}.")


# --------------------------------------------------------------------------- #
@cli.command(name="polisis-cache")
@click.option("--corpus", "corpus_root", required=True)
@click.option("--polisis-root", default=None, help="path to polisis-reimplement")
def polisis_cache(corpus_root, polisis_root) -> None:
    """Build the POLISIS cache."""
    from .classify.polisis_connector import DEFAULT_POLISIS_ROOT, build_cache

    root = polisis_root or DEFAULT_POLISIS_ROOT
    click.echo(f"running POLISIS over corpus prose docs (root={root}) ...")
    path = build_cache(corpus_root, polisis_root=root)
    click.echo(f"wrote verdict cache -> {path}")


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


if __name__ == "__main__":
    cli()