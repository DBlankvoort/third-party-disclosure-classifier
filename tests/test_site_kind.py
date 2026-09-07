"""Validate how a live URL is typed and which views it carries."""

from __future__ import annotations

import pytest

from tpd.expand import Expansion, target_for_url
from tpd.site_kind import (
    default_view_for,
    graph_views_for,
    kind_for,
    profile,
    store_app_ref,
    store_target,
    supports,
    views_for,
)
from tpd.typology import TargetType


class TestStoreListings:
    def test_a_play_listing_names_its_package(self):
        assert store_app_ref(
            "https://play.google.com/store/apps/details?id=com.whatsapp&hl=en"
        ) == (TargetType.PLAY_STORE_APP.value, "com.whatsapp")

    def test_an_app_store_listing_names_its_track_id(self):
        assert store_app_ref(
            "https://apps.apple.com/us/app/whatsapp-messenger/id310633997"
        ) == (TargetType.APP_STORE_APP.value, "310633997")

    def test_a_store_page_naming_no_app_is_not_a_listing(self):
        for url in ("https://play.google.com/store/apps",
                    "https://play.google.com/store/apps/details?id=notapackage",
                    "https://apps.apple.com/us/charts"):
            assert store_app_ref(url) is None

    def test_an_ordinary_site_is_not_a_listing(self):
        assert store_app_ref("https://www.smbc-comics.com/privacy") is None

    def test_a_listing_targets_the_app_and_not_the_store(self):
        target = target_for_url(
            "https://play.google.com/store/apps/details?id=com.whatsapp")
        assert target.type == TargetType.PLAY_STORE_APP.value
        assert target.app_id == "com.whatsapp"
        assert "play-google" not in target.id

    def test_any_other_url_targets_its_own_origin(self):
        target = target_for_url("https://www.smbc-comics.com/comic/1")
        assert target.type == TargetType.WEBSITE.value
        assert target.id == "website__www-smbc-comics-com"


class TestSiteKind:
    def test_a_registered_vendor_is_vendor_side(self):
        assert kind_for("https://www.criteo.com") == TargetType.DATA_BROKER.value

    def test_a_publisher_is_an_ordinary_website(self):
        assert kind_for("https://www.smbc-comics.com") == TargetType.WEBSITE.value

    def test_a_listing_keeps_its_app_type(self):
        url = "https://play.google.com/store/apps/details?id=com.whatsapp"
        assert kind_for("https://play.google.com", target_for_url(url)) == (
            TargetType.PLAY_STORE_APP.value)

    def test_publishing_a_vendor_record_marks_a_site_vendor_side(self):
        docs = [type("D", (), {"role": "sellers_json", "ok": True})()]
        assert kind_for("https://example.invalid", docs=docs) == (
            TargetType.DATA_BROKER.value)


class TestViewPolicy:
    def test_a_publisher_carries_no_vendor_listings(self):
        views = views_for(TargetType.WEBSITE.value)
        assert "lists_vendor" not in views
        assert not supports(TargetType.WEBSITE.value, "lists_vendor")
        assert supports(TargetType.WEBSITE.value, "contacts_domain")

    def test_a_vendor_side_site_carries_them(self):
        assert supports(TargetType.DATA_BROKER.value, "lists_vendor")

    def test_an_app_carries_only_its_own_prose(self):
        for kind in (TargetType.PLAY_STORE_APP.value, TargetType.APP_STORE_APP.value):
            assert views_for(kind) == ["discloses_relation_with"]
            assert not supports(kind, "contacts_domain")
            assert not supports(kind, "authorises_inventory_sale")

    def test_an_app_opens_on_prose_rather_than_a_combined_view(self):
        kind = TargetType.PLAY_STORE_APP.value
        assert default_view_for(kind) == "discloses_relation_with"
        assert graph_views_for(kind) == ["discloses_relation_with"]

    def test_a_site_with_several_views_keeps_the_combined_one(self):
        assert graph_views_for(TargetType.WEBSITE.value)[0] == "main"

    def test_the_combined_view_is_always_supported(self):
        assert supports(TargetType.PLAY_STORE_APP.value, "main")

    def test_a_profile_reports_why_a_site_reads_as_vendor_side(self):
        assert profile("https://www.criteo.com")["site_kind_signals"]
        assert profile("https://www.smbc-comics.com")["site_kind_signals"] == []


class TestWalkRejectsViewsItCannotAnswer:
    def test_a_publisher_walk_will_not_collect_vendor_listings(self, tmp_path):
        with pytest.raises(ValueError, match="not evidence about a website"):
            Expansion(tmp_path, "https://www.smbc-comics.com",
                      evidence_kind="lists_vendor")

    def test_an_app_walk_will_not_collect_the_store_s_traffic(self, tmp_path):
        with pytest.raises(ValueError, match="not evidence about a play_store_app"):
            Expansion(tmp_path,
                      "https://play.google.com/store/apps/details?id=com.whatsapp",
                      evidence_kind="contacts_domain")

    def test_a_vendor_side_walk_may_collect_vendor_listings(self, tmp_path):
        walk = Expansion(tmp_path, "https://www.criteo.com",
                         evidence_kind="lists_vendor")
        assert walk.site_kind == TargetType.DATA_BROKER.value

    def test_a_walk_reports_its_profile_in_every_snapshot(self, tmp_path):
        walk = Expansion(tmp_path, "https://www.smbc-comics.com")
        snapshot = walk.snapshot()
        assert snapshot["site_kind"] == TargetType.WEBSITE.value
        assert "lists_vendor" not in snapshot["graph_views"]
