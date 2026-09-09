from tpd_eval.communication import communication_errors
from tpd_eval.schema import SCHEMA_VERSION, evidence_id, validate_manifest, validate_record
from tpd_eval.splits import leakage_violations
from tpd_eval.staleness import stale_reasons


def source(**changes):
    value = {"target_id": "t", "document_id": "d", "document_sha256": "aaa",
             "passage_sha256": "bbb"}
    value.update(changes)
    return value


def record(src=None):
    src = src or source()
    return {"schema_version": SCHEMA_VERSION, "item_id": evidence_id("document", src),
            "task": "document", "split": "development", "source": src,
            "prediction": {"verdict": "yes"},
            "annotations": {"annotator_1": None, "annotator_2": None,
                            "adjudicated": None}}


def test_identifier_is_stable_across_unrelated_metadata_and_order():
    a = source(url="https://example/a", graph_index=1)
    b = source(graph_index=99, url="https://example/changed")
    assert evidence_id("document", a) == evidence_id("document", b)


def test_changed_content_invalidates_label_and_identifier():
    old = record()
    current = {"document_sha256": "changed", "passage_sha256": "bbb"}
    assert "changed document_sha256" in stale_reasons(old, current)
    assert evidence_id("document", old["source"]) != evidence_id("document", source(document_sha256="changed"))


def test_chain_hop_identity_change_is_stale():
    old = record(source(hop_evidence_ids=["h1", "h2"]))
    old["task"] = "chain"
    assert stale_reasons(old, {"document_sha256": "aaa", "passage_sha256": "bbb",
                               "hop_evidence_ids": ["h1", "h3"]})[-1] == "changed hop evidence identity"


def test_cross_split_family_and_template_leakage():
    targets = [
        {"target_id": "a", "split": "development", "corporate_family": "f", "template_group": "x"},
        {"target_id": "b", "split": "test", "corporate_family": "f", "template_group": "x"},
    ]
    keys = {x["key"] for x in leakage_violations(targets)}
    assert keys == {"corporate_family", "template_group"}


def test_manifest_requires_split_provenance():
    manifest = {"schema_version": SCHEMA_VERSION, "corpus_snapshot_id": "s",
                "annotation_version": "a", "created_at": "now", "random_seed": 1,
                "targets": [{"target_id": "t", "split": "test"}]}
    errors = validate_manifest(manifest)
    assert any("selected_by" in e for e in errors)
    assert any("near_duplicate_group" in e for e in errors)


def test_bad_identity_is_rejected():
    item = record()
    item["item_id"] = "document:wrong"
    assert "item_id does not match source evidence" in validate_record(item)


def test_ui_contact_cannot_claim_data_was_transmitted():
    errors = communication_errors({"evidence_kind": "contact",
        "wording": "This company received your data", "collection_complete": False,
        "shows_incompleteness": True, "provenance_visible": True})
    assert "transfer wording requires verified_transmission" in errors


def test_verified_transmission_needs_typed_evidence():
    errors = communication_errors({"evidence_kind": "verified_transmission",
        "wording": "Data transmitted", "collection_complete": True,
        "provenance_visible": True})
    assert "verified_transmission requires typed transmission evidence" in errors
