"""Run a fine-tuned PrivBERT classifier over each policy segment."""
from __future__ import annotations

import warnings
from pathlib import Path
from typing import Dict, List, Optional

import torch

from .attributes import ATTRIBUTE_ORDER, ATTRIBUTES, model_dirname

THRESHOLD = 0.5
MAX_LEN = 512
DEFAULT_MODELS_ROOT = Path(__file__).resolve().parent.parent.parent / "models"


class _AttributeClassifier:
    """A single fine-tuned PrivBERT model + its tokenizer, for one OPP-115 attribute."""

    def __init__(self, attribute: str, model_dir: Path, device: str):
        from transformers import AutoTokenizer

        self.attribute = attribute
        self.device = device
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            self.model = torch.load(
                model_dir / "pytorch-privbert.bin",
                map_location=device,
                weights_only=False,
            )
        self.model.to(device)
        self.model.eval()
        self.tokenizer = AutoTokenizer.from_pretrained(str(model_dir))
        self.num_labels = ATTRIBUTES[attribute].num_labels

    @torch.no_grad()
    def probabilities(self, segment: str) -> List[float]:
        enc = self.tokenizer.encode_plus(
            " ".join(segment.split()),
            add_special_tokens=True,
            max_length=MAX_LEN,
            padding="max_length",
            truncation=True,
            return_token_type_ids=True,
            return_tensors="pt",
        )
        ids = enc["input_ids"].to(self.device, dtype=torch.long)
        mask = enc["attention_mask"].to(self.device, dtype=torch.long)
        tti = enc["token_type_ids"].to(self.device, dtype=torch.long)
        logits = self.model(ids, mask, tti).logits
        return torch.sigmoid(logits)[0].cpu().tolist()


def _present(probs: List[float], index: int) -> int:
    return 1 if probs[index] > THRESHOLD else 0


def annotate(main_probs: List[float], segment_text: str) -> Dict[str, object]:
    """Flatten the Main-attribute probabilities into boolean annotation keys."""
    return {
        "segment_text": segment_text,
        # Main (segment classifier) — only the three used columns
        "main_first": _present(main_probs, 0),
        "main_third": _present(main_probs, 1),
        "main_audience": _present(main_probs, 5),
    }


class HierarchicalClassifier:
    """Loads the Main-attribute PrivBERT model and annotates policy segments."""

    def __init__(self, models_root: Optional[Path] = None, device: Optional[str] = None):
        self.models_root = Path(models_root) if models_root else DEFAULT_MODELS_ROOT
        self.device = device or ("cuda" if torch.cuda.is_available() else "cpu")
        missing = [a for a in ATTRIBUTE_ORDER if not self._model_path(a).exists()]
        if missing:
            raise FileNotFoundError(
                "Missing fine-tuned models for: " + ", ".join(missing) + "."
            )
        self._classifiers: Dict[str, _AttributeClassifier] = {
            a: _AttributeClassifier(a, self._model_path(a).parent, self.device)
            for a in ATTRIBUTE_ORDER
        }

    def _model_path(self, attribute: str) -> Path:
        return self.models_root / model_dirname(attribute) / "pytorch-privbert.bin"

    def classify_segment(self, segment: str) -> Dict[str, object]:
        """Return the flat annotation dict for one segment."""
        main_probs = self._classifiers["Main"].probabilities(segment)
        return annotate(main_probs, segment)

    def classify_policy(self, segments: List[str]) -> List[Dict[str, object]]:
        """Annotate every segment of a policy."""
        return [self.classify_segment(s) for s in segments]
