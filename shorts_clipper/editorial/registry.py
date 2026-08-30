from __future__ import annotations

import importlib
import inspect
import logging
import pkgutil

import shorts_clipper.editorial.plugins
from shorts_clipper.editorial.interfaces import EditorialJudge

log = logging.getLogger(__name__)


class PluginRegistry:
    """Discovers and manages EditorialJudge plugins."""

    def __init__(self):
        self._judges: dict[str, EditorialJudge] = {}
        self._discover_plugins()

    def _discover_plugins(self):
        """Dynamically loads all plugins in the shorts_clipper.editorial.plugins package."""
        package = shorts_clipper.editorial.plugins
        prefix = package.__name__ + "."

        for _, modname, _ in pkgutil.iter_modules(package.__path__, prefix):
            try:
                module = importlib.import_module(modname)
                for _name, obj in inspect.getmembers(module, inspect.isclass):
                    if issubclass(obj, EditorialJudge) and obj is not EditorialJudge:
                        judge_instance = obj()
                        self._judges[judge_instance.name] = judge_instance
                        log.debug(f"Loaded EditorialJudge: {judge_instance.name}")
            except Exception as e:
                log.error(f"Failed to load plugin {modname}: {e}")

    def get_all_judges(self) -> list[EditorialJudge]:
        return list(self._judges.values())

    def get_judge(self, name: str) -> EditorialJudge | None:
        return self._judges.get(name)
