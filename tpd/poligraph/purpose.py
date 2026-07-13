"""Purpose phrase classification."""

from __future__ import annotations

import json
from functools import lru_cache
from importlib import resources
from typing import Optional

from .graph import Purpose


@lru_cache(maxsize=1)
def _keywords() -> dict[str, list[str]]:
    with resources.files("tpd.poligraph.data").joinpath("purpose_keywords.json").open() as f:
        spec = json.load(f)
    return {k: v for k, v in spec.items() if not k.startswith("_")}


class PurposeClassifier:
    """Multi-label purpose classifier (keyword-based by default)."""

    def __init__(self, model_dir: Optional[str] = None):
        self._model = None
        if model_dir:
            try:
                from setfit import SetFitModel
                self._model = SetFitModel.from_pretrained(model_dir)
                self._labels = list(Purpose)
            except Exception:
                self._model = None

    def classify(self, phrase: str) -> set[Purpose]:
        if self._model is not None:  # pragma: no cover
            return self._classify_model(phrase)
        return self._classify_keywords(phrase)

    def _classify_keywords(self, phrase: str) -> set[Purpose]:
        text = " " + phrase.lower() + " "
        labels: set[Purpose] = set()
        for cat, cues in _keywords().items():
            if any(cue in text for cue in cues):
                labels.add(Purpose(cat))
        if not labels:
            labels.add(Purpose.OTHER)
        return labels

    def _classify_model(self, phrase: str) -> set[Purpose]:  # pragma: no cover
        preds = self._model.predict([phrase])[0]
        labels = set()
        for lbl, val in zip(self._labels, preds):
            if val:
                labels.add(lbl)
        return labels or {Purpose.OTHER}


@lru_cache(maxsize=1)
def default_purpose_classifier() -> PurposeClassifier:
    return PurposeClassifier()
