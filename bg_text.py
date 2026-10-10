"""
Български думи в готовия текст.

Данните към AI са на английски (имена на знаци и планети, „natal“), а AI понякога ги препраща в текста: „Новолуние в Libra“,
„natal Луна“. Тук такива думи се заменят с български. Правилата към AI (ai_interpreter._get_bulgarian_language_rules) казват същото;
замяната е последната предпазна мрежа и работи за всеки отговор, включително поправките. Имената на хората не се пипат:
„Лео“ или „Leo“ като име остава име.
"""
import re
from contextvars import ContextVar, Token
from typing import Iterable, Tuple

SIGNS = {"Aries": "Овен", "Taurus": "Телец", "Gemini": "Близнаци", "Cancer": "Рак", "Leo": "Лъв", "Virgo": "Дева",
         "Libra": "Везни", "Scorpio": "Скорпион", "Sagittarius": "Стрелец", "Capricorn": "Козирог",
         "Aquarius": "Водолей", "Pisces": "Риби"}
PLANETS = {"Sun": "Слънце", "Moon": "Луна", "Mercury": "Меркурий", "Venus": "Венера", "Mars": "Марс", "Jupiter": "Юпитер",
           "Saturn": "Сатурн", "Uranus": "Уран", "Neptune": "Нептун", "Pluto": "Плутон", "Chiron": "Хирон"}
# Род на българската дума (за „natal“ → натален / натална / натално)
FEMININE = {"Луна", "Венера", "карта", "картата", "позиция", "позицията", "планета", "планетата", "куспида"}
NEUTER = {"Слънце", "Слънцето"}

protected: ContextVar[Tuple[str, ...]] = ContextVar("astro_protected_names", default=())

_WORD = r"(?<![\w-])%s(?![\w-])"
ASPECTS = {"conjunction": "съвпад", "sextile": "секстил", "square": "квадратура", "trine": "тригон", "opposition": "опозиция",
           "retrograde": "ретрограден"}


def _any(words) -> str:
    return _WORD % ("(" + "|".join(words) + ")")


_SIGN_RE = re.compile(_any(SIGNS))
_SIGN_UPPER_RE = re.compile(_any(k.upper() for k in SIGNS))
_PLANET_RE = re.compile(_any(PLANETS))
_ASPECT_RE = re.compile(_any(list(ASPECTS) + [k.capitalize() for k in ASPECTS]))
_NATAL_RE = re.compile(r"(?<![\w-])([Nn]atal|NATAL|[Tt]ransit(?:ing)?)(?![\w-])(\s+)?([А-Яа-яA-Za-z]+)?")
_PREP_RE = re.compile(r"(?<![\w-])([Вв])(\s+)(?=[ВвФф])")           # „в Везни“ → „във Везни“


def bind(names: Iterable[str]) -> Token:
    return protected.set(tuple(n for n in names if n))


def unbind(token: Token) -> None:
    protected.reset(token)


def _ending(next_word: str) -> str:
    """Окончание по рода на следващата дума: "" мъжки, "а" женски, "о" среден."""
    if next_word in NEUTER:
        return "о"
    return "а" if next_word in FEMININE else ""


def _natal(match: re.Match) -> str:
    word, space, following = match.group(1), match.group(2) or "", match.group(3) or ""
    upper = word.isupper()
    natal = word.lower() == "natal"
    following = PLANETS.get(following, following)
    # „natal картата“ → „наталната карта“: определеният член вече е в следващата дума
    if natal and following in ("карта", "картата"):
        out = ("наталната" if following == "картата" else "натална") + space + "карта"
        return out.upper() if upper else out
    forms = {"натал": {"": "натален", "а": "натална", "о": "натално"}, "транзит": {"": "транзитен", "а": "транзитна", "о": "транзитно"}}
    out = forms["натал" if natal else "транзит"][_ending(following)] + space + following
    return out.upper() if upper else out


def _aspect(match: re.Match) -> str:
    """Дума за аспект: с главна буква само в началото на изречение или ред."""
    word = ASPECTS[match.group(1).lower()]
    before = match.string[:match.start()].rstrip(" \t*#-•>")
    return word.capitalize() if not before or before[-1] in ".!?:\n" else word


def bulgarianize(text: str) -> str:
    """Заменя английските имена на знаци и планети и „natal/transit“ с български. Защитените имена остават."""
    if not text:
        return text
    keep = sorted(protected.get(), key=len, reverse=True)
    holders = {}
    for index, name in enumerate(keep):
        marker = f"\u0000{index}\u0000"
        holders[marker] = name
        text = re.sub(_WORD % re.escape(name), marker, text)
    text = _SIGN_RE.sub(lambda m: SIGNS[m.group(1)], text)
    text = _SIGN_UPPER_RE.sub(lambda m: SIGNS[next(k for k in SIGNS if k.upper() == m.group(1))].upper(), text)
    text = _PLANET_RE.sub(lambda m: PLANETS[m.group(1)], text)
    text = _ASPECT_RE.sub(lambda m: _aspect(m), text)
    text = _NATAL_RE.sub(_natal, text)
    text = _PREP_RE.sub(lambda m: ("Във" if m.group(1) == "В" else "във") + m.group(2), text)
    for marker, name in holders.items():
        text = text.replace(marker, name)
    return text


def for_report(content: str, params: dict, profile_name: str = "") -> str:
    """Запазен отчет за показване и износ: старите отчети с английски думи се четат на български (записът не се променя)."""
    params = params or {}
    token = bind([params.get("name"), params.get("partner_name"), profile_name])
    try:
        return bulgarianize(content)
    finally:
        unbind(token)
