"""Validate tpd.classify.platform.parse_platform_label."""

from __future__ import annotations

from tpd.classify.platform import parse_platform_label


class TestPlayLabels:
    def test_sharing_declared(self):
        lab = parse_platform_label(
            "Data safety. This app may share these data types with third parties: "
            "Location, App activity."
        )
        assert lab.has_label and lab.shares and lab.kind == "play"

    def test_no_data_shared(self):
        lab = parse_platform_label(
            "Data safety. No data shared with third parties. "
            "The developer says this app doesn't share user data."
        )
        assert lab.has_label and not lab.shares and lab.kind == "play"

    def test_role_forces_play_parse(self):
        lab = parse_platform_label("May be shared with other companies.",
                                   role="play_data_safety")
        assert lab.has_label and lab.shares and lab.kind == "play"

    def test_affirmative_share_overrides_no_share_boilerplate(self):
        lab = parse_platform_label(
            "Data safety. This app may share these data types. "
            "The developer does not share other data."
        )
        assert lab.shares


class TestAppleLabels:
    def test_tracking_label(self):
        lab = parse_platform_label(
            "App Privacy. The developer indicated the following: "
            "Data Used to Track You."
        )
        assert lab.has_label and lab.shares and lab.kind == "apple"

    def test_label_without_sharing(self):
        lab = parse_platform_label("App Privacy. Data Not Collected.")
        assert lab.has_label and not lab.shares and lab.kind == "apple"


class TestNoLabel:
    def test_plain_text(self):
        lab = parse_platform_label("This is a privacy policy about cookies.")
        assert not lab.has_label and not lab.shares and lab.kind == ""
