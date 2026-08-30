from __future__ import annotations

import abc

from shorts_clipper.editorial.models import FeatureSet, JudgeResult


class EditorialJudge(abc.ABC):
    """Abstract base class for all Editorial Judges."""

    @property
    @abc.abstractmethod
    def name(self) -> str:
        """Name of the judge (e.g., 'HookJudge')."""
        pass

    @abc.abstractmethod
    def evaluate(self, features: FeatureSet) -> JudgeResult:
        """Evaluate the features and return a JudgeResult."""
        pass
