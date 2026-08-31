from shorts_clipper.compliance.gate import ComplianceGate
from shorts_clipper.compliance.rules import ComplianceRules
from shorts_clipper.core.settings import Settings


def make_settings(**overrides):
    defaults = {
        "compliance_enabled": True,
        "compliance_llm": False,
        "compliance_finance_strict": False,
        "compliance_auto_disclaimers": True,
        "compliance_report_dir": "outputs/compliance",
        "gemini_api_key": None,
    }
    defaults.update(overrides)
    return Settings(**defaults)


def test_adds_ad_tag_when_affiliate():
    gate = ComplianceGate(make_settings())
    safe, note = gate.suggest_description(
        "Скидки на скины CS2", is_finance=False, affiliate_enabled=True
    )
    assert "#реклама" in safe
    assert note


def test_adds_finance_disclaimer():
    rules = ComplianceRules()
    gate = ComplianceGate(make_settings())
    safe, note = gate.suggest_description(
        "Обучение трейдингу", is_finance=True, affiliate_enabled=False
    )
    assert rules.has_disclaimer(safe)
    assert "индивидуальной инвестиционной рекомендацией" in safe
    assert note


def test_adds_both_when_both_needed():
    gate = ComplianceGate(make_settings())
    safe, note = gate.suggest_description(
        "Курс по форекс", is_finance=True, affiliate_enabled=True
    )
    assert "#реклама" in safe
    assert "инвестиционной рекомендацией" in safe
    assert note


def test_no_change_when_nothing_needed():
    gate = ComplianceGate(make_settings())
    base = "Обычное описание видео про котиков"
    safe, note = gate.suggest_description(base, is_finance=False, affiliate_enabled=False)
    assert safe == base
    assert note == ""


def test_idempotent_no_double_append():
    gate = ComplianceGate(make_settings())
    base = (
        "Обучение трейдингу #реклама. "
        "Риск потери капитала, не является инвестиционной рекомендацией."
    )
    safe1, _ = gate.suggest_description(base, is_finance=True, affiliate_enabled=True)
    safe2, _ = gate.suggest_description(safe1, is_finance=True, affiliate_enabled=True)
    assert safe1 == safe2
    assert safe2.count("#реклама") == 1
    assert safe2.count("инвестиционной рекомендацией") == 1
