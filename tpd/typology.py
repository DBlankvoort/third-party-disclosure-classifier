"""Machine-readable typology of third-party disclosure patterns."""

from __future__ import annotations

from enum import Enum

class TargetType(str, Enum):
    """The kind of target whose document set we analyse."""

    WEBSITE = "website"
    DATA_BROKER = "data_broker"
    PLAY_STORE_APP = "play_store_app"
    APP_STORE_APP = "app_store_app"


# --------------------------------------------------------------------------- #
# Disclosure facets
# --------------------------------------------------------------------------- #
class Medium(str, Enum):
    """The level of document structuredness."""

    PROSE = "prose"                        # narrative policy text
    STRUCTURED = "structured"              # human-readable table / structured list
    MACHINE_READABLE = "machine_readable"  # standardised machine-readable file
    OTHER_DOC = "other_doc"                # any other relevant doc (partners/help/FAQ)


class Specificity(str, Enum):
    """The level of specificity by which third parties are disclosed."""

    NAMED = "named"        # a specific organisation
    CATEGORY = "category"  # a role / category only ("advertising partners")
    GENERIC = "generic"    # a bare unnamed reference ("third parties")


# --------------------------------------------------------------------------- #
# A facet is a single (Medium, Specificity) pair, encoded as "medium:specificity".
# A facet set is a ``;``-joined list of facets.
# --------------------------------------------------------------------------- #
def facet_code(medium: Medium, specificity: Specificity) -> str:
    """Produce a facet given a medium and specificity."""
    return f"{medium.value}:{specificity.value}"


_VALID_MEDIA = {m.value for m in Medium}


def media_of(facets: set[str]) -> set[Medium]:
    """The set of media present in a facet set."""
    media: set[Medium] = set()
    for code in facets:
        m = code.partition(":")[0]
        if m in _VALID_MEDIA:
            media.add(Medium(m))
    return media
