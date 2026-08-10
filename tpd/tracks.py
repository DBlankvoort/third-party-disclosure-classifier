"""The subject matter a disclosed relationship concerns."""

from __future__ import annotations

# --------------------------------------------------------------------------- #
# Track
# --------------------------------------------------------------------------- #
INVENTORY = "inventory"
PERSONAL_DATA = "personal_data"

TRACKS = (PERSONAL_DATA, INVENTORY)

# Relation sources recording authorisation to sell advertising inventory.
_INVENTORY_SOURCES = frozenset({"ads_txt", "app_ads_txt", "sellers_json"})


def track_for_source(source: str) -> str:
    """The track one relation source belongs to."""
    return INVENTORY if source in _INVENTORY_SOURCES else PERSONAL_DATA


def track_for_sources(sources) -> str:
    """The track a set of relation sources belongs to."""
    values = {track_for_source(s) for s in (sources or ())}
    return PERSONAL_DATA if PERSONAL_DATA in values or not values else INVENTORY


# --------------------------------------------------------------------------- #
# Data subject
# --------------------------------------------------------------------------- #
SITE_VISITOR = "site_visitor"
SERVICE_DATA = "service_data"
NOT_APPLICABLE = "not_applicable"
UNKNOWN = "unknown"

SUBJECTS = (SITE_VISITOR, SERVICE_DATA, NOT_APPLICABLE, UNKNOWN)

_ONWARD_SUBJECTS = frozenset({SERVICE_DATA, NOT_APPLICABLE})


def carries_upstream_data(subject: str, strict: bool = False) -> bool:
    """Whether an arrangement can extend a chain that arrived from upstream."""
    if subject in _ONWARD_SUBJECTS:
        return True
    return not strict and subject in ("", UNKNOWN)


SERVICE_DATA_ROLES = frozenset({"subprocessor_list", "dpa"})
