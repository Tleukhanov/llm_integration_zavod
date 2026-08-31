import os
from pathlib import Path

from shorts_clipper.core.settings import Settings


def test_compliance_settings_defaults():
    saved = {k: os.environ.get(k) for k in (
        "SHORTS_COMPLIANCE_ENABLED",
        "SHORTS_COMPLIANCE_LLM",
        "SHORTS_COMPLIANCE_FINANCE_STRICT",
        "SHORTS_COMPLIANCE_AUTO_DISCLAIMERS",
        "SHORTS_COMPLIANCE_REPORT_DIR",
    )}
    try:
        for k in saved:
            os.environ.pop(k, None)
        s = Settings.from_env(".env.does.not.exist")
        assert s.compliance_enabled is True
        assert s.compliance_llm is True
        assert s.compliance_finance_strict is False
        assert s.compliance_auto_disclaimers is True
        assert s.compliance_report_dir == Path("outputs/compliance")
    finally:
        for k, v in saved.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v


def test_compliance_settings_from_env(tmp_path, monkeypatch):
    env_file = tmp_path / ".env"
    env_file.write_text(
        "SHORTS_COMPLIANCE_ENABLED=false\n"
        "SHORTS_COMPLIANCE_LLM=false\n"
        "SHORTS_COMPLIANCE_FINANCE_STRICT=true\n"
        "SHORTS_COMPLIANCE_AUTO_DISCLAIMERS=false\n"
        "SHORTS_COMPLIANCE_REPORT_DIR=outputs/reports\n",
        encoding="utf-8",
    )
    s = Settings.from_env(env_file)
    assert s.compliance_enabled is False
    assert s.compliance_llm is False
    assert s.compliance_finance_strict is True
    assert s.compliance_auto_disclaimers is False
    assert s.compliance_report_dir == Path("outputs/reports")
