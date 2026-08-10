"""Reading gold out of the annotation sheets."""

from __future__ import annotations

import csv

from tpd.classify.run import CorpusResult
from tpd.classify.typology_clf import DocClassification, TargetClassification
from tpd.collect.base import CollectedDoc, Corpus, Target

from tpd_eval.labeling import (
    RELEVANCE_FIELDS,
    TYPOLOGY_FIELDS,
    load_presence_doc_ids,
    load_typology_gold_by_doc,
    load_typology_gold_docs,
)
from tpd_eval.metrics import agreement, naming_rate, policy_identification

TID = "website__example"


def _sheet(path, fields, rows):
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=fields)
        w.writeheader()
        for row in rows:
            w.writerow({k: row.get(k, "") for k in fields})
    return path


def _sheets(tmp_path):
    """A target whose first document is reviewed and second is not."""
    rel = _sheet(tmp_path / "relevance_labels.csv", RELEVANCE_FIELDS, [
        {"target_id": TID, "doc_id": "privacy_policy-00", "gold_relevant": "1"},
        {"target_id": TID, "doc_id": "help_doc-01", "gold_relevant": "0"},
        {"target_id": TID, "doc_id": "ads_txt-02", "gold_relevant": ""},
    ])
    typ = _sheet(tmp_path / "typology_labels.csv", TYPOLOGY_FIELDS, [
        {"target_id": TID, "doc_id": "privacy_policy-00",
         "gold_facets": "prose:named;prose:generic"},
        {"target_id": TID, "doc_id": "help_doc-01", "gold_facets": ""},
        {"target_id": TID, "doc_id": "ads_txt-02", "gold_facets": ""},
    ])
    return rel, typ


class TestUnreviewedDocuments:
    def test_unreviewed_document_is_excluded(self, tmp_path):
        rel, typ = _sheets(tmp_path)
        assert load_typology_gold_docs(typ, reviewed_path=rel) == {
            TID: {"privacy_policy-00", "help_doc-01"}
        }

    def test_without_the_relevance_sheet_every_row_counts(self, tmp_path):
        _, typ = _sheets(tmp_path)
        assert load_typology_gold_docs(typ) == {
            TID: {"privacy_policy-00", "help_doc-01", "ads_txt-02"}
        }

    def test_reviewed_blank_row_is_an_empty_facet_set(self, tmp_path):
        rel, typ = _sheets(tmp_path)
        by_doc = load_typology_gold_by_doc(typ, reviewed_path=rel)
        assert by_doc[(TID, "help_doc-01")] == set()
        assert by_doc[(TID, "privacy_policy-00")] == {"prose:named", "prose:generic"}
        assert (TID, "ads_txt-02") not in by_doc


def _result(*docs: DocClassification) -> CorpusResult:
    tc = TargetClassification(target_id=TID, target_type="website", classified=True)
    tc.docs = list(docs)
    tc.facets = sorted({f for d in docs for f in d.facets})
    return CorpusResult(targets=[tc])


class TestAgreement:
    def test_prediction_on_an_unreviewed_document_is_not_an_error(self, tmp_path):
        rel, typ = _sheets(tmp_path)
        result = _result(
            DocClassification(doc_id="privacy_policy-00",
                              facets=["prose:generic", "prose:named"]),
            DocClassification(doc_id="ads_txt-02", facets=["machine_readable:named"]),
        )
        report = agreement(
            result,
            {TID: {"prose:named", "prose:generic"}},
            labeled_docs=load_typology_gold_docs(typ, reviewed_path=rel),
            doc_gold=load_typology_gold_by_doc(typ, reviewed_path=rel),
        )
        assert report.exact_agreement == 1.0
        assert report.n_gold_docs == 1
        assert report.doc_agreement == 1.0


class TestNamingRate:
    def test_platform_label_surfaces_leave_the_denominator(self):
        tc = TargetClassification(target_id="play_store_app__x",
                                  target_type="play_store_app")
        tc.docs = [
            DocClassification(doc_id="store_listing-00", role="store_listing"),
            DocClassification(doc_id="play_data_safety-01", role="play_data_safety"),
            DocClassification(doc_id="privacy_policy-02", role="privacy_policy",
                              named_orgs=["google analytics"]),
        ]
        report = naming_rate(CorpusResult(targets=[tc]))["app"]
        assert (report.n_named, report.n_docs) == (1, 1)


class TestPropagationStaleness:
    def test_clauses_no_longer_extracted_leave_the_review(self):
        from tpd_eval.metrics import propagation

        gold = {f"t::e{i}::personal information": True for i in range(30)}
        gold["t::vendor::access information"] = False

        report = propagation(gold)
        assert (report.n_false, report.n_reviewed) == (1, 31)
        assert not report.passed

        current = {cid for cid in gold if cid.endswith("personal information")}
        report = propagation(gold, clause_ids=current)
        assert (report.n_false, report.n_reviewed, report.n_stale) == (0, 30, 1)
        assert report.passed


class TestPolicyIdentification:
    def test_gold_doc_ids_decide(self, tmp_path):
        corpus = Corpus(tmp_path / "corpus")
        corpus.write_manifest(
            Target(id=TID, type="website"),
            [CollectedDoc(doc_id="privacy_policy-00", url="https://elsewhere/p",
                          role="privacy_policy", http_status=200,
                          raw_path=f"{TID}/docs/privacy_policy-00.html")],
        )
        presence = _sheet(tmp_path / "presence.csv",
                          ["target_id", "gold_pp_doc_ids"],
                          [{"target_id": TID, "gold_pp_doc_ids": "manual-00"}])
        gold_docs = load_presence_doc_ids(presence, "gold_pp_doc_ids")

        by_role = policy_identification(corpus, {TID: True}, "website", [TID])
        by_doc = policy_identification(corpus, {TID: True}, "website", [TID],
                                       gold_doc_ids=gold_docs)
        assert by_role.n_identified == 1
        assert by_doc.n_identified == 0
