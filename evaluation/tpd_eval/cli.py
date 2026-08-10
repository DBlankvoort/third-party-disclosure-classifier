"""Command line interface for the evaluation harness."""

from __future__ import annotations

from pathlib import Path

import click
from tpd.classify.run import classify_corpus
from tpd.collect.base import Corpus
from tpd.collect.runner import usable_target_ids
from tpd.entities import load_entity_domains
from tpd.tracks import PERSONAL_DATA

from . import (
    APP_TARGET_TYPES,
    CHAIN_PARTIES,
    agreement,
    arrangement_coverage,
    chain_verification,
    detected_arrangements,
    distinct_data_type_clauses,
    latency,
    load_chain_gold,
    load_coverage_gold,
    load_presence_doc_ids,
    load_presence_gold,
    load_propagation_gold,
    load_relevance_gold,
    load_typology_gold,
    load_typology_gold_by_doc,
    load_typology_gold_docs,
    naming_rate,
    ontology_accommodation,
    policy_identification,
    propagation,
    relevance,
    sample_targets,
    structured_list_identification,
    write_chain_sheet,
    write_coverage_sheet,
    write_entity_resolution_sheet,
    write_propagation_sheet,
    write_relevance_sheet,
    write_typology_sheet,
)


def _scored_ids(corpus, include_unusable: bool):
    """Target ids the metrics run over: the usable corpus by default."""
    return None if include_unusable else usable_target_ids(corpus)


@click.group()
def cli() -> None:
    """Evaluation harness for the third-party disclosure classifier."""


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
@click.option("--graph", "graph_path", default=None,
              help="graph JSON for the chain and entity-resolution sheets")
def label(corpus_root, out_dir, no_ner, workers, include_unusable, order_seed, merge_dir,
          graph_path) -> None:
    """Emit pre-filled hand-labelling sheets"""
    from tpd.classify.poligraph_connector import corpus_relations, poligraph_available

    from .labeling import DEFAULT_ORDER_SEED

    seed = DEFAULT_ORDER_SEED if order_seed is None else order_seed
    corpus = Corpus(corpus_root)
    ids = _scored_ids(corpus, include_unusable)
    result = classify_corpus(corpus, use_ner=not no_ner, workers=workers, target_ids=ids)
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

    if poligraph_available():
        prop = Path(out_dir) / "propagation_labels.csv"
        prior_prop = Path(merge_dir) / "propagation_labels.csv" if merge_dir else None
        if prior_prop and not prior_prop.exists():
            prior_prop = None
        relations_by_target = corpus_relations(corpus, target_ids=ids)
        n3 = write_propagation_sheet(relations_by_target, prop, order_seed=seed,
                                     prior_path=prior_prop)
        click.echo(f"wrote {n3} propagation rows -> {prop} (random order, seed={seed})")

        cov = Path(out_dir) / "coverage_labels.csv"
        prior_cov = Path(merge_dir) / "coverage_labels.csv" if merge_dir else None
        if prior_cov and not prior_cov.exists():
            prior_cov = None
        sample = sample_targets(ids or corpus.list_targets(), order_seed=seed)
        n4 = write_coverage_sheet(relations_by_target, cov, sample, order_seed=seed,
                                  prior_path=prior_cov)
        click.echo(f"wrote {n4} coverage rows -> {cov} for {len(sample)} sampled "
                   f"target(s): {', '.join(sample)}")

    if graph_path:
        from tpd.sharing_graph import SharingGraph, sharing_chains

        g = SharingGraph.load(graph_path)
        chains_path = Path(out_dir) / "chain_labels.csv"
        prior_chains = Path(merge_dir) / "chain_labels.csv" if merge_dir else None
        if prior_chains and not prior_chains.exists():
            prior_chains = None
        found = sharing_chains(g, parties=CHAIN_PARTIES, track=PERSONAL_DATA)
        n5 = write_chain_sheet(found, g, chains_path, order_seed=seed,
                               prior_path=prior_chains)
        stated = sum(1 for c in found if c.subject_stated)
        click.echo(f"wrote {n5} personal-data chain rows -> {chains_path} "
                   f"({CHAIN_PARTIES} parties per chain; {stated} state whose "
                   f"data every onward hop covers)")

        res_path = Path(out_dir) / "entity_resolution.csv"
        prior_res = Path(merge_dir) / "entity_resolution.csv" if merge_dir else None
        if prior_res and not prior_res.exists():
            prior_res = None
        overrides = load_entity_domains(prior_res) if prior_res else {}
        n6 = write_entity_resolution_sheet(g, res_path, overrides=overrides,
                                           prior_path=prior_res)
        click.echo(f"wrote {n6} entity rows -> {res_path}")


