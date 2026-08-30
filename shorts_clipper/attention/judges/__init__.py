"""Attention Judges package."""

from shorts_clipper.attention.judges.base import AttentionJudge, JudgeRegistry
from shorts_clipper.attention.judges.cognitive import (
    CuriosityJudge,
    InformationDensityJudge,
    PredictionErrorJudge,
)
from shorts_clipper.attention.judges.editorial import PayoffJudge, ScrollStopJudge

__all__ = [
    "JudgeRegistry",
    "AttentionJudge",
    "CuriosityJudge",
    "InformationDensityJudge",
    "PredictionErrorJudge",
    "ScrollStopJudge",
    "PayoffJudge",
]
