from pathlib import Path

import pytest

from shorts_clipper.cli import doctor as doctor_module
from shorts_clipper.cli.doctor import run_doctor
from shorts_clipper.core.settings import Settings


@pytest.fixture(autouse=True)
def _clean_token_env(monkeypatch):
    for var in (
        "GEMINI_API_KEY",
        "YOUTUBE_API_KEY",
        "IG_ACCESS_TOKEN",
        "IG_ACCOUNT_ID",
        "TT_CLIENT_KEY",
        "TT_CLIENT_SECRET",
        "TT_ACCESS_TOKEN",
    ):
        monkeypatch.delenv(var, raising=False)


def _settings(**overrides) -> Settings:
    defaults = {
        "output_dir": Path("outputs"),
        "models_dir": Path("models"),
        "cache_dir": Path(".cache/shorts-clipper"),
    }
    defaults.update(overrides)
    return Settings(**defaults)


def test_returns_zero_with_valid_ffmpeg_and_no_tokens(tmp_path, capsys):
    env_file = tmp_path / ".env"
    env_file.write_text(
        "SHORTS_OUTPUT_DIR=outputs\nSHORTS_MODELS_DIR=models\n", encoding="utf-8"
    )
    settings = Settings.from_env(env_path=env_file)

    code = run_doctor(settings)

    out = capsys.readouterr().out
    assert code == 0
    assert "[OK] Python" in out
    assert "[OK] ffmpeg" in out
    assert "[OK] PyAV" in out
    assert "[WARN] Gemini API key" in out


def test_ffmpeg_missing_exits_one(capsys, monkeypatch):
    def boom():
        raise RuntimeError("ffmpeg not found")

    monkeypatch.setattr(doctor_module, "ffmpeg_path", boom)

    code = run_doctor(_settings())

    out = capsys.readouterr().out
    assert code == 1
    assert "[MISS] ffmpeg" in out


def test_missing_required_token_prints_warn_not_failure(capsys):
    settings = _settings(gemini_api_key=None, publish_platforms=["youtube", "instagram"])

    code = run_doctor(settings)

    out = capsys.readouterr().out
    assert code == 0
    assert "[WARN] Gemini API key (GEMINI_API_KEY)" in out
    assert "[WARN] YouTube publish auth" in out
    assert "IG_ACCESS_TOKEN" in out