"""
Непознат час на раждане (Фаза 12).

Часът не се измисля. Когато часът на раждане е неизвестен, картата няма Асцендент, МС, домове и управители на домове, а
Луната няма позиция: смята се само в кои знаци е била през деня (виж engine._calculate_chart_without_time). Този модул
прави две неща:
- добавя към системния промпт на всяка AI заявка правило какво не се пише за този човек. Правилото върви през ContextVar,
  както контекстът на отношенията (relationship.py), затова шаблоните на темите не се променят;
- съставя бележката за читателя, която влиза в началото на запазения анализ.

Правилото и бележката важат за човека, чийто час е неизвестен. Вторият човек (ако часът му е известен) се анализира както
обикновено, включително домовете му.
"""
from contextvars import ContextVar, Token
from typing import Dict, List, Optional, Sequence, Tuple

from text_check import SIGN_BG

current: ContextVar[str] = ContextVar("astro_birth_time_context", default="")
# Напомняне в края на потребителския промпт: шаблоните му описват секции с домове, които за тези хора не съществуват
reminder: ContextVar[str] = ContextVar("astro_birth_time_reminder", default="")

# Името на тялото в изречение
BODY_BG = {"Sun": "Слънцето", "Moon": "Луната", "Mercury": "Меркурий", "Venus": "Венера", "Mars": "Марс",
           "Jupiter": "Юпитер", "Saturn": "Сатурн", "Uranus": "Уран", "Neptune": "Нептун", "Pluto": "Плутон",
           "Node": "Лунният възел", "Chiron": "Хирон"}

RULES = (
    "BIRTH TIME UNKNOWN (stated by the user) for: {names}.\n"
    "For each of these people the data has NO houses, NO Ascendant, NO MC, NO house cusps and NO house rulers. This rule "
    "overrides every earlier instruction or example that asks for an Ascendant, MC, house, house-ruler or 'houses of "
    "emphasis' section or that uses those points: for these people write none of those sections and do not mention "
    "houses, the Ascendant or rising sign, the MC or house rulers at all, not even to explain what is missing. Build the "
    "analysis from planets in signs, aspects between planets, the theme and the question. If another person in this "
    "analysis has a known birth time, houses work for that person as usual.\n"
    "Planets of these people are given for local noon of the birth date. The Moon has no position and no aspects. Where the "
    "data lists possible_signs for a body, name those signs: for two signs say that the sign depends on the birth time, "
    "never pick one and never invent a degree for the Moon. The reader already sees a note about the unknown birth time at "
    "the top of the analysis, so do not repeat it and do not apologize for what is missing."
)


REMINDER = (
    "REMINDER (birth time unknown for: {names}): there is no house, house-overlay, house-ruler, Ascendant or MC data for "
    "them. Ignore every instruction above that asks for houses, house overlays, house rulers, the Ascendant or the MC "
    "for these people and do not write those parts."
)


def unknown_people(people: Sequence[Tuple[str, Optional[Dict]]]) -> List[Tuple[str, Dict]]:
    """Хората, чиято карта е без час: [(име, карта)]."""
    return [(name, chart) for name, chart in people if chart and chart.get("time_known") is False]


def block(names: Sequence[str]) -> str:
    """Правилото за системния промпт ("" когато няма човек без час)."""
    names = [n for n in names if n]
    return RULES.format(names=", ".join(names)) if names else ""


def _signs_text(signs: Sequence[str]) -> str:
    return " или ".join(SIGN_BG.get(s, s) for s in signs)


def sign_sentences(chart: Dict) -> List[str]:
    """Кои тела зависят от часа: „Луната е в Козирог или Водолей“, „Луната е в Овен (знакът е сигурен, градусът не е)“."""
    out = []
    for planet, info in (chart.get("sign_ranges") or {}).items():
        label = BODY_BG.get(planet, planet)
        signs = info.get("signs") or []
        if len(signs) == 1:
            out.append(f"{label} е в {_signs_text(signs)} (знакът е сигурен, точният градус не е)")
        elif signs:
            out.append(f"{label} е в {_signs_text(signs)}, според часа")
    return out


def note(people: Sequence[Tuple[str, Optional[Dict]]]) -> str:
    """Бележката в началото на анализа ("" когато часът е известен за всички)."""
    unknown = unknown_people(people)
    if not unknown:
        return ""
    names = " и ".join(name for name, _ in unknown)
    parts = [f"**Бележка за часа на раждане.** За {names} часът на раждане не е известен, затова анализът не включва "
             f"Асцендент, MC и домове. Планетите са изчислени за 12:00 местно време на датата на раждане."]
    for name, chart in unknown:
        sentences = sign_sentences(chart)
        if sentences:
            prefix = f"За {name}: " if len(unknown) > 1 else ""
            parts.append(prefix + "; ".join(sentences) + ".")
    return " ".join(parts)


def bind(names: Sequence[str]) -> Tuple[Token, Token]:
    """Закача правилото и напомнянето за текущата задача (виж generation.run). Връща токени за unbind."""
    names = [n for n in names if n]
    return current.set(block(names)), reminder.set(REMINDER.format(names=", ".join(names)) if names else "")


def unbind(tokens: Tuple[Token, Token]) -> None:
    current.reset(tokens[0])
    reminder.reset(tokens[1])
