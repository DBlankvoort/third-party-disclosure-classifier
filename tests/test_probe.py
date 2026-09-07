"""Validate tpd.probe and the consent state it attaches to observed traffic."""

from __future__ import annotations

import json
import time

from tpd import expand as expand_mod
from tpd import probe as probe_mod
from tpd.collect.base import Corpus
from tpd.probe import (
    POST_CONSENT,
    PRE_CONSENT,
    _is_accept,
    cached_probe,
    merge_requests,
    read_probe,
)
from tpd.sharing_graph import EdgeKind, SharingGraph, add_target, target_node_id
from tpd.traffic import consent_state, observed_hosts

ORIGIN = "https://example.com"


class TestAcceptControl:
    def test_accept_all_labels_are_recognised(self):
        for label in ("Accept all", "ACCEPT ALL COOKIES", "Alle akzeptieren",
                      "Tout accepter", "Alles accepteren", "Accept and continue"):
            assert _is_accept(label)

    def test_refusal_and_settings_controls_are_never_clicked(self):
        for label in ("Reject all", "Manage preferences", "Cookie settings",
                      "Alle ablehnen", "Customize choices", "Accept only necessary"):
            assert not _is_accept(label)

    def test_prose_is_not_a_control(self):
        assert not _is_accept(
            "We and our partners accept all responsibility for the data we "
            "process on the basis of your consent"
        )

    def test_blank_labels_are_not_controls(self):
        assert not _is_accept("   ")


class TestMergeRequests:
    def test_lists_are_unioned(self):
        merged = merge_requests(
            [{"url": "https://a.example/x.js", "type": "script"}],
            [{"url": "https://b.example/y.js", "type": "script"}],
        )
        assert {r["url"] for r in merged} == {
            "https://a.example/x.js", "https://b.example/y.js",
        }

    def test_a_pre_consent_request_stays_pre_consent(self):
        merged = merge_requests(
            [{"url": "https://a.example/x.js", "type": "script",
              "consent": POST_CONSENT}],
            [{"url": "https://a.example/x.js", "type": "script",
              "consent": PRE_CONSENT}],
        )
        assert [r["consent"] for r in merged] == [PRE_CONSENT]

    def test_unlabelled_traffic_survives(self):
        merged = merge_requests([{"url": "https://a.example/x.js", "type": "script"}])
        assert merged[0].get("consent", "") == ""


class TestConsentState:
    def test_a_party_seen_before_the_dialog_is_unconditional(self):
        assert consent_state({PRE_CONSENT, POST_CONSENT}) == PRE_CONSENT

    def test_a_party_seen_only_after_consent_is_conditional(self):
        assert consent_state({POST_CONSENT}) == POST_CONSENT

    def test_unprobed_traffic_states_nothing(self):
        assert consent_state({""}) == ""


class TestObservedConsent:
    REQUESTS = [
        {"url": "https://securepubads.g.doubleclick.net/tag.js", "type": "script",
         "consent": POST_CONSENT},
        {"url": "https://widgets.skimresources.com/s.js", "type": "script",
         "consent": PRE_CONSENT},
    ]

    def test_each_organisation_carries_its_consent_state(self):
        by_entity = {o["entity"]: o["consent"]
                     for o in observed_hosts(self.REQUESTS, ORIGIN)}
        assert by_entity == {"Google": POST_CONSENT, "Skimlinks": PRE_CONSENT}

    def test_contact_edges_carry_the_consent_state(self):
        graph = SharingGraph()
        observed = observed_hosts(self.REQUESTS, ORIGIN)
        add_target(graph, "website__example", "example.com", [], observed=observed)
        states = {
            ev.consent
            for edge in graph.edges.values()
            if edge.kind == EdgeKind.CONTACTS_DOMAIN
            and edge.src == target_node_id("website__example")
            for ev in edge.evidence
        }
        assert states == {PRE_CONSENT, POST_CONSENT}


# --------------------------------------------------------------------------- #
# The stored capture the popup and the graph share
# --------------------------------------------------------------------------- #
_PROBED = [{"url": "https://doubleclick.net/px", "type": "image",
            "consent": PRE_CONSENT}]


