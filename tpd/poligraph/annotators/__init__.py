from .base import Annotator, AnnotatorContext, ParsedSentence
from .collection import CollectionAnnotator
from .coreference import CoreferenceAnnotator
from .listann import ListAnnotator
from .purpose import PurposeAnnotator
from .subsumption import SubsumptionAnnotator

# Order matters
DEFAULT_ANNOTATORS = [
    CollectionAnnotator,
    SubsumptionAnnotator,
    PurposeAnnotator,
    CoreferenceAnnotator,
    ListAnnotator,
]

__all__ = [
    "Annotator", "AnnotatorContext", "ParsedSentence",
    "CollectionAnnotator", "SubsumptionAnnotator", "PurposeAnnotator",
    "CoreferenceAnnotator", "ListAnnotator", "DEFAULT_ANNOTATORS",
]
