"""Run the hierarchy of fine-tuned PrivBERT classifiers over each policy segment."""
from __future__ import annotations

import warnings
from pathlib import Path
from typing import Dict, List, Optional

import torch

from polisis.attributes import ATTRIBUTE_ORDER, ATTRIBUTES, model_dirname

THRESHOLD = 0.5
MAX_LEN = 512
DEFAULT_MODELS_ROOT = Path(__file__).resolve().parent.parent / "models"


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


def annotate(raw: Dict[str, List[float]], segment_text: str) -> Dict[str, object]:
    """Flatten per-attribute probabilities into boolean annotation keys."""
    main = raw.get("Main", [0.0] * ATTRIBUTES["Main"].num_labels)
    ident = raw.get("Identifiability", [0.0] * ATTRIBUTES["Identifiability"].num_labels)
    does = raw.get("Does or Does Not", [0.0] * ATTRIBUTES["Does or Does Not"].num_labels)
    purpose = raw.get("Purpose", [0.0] * ATTRIBUTES["Purpose"].num_labels)
    info = raw.get("Personal Information Type",
                   [0.0] * ATTRIBUTES["Personal Information Type"].num_labels)
    a1 = raw.get("Action First-Party", [0.0] * ATTRIBUTES["Action First-Party"].num_labels)
    a3 = raw.get("Action Third-Party", [0.0] * ATTRIBUTES["Action Third-Party"].num_labels)
    aud = raw.get("Audience Type", [0.0] * ATTRIBUTES["Audience Type"].num_labels)

    return {
        "segment_text": segment_text,
        # Action First-Party
        "action_first_mobile": _present(a1, 0),
        "action_first_website": _present(a1, 1),
        # Action Third-Party
        "action_third_website": _present(a3, 0),
        "action_third_see": _present(a3, 1),
        # Audience (reported for completeness; not used by the Apple label rules)
        "children": _present(aud, 0),
        # Does / Does Not
        "does": _present(does, 0),
        "does_not": _present(does, 1),
        # Identifiability
        "aggregated": _present(ident, 0),
        "identifiable": _present(ident, 1),
        # Main (segment classifier) — only the three used columns
        "main_first": _present(main, 0),
        "main_third": _present(main, 1),
        "main_audience": _present(main, 5),
        # Personal Information Type
        "computer_info": _present(info, 0),
        "contact": _present(info, 1),
        "cookies": _present(info, 2),
        "demographic": _present(info, 3),
        "financial": _present(info, 4),
        "generic": _present(info, 5),
        "health": _present(info, 6),
        "ip": _present(info, 7),
        "location": _present(info, 8),
        "personal_id": _present(info, 9),
        "social": _present(info, 10),
        "survey": _present(info, 11),
        "online_activities": _present(info, 12),
        "profile": _present(info, 13),
        "info_unspecified": _present(info, 14),
        # Purpose
        "additional": _present(purpose, 0),
        "advertising": _present(purpose, 1),
        "analytics": _present(purpose, 2),
        "basic": _present(purpose, 3),
        "legal": _present(purpose, 4),
        "marketing": _present(purpose, 5),
        "merger": _present(purpose, 6),
        "personalization": _present(purpose, 7),
        "operation": _present(purpose, 8),
        "purpose_unspecified": _present(purpose, 9),
    }


class HierarchicalClassifier:
    """Loads the eight PrivBERT models and annotates policy segments."""

    def __init__(self, models_root: Optional[Path] = None, device: Optional[str] = None,
                 gate: bool = True):
        self.models_root = Path(models_root) if models_root else DEFAULT_MODELS_ROOT
        self.device = device or ("cuda" if torch.cuda.is_available() else "cpu")
        self.gate = gate
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
        raw: Dict[str, List[float]] = {"Main": main_probs}
        addresses_collection = (main_probs[0] > THRESHOLD) or (main_probs[1] > THRESHOLD)
        if (not self.gate) or addresses_collection:
            for attribute in ATTRIBUTE_ORDER:
                if attribute == "Main":
                    continue
                raw[attribute] = self._classifiers[attribute].probabilities(segment)
        return annotate(raw, segment)

    def classify_policy(self, segments: List[str]) -> List[Dict[str, object]]:
        """Annotate every segment of a policy."""
        return [self.classify_segment(s) for s in segments]
