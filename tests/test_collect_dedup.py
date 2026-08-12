"""De-duplication and role caps."""

from __future__ import annotations

import pytest

from tpd import lexicons
from tpd.collect import playstore, registry, web
from tpd.collect.base import Corpus, FetchResult, Target

HOME_HTML = """
<html><head><title>Example</title></head><body>
<a href="/privacy-policy">Privacy Policy</a>
<a href="/cookie-policy">Cookie Policy</a>
<a href="/subprocessors">Sub-processors</a>
<a href="/do-not-sell">Do Not Sell My Info</a>
</body></html>
"""


@pytest.fixture()
def corpus(tmp_path):
    return Corpus(tmp_path / "corpus")


def _install_fetch(monkeypatch, handler, *modules):
    def _fetch(url, cache_dir=None, timeout=15, force=False, delay=0.0):
        return handler(url)
    for mod in (web, registry, *modules):
        monkeypatch.setattr(mod, "fetch", _fetch)
    monkeypatch.setattr(registry, "warm_cache", lambda *a, **k: None)


class TestRedirectToOnePage:
    def test_rotating_body_still_deduplicates(self, corpus, monkeypatch):
        calls = {"n": 0}

        def handler(url):
            # Body differs on every fetch, so content hashing cannot collapse it.
            calls["n"] += 1
            return FetchResult(
                url=url, status=200, content_type="text/html",
                text=HOME_HTML.replace("Example", f"Example {calls['n']}"),
                final_url="https://example.com/",
            )

        _install_fetch(monkeypatch, handler)
        docs = web.collect_website(
            Target(id="website__example", type="website", url="https://example.com"),
            corpus,
        )
        assert len(docs) == 1
        assert {d.url for d in docs} == {"https://example.com/"}


class TestCompanionRoleCap:
    def test_discovered_links_capped_per_role(self, corpus, monkeypatch):
        many = "".join(
            f'<a href="/help/article-{i}">Help</a>' for i in range(12)
        )
        home = f"<html><head><title>Example</title></head><body>{many}</body></html>"

        def handler(url):
            body = home if url.rstrip("/") == "https://example.com" else (
                "<html><head><title>Article</title></head><body>"
                f"<p>Article at {url}.</p></body></html>"
            )
            return FetchResult(url=url, status=200, content_type="text/html",
                               text=body, final_url=url)

        _install_fetch(monkeypatch, handler)
        docs = web.collect_website(
            Target(id="website__example", type="website", url="https://example.com"),
            corpus,
        )
        help_docs = [d for d in docs if d.role == "help_doc"]
        assert len(help_docs) == lexicons.MAX_DOCS_PER_ROLE


class TestHelpDocDiscovery:
    @pytest.mark.parametrize("hay", [
        "Help /help",
        "Support https://support.example.com/",
        "FAQ /faq",
        "Help Center /hc/en-us/articles/360",
        "Knowledge Base /kb",
    ])
    def test_matches_entry_points(self, hay):
        pattern = dict(lexicons.LINK_DISCOVERY)["help_doc"]
        assert pattern.search(hay)

    @pytest.mark.parametrize("hay", [
        "Fellowship Support Programme /education/fellowship-support-programme",
        "ARST Support Payments /membership/payments-and-support/arst",
        "FAQ on changes to consent /practice-tools/faq-on-changes-to-consent",
    ])
    def test_ignores_content_links(self, hay):
        pattern = dict(lexicons.LINK_DISCOVERY)["help_doc"]
        assert not pattern.search(hay)


