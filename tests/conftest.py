import logging
from pathlib import Path
from unittest import mock

import pytest


@pytest.fixture(autouse=True)
def disable_sleep():
    with mock.patch("time.sleep", return_value=None):
        yield


def _isolate_app_logs():
    root = logging.getLogger()
    removed = []
    for handler in list(root.handlers):
        base = getattr(handler, "baseFilename", None)
        if base and Path(base).name == "app.log":
            root.removeHandler(handler)
            handler.close()
            removed.append(handler)
    yield
    for handler in removed:
        root.addHandler(handler)


@pytest.fixture(autouse=True, scope="session")
def isolate_pipeline_logs():
    """Keep pipeline file logs out of outputs/app.log during the test session."""
    yield from _isolate_app_logs()
