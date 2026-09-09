"""CLI for protocol validation, annotation export, and held-out scoring."""

from __future__ import annotations

import json
from pathlib import Path

import click

from .provenance import repository_state
from .report import markdown, score_records
from .schema import read_jsonl, validate_manifest, validate_record, write_jsonl
from .splits import leakage_violations
from .staleness import stale_reasons


def _current_sources(manifest: dict) -> dict[str, dict]:
    return {document["document_id"]: document
            for target in manifest.get("targets", [])
            for document in target.get("documents", []) if document.get("document_id")}


def _stale_errors(rows: list[dict], manifest: dict) -> list[str]:
    current = _current_sources(manifest)
    errors = []
    for row in rows:
        document_id = (row.get("source") or {}).get("document_id")
        if document_id and document_id in current:
            reasons = stale_reasons(row, current[document_id])
            if reasons:
                errors.append(f"stale {row.get('item_id')}: {', '.join(reasons)}")
    return errors


@click.group()
def cli() -> None:
    """Evidence-correctness evaluation; legacy CSV labels are never scored."""


@cli.command("validate")
@click.option("--manifest", type=click.Path(exists=True, dir_okay=False), required=True)
@click.option("--annotations", type=click.Path(exists=True, dir_okay=False))
@click.option("--final", "require_final", is_flag=True,
              help="Require two annotations and an adjudicated verdict.")
def validate_cmd(manifest: str, annotations: str | None, require_final: bool) -> None:
    """Validate schema, protocol fields, stable IDs, and split leakage."""
    data = json.loads(Path(manifest).read_text(encoding="utf-8"))
    errors = validate_manifest(data)
    for leak in leakage_violations(data.get("targets", [])):
        errors.append(f"split leakage: {leak}")
    if annotations:
        rows = read_jsonl(annotations)
        for i, row in enumerate(rows, 1):
            errors.extend(f"annotations line {i}: {e}" for e in
                          validate_record(row, allow_unadjudicated=not require_final))
        errors.extend(_stale_errors(rows, data))
    if errors:
        raise click.ClickException("\n".join(errors))
    click.echo("valid: schema and split protocol checks passed")


@cli.command("annotation-sheet")
@click.option("--candidates", type=click.Path(exists=True, dir_okay=False), required=True)
@click.option("--out", type=click.Path(dir_okay=False), required=True)
def annotation_sheet(candidates: str, out: str) -> None:
    """Create a blank double-annotation JSONL sheet from candidate records."""
    rows = read_jsonl(candidates)
    for row in rows:
        row["annotations"] = {"annotator_1": None, "annotator_2": None,
                              "adjudicated": None}
        errors = validate_record(row)
        if errors:
            raise click.ClickException(f"{row.get('item_id')}: {'; '.join(errors)}")
    write_jsonl(out, rows)
    click.echo(f"wrote {len(rows)} items to {out}")


@cli.command("score")
@click.option("--manifest", type=click.Path(exists=True, dir_okay=False), required=True)
@click.option("--annotations", type=click.Path(exists=True, dir_okay=False), required=True)
@click.option("--out", type=click.Path(dir_okay=False), required=True)
@click.option("--allow-non-test", is_flag=True,
              help="Permit development/validation scoring for diagnostics; clearly marked.")
def score_cmd(manifest: str, annotations: str, out: str, allow_non_test: bool) -> None:
    """Score adjudicated labels. Refuses legacy CSV and mixed/non-test reports by default."""
    if Path(annotations).suffix.lower() == ".csv":
        raise click.ClickException("legacy CSV labels are contaminated development artifacts")
    data = json.loads(Path(manifest).read_text(encoding="utf-8"))
    errors = validate_manifest(data)
    errors += [f"split leakage: {x}" for x in leakage_violations(data.get("targets", []))]
    rows = read_jsonl(annotations)
    for i, row in enumerate(rows, 1):
        errors += [f"line {i}: {x}" for x in validate_record(row, allow_unadjudicated=True)]
    errors += _stale_errors(rows, data)
    splits = {r.get("split") for r in rows}
    if not allow_non_test and splits - {"test"}:
        errors.append("reported performance must contain test items only")
    if errors:
        raise click.ClickException("\n".join(errors))
    provenance = {**repository_state(Path(__file__).resolve().parents[2]),
                  "corpus_snapshot_id": data["corpus_snapshot_id"],
                  "annotation_version": data["annotation_version"],
                  "random_seed": data["random_seed"],
                  "configuration": data.get("configuration", {}),
                  "browser": data.get("browser", {}),
                  "collection": data.get("collection", {}),
                  "diagnostic_non_test": bool(splits - {"test"})}
    result = score_records(rows, provenance)
    Path(out).write_text(markdown(result), encoding="utf-8")
    Path(str(out) + ".json").write_text(json.dumps(result, indent=2, sort_keys=True),
                                              encoding="utf-8")
    click.echo(f"wrote report to {out}; no acceptance threshold applied")
