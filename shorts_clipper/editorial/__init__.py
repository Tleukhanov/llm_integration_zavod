"""Editorial Core - Deterministic decision making engine."""

from shorts_clipper.editorial.engine import EditorialEngine
from shorts_clipper.editorial.interfaces import EditorialJudge
from shorts_clipper.editorial.models import EditorialDecision, FeatureSet, JudgeResult
from shorts_clipper.editorial.registry import PluginRegistry

__all__ = [
    "EditorialEngine",
    "EditorialDecision",
    "FeatureSet",
    "JudgeResult",
    "EditorialJudge",
    "PluginRegistry",
]
