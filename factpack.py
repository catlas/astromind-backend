"""
Проверени факти за AI (Фаза 8).

AI получава готови знак, градус, дом, управител и аспекти с ясен собственик и посока,
вместо сурови градуси и примери за подражание. Тук няма AI и няма мрежа: само чисти
функции върху картите от engine.calculate_chart, затова всичко се тества офлайн.

Правила:
- Куспидите и позициите имат знак и градус, изчислени от незакръглената дължина.
- Домът на планетата по натала и домът в картата на момента не се смесват:
  транзитните планети нямат собствено поле "house".
- Наслагванията („планетите на единия в домовете на другия“) са винаги две, с имена.
- Аспектите между небето в момента и наталната карта идват от aspects_engine с една
  политика на орбисите (виж ORB_POLICY_VERSION).
"""
import json
import re
import unicodedata
from typing import Dict, List, Optional

from aspects_engine import (
    TRANSIT_SNAPSHOT_MAX_ORB,
    calculate_synastry_aspects,
    calculate_transit_aspects_to_natal,
)
from engine import AstrologyEngine, decimal_to_dms, house_for_longitude

FACTPACK_VERSION = "8.0"
HOUSE_KEYS = [f"House{i}" for i in range(1, 13)]
# Тела, които се броят като „ретроградни планети“ (Слънце и Луна не са; възелът е точка, не планета)
RETROGRADE_BODIES = ("Mercury", "Venus", "Mars", "Jupiter", "Saturn", "Uranus", "Neptune", "Pluto", "Chiron")
MAX_TRANSIT_ASPECTS = 30

FIRST_PERSON_DEFAULT = "Първи човек"
SECOND_PERSON_DEFAULT = "Втори човек"

_NAME_NOISE = re.compile(r"[^\w\s.\-’']", re.UNICODE)
_MALE = {"male", "m", "man", "мъж", "мъжки"}
_FEMALE = {"female", "f", "woman", "жена", "женски"}


def display_name(raw: Optional[str], default: str, limit: int = 60) -> str:
    """Име за показване в подканата: без управляващи знаци и странни символи, до limit знака."""
    text = unicodedata.normalize("NFKC", str(raw or ""))
    text = _NAME_NOISE.sub(" ", text)
    text = " ".join(text.split())[:limit].strip()
    return text or default


def gender_label(raw: Optional[str]) -> str:
    """male / female / unknown. Всичко друго (включително „друг“ и празно) е unknown."""
    value = str(raw or "").strip().lower()
    if value in _MALE:
        return "male"
    if value in _FEMALE:
        return "female"
    return "unknown"


def section(title: str, note: str, payload) -> str:
    """Един надписан блок от данни в подканата. Компактен JSON (по-малко токени)."""
    body = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
    return f"--- {title} ---\n{note}\n{body}\n\n"


def identity_section(first_name: str, first_gender: Optional[str],
                     second_name: Optional[str] = None, second_gender: Optional[str] = None) -> str:
    """Кои са хората и какъв пол е известен. При неизвестен пол езикът е неутрален."""
    people = {"first_person": {"name": first_name, "gender": gender_label(first_gender)}}
    if second_name:
        people["second_person"] = {"name": second_name, "gender": gender_label(second_gender)}
    note = ("Use these names exactly. If a person's gender is \"unknown\", write in gender-neutral Bulgarian: "
            "never guess gender from a name, from the chart or from the account holder, and prefer \"Вие\" or "
            "impersonal wording over he/she forms.")
    return section("PEOPLE", note, people)


def _retrograde(planet: Dict) -> bool:
    speed = planet.get("speed")
    return bool(speed is not None and speed < 0)


def house_table(chart: Dict) -> Dict[str, Dict[str, str]]:
    """Куспида (знак и градус) и управител за всичките 12 дома. Управител: модерен (Плутон, Уран, Нептун)."""
    houses = chart.get("houses") or {}
    table: Dict[str, Dict[str, str]] = {}
    for key in HOUSE_KEYS:
        longitude = houses.get(key)
        if longitude is None:
            continue
        parts = decimal_to_dms(longitude)
        table[key] = {"cusp": parts["str"], "ruler": AstrologyEngine.get_house_ruler(parts["sign"])}
    return table


