"""Validate tpd.probe and the consent state it attaches to observed traffic."""

from __future__ import annotations

from tpd.probe import (
    POST_CONSENT,
    PRE_CONSENT,
    _is_accept,
    merge_requests,
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
