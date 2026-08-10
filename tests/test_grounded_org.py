"""Validate tpd.classify.named_entities.grounded_org."""

from __future__ import annotations

import pytest

from tpd.classify.named_entities import grounded_org


@pytest.mark.parametrize("name", [
    "Stripe",                     # gazetteer
    "google analytics",           # gazetteer, lower-cased by the normalizer
    "amazon web services",
    "Klarna AB",                  # corporate-form suffix
    "Cognism Limited",
    "Crunchbase Inc",
    "API Hub, Inc",
    "Mastercard Europe S.A",
    "The Walt Disney Company",    # weak tail, but a name in front of it
    "doubleclick.net",            # written as an address
    "streamr.ai",
])
def test_grounded(name):
    assert grounded_org(name)


@pytest.mark.parametrize("name", [
    "advertising platform",       # category
    "advertising partner",
    "analytics provider",
    "payment processor",
    "law enforcement agency",
    "supervisory authority",
    "unspecified third party",    # generic
    "financial company",          # weak tail with a bare qualifier
    "ad company",
    "which company",
    "End Customer",               # capitalised defined term
    "Annual Report",
    "Digital Identifier",
    "Learn More",
    "",
])
def test_not_grounded(name):
    assert not grounded_org(name)
