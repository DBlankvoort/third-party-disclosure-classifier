"""Eight OPP-115 classifiers."""
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
    "Identifiability": Attribute("Identifiability", [
        "Aggregated or anonymized",  # 0
        "Identifiable",              # 1
        "Unspecified",               # 2
    ]),
    "Does or Does Not": Attribute("Does or Does Not", [
        "Does",      # 0
        "Does Not",  # 1
    ]),
    "Purpose": Attribute("Purpose", [
        "Additional service/feature",      # 0
        "Advertising",                     # 1
        "Analytics/Research",              # 2
        "Basic service/feature",           # 3
        "Legal requirement",               # 4
        "Marketing",                       # 5
        "Merger/Acquisition",              # 6
        "Personalization/Customization",   # 7
        "Service operation and security",  # 8
        "Unspecified",                     # 9
    ]),
    "Personal Information Type": Attribute("Personal Information Type", [
        "Computer information",          # 0
        "Contact",                       # 1
        "Cookies and tracking elements",  # 2
        "Demographic",                   # 3
        "Financial",                     # 4
        "Generic personal information",  # 5
        "Health",                        # 6
        "IP address and device IDs",     # 7
        "Location",                      # 8
        "Personal identifier",           # 9
        "Social media data",             # 10
        "Survey data",                   # 11
        "User online activities",        # 12
        "User profile",                  # 13
        "Unspecified",                   # 14
    ]),
    "Audience Type": Attribute("Audience Type", [
        "Children",                       # 0
        "Californians",                   # 1
        "Citizens from other countries",  # 2
        "Europeans",                      # 3
    ]),
    "Action First-Party": Attribute("Action First-Party", [
        "Collect in mobile app",  # 0
        "Collect on website",     # 1
    ]),
    "Action Third-Party": Attribute("Action Third-Party", [
        "Collect on first party website/app",  # 0
        "See",                                 # 1
    ]),
}

ATTRIBUTE_ORDER = [
    "Main",
    "Identifiability",
    "Does or Does Not",
    "Purpose",
    "Personal Information Type",
    "Action First-Party",
    "Action Third-Party",
    "Audience Type",
]


def model_dirname(attribute: str) -> str:
    """Directory under the models root holding the fine-tuned model for a given attribute."""
    return attribute
