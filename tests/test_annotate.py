"""Tests for manual annotation."""

import csv

import pytest

from tpd.annotate.store import PRESENCE_SHEET, AnnotationStore
from tpd.collect.base import CollectedDoc, Corpus, Target
from tpd.evaluate.labeling import (
    PROPAGATION_FIELDS,
    RELEVANCE_FIELDS,
    TYPOLOGY_FIELDS,
    load_presence_gold,
    load_propagation_gold,
    load_relevance_gold,
    load_typology_gold,
)

TID = "website__example-com"
CLAUSE = f"{TID}::google::device information"


def _sheet(path, fields, rows):
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=fields)
        w.writeheader()
        for r in rows:
            w.writerow(r)


def _read_rows(path):
    with open(path, newline="", encoding="utf-8") as f:
        return [dict(r) for r in csv.DictReader(f)]


@pytest.fixture()
def store(tmp_path):
    corpus = Corpus(tmp_path / "corpus")
    target = Target(id=TID, type="website", name="example-com", url="https://example.com/")
    pp = CollectedDoc(doc_id="privacy_policy-00", url="https://example.com/privacy",
                      role="privacy_policy", http_status=200)
    ads = CollectedDoc(doc_id="ads_txt-01", url="https://example.com/ads.txt",
                       role="ads_txt", http_status=200)
    corpus.save_doc(TID, pp, "<html><body><p>We share data with Google.</p></body></html>")
    corpus.save_doc(TID, ads, "google.com, pub-123, DIRECT")
    corpus.write_manifest(target, [pp, ads])

    labels = tmp_path / "labels"
    labels.mkdir()
    base = {"label_order": "1", "target_id": TID, "target_type": "website"}
    _sheet(labels / "relevance_labels.csv", RELEVANCE_FIELDS, [
        {**base, "doc_id": d.doc_id, "role": d.role, "url": d.url,
         "predicted_relevant": "1", "gold_relevant": "", "notes": ""}
        for d in (pp, ads)
    ])
    _sheet(labels / "typology_labels.csv", TYPOLOGY_FIELDS, [
        {**base, "doc_id": d.doc_id, "role": d.role, "url": d.url,
         "predicted_doc_facets": "prose:named", "gold_facets": "", "notes": ""}
        for d in (pp, ads)
    ])
    _sheet(labels / "propagation_labels.csv", PROPAGATION_FIELDS, [
        {"label_order": "1", "clause_id": CLAUSE, "target_id": TID,
         "entity": "google", "data_type": "device information",
         "predicted_propagated": "ip address;device identifier",
         "gold_correct": "", "notes": ""},
    ])
    return AnnotationStore(tmp_path / "corpus", labels)


def test_presence_sheet_created(store):
    assert (store.labels_dir / PRESENCE_SHEET).exists()
    rows = list(csv.DictReader(open(store.labels_dir / PRESENCE_SHEET, encoding="utf-8")))
    assert [r["target_id"] for r in rows] == [TID]
    assert rows[0]["url"] == "https://example.com/"


def test_gold_round_trips_through_eval_loaders(store):
    store.save_relevance(TID, "privacy_policy-00", "1", "clear disclosure")
    store.save_relevance(TID, "ads_txt-01", "0")
    store.save_typology(TID, "privacy_policy-00", "prose:named;prose:category")
    store.save_presence(TID, gold_pp_present="1", gold_list_present="0",
                        gold_pp_doc_ids="privacy_policy-00")
    store.save_propagation(CLAUSE, "0", "over-propagates")

    assert load_relevance_gold(store.labels_dir / "relevance_labels.csv") == {
        (TID, "privacy_policy-00"): 1, (TID, "ads_txt-01"): 0,
    }
    assert load_typology_gold(store.labels_dir / "typology_labels.csv") == {
        TID: {"prose:named", "prose:category"},
    }
    assert load_presence_gold(store.labels_dir / PRESENCE_SHEET, "gold_pp_present") == {TID: True}
    assert load_presence_gold(store.labels_dir / PRESENCE_SHEET, "gold_list_present") == {TID: False}
    assert load_propagation_gold(store.labels_dir / "propagation_labels.csv") == {CLAUSE: False}


def test_typology_none_marks_touched_but_empty(store):
    store.save_relevance(TID, "privacy_policy-00", "1")
    store.save_typology(TID, "privacy_policy-00", "none")
    assert load_typology_gold(store.labels_dir / "typology_labels.csv") == {TID: set()}


