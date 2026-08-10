"""DuckDuckGo Tracker Radar."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from functools import lru_cache

from . import DATA_DIR

INDEX_PATH = DATA_DIR / "tracker_radar.json"


@dataclass(frozen=True, slots=True)
class DomainRecord:
    """One request domain and the organisation Tracker Radar attributes it to."""

    domain: str
    entity_name: str = ""
    categories: tuple[str, ...] = ()
    prevalence: float = 0.0


@dataclass(slots=True)
class EntityRecord:
    """One organisation, with the domains the index attributes to it."""

    name: str
    display_name: str = ""
    prevalence: float = 0.0
    domains: list[str] = field(default_factory=list)
    categories: list[str] = field(default_factory=list)


@dataclass
class TrackerRadarIndex:
    domains: dict[str, DomainRecord]
    entities: dict[str, EntityRecord]

    def lookup_domain(self, host: str) -> DomainRecord | None:
        return self.domains.get((host or "").strip().lower())

    def lookup_entity(self, name: str) -> EntityRecord | None:
        return self.entities.get((name or "").strip())


def _prevalence(value) -> float:
    if isinstance(value, dict):
        return float(value.get("total") or 0.0)
    try:
        return float(value or 0.0)
    except (TypeError, ValueError):
        return 0.0


@lru_cache(maxsize=1)
def index() -> TrackerRadarIndex:
    """The bundled index, read once per process."""
    if not INDEX_PATH.exists():
        return TrackerRadarIndex(domains={}, entities={})
    raw = json.loads(INDEX_PATH.read_text(encoding="utf-8"))

    entities: dict[str, EntityRecord] = {}
    for name, rec in (raw.get("entities") or {}).items():
        entities[name] = EntityRecord(
            name=name,
            display_name=(rec.get("display_name") or name),
            prevalence=_prevalence(rec.get("prevalence")),
        )

    domains: dict[str, DomainRecord] = {}
    for host, rec in (raw.get("domains") or {}).items():
        owner = rec.get("entity_name") or ""
        categories = tuple(rec.get("categories") or ())
        prevalence = _prevalence(rec.get("prevalence"))
        domains[host] = DomainRecord(
            domain=host, entity_name=owner,
            categories=categories, prevalence=prevalence,
        )
        if not owner:
            continue
        entity = entities.get(owner)
        if entity is None:
            entity = entities[owner] = EntityRecord(name=owner, display_name=owner)
        entity.domains.append(host)
        for c in categories:
            if c not in entity.categories:
                entity.categories.append(c)

    for entity in entities.values():
        entity.domains.sort(key=lambda d: (-domains[d].prevalence, len(d), d))
    return TrackerRadarIndex(domains=domains, entities=entities)


def available() -> bool:
    return bool(index().domains)
