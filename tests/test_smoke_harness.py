"""Unit tests for the smoke test harness.

These tests do NOT hit the network. They only verify that the harness
script exists and that its artifact-scanning helper detects real files
(including the nested ``<output_dir>/run_<timestamp>/`` layout the pipeline
actually writes into).
"""

from __future__ import annotations

import logging
import sys
from pathlib import Path

import av
import pytest

SCRIPTS_DIR = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS_DIR))

from smoke_test import scan_outputs  # noqa: E402

logger = logging.getLogger(__name__)


def _make_vertical_mp4(path: Path, width: int = 720, height: int = 1280) -> Path:
    """Write a tiny valid vertical MP4 so PyAV can inspect it."""
    container = av.open(str(path), mode="w")
    try:
        stream = container.add_stream("mpeg4", rate=10)
        stream.width = width
        stream.height = height
        stream.pix_fmt = "yuv420p"
        for i in range(3):
            frame = av.VideoFrame(width, height, "yuv420p")
            for plane in frame.planes:
                plane.update(bytes([(i * 64) % 256]) * plane.buffer_size)
            for packet in stream.encode(frame):
                container.mux(packet)
        for packet in stream.encode():
            container.mux(packet)
    finally:
        container.close()
    return path


def test_harness_script_exists():
    assert (SCRIPTS_DIR / "smoke_test.py").is_file()


def _write_run_dir(output_dir: Path) -> Path:
    run_dir = output_dir / "run_20260101_120000"
    run_dir.mkdir(parents=True, exist_ok=True)
    _make_vertical_mp4(run_dir / "rendered_clip_1.mp4")
    (run_dir / "thumbnail_1.jpg").write_bytes(b"\xff\xd8\xff\xe0fakejpeg")
    (run_dir / "final_metadata_1.json").write_text("{}", encoding="utf-8")
    return run_dir


def test_scan_outputs_detects_nested_artifacts(tmp_path):
    _write_run_dir(tmp_path)

    result = scan_outputs(tmp_path, logger)

    assert result["found_mp4"] is True
    assert result["found_jpg"] is True
    assert result["found_metadata"] is True
    assert result["vertical"] is True
    assert result["duration_sec"] > 0


def test_scan_outputs_accepts_plain_callable(tmp_path):
    run_dir = _write_run_dir(tmp_path)
    messages: list[str] = []

    result = scan_outputs(run_dir, messages.append)

    assert result["found_mp4"] is True
    assert any("Verified clip" in msg for msg in messages)


def test_scan_outputs_empty_dir(tmp_path):
    result = scan_outputs(tmp_path, logger)

    assert result["found_mp4"] is False
    assert result["found_jpg"] is False
    assert result["found_metadata"] is False
    assert result["vertical"] is False
    assert result["duration_sec"] == 0.0


if __name__ == "__main__":
    pytest.main([__file__])