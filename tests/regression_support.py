"""
Основа за регресионните тестове от одита на 11.10.2026: синтетичните профили, календарите и фактите, с които одиторът е проверявал
реалните текстове (backend/tests/fixtures/regression_audit_texts.json). Не е тест; ползва се от test_phase22_*.py и следващите.

Синтетични данни: Тест А — одит (15.02.1990 13:00, София) и Иван (12.07.1985 18:45, Виена). Единичните периоди са във Виена,
двойните в София (отделна зона на прогнозата): разликата от един час е умишлена.
"""
import json
import os

import testenv  # noqa: F401  (трябва да е преди engine)
import engine
import text_check as tc
from scanner import TransitScanner

FIXTURES = os.path.join(os.path.dirname(os.path.abspath(__file__)), "fixtures")
A_NAME = "Тест А — одит"
IVAN = "Иван"
A_BIRTH = dict(date="1990-02-15", time="13:00", lat=42.6977, lon=23.3219)
IVAN_BIRTH = dict(date="1985-07-12", time="18:45", lat=48.2082, lon=16.3738)
SNAPSHOT = "2026-10-10 12:00"

_cache = {}


def texts():
    if "texts" not in _cache:
        with open(os.path.join(FIXTURES, "regression_audit_texts.json"), encoding="utf-8") as f:
            _cache["texts"] = json.load(f)
    return _cache["texts"]


def chart(key):
    if ("chart", key) not in _cache:
        birth = {"A": A_BIRTH, "Ivan": IVAN_BIRTH}[key]
        _cache[("chart", key)] = engine.get_engine().calculate_chart(**birth)
    return _cache[("chart", key)]


def sky(pair: bool):
    """Небето на 10.10.2026 12:00: във Виена за единичния анализ, в София за двойния."""
    key = ("sky", pair)
    if key not in _cache:
        place, zone = (A_BIRTH, "Europe/Sofia") if pair else (IVAN_BIRTH, "Europe/Vienna")
        _cache[key] = engine.get_engine().calculate_chart("2026-10-10", "12:00", place["lat"], place["lon"], zone=zone)
    return _cache[key]


def calendar(pair: bool, end="2026-10-31"):
    key = ("cal", pair, end)
    if key not in _cache:
        eng = engine.get_engine()
        if pair:
            _cache[key] = TransitScanner(engine_instance=eng).build_calendar(
                chart("A"), "2026-10-01", end, A_BIRTH["lat"], A_BIRTH["lon"], partner_chart=chart("Ivan"), tz_name="Europe/Sofia")
        else:
            _cache[key] = TransitScanner(engine_instance=eng).build_calendar(
                chart("Ivan"), "2026-10-01", end, IVAN_BIRTH["lat"], IVAN_BIRTH["lon"], tz_name="Europe/Vienna")
    return _cache[key]


def facts(mode: str, pair: bool, end="2026-10-31"):
    """Фактите за проверка на текст (както ги строи генерацията)."""
    key = ("facts", mode, pair, end)
    if key not in _cache:
        kwargs = dict(mode=mode, user_name=A_NAME if pair else IVAN, natal_chart=chart("A" if pair else "Ivan"),
                      partner_name=IVAN if pair else None, partner_chart=chart("Ivan") if pair else None)
        if mode == "snapshot":
            kwargs.update(transit_chart=sky(pair), target_date=SNAPSHOT)
        if mode == "period":
            kwargs.update(calendar=calendar(pair, end), report_date="2026-10-11")
        _cache[key] = tc.build_facts(**kwargs)
    return _cache[key]


CHILD_BIRTH = dict(date="2014-05-20", time="09:15", lat=42.1354, lon=24.7453)          # Пловдив, дете на 12 години


def unknown_ivan():
    """Иван без известен час (№47/48): картата е за 12:00 местно, без домове, ъгли и точна Луна."""
    if "unknown_ivan" not in _cache:
        _cache["unknown_ivan"] = engine.get_engine().calculate_chart(
            IVAN_BIRTH["date"], "12:00", IVAN_BIRTH["lat"], IVAN_BIRTH["lon"], time_known=False)
    return _cache["unknown_ivan"]


def child():
    if "child" not in _cache:
        _cache["child"] = engine.get_engine().calculate_chart(**CHILD_BIRTH)
    return _cache["child"]


def facts_for(row_id):
    """Фактите за един от реалните текстове на одита (по номер на опита)."""
    row = texts()[str(row_id)]
    if row_id == 47:                       # единичен натален, Иван без час
        return tc.build_facts(mode="natal", user_name=IVAN, natal_chart=unknown_ivan())
    if row_id == 48:                       # двоен моментен: А с известен час, Иван без час
        return tc.build_facts(mode="snapshot", user_name=A_NAME, natal_chart=chart("A"), partner_name=IVAN,
                              partner_chart=unknown_ivan(), transit_chart=sky(True), target_date=SNAPSHOT)
    if row_id == 49:                       # двоен натален: родител (Тест А) и дете (Пловдив, 2014)
        return tc.build_facts(mode="natal", user_name=A_NAME, natal_chart=chart("A"), partner_name="Тест дете — одит",
                              partner_chart=child())
    end = "2026-11-30" if row_id == 50 else "2026-10-31"
    return facts(row["mode"], row["pair"], end)


def violations(text, facts_obj):
    return [(v.code, v.severity, v.detail) for v in tc.check_text(text, facts_obj)]


def codes(text, facts_obj, severity=None):
    return sorted({v.code for v in tc.check_text(text, facts_obj) if severity in (None, v.severity)})
