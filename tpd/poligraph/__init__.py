"""Re-implementation of the PoliGraph framework."""

from .graph import (
    Action,
    CollectEdge,
    EdgeType,
    NodeType,
    PoliGraph,
    Purpose,
    CORE_PURPOSES,
    NON_CORE_PURPOSES,
    UNSPECIFIED_DATA,
    UNSPECIFIED_ACTOR,
    FIRST_PARTY,
)
from .ontology import (
    DataOntology,
    EntityOntology,
    LocalOntology,
    global_data_ontology,
    global_entity_ontology,
)

__version__ = "0.1.0"

__all__ = [
    "PoliGraph", "NodeType", "EdgeType", "Action", "Purpose", "CollectEdge",
    "CORE_PURPOSES", "NON_CORE_PURPOSES",
    "UNSPECIFIED_DATA", "UNSPECIFIED_ACTOR", "FIRST_PARTY",
    "DataOntology", "EntityOntology", "LocalOntology",
    "global_data_ontology", "global_entity_ontology",
]
