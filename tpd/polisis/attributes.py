"""Label layout for the fine-tuned PrivBERT policy-segment classifier."""

# DO NOT REORDER
LABELS = [
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
]

THIRD_PARTY_INDEX = LABELS.index("Third Party Sharing/Collection")

# Directory holding the fine-tuned model.
MODEL_DIRNAME = "Main"
