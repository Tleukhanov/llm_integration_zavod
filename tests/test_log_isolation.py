"""Tests for pipeline log isolation during pytest runs."""

import logging
import time

import pytest

from tests.conftest import _isolate_app_logs

MARKER = "LOG_ISOLATION_MARKER_9f3b7a"


def _emit_marker():
    root = logging.getLogger()
    root.info(MARKER)


def test_app_log_not_created_during_tests(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(logging.getLogger(), "level", logging.INFO)
    log_file = tmp_path / "outputs" / "app.log"
    _emit_marker()
    assert not log_file.exists()


def test_existing_app_log_not_modified_during_tests(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(logging.getLogger(), "level", logging.INFO)
    outputs = tmp_path / "outputs"
    outputs.mkdir()
    log_file = outputs / "app.log"
    log_file.write_text("previous content\n", encoding="utf-8")
    mtime_before = log_file.stat().st_mtime_ns
    _emit_marker()
    time.sleep(0.05)
    assert log_file.read_text(encoding="utf-8") == "previous content\n"
    assert log_file.stat().st_mtime_ns == mtime_before


def test_logging_restored_after_teardown(tmp_path):
    root = logging.getLogger()
    (tmp_path / "outputs").mkdir()
    handler = logging.FileHandler(tmp_path / "outputs" / "app.log", encoding="utf-8")
    root.addHandler(handler)
    try:
        gen = _isolate_app_logs()
        next(gen)
        assert handler not in root.handlers
        with pytest.raises(StopIteration):
            next(gen)
        assert handler in root.handlers
    finally:
        if handler in root.handlers:
            root.removeHandler(handler)
        handler.close()
