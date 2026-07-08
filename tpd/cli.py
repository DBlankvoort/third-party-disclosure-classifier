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
    goal2_relevance,
    goal3_agreement,
    goal4_latency,
    load_relevance_gold,
    load_typology_gold,
    load_typology_gold_docs,
    write_relevance_sheet,
    write_typology_sheet,
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