# --------------------------------------------------------------------------- #
@cli.command(name="score")
@click.option("--corpus", "corpus_root", required=True)
@click.option("--relevance-gold", default=None, help="hand-labelled relevance sheet")
@click.option("--typology-gold", default=None, help="hand-labelled typology sheet")
@click.option("--pp-presence-gold", default=None,
              help="sheet with a target_id + gold_pp_present column")
@click.option("--list-presence-gold", default=None,
              help="sheet with a target_id + gold_list_present column")
@click.option("--propagation-gold", default=None,
              help="hand-reviewed propagation_labels.csv")
@click.option("--chain-gold", default=None,
              help="hand-verified chain_labels.csv")
@click.option("--coverage-gold", default=None,
              help="hand-labelled coverage_labels.csv")
@click.option("--graph", "graph_path", default=None,
              help="graph JSON the chain gold was written against")
@click.option("--no-ner", is_flag=True)
@click.option("--polisis", is_flag=True)
@click.option("--workers", type=int, default=8, show_default=True,
              help="documents classified in parallel per target")
@click.option("--include-unusable", is_flag=True)
def score(corpus_root, relevance_gold, typology_gold, pp_presence_gold, list_presence_gold,
          propagation_gold, chain_gold, coverage_gold, graph_path, no_ner, polisis,
          workers, include_unusable) -> None:
    """Score key metrics."""
    corpus = Corpus(corpus_root)
    cache = None
    if polisis:
        from tpd.classify.polisis_connector import load_cache

        cache = load_cache(corpus_root)
    ids = _scored_ids(corpus, include_unusable)
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
        labeled_docs=(
            load_typology_gold_docs(typology_gold, reviewed_path=relevance_gold)
            if typology_gold else None
        ),
        doc_gold=(
            load_typology_gold_by_doc(typology_gold, reviewed_path=relevance_gold)
            if typology_gold else None
        ),
    )
    click.echo(typology_agreement.summary)

    # Fetching-documents KPIs (project-goals/KPI.md #8-#13).
    click.echo("")
    for report in naming_rate(result).values():
        click.echo(report.summary)

    pp_gold = load_presence_gold(pp_presence_gold, "gold_pp_present") if pp_presence_gold else {}
    list_gold = load_presence_gold(list_presence_gold, "gold_list_present") if list_presence_gold else {}
    pp_docs = load_presence_doc_ids(pp_presence_gold, "gold_pp_doc_ids") if pp_presence_gold else {}
    list_docs = load_presence_doc_ids(list_presence_gold, "gold_list_doc_ids") if list_presence_gold else {}
    ids_by_group = {"website": [], "app": []}
    for tc in result.targets:
        ids_by_group["app" if tc.target_type in APP_TARGET_TYPES else "website"].append(tc.target_id)
    for group, group_ids in ids_by_group.items():
        click.echo(policy_identification(corpus, pp_gold, group, target_ids=group_ids,
                                         gold_doc_ids=pp_docs).summary)
        click.echo(structured_list_identification(corpus, list_gold, group, target_ids=group_ids,
                                                  gold_doc_ids=list_docs).summary)

    # Data-sharing ontology KPIs
    from tpd.classify.poligraph_connector import corpus_relations, poligraph_available

    if poligraph_available():
        click.echo("")
        relations_by_target = corpus_relations(corpus, target_ids=ids)
        click.echo(ontology_accommodation(relations_by_target).summary)
        if propagation_gold:
            gold = load_propagation_gold(propagation_gold)
            if gold:
                current = {
                    f"{c['target_id']}::{c['entity']}::{c['data_type']}"
                    for c in distinct_data_type_clauses(relations_by_target)
                }
                click.echo(propagation(gold, clause_ids=current).summary)
            else:
                click.echo("No filled gold_correct rows found.")

        if coverage_gold:
            gold_arrangements = load_coverage_gold(coverage_gold)
            if gold_arrangements:
                detected = set(detected_arrangements(relations_by_target))
                click.echo(arrangement_coverage(gold_arrangements, detected).summary)
            else:
                click.echo("No filled gold_arrangement rows found.")

    if chain_gold:
        if not graph_path:
            click.echo("--chain-gold needs --graph to name the chains it verifies.")
        else:
            from tpd.sharing_graph import SharingGraph, sharing_chains

            g = SharingGraph.load(graph_path)
            gold_chains = load_chain_gold(chain_gold)
            if gold_chains:
                click.echo(chain_verification(
                    sharing_chains(g, parties=CHAIN_PARTIES,
                                   track=PERSONAL_DATA), gold_chains).summary)
            else:
                click.echo("No filled gold_verified rows found.")