def test_steps_and_progress(store):
    state = store.state()
    steps = state["targets"][0]["steps"]
    assert steps == {"docs_done": 0, "docs_total": 2, "s1": False, "s2": False,
                     "s3": False, "clauses_total": 1, "clauses_done": 0}

    store.save_relevance(TID, "privacy_policy-00", "1")
    store.save_relevance(TID, "ads_txt-01", "0")
    store.save_typology(TID, "privacy_policy-00", "prose:named")
    store.save_presence(TID, gold_pp_present="1", gold_list_present="0")
    store.save_propagation(CLAUSE, "1")

    state = store.state()
    steps = state["targets"][0]["steps"]
    assert steps["s1"] and steps["s2"] and steps["s3"]
    progress = state["progress"]
    assert progress["relevance_targets"]["done"] == 1
    assert progress["presence_website"]["done"] == 1
    assert progress["presence_app"]["done"] == 0
    assert progress["typology_docs"]["done"] == 1
    assert progress["clauses"]["done"] == 1


def test_manual_doc_lifecycle(store, monkeypatch):
    monkeypatch.setattr(
        AnnotationStore, "_fetch_manual",
        lambda self, url, render: ("<html><body><p>Missed vendor page.</p></body></html>",
                                   url, ""),
    )
    doc_id = store.add_manual_doc(TID, "https://example.com/partners")
    assert doc_id == "manual-00"

    # Appears in the sheet + detail, marked manual, and serves HTML.
    detail = store.target_detail(TID)
    by_id = {d["doc_id"]: d for d in detail["docs"]}
    assert by_id["manual-00"]["manual"] is True
    doc, html = store.doc_html(TID, "manual-00")
    assert doc is not None and "Missed vendor page" in html

    # Duplicate URLs are rejected.
    with pytest.raises(ValueError):
        store.add_manual_doc(TID, "https://example.com/partners/")
    store.save_relevance(TID, "manual-00", "1", "fetcher missed this")
    gold = load_relevance_gold(store.labels_dir / "relevance_labels.csv")
    assert gold[(TID, "manual-00")] == 1
    store.save_relevance(TID, "privacy_policy-00", "0")
    store.save_relevance(TID, "ads_txt-01", "0")
    store.save_presence(TID, gold_pp_present="1", gold_list_present="0")
    steps = store.state()["targets"][0]["steps"]
    assert steps["s1"] and not steps["s2"]
    store.save_typology(TID, "manual-00", "structured:named")
    steps = store.state()["targets"][0]["steps"]
    assert steps["s2"]
    assert load_typology_gold(store.labels_dir / "typology_labels.csv") == {
        TID: {"structured:named"},
    }

    # Gold survives regeneration of both sheets
    import tpd.annotate.store as store_mod
    for name, fields in (("relevance_labels.csv", RELEVANCE_FIELDS),
                         ("typology_labels.csv", TYPOLOGY_FIELDS)):
        fresh = [dict(r, **{k: "" for k in ("gold_relevant", "gold_facets") if k in r})
                 for r in _read_rows(store.labels_dir / name) if r.get("role") != "manual"]
        store_mod._write_sheet(store.labels_dir / name, fields, fresh)
    reloaded = AnnotationStore(store.corpus.root, store.labels_dir)
    assert load_relevance_gold(store.labels_dir / "relevance_labels.csv") == {
        (TID, "manual-00"): 1,
    }
    assert load_typology_gold(store.labels_dir / "typology_labels.csv") == {
        TID: {"structured:named"},
    }
    assert reloaded.target_detail(TID)["docs"][-1]["doc_id"] == "manual-00"

    # Removal
    reloaded.save_presence(TID, gold_pp_doc_ids="manual-00")
    reloaded.remove_manual_doc(TID, "manual-00")
    assert load_relevance_gold(reloaded.labels_dir / "relevance_labels.csv") == {}
    assert load_typology_gold(reloaded.labels_dir / "typology_labels.csv") == {}
    assert reloaded.doc_html(TID, "manual-00")[0] is None
    assert not (reloaded.corpus.root / TID / "manual" / "manual-00.html").exists()
    pres = {r["target_id"]: r for r in reloaded.presence}
    assert pres[TID]["gold_pp_doc_ids"] == ""
    with pytest.raises(ValueError):
        reloaded.remove_manual_doc(TID, "privacy_policy-00")


def test_target_detail_flags(store):
    store.save_presence(TID, gold_pp_doc_ids="privacy_policy-00",
                        gold_list_doc_ids="ads_txt-01")
    detail = store.target_detail(TID)
    by_id = {d["doc_id"]: d for d in detail["docs"]}
    assert by_id["privacy_policy-00"]["is_pp"] is True
    assert by_id["privacy_policy-00"]["is_list"] is False
    assert by_id["ads_txt-01"]["is_list"] is True
    assert by_id["ads_txt-01"]["machine_readable"] is True

    doc, html = store.doc_html(TID, "privacy_policy-00")
    assert doc is not None and "Google" in html
