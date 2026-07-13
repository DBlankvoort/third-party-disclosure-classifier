"""Run a fine-tuned PrivBERT classifier over each policy segment."""
from __future__ import annotations

import warnings
from pathlib import Path
from typing import Dict, List, Optional

import torch

from .attributes import LABELS, MODEL_DIRNAME, THIRD_PARTY_INDEX

THRESHOLD = 0.5
MAX_LEN = 512
DEFAULT_MODELS_ROOT = Path(__file__).resolve().parent.parent.parent / "models"


class ThirdPartyClassifier:
    """Loads the fine-tuned PrivBERT model and flags third-party-sharing segments."""

    def __init__(self, models_root: Optional[Path] = None, device: Optional[str] = None):
        self.models_root = Path(models_root) if models_root else DEFAULT_MODELS_ROOT
        self.device = device or ("cuda" if torch.cuda.is_available() else "cpu")
        model_dir = self.models_root / MODEL_DIRNAME
        model_path = model_dir / "pytorch-privbert.bin"
        if not model_path.exists():
            raise FileNotFoundError(f"Missing fine-tuned model at {model_path}.")

        from transformers import AutoTokenizer

        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            self.model = torch.load(model_path, map_location=self.device, weights_only=False)
        self.model.to(self.device)
        self.model.eval()
        self.tokenizer = AutoTokenizer.from_pretrained(str(model_dir))

    @torch.no_grad()
    def _probabilities(self, segment: str) -> List[float]:
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

    def classify_segment(self, segment: str) -> Dict[str, object]:
        """Return the flat annotation dict for one segment."""
        probs = self._probabilities(segment)
        return {
            "segment_text": segment,
            "main_third": 1 if probs[THIRD_PARTY_INDEX] > THRESHOLD else 0,
        }

    def classify_policy(self, segments: List[str]) -> List[Dict[str, object]]:
        """Annotate every segment of a policy."""
        return [self.classify_segment(s) for s in segments]