# --------------------------------------------------------------------------- #
@cli.command()
@click.option("--corpus", "corpus_root", required=True)
@click.option("--labels", "labels_dir", required=True,
              help="sheet directory produced by `tpd-eval label` (gold is written back here)")
@click.option("--host", default="127.0.0.1", show_default=True)
@click.option("--port", type=int, default=8765, show_default=True)
def annotate(corpus_root, labels_dir, host, port) -> None:
    """Serve the gold-labelling interface."""
    from .annotate import run_server

    run_server(corpus_root, labels_dir, host=host, port=port)


# --------------------------------------------------------------------------- #
@cli.command(name="findings")
@click.option("--graph", "graph_path", required=True, help="graph JSON to read")
@click.option("--corpus", "corpus_root", default=None,
              help="corpus the graph was walked from, for the opacity measures")
@click.option("--out", "out_path", required=True, help="HTML page to write")
@click.option("--origin", default="", help="the seed the walk started from")
@click.option("--title", default=None, help="page title")
@click.option("--no-ner", is_flag=True)
@click.option("--no-classify", is_flag=True,
              help="skip the typology pass, leaving the specificity section absent")
@click.option("--workers", type=int, default=8, show_default=True)
@click.option("--include-unusable", is_flag=True)
def findings_cmd(graph_path, corpus_root, out_path, origin, title, no_ner,
                 no_classify, workers, include_unusable) -> None:
    """Measure a walk and write the findings page it supports."""
    from tpd.sharing_graph import SharingGraph

    from .findings import measure, write_findings

    graph = SharingGraph.load(graph_path)
    corpus = Corpus(corpus_root) if corpus_root else None
    ids = _scored_ids(corpus, include_unusable) if corpus else None

    result = None
    if corpus is not None and not no_classify:
        click.echo("classifying the corpus for the specificity distribution ...")
        result = classify_corpus(corpus, use_ner=not no_ner,
                                 target_ids=list(ids) if ids else None,
                                 workers=workers)

    def _progress(i, n, tid):
        if i % 10 == 0 or i == n:
            click.echo(f"  measured {i}/{n} document sets ({tid})")

    measurements = measure(
        graph, corpus=corpus, classify_result=result, origin=origin,
        target_ids=ids, use_ner=not no_ner, progress=_progress,
    )
    page, data = write_findings(measurements, out_path, title=title)
    click.echo(f"wrote {page}")
    click.echo(f"wrote {data}")


if __name__ == "__main__":
    cli()
