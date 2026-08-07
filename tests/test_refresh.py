"""Validate tpd.refresh."""

from __future__ import annotations

from tpd.collect.base import CollectedDoc, Corpus, Target
from tpd.refresh import (
    DocState,
    Snapshot,
    collection_degraded,
    diff_snapshots,
    load_snapshot,
    merge_snapshots,
    record_refresh,
    relation_key,
    snapshot_target,
)


def _snap(target="t", docs=(), orgs=(), rels=()):
    return Snapshot(
        target_id=target,
        docs={d.doc_id: d for d in docs},
        named_orgs=list(orgs),
        relations=list(rels),
    )


def _doc(doc_id, status=200, content="x", ok=True):
    return DocState(doc_id=doc_id, url=f"https://e.test/{doc_id}",
                    role="privacy_policy", http_status=status,
                    content_hash=content, ok=ok)


class TestDiff:
    def test_revised_document_is_a_content_change(self):
        d = diff_snapshots(_snap(docs=[_doc("a", content="v1")]),
                           _snap(docs=[_doc("a", content="v2")]))
        assert d.changed_docs == ["a"]
        assert d.content_changed
        assert not d.regressed

    def test_newly_failing_document_is_a_regression_not_a_revision(self):
        d = diff_snapshots(
            _snap(docs=[_doc("a")]),
            _snap(docs=[_doc("a", status=403, content="", ok=False)]),
        )
        assert d.broken_docs == ["a"]
        assert d.changed_docs == []
        assert d.regressed

    def test_party_additions_and_losses_are_reported(self):
        d = diff_snapshots(_snap(orgs=["criteo"]), _snap(orgs=["taboola"]))
        assert d.added_parties == ["taboola"]
        assert d.removed_parties == ["criteo"]

    def test_identical_snapshots_show_no_change(self):
        assert not diff_snapshots(_snap(orgs=["a"]), _snap(orgs=["a"])).content_changed


class TestMonotonicMerge:
    def test_degraded_collection_keeps_established_parties(self):
        old = _snap(docs=[_doc("a")], orgs=["criteo", "taboola"])
        new = _snap(docs=[_doc("a", status=403, content="", ok=False)], orgs=[])
        merged, diff = merge_snapshots(old, new)
        assert merged.named_orgs == ["criteo", "taboola"]
        assert diff.regressed

    def test_degraded_collection_keeps_the_readable_document(self):
        old = _snap(docs=[_doc("a", content="v1")])
        new = _snap(docs=[_doc("a", status=503, content="", ok=False)])
        merged, _ = merge_snapshots(old, new)
        assert merged.docs["a"].ok

    def test_healthy_collection_replaces_the_reading(self):
        old = _snap(docs=[_doc("a", content="v1")], orgs=["criteo"])
        new = _snap(docs=[_doc("a", content="v2")], orgs=["taboola"])
        merged, diff = merge_snapshots(old, new)
        # A publisher that drops a party from a policy it still serves is a
        # genuine revision, so the new reading stands.
        assert merged.named_orgs == ["taboola"]
        assert diff.removed_parties == ["criteo"]

    def test_growth_is_accepted(self):
        old = _snap(docs=[_doc("a", content="v1")], orgs=["criteo"])
        new = _snap(docs=[_doc("a", content="v2")], orgs=["criteo", "taboola"])
        merged, _ = merge_snapshots(old, new)
        assert merged.named_orgs == ["criteo", "taboola"]

    def test_degradation_detection(self):
        old = _snap(docs=[_doc("a")])
        assert collection_degraded(old, _snap(docs=[]))
        assert not collection_degraded(old, _snap(docs=[_doc("a", content="v2")]))


class TestRelationKey:
    def test_direction_distinguishes_otherwise_equal_relations(self):
        base = {"entity": "e", "data_type": "d", "action": "collect"}
        assert relation_key({**base, "direction": "upstream"}) != relation_key(
            {**base, "direction": "downstream"})


class TestPersistence:
    def _corpus(self, tmp_path):
        corpus = Corpus(tmp_path / "corpus")
        target = Target(id="website__e", type="website", name="e.test",
                        url="https://e.test")
        doc = CollectedDoc(doc_id="privacy_policy-00", url="https://e.test/p",
                           role="privacy_policy", http_status=200)
        corpus.save_doc(target.id, doc, "<html>We share with Criteo.</html>")
        corpus.write_manifest(target, [doc])
        return corpus, target

    def test_first_refresh_records_a_baseline_without_a_diff(self, tmp_path):
        corpus, target = self._corpus(tmp_path)
        assert record_refresh(corpus, target.id, ["criteo"], []) is None
        assert load_snapshot(corpus, target.id).named_orgs == ["criteo"]

    def test_second_refresh_diffs_against_the_baseline(self, tmp_path):
        corpus, target = self._corpus(tmp_path)
        record_refresh(corpus, target.id, ["criteo"], [])
        diff = record_refresh(corpus, target.id, ["criteo", "taboola"], [])
        assert diff is not None
        assert diff.added_parties == ["taboola"]

    def test_snapshot_hashes_document_content(self, tmp_path):
        corpus, target = self._corpus(tmp_path)
        snap = snapshot_target(corpus, target.id, [], [])
        assert snap.docs["privacy_policy-00"].content_hash
