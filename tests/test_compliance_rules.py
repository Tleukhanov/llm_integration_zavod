import pytest

from shorts_clipper.compliance.rules import ComplianceRules


@pytest.fixture
def rules():
    return ComplianceRules()


def test_hard_block_patterns_match_bad_strings(rules):
    bad_strings = [
        "Заработай миллион за месяц!",
        "гарантированный доход",
        "быстрый заработок без вложений",
        "пассивный доход за деньги",
        "бесплатно деньги прямо сейчас",
        "casino онлайн",
        "играть в казино",
        "ставки на спорт",
        "betting site real money",
        "online секс услуги",
        "porn video here",
        "naked people",
        "купить интим услуги",
        "get rich quick",
        "make money fast online",
        "easy money guaranteed",
    ]
    for text in bad_strings:
        assert rules.scan_text(text), f"expected {text!r} to be flagged"


def test_hard_block_patterns_dont_match_clean_strings(rules):
    clean_strings = [
        "Как приготовить пасту дома — простое видео",
        "Лучшие моменты матча CS2, камбек за 5 минут",
        "Новый сезон сериала — обзор",
        "Рецепт вкусного завтрака за 10 минут",
        "Прохождение хоррора без комментариев",
        "Топ-5 городов для отпуска в России",
        "Как настроить домашнюю сеть",
        "Обзор нового телефона, тест камеры",
        "Смешные моменты кошек",
        "Как научиться играть на гитаре",
    ]
    for text in clean_strings:
        assert not rules.scan_text(text), f"expected {text!r} to pass"


def test_finance_detection(rules):
    finance_texts = [
        "Обучение трейдингу для начинающих",
        "Курс по форекс инвестициям",
        "Трейдинг криптовалют, торговля биткоином",
        "Инвестиции в акции фондового рынка",
        "Инфопродукт по заработку на бирже",
    ]
    for text in finance_texts:
        assert rules.check_finance(text), f"expected {text!r} to be finance"


def test_non_finance_not_detected(rules):
    non_finance = [
        "Английский язык для путешествий простыми словами",
        "Готовим суши дома пошагово",
        "Как сдавать экзамены без стресса",
        "Обзор нового ноутбука для учебы",
    ]
    for text in non_finance:
        assert not rules.check_finance(text), f"expected {text!r} to not be finance"


def test_missing_disclaimer_logic(rules):
    # Finance topic WITHOUT disclaimer -> not compliant
    finance_no_disc = "Трейдинг: как начать торговать на форексе"
    assert rules.check_finance(finance_no_disc)
    assert not rules.has_disclaimer(finance_no_disc)

    # Finance topic WITH disclaimer -> compliant
    finance_with_disc = (
        "Трейдинг: как начать торговать на форексе. "
        "Риск потери капитала. Не является инвестиционной рекомендацией."
    )
    assert rules.check_finance(finance_with_disc)
    assert rules.has_disclaimer(finance_with_disc)


def test_ad_disclosure_and_affiliate(rules):
    assert rules.has_affiliate_link("Ссылка в описании на наш магазин")
    assert rules.has_affiliate_link("Подпишись, ссылка в описании")
    assert not rules.has_affiliate_link("Просто интересное видео")

    assert rules.has_ad_disclosure("#реклама помогла создать этот ролик")
    assert rules.has_ad_disclosure("Реклама продукта партнёра")
    assert not rules.has_ad_disclosure("Обычное описание без спонсоров")
