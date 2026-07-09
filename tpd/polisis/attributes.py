"""Relevant OPP-115 classifier."""
from dataclasses import dataclass
from typing import List


@dataclass(frozen=True)
class Attribute:
    name: str            # == model directory under models/ and OPP-115 attribute
    values: List[str]    # index i == logit/sigmoid column i

    @property
    def num_labels(self) -> int:
        return len(self.values)


# DO NOT REORDER
ATTRIBUTES = {
    "Main": Attribute("Main", [
        "First Party Collection/Use",            # 0
        "Third Party Sharing/Collection",        # 1
        "User Access, Edit and Deletion",        # 2
        "Data Retention",                        # 3
        "Data Security",                         # 4
        "International and Specific Audiences",  # 5
        "Do Not Track",                          # 6
        "Policy Change",                         # 7
        "User Choice/Control",                   # 8
        "Introductory/Generic",                  # 9
        "Practice not covered",                  # 10
        "Privacy contact information",           # 11
    ]),
}

ATTRIBUTE_ORDER = [
    "Main",
]


def model_dirname(attribute: str) -> str:
    """Directory under the models root holding the fine-tuned model for a given attribute."""
    return attribute
