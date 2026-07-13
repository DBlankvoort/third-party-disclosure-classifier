"""Shared fixtures for the test suite."""

from __future__ import annotations

import pytest


def _small_model_available() -> bool:
    try:
        import en_core_web_sm  # noqa: F401
        return True
    except ImportError:
        return False


HAVE_MODEL = _small_model_available()

requires_model = pytest.mark.skipif(
    not HAVE_MODEL, reason="spaCy model en_core_web_sm not installed"
)


@pytest.fixture(scope="session")
def poligrapher():
    """A PoliGrapher wired to the small spaCy model."""
    if not HAVE_MODEL:
        pytest.skip("spaCy model en_core_web_sm not installed")
    from tpd.poligraph.nlp import NLP
    from tpd.poligraph.poligrapher import PoliGrapher

    return PoliGrapher(nlp=NLP(model="en_core_web_sm"))
