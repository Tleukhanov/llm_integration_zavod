import pytest

from shorts_clipper.compliance.gate import ComplianceGate, ComplianceVerdict
from shorts_clipper.core.settings import Settings


def make_settings(**overrides):
    defaults = {
        "compliance_enabled": True,
        "compliance_llm": False,  # hermetic — no live API by default
        "compliance_finance_strict": False,
        "compliance_auto_disclaimers": True,
        "compliance_report_dir": "outputs/compliance",
        "gemini_api_key": None,
    }
    defaults.update(overrides)
    return Settings(**defaults)


def test_clean_text_passes():
    gate = ComplianceGate(make_settings())
    v = gate.check("Простое видео про котиков", "Милые моменты из жизни кошек")
    assert v.level == "pass"
    assert v.passed is True


def test_banned_word_blocks():
    gate = ComplianceGate(make_settings())
    v = gate.check("Заработай миллион прямо сейчас", "Ссылка в описании")
    assert v.level == "block"
    assert v.passed is False
    assert any("hard_block" in r for r in v.reasons)


def test_finance_without_disclaimer_non_strict_review():
    gate = ComplianceGate(make_settings(compliance_finance_strict=False))
    v = gate.check("Трейдинг для начинающих", "Учимся торговать на форексе")
    assert v.level == "review"
    assert v.passed is True


def test_finance_without_disclaimer_strict_blocks():
    gate = ComplianceGate(make_settings(compliance_finance_strict=True))
    v = gate.check("Инвестиции в акции", "Курс по обучению трейдингу")
    assert v.level == "block"
    assert v.passed is False


def test_finance_with_disclaimer_pass():
    gate = ComplianceGate(make_settings(compliance_finance_strict=True))
    v = gate.check(
        "Обучение трейдингу",
        "Риск потери капитала. Не является индивидуальной инвестиционной рекомендацией.",
    )
    assert v.level == "pass"
    assert v.passed is True


def test_disabled_gate_always_pass():
    gate = ComplianceGate(make_settings(compliance_enabled=False))
    v = gate.check("ЗАРАБОТАЙ МИЛЛИОН", "порн казино ставки")
    assert v.level == "pass"
    assert v.passed is True


def test_llm_failure_degrades_to_review(monkeypatch):
    class _Fake:
        def generate_content(self, *a, **kw):
            raise RuntimeError("api down")

    monkeypatch.setattr(
        "shorts_clipper.providers.gemini.GeminiProvider",
        lambda **kw: _Fake(),
    )
    gate = ComplianceGate(make_settings(compliance_llm=True, gemini_api_key="test"))
    v = gate.check("Нейтральное видео", "Описание")
    assert v.level == "review"
    assert v.passed is True


def test_llm_block_wins(monkeypatch):
    class _FakeResp:
        text = '{"violations": ["explicitly promoting gambling"], "level": "block"}'

    class _Fake:
        def generate_content(self, *a, **kw):
            return _FakeResp()

    monkeypatch.setattr(
        "shorts_clipper.providers.gemini.GeminiProvider",
        lambda **kw: _Fake(),
    )
    gate = ComplianceGate(make_settings(compliance_llm=True, gemini_api_key="test"))
    v = gate.check("Видео о ставках", "Полная информация о спортивных ставках")
    assert v.level == "block"
    assert v.passed is False


def test_llm_pass_cannot_override_rules_block(monkeypatch):
    class _FakeResp:
        text = '{"violations": [], "level": "pass"}'

    class _Fake:
        def generate_content(self, *a, **kw):
            return _FakeResp()

    monkeypatch.setattr(
        "shorts_clipper.providers.gemini.GeminiProvider",
        lambda **kw: _Fake(),
    )
    gate = ComplianceGate(make_settings(compliance_llm=True, gemini_api_key="test"))
    v = gate.check("КАЗИНО онлайн", "Ставки на спорт и рулетка")
    assert v.level == "block"
    assert v.passed is False


def test_verdict_is_dataclass():
    gate = ComplianceGate(make_settings())
    v = gate.check("тест", "описание")
    assert isinstance(v, ComplianceVerdict)
    assert v.checks


def test_block_report_written(tmp_path):
    gate = ComplianceGate(
        make_settings(compliance_report_dir=str(tmp_path))
    )
    v = gate.check("Заработай на ставках", "Быстрый заработок за день")
    assert v.level == "block"
    gate.write_block_report("video.mp4", "Заработай на ставках", "Быстрый заработок за день", v)
    reports = list(tmp_path.glob("blocked_*.json"))
    assert len(reports) == 1
    import json

    with open(reports[0], encoding="utf-8") as f:
        data = json.load(f)
    assert data["title"] == "Заработай на ставках"
    assert data["level"] == "block"
