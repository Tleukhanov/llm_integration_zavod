"""Base class and Registry for Attention Judges."""

from abc import ABC, abstractmethod

from shorts_clipper.attention.models import FeatureSet, JudgeResult


class AttentionJudge(ABC):
    """Abstract base class for all attention judges."""

    @abstractmethod
    def evaluate(self, features: FeatureSet) -> JudgeResult:
        """Evaluates the features and returns a JudgeResult."""
        pass


class JudgeRegistry:
    """Registry pattern for composable judges (Agent F)."""

    _judges: dict[str, type[AttentionJudge]] = {}

    @classmethod
    def register(cls, name: str):
        def wrapper(judge_cls: type[AttentionJudge]):
            cls._judges[name] = judge_cls
            return judge_cls

        return wrapper

    @classmethod
    def get_all_judges(cls) -> dict[str, AttentionJudge]:
        """Instantiates and returns all registered judges."""
        return {name: judge_cls() for name, judge_cls in cls._judges.items()}