def house_rulers(chart: Dict) -> Dict[str, str]:
    """{"house_1_ruler": "Mars", ..., "house_12_ruler": ...}: същите ключове като engine.get_house_rulers."""
    return {f"house_{key[5:]}_ruler": row["ruler"] for key, row in house_table(chart).items()}


def _retrograde_summary(planets: Dict[str, Dict]) -> Dict:
    names = [name for name in RETROGRADE_BODIES if name in planets and planets[name].get("retrograde")]
    return {"retrograde_planets": names, "retrograde_count": len(names)}


def natal_view(chart: Dict) -> Dict:
    """Натална карта за AI: без сурови градуси; всяка планета със знак, позиция, натален дом и ретроградност."""
    planets: Dict[str, Dict] = {}
    for name, planet in (chart.get("planets") or {}).items():
        if planet.get("longitude") is None:
            continue
        planets[name] = {
            "zodiac_sign": planet.get("zodiac_sign"),
            "formatted_pos": planet.get("formatted_pos"),
            "house": planet.get("house"),
            "retrograde": _retrograde(planet),
        }
    angles = chart.get("angles") or {}
    return {
        "planets": planets,
        **_retrograde_summary(planets),
        "houses": house_table(chart),
        "angles": {key: angles[key] for key in ("Ascendant_formatted", "MC_formatted", "Ascendant_sign", "MC_sign")
                   if key in angles},
        "datetime_local": chart.get("datetime_local"),
        "timezone": chart.get("timezone"),
    }


def transit_view(chart: Dict) -> Dict:
    """
    Небето в избрания момент. Без поле "house": домът на транзитна планета е само в таблицата
    „транзитните планети в натални домове“, никога от картата на момента.
    """
    planets: Dict[str, Dict] = {}
    for name, planet in (chart.get("planets") or {}).items():
        if planet.get("longitude") is None:
            continue
        planets[name] = {
            "zodiac_sign": planet.get("zodiac_sign"),
            "formatted_pos": planet.get("formatted_pos"),
            "retrograde": _retrograde(planet),
        }
    return {
        "planets": planets,
        **_retrograde_summary(planets),
        "datetime_local": chart.get("datetime_local"),
        "datetime_utc": chart.get("datetime_utc"),
        "timezone": chart.get("timezone"),
    }


def overlay(houses_chart: Dict, planets_chart: Dict) -> Dict[str, int]:
    """В кой дом на houses_chart попада всяка планета на planets_chart (куспидите са на houses_chart)."""
    cusps = houses_chart.get("houses") or {}
    if not cusps:
        raise ValueError("Липсват куспиди за наслагването")
    result: Dict[str, int] = {}
    for name, planet in (planets_chart.get("planets") or {}).items():
        longitude = planet.get("longitude")
        if longitude is None:
            continue
        house = house_for_longitude(longitude, cusps)
        if house is not None:
            result[name] = house
    return result


def synastry_aspects(first_chart: Dict, second_chart: Dict, first_name: str, second_name: str) -> List[Dict]:
    """Аспекти между двете карти с изрично име на собственика на всяка планета."""
    rows = calculate_synastry_aspects(first_chart, second_chart, use_wider_orbs=False)
    return [
        {"person1": first_name, "planet1": row["planet1"], "aspect": row["aspect"],
         "person2": second_name, "planet2": row["planet2"], "angle": row["angle"], "orb": row["orb"]}
        for row in rows
    ]


def transit_aspects(natal_chart: Dict, transit_chart: Dict) -> List[Dict]:
    """Активни аспекти между небето в момента и наталната карта (политика: до TRANSIT_SNAPSHOT_MAX_ORB°)."""
    rows = calculate_transit_aspects_to_natal(
        natal_chart, transit_chart, max_orb=TRANSIT_SNAPSHOT_MAX_ORB, include_angles=True)
    return rows[:MAX_TRANSIT_ASPECTS]
