"""Parse app-store privacy labels."""

from __future__ import annotations

import re
from dataclasses import dataclass

# Google Play Data safety
_PLAY_LABEL_RE = re.compile(r"\bdata safety\b", re.I)
_PLAY_SHARE_RE = re.compile(
    r"\bdata shared\b|shared? with (?:other companies|third part)|"
    r"this app may share these data types|may be shared with",
    re.I,
)
_PLAY_NO_SHARE_RE = re.compile(
    r"no data shared with third part|"
    r"(?:this )?(?:app|developer) (?:does ?n['o]t|do not) share",
    re.I,
)
_PLAY_AFFIRM_SHARE_RE = re.compile(
    r"this app may share these data types|may be shared with",
    re.I,
)

# Apple App Privacy
_APPLE_LABEL_RE = re.compile(r"\bapp privacy\b|privacy nutrition", re.I)
_APPLE_SHARE_RE = re.compile(
    r"data used to track you|data linked to you|used to track you across",
    re.I,
)


@dataclass
class PlatformLabel:
    has_label: bool = False
    shares: bool = False
    kind: str = ""   # "play" | "apple" | ""


def parse_platform_label(text: str, role: str = "") -> PlatformLabel:
    """Detect a platform privacy label + whether it declares third-party sharing."""
    if role == "play_data_safety" or _PLAY_LABEL_RE.search(text):
        shares = bool(_PLAY_SHARE_RE.search(text))
        if shares and _PLAY_NO_SHARE_RE.search(text) and not _PLAY_AFFIRM_SHARE_RE.search(text):
            shares = False
        return PlatformLabel(has_label=True, shares=shares, kind="play")
    if _APPLE_LABEL_RE.search(text):
        return PlatformLabel(
            has_label=True, shares=bool(_APPLE_SHARE_RE.search(text)), kind="apple"
        )
    return PlatformLabel()
