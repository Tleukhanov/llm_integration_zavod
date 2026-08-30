"""V3.3 Attention Optimization Engine."""

from shorts_clipper.attention.engine import AttentionEngine, SimulationEngine
from shorts_clipper.attention.features import FeatureExtractor
from shorts_clipper.attention.models import (
    AttentionReport,
    AttentionState,
    AttentionTimeline,
    CounterfactualVariant,
    EditorialRecommendation,
    FeatureSet,
    JudgeResult,
    SimulationResult,
)

__all__ = [
    "AttentionReport",
    "FeatureSet",
    "JudgeResult",
    "AttentionState",
    "AttentionTimeline",
    "EditorialRecommendation",
    "SimulationResult",
    "CounterfactualVariant",
    "FeatureExtractor",
    "AttentionEngine",
    "SimulationEngine",
]