def _stub_probe(monkeypatch, requests=_PROBED, accepted="Accept all"):
    """Stand in for the browser launch, recording how often it happens."""
    calls: list[str] = []

    def fake(origin, **kwargs):
        calls.append(origin)
        return list(requests), accepted

    monkeypatch.setattr(probe_mod, "probe_origin", fake)
    return calls


class TestCachedProbe:
    SITE = "https://www.smbc-comics.com"

    def test_a_first_call_probes_and_stores(self, tmp_path, monkeypatch):
        calls = _stub_probe(monkeypatch)
        record = cached_probe(tmp_path, self.SITE)
        assert calls == [self.SITE]
        assert record["cached"] is False
        assert record["accepted"] == "Accept all"
        assert read_probe(tmp_path)["requests"] == _PROBED

    def test_a_second_call_reuses_the_capture(self, tmp_path, monkeypatch):
        calls = _stub_probe(monkeypatch)
        cached_probe(tmp_path, self.SITE)
        record = cached_probe(tmp_path, self.SITE)
        assert calls == [self.SITE]
        assert record["cached"] is True
        assert record["requests"] == _PROBED

    def test_a_forced_call_probes_again(self, tmp_path, monkeypatch):
        calls = _stub_probe(monkeypatch)
        cached_probe(tmp_path, self.SITE)
        cached_probe(tmp_path, self.SITE, force=True)
        assert len(calls) == 2

    def test_a_stale_capture_is_replaced(self, tmp_path, monkeypatch):
        calls = _stub_probe(monkeypatch)
        cached_probe(tmp_path, self.SITE)
        stored = json.loads((tmp_path / "traffic.json").read_text())
        stored["probed_at"] = time.time() - 10_000
        (tmp_path / "traffic.json").write_text(json.dumps(stored))
        cached_probe(tmp_path, self.SITE, max_age=3600)
        assert len(calls) == 2

    def test_an_unavailable_probe_reports_itself(self, tmp_path, monkeypatch):
        _stub_probe(monkeypatch, requests=[], accepted="")
        record = cached_probe(tmp_path, self.SITE)
        assert record["available"] is False
        assert record["requests"] == []
        # A failed launch must not be stored as though it were a result.
        assert read_probe(tmp_path) is None

    def test_a_failed_probe_falls_back_to_what_was_stored(self, tmp_path, monkeypatch):
        _stub_probe(monkeypatch)
        cached_probe(tmp_path, self.SITE)
        _stub_probe(monkeypatch, requests=[], accepted="")
        record = cached_probe(tmp_path, self.SITE, force=True)
        assert record["requests"] == _PROBED
        assert record["cached"] is True

    def test_unreadable_storage_is_not_a_capture(self, tmp_path):
        (tmp_path / "traffic.json").write_text("{not json")
        assert read_probe(tmp_path) is None


class TestSharedCapture:
    """The popup and the walk must count the same contacts."""

    SITE = "https://www.smbc-comics.com"

    def test_both_readers_share_one_capture(self, tmp_path, monkeypatch):
        calls = _stub_probe(monkeypatch)
        corpus = Corpus(tmp_path)
        target = expand_mod.target_for_origin(self.SITE)
        first = expand_mod.probed_requests(corpus, target, self.SITE)
        second = expand_mod.probed_requests(corpus, target, self.SITE)
        assert first == second == _PROBED
        assert len(calls) == 1

    def test_the_capture_sits_beside_the_target_s_documents(self, tmp_path, monkeypatch):
        _stub_probe(monkeypatch)
        corpus = Corpus(tmp_path)
        target = expand_mod.target_for_origin(self.SITE)
        expand_mod.probed_requests(corpus, target, self.SITE)
        assert (corpus.root / target.id / "traffic.json").exists()

    def test_a_session_request_joins_a_probed_one(self):
        merged = merge_requests(
            [{"url": "https://session.example/a", "type": "script"}], _PROBED,
        )
        assert {r["url"] for r in merged} == {
            "https://session.example/a", "https://doubleclick.net/px"}
