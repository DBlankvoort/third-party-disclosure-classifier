from tpd_eval.metrics import (
    agreement,
    binary,
    bootstrap_ci,
    chain,
    claim_tuple,
    document_collection,
    prf,
)
from tpd_eval.schema import CLAIM_FIELDS


def row(verdict="yes", predicted="yes", task="document", **extra):
    base = {"task": task, "prediction": {"verdict": predicted},
            "annotations": {"adjudicated": {"verdict": verdict}}, "source": {"target_id": "t"}}
    base.update(extra)
    return base


def test_prf_has_raw_denominators_and_undefined_precision():
    assert prf(0, 0, 2)["precision"] is None
    assert prf(3, 1, 2)["gold_positive"] == 5


def test_unavailable_probe_is_not_a_negative():
    result = binary([row(verdict="no", predicted="no", measurement={"available": False})])
    assert result["unavailable"] == 1 and result["scored"] == 0


def test_ambiguous_is_an_abstention():
    result = binary([row(verdict="ambiguous")])
    assert result["abstained"] == 1 and result["status"] == "not_enough_labeled_data"


def test_claim_exact_requires_every_component_including_unstated():
    values = {f: "unstated" for f in CLAIM_FIELDS}
    prediction = dict(values)
    record = row(task="claim", prediction=prediction)
    record["annotations"]["adjudicated"]["values"] = values
    assert claim_tuple([record])["exact_accuracy"] == 1
    prediction["purpose"] = "advertising"
    assert claim_tuple([record])["exact_accuracy"] == 0


def test_chain_requires_every_hop_and_reports_duplicates():
    record = row(task="chain", semantic_path=["a", "b"],
                 candidate_expected=True)
    record["annotations"]["adjudicated"]["hops"] = [
        {"verdict": "yes"}, {"verdict": "no"}]
    result = chain([record, record])
    assert result["hop_precision"] == .5
    assert result["duplicate_paths"] == 1


def test_agreement_pairs_raw_with_kappa():
    records = []
    for a, b in [("yes", "yes"), ("yes", "no"), ("no", "no")]:
        r = row()
        r["annotations"].update(annotator_1={"verdict": a}, annotator_2={"verdict": b})
        records.append(r)
    result = agreement(records)
    assert result["raw_agreement"] == 2 / 3
    assert result["cohen_kappa"] is not None


def test_bootstrap_refuses_tiny_samples():
    assert bootstrap_ci([1] * 19, lambda x: sum(x) / len(x), seed=1)["status"] == "not_enough_labeled_data"


def test_document_discovery_is_not_conflated_with_fetch():
    item = row(task="document", prediction={"discovered": "no", "fetched": "yes"})
    item["annotations"]["adjudicated"]["values"] = {
        "discovered": "yes", "fetched": "yes"}
    result = document_collection([item])
    assert result["discovered"]["fn"] == 1
    assert result["fetched"]["tp"] == 1
