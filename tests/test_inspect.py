"""Tests for the ``inspect`` CLI command (read-only quality report).

No network and no ffmpeg binary required: media fixtures are tiny mp4 files
created inline with PyAV, and the compliance gate runs rules-only.
"""

import json
from pathlib import Path
from types import SimpleNamespace

from shorts_clipper.__main__ import main
from shorts_clipper.core.settings import Settings
from shorts_clipper.inspect import run_inspect


def _make_settings() -> Settings:
    return Settings(
        compliance_enabled=True,
        compliance_llm=False,
        compliance_finance_strict=False,
        compliance_report_dir=Path("outputs/compliance"),
        gemini_api_key=None,
    )


def _make_mp4(
    path: Path,
    *,
    width: int = 320,
    height: int = 240,
    seconds: float = 4.0,
    fps: int = 30,
    with_audio: bool = True,
) -> Path:
    import av

    container = av.open(str(path), mode="w")
    vstream = container.add_stream("libx264", rate=fps)
    vstream.width = width
    vstream.height = height
    vstream.pix_fmt = "yuv420p"
    for i in range(int(seconds * fps)):
        frame = av.VideoFrame(width, height, "yuv420p")
        frame.pts = i
        for packet in vstream.encode(frame):
            container.mux(packet)
    for packet in vstream.encode():
        container.mux(packet)
    container.close()
    return path


def _make_ass(path: Path) -> Path:
    path.write_text(
        "[V4+ Styles]\n"
        "Format: Name, Fontname, Fontsize, PrimaryColour\n"
        "Style: Default,Arial,48,&H00FFFFFF\n"
        "Style: Big,Arial,72,&H00FFFFFF\n"
        "\n"
        "[Events]\n"
        "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text\n"
        "Dialogue: 0,0:00:00.10,0:00:01.00,Default,,0,0,0,,First line {\\b1}here{\\b0}\n"
        "Dialogue: 0,0:00:01.10,0:00:02.00,Default,,0,0,0,,Second line\n"
        "Dialogue: 0,0:00:02.10,0:00:03.00,Big,,0,0,0,,Third line\n"
        "Dialogue: 0,0:00:03.10,0:00:04.00,Default,,0,0,0,,Fourth line\n",
        encoding="utf-8",
    )
    return path


def _make_sidecar(path: Path, **overrides) -> Path:
    meta = {
        "title": "Простое видео про котиков",
        "description": "Милые моменты из жизни кошек",
        "tags": ["shorts", "pets"],
        "publish_status": "idle",
        "video_id": "stock-1",
        "source_url": "",
        "niche": "pets",
        "script": "Котики — лучшие.",
    }
    meta.update(overrides)
    path.write_text(json.dumps(meta, ensure_ascii=False), encoding="utf-8")
    return path


def _run_inspect(path: Path, settings: Settings | None = None, capsys=None) -> tuple[int, str]:
    args = SimpleNamespace(file=str(path))
    code = run_inspect(args, settings or _make_settings())
    assert capsys is not None
    captured = capsys.readouterr()
    return code, captured.out + captured.err


def test_inspect_prints_media_info(tmp_path, capsys):
    mp4 = _make_mp4(tmp_path / "clip.mp4")
    code, out = _run_inspect(mp4, capsys=capsys)
    assert code == 0
    assert "Media" in out
    assert "duration: 4.00s" in out
    assert "resolution: 320x240" in out
    assert "video codec: h264" in out
    assert "fps: 30.00" in out
    assert "bitrate:" in out


def test_inspect_missing_file_exits_1(tmp_path, capsys):
    code, out = _run_inspect(tmp_path / "nope.mp4", capsys=capsys)
    assert code == 1
    assert "File not found" in out


def test_inspect_sidecar_and_ass_present(tmp_path, capsys):
    mp4 = _make_mp4(tmp_path / "clip.mp4")
    _make_sidecar(
        tmp_path / "clip.json",
        title="Мой заголовок",
        niche="tech",
        script="Текст скрипта",
        publish_status="scheduled",
    )
    _make_ass(tmp_path / "clip.ass")
    code, out = _run_inspect(mp4, capsys=capsys)
    assert code == 0
    assert "title: Мой заголовок" in out
    assert "niche: tech" in out
    assert "script: Текст скрипта" in out
    assert "publish_status: scheduled" in out
    assert "styles: 2" in out
    assert "1. First line here" in out
    assert "2. Second line" in out
    assert "3. Third line" in out
    assert "Fourth line" not in out
    assert "verdict: PASS" in out


def test_inspect_sidecar_and_ass_absent(tmp_path, capsys):
    mp4 = _make_mp4(tmp_path / "clip.mp4")
    code, out = _run_inspect(mp4, capsys=capsys)
    assert code == 0
    assert "no ASS" in out
    assert "no sidecar" in out
    assert "skipped (no sidecar)" in out


def test_inspect_compliance_verdict_shown(tmp_path, capsys):
    mp4 = _make_mp4(tmp_path / "clip.mp4")
    _make_sidecar(tmp_path / "clip.json", title="Заработай миллион прямо сейчас")
    code, out = _run_inspect(mp4, capsys=capsys)
    assert code == 0
    assert "verdict: FAIL" in out
    assert "level=block" in out


def test_inspect_quick_checks(tmp_path, capsys):
    mp4 = _make_mp4(tmp_path / "clip.mp4")
    code, out = _run_inspect(mp4, capsys=capsys)
    assert code == 0
    assert "duration 3-65s: PASS" in out
    assert "resolution 1080x1920: FAIL" in out
    assert "has audio: FAIL" in out
    assert "has subtitles: FAIL" in out


def test_main_inspect_exit_codes(tmp_path, capsys):
    mp4 = _make_mp4(tmp_path / "clip.mp4")
    assert main(["inspect", str(mp4)]) == 0
    capsys.readouterr()
    assert main(["inspect", str(tmp_path / "missing.mp4")]) == 1