class TestConventionalPathRedirect:
    def test_policy_search_continues_past_home_redirect(self, corpus, monkeypatch):
        def handler(url):
            path = url.rstrip("/").removeprefix("https://example.com")
            if path == "/privacy":
                return FetchResult(
                    url=url, status=200, content_type="text/html",
                    text="<html><head><title>Example</title></head><body>"
                         "<p>Welcome.</p></body></html>",
                    final_url="https://example.com/")
            if path == "/privacy-policy":
                return FetchResult(
                    url=url, status=200, content_type="text/html",
                    text="<html><head><title>Privacy</title></head><body>"
                         "<p>We share data with partners.</p></body></html>",
                    final_url=url)
            return FetchResult(url=url, status=404, content_type="", text="")

        _install_fetch(monkeypatch, handler)
        docs = web.collect_website(
            Target(id="website__example", type="website", url="https://example.com"),
            corpus,
        )
        policies = [d for d in docs if d.role == "privacy_policy"]
        assert len(policies) == 1
        assert policies[0].url.endswith("/privacy-policy")

    def test_companion_probe_rejects_home_redirect(self, corpus, monkeypatch):
        def handler(url):
            if url.rstrip("/") == "https://example.com":
                return FetchResult(
                    url=url, status=200, content_type="text/html",
                    text="<html><head><title>Example</title></head><body>"
                         "<p>Welcome.</p></body></html>",
                    final_url=url)
            # Every other path is served the homepage by redirect.
            return FetchResult(
                url=url, status=200, content_type="text/html",
                text="<html><head><title>Example</title></head><body>"
                     "<p>Welcome.</p></body></html>",
                final_url="https://example.com/")

        _install_fetch(monkeypatch, handler)
        docs = web.collect_website(
            Target(id="website__example", type="website", url="https://example.com"),
            corpus,
        )
        assert docs == []

    def test_homepage_below_root_is_recognised(self, corpus, monkeypatch):
        def handler(url):
            if url.rstrip("/") == "https://example.com/us":
                return FetchResult(
                    url=url, status=200, content_type="text/html",
                    text="<html><head><title>Example</title></head><body>"
                         "<p>Welcome.</p></body></html>",
                    final_url=url)
            return FetchResult(
                url=url, status=200, content_type="text/html",
                text="<html><head><title>Example</title></head><body>"
                     "<p>Welcome.</p></body></html>",
                final_url="https://example.com/us/")

        _install_fetch(monkeypatch, handler)
        docs = web.collect_website(
            Target(id="website__example", type="website", url="https://example.com/us"),
            corpus,
        )
        assert docs == []


ADS_TXT = "google.com, pub-0000000000000000, DIRECT, f08c47fec0942fa0\n"


class TestRegistryFiles:
    def test_website_collects_ads_txt(self, corpus, monkeypatch):
        def handler(url):
            if url == "https://example.com/ads.txt":
                return FetchResult(url=url, status=200, content_type="text/plain",
                                   text=ADS_TXT, final_url=url)
            if url.rstrip("/") == "https://example.com":
                return FetchResult(url=url, status=200, content_type="text/html",
                                   text=HOME_HTML, final_url=url)
            return FetchResult(url=url, status=404, content_type="", text="")

        _install_fetch(monkeypatch, handler)
        docs = web.collect_website(
            Target(id="website__example", type="website", url="https://example.com"),
            corpus,
        )
        ads = [d for d in docs if d.role == "ads_txt"]
        assert len(ads) == 1
        assert corpus.read_doc_html(ads[0]) == ADS_TXT
        _, saved = corpus.read_manifest("website__example")
        assert "ads_txt" in {d.role for d in saved}

    def test_play_app_collects_app_ads_txt_from_developer_domain(
        self, corpus, monkeypatch
    ):
        def handler(url):
            if url == "https://dev.example/app-ads.txt":
                return FetchResult(url=url, status=200, content_type="text/plain",
                                   text=ADS_TXT, final_url=url)
            if url.startswith("https://play.google.com/"):
                return FetchResult(
                    url=url, status=200, content_type="text/html",
                    text="<html><head><title>App</title></head><body>"
                         '<a href="https://dev.example/privacy">Privacy Policy</a>'
                         "</body></html>",
                    final_url=url)
            if url == "https://dev.example/privacy":
                return FetchResult(
                    url=url, status=200, content_type="text/html",
                    text="<html><head><title>Privacy</title></head><body>"
                         "<p>We share data with partners.</p></body></html>",
                    final_url=url)
            return FetchResult(url=url, status=404, content_type="", text="")

        _install_fetch(monkeypatch, handler, playstore)
        docs = playstore.collect_play_app(
            Target(id="play_store_app__example", type="play_store_app",
                   app_id="com.example.app"),
            corpus,
        )
        roles = {d.role for d in docs}
        assert "app_ads_txt" in roles
        assert {d.url for d in docs if d.role == "app_ads_txt"} == {
            "https://dev.example/app-ads.txt"
        }


class TestEmptyBody:
    def test_policy_search_continues_past_empty_response(self, corpus, monkeypatch):
        def handler(url):
            if url.rstrip("/").endswith("/privacy"):
                return FetchResult(url=url, status=200, content_type="application/pdf",
                                   text="", final_url=url)
            if url.rstrip("/").endswith("/privacy-policy"):
                return FetchResult(
                    url=url, status=200, content_type="text/html",
                    text="<html><head><title>Privacy</title></head><body>"
                         "<p>We share data with partners.</p></body></html>",
                    final_url=url)
            return FetchResult(url=url, status=404, content_type="", text="")

        _install_fetch(monkeypatch, handler)
        docs = web.collect_website(
            Target(id="website__example", type="website", url="https://example.com"),
            corpus,
        )
        policies = [d for d in docs if d.role == "privacy_policy"]
        assert len(policies) == 1
        assert policies[0].url.endswith("/privacy-policy")
        assert corpus.read_doc_html(policies[0])
