"""De-duplication and role caps."""

from __future__ import annotations

import pytest

from tpd import lexicons
from tpd.collect import web
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


def _install_fetch(monkeypatch, handler):
    def _fetch(url, cache_dir=None, timeout=15, force=False, delay=0.0):
        return handler(url)
    monkeypatch.setattr(web, "fetch", _fetch)


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
