"""
Натален анализ за двама (синастрия): фактите са карти от кода, AI само тълкува (трето приложение на card_engine.py).

Групи карти (всяка с идентификатор и заглавие, написано от кода):
- A и B: собствената натална карта на всеки човек: най-важните позиции (планета в знак и дом) и най-важните вътрешни аспекти;
- S: синастричните аспекти (планета на едного към планета на другия, с имената и на двамата);
- H: домовете на другия: „Венера на Иван попада в 7-ми дом на Мария“, винаги с посока (чия планета, в чий дом). Без час на
  раждане няма домове и тези карти не се измислят.

Подборът НЕ е само по орбис: орбисът подрежда, а важността идва от темата, отношенията (приятели, семейство, работа, родител и дете,
романтични) и ключовите думи на въпроса. Резултатът е score (по-малко е по-важно); картите с най-малък score са ключови.
В затварящите блокове (обобщение, съвети, отговор, заключение) няма планети, аспекти, знаци и домове: те са само смисъл, а фактите са
в картите. Лексикалната проверка на отговора е помощен филтър, не доказателство за смислов отговор.
"""
from typing import Dict, List, Optional, Tuple

import card_engine
import factpack
from aspects_engine import calculate_natal_aspects
from card_engine import Card, CardMode, Context
from pair_snapshot import _DEFINITE, _OBJECT, _ORDINAL, _orb, synastry_heading, with_word
from text_check import ASPECT_BG, PLANET_BG, SIGN_BG

DEFAULT_ORDER = ("Sun", "Moon", "Venus", "Mars", "Mercury", "Saturn", "Jupiter", "Node", "Chiron", "Pluto", "Neptune", "Uranus", "ASC", "MC")
LIMITS = {"placements": 3, "aspects": 2, "synastry": 5, "overlays": 3}

THEME_PLANETS = {
    "general": {"Sun", "Moon", "Venus", "Mars", "Mercury"},
    "love": {"Venus", "Mars", "Moon", "Sun"},
    "career": {"Sun", "Mars", "Saturn", "Jupiter", "Mercury", "MC"},
    "money": {"Venus", "Jupiter", "Saturn", "Mercury"},
    "health": {"Moon", "Mars", "Saturn", "Sun"},
    "karmic": {"Node", "Saturn", "Moon", "Pluto", "Chiron"},
}
RELATION_PLANETS = {
    "romantic": {"Venus", "Mars", "Moon", "Sun"},
    "friend": {"Mercury", "Jupiter", "Sun", "Moon"},
    "family": {"Moon", "Saturn", "Venus", "Sun"},
    "parent_child": {"Moon", "Saturn", "Sun", "Jupiter"},
    "work": {"Mercury", "Saturn", "Mars", "Jupiter", "Sun", "MC"},
    "general": {"Sun", "Moon", "Mercury", "Venus", "Mars"},
}
THEME_HOUSES = {"general": {1, 4, 7, 10}, "love": {5, 7, 8}, "career": {10, 6, 2}, "money": {2, 8}, "health": {6, 1, 12}, "karmic": {12, 4, 8}}
RELATION_HOUSES = {"romantic": {5, 7, 8, 1}, "friend": {11, 3, 7}, "family": {4, 10, 5}, "parent_child": {4, 5, 10, 6},
                   "work": {6, 10, 2, 7}, "general": {1, 4, 7, 10}}
# Основи на думите във въпроса -> планети, които той засяга
QUESTION_STEMS = (
    (("пари", "финанс", "разход", "бюджет", "заплат"), {"Venus", "Jupiter", "Saturn"}),
    (("работ", "кариер", "задач", "проект", "колег"), {"Sun", "Mars", "Saturn", "Mercury", "MC"}),
    (("дете", "деца", "родител", "отглежд", "възпит"), {"Moon", "Saturn"}),
    (("любов", "близост", "романт", "привличан", "интим"), {"Venus", "Mars", "Moon"}),
    (("здрав", "сън", "енерги", "умор", "стрес"), {"Moon", "Mars", "Saturn"}),
    (("разговор", "общув", "говор", "комуникац", "спор", "кавга"), {"Mercury", "Moon"}),
    (("граници", "авторитет", "отговорност", "дисциплин"), {"Saturn", "Mars"}),
    (("доверие", "сигурност", "стабилност", "привързан"), {"Moon", "Saturn", "Venus"}),
)


def question_planets(question: str) -> set:
    low = (question or "").lower()
    out: set = set()
    for stems, planets in QUESTION_STEMS:
        if any(stem in low for stem in stems):
            out |= planets
    return out


class Relevance:
    """Колко е важна планета или дом за темата, отношенията и въпроса. Само подрежда; не променя фактите."""

    def __init__(self, report_type: str, relation: str, question: str):
        self.theme = THEME_PLANETS.get(report_type, THEME_PLANETS["general"])
        self.relation = RELATION_PLANETS.get(relation or "general", RELATION_PLANETS["general"])
        self.asked = question_planets(question)
        self.theme_houses = THEME_HOUSES.get(report_type, THEME_HOUSES["general"])
        self.relation_houses = RELATION_HOUSES.get(relation or "general", RELATION_HOUSES["general"])

    def planet(self, name: str) -> int:
        return (name in self.theme) + (name in self.relation) + (name in self.asked)

    def house(self, number: int) -> int:
        return (number in self.theme_houses) + (number in self.relation_houses)

    def order(self, name: str) -> int:
        return DEFAULT_ORDER.index(name) if name in DEFAULT_ORDER else len(DEFAULT_ORDER)


def _deg(position: str) -> str:
    """„Gemini 6°53'“ -> „6°53'“."""
    return position.split(" ", 1)[1] if " " in position else position


def placement_heading(planet: str, owner: str, sign: str, degrees: str, house: Optional[int]) -> str:
    where = f"в {SIGN_BG.get(sign, sign)} ({degrees})"
    if house:
        where += f", в {_ORDINAL[house]} дом"
    return f"{_DEFINITE.get(planet, planet)} на {owner} е {where}."


def natal_aspect_heading(row: Dict, owner: str) -> str:
    obj = _OBJECT.get(row['planet2'], row['planet2'])
    return (f"{_DEFINITE.get(row['planet1'], row['planet1'])} на {owner} е в {ASPECT_BG[row['aspect']]} {with_word(obj)} "
            f"{obj} на {owner} (орб {_orb(row['orb'])}).")


def overlay_heading(planet: str, planet_owner: str, house: int, house_owner: str) -> str:
    return f"{_DEFINITE.get(planet, planet)} на {planet_owner} попада в {_ORDINAL[house]} дом на {house_owner}."


def _placements(chart: Dict, owner_key: str, owner: str, rel: Relevance) -> List[Card]:
    cards: List[Card] = []
    known = chart.get("time_known") is not False
    uncertain = set((chart.get("sign_ranges") or {}).keys())            # знакът се мени през деня: без час не се твърди
    planets = [p for p in chart.get("planets", {}) if p in PLANET_BG and p not in uncertain]
    for planet in sorted(planets, key=lambda p: (-rel.planet(p), rel.order(p)))[:LIMITS["placements"]]:
        info = chart["planets"][planet]
        formatted = info.get("formatted_pos") or ""
        sign = formatted.split(" ", 1)[0]
        house = info.get("house") if known else None
        if sign not in SIGN_BG:
            continue
        cards.append(Card("", "", placement_heading(planet, owner, sign, _deg(formatted), house),
                          row={"kind": "placement", "owner": owner_key, "planet": planet, "sign": sign, "house": house},
                          score=-1.0 * rel.planet(planet) + rel.order(planet) / 100.0))
    return cards


def _natal_aspects(chart: Dict, owner_key: str, owner: str, rel: Relevance) -> List[Card]:
    rows = []
    for row in calculate_natal_aspects(chart, use_wider_orbs=False):
        weight = rel.planet(row["planet1"]) + rel.planet(row["planet2"])
        if weight <= 0 or row["planet1"] not in PLANET_BG or row["planet2"] not in PLANET_BG:
            continue
        rows.append((float(row["orb"]) - 0.8 * min(weight, 3), row))
    cards = []
    for score, row in sorted(rows, key=lambda x: x[0])[:LIMITS["aspects"]]:
        cards.append(Card("", "", natal_aspect_heading(row, owner),
                          row={"kind": "natal", "owner": owner_key, "p1": row["planet1"], "p2": row["planet2"], "aspect": row["aspect"],
                               "orb": row["orb"]}, score=score))
    return cards


def _overlays(houses_chart: Dict, planets_chart: Dict, planet_owner_key: str, planet_owner: str, house_owner_key: str, house_owner: str,
              rel: Relevance) -> List[Card]:
    if not factpack.has_houses(houses_chart):
        return []
    cards = []
    ranked = []
    for planet, house in factpack.overlay(houses_chart, planets_chart).items():
        if planet not in PLANET_BG:
            continue
        ranked.append((-(rel.planet(planet) + rel.house(house)), rel.order(planet), planet, house))
    for total, _order, planet, house in sorted(ranked)[:LIMITS["overlays"]]:
        cards.append(Card("", "", overlay_heading(planet, planet_owner, house, house_owner),
                          row={"kind": "overlay", "po": planet_owner_key, "ho": house_owner_key, "planet": planet, "house": house},
                          score=float(total)))
    return cards


def build_cards(ctx: Context) -> List[Card]:
    rel = Relevance(ctx.report_type, ctx.relation, ctx.question)
    groups: Dict[str, List[Card]] = {
        "A": _placements(ctx.natal_chart, "user", ctx.first, rel) + _natal_aspects(ctx.natal_chart, "user", ctx.first, rel),
        "B": _placements(ctx.partner_chart, "partner", ctx.second, rel) + _natal_aspects(ctx.partner_chart, "partner", ctx.second, rel),
    }
    rows = []
    for row in factpack.synastry_aspects(ctx.natal_chart, ctx.partner_chart, ctx.first, ctx.second):
        if row["planet1"] not in PLANET_BG or row["planet2"] not in PLANET_BG:
            continue
        weight = rel.planet(row["planet1"]) + rel.planet(row["planet2"])
        rows.append((float(row["orb"]) - 0.8 * min(weight, 3), row))
    groups["S"] = []
    for score, row in sorted(rows, key=lambda x: x[0])[:LIMITS["synastry"]]:
        card = Card("", "", synastry_heading(row), row={"kind": "synastry", "o1": row["person1"], "p1": row["planet1"], "aspect": row["aspect"],
                                                          "o2": row["person2"], "p2": row["planet2"], "orb": row["orb"]}, score=score)
        groups["S"].append(card)
    groups["H"] = (_overlays(ctx.partner_chart, ctx.natal_chart, "user", ctx.first, "partner", ctx.second, rel)
                   + _overlays(ctx.natal_chart, ctx.partner_chart, "partner", ctx.second, "user", ctx.first, rel))
    out: List[Card] = []
    for group in ("A", "B", "S", "H"):
        for index, card in enumerate(sorted(groups[group], key=lambda c: c.score if c.score is not None else 0.0) if group != "H" else groups[group], 1):
            card.group, card.id = group, f"{group}{index}"
            out.append(card)
    return out


SYSTEM_PROMPT = (
    "MODE: TWO-PERSON NATAL ANALYSIS (SYNASTRY). You INTERPRET pre-written FACT CARDS; you do not state facts yourself.\n"
    "THEME: {theme} Keep EVERY section on this theme and on the relationship named below.\n"
    "People: {first} and {second}. Relationship: {relation}.\n\n"
    "The code has already written every fact: the position of each person's own planets, the aspects inside each chart, the aspects between "
    "the two charts, and which planet of one person falls in which house of the other (the direction is always named: whose planet, "
    "whose house). The facts are printed in the card headings above your text, so you never restate them.\n\n"
    "OUTPUT FORMAT (strict). Output ONLY sections. Each section starts with its tag alone on a line:\n"
    "{tags}"
    "[[SUMMARY]]  4-6 sentences: what the two people bring to each other and where it is easy or hard between them.\n"
    "[[ADVICE]]  3-5 bullets starting with \"- \": practical advice for the two people in line with the relationship, the theme and the question.\n"
    "{answer_tag}"
    "[[CONCLUSION]]  2-3 sentences with a reasoned overall conclusion.\n\n"
    "RULES FOR EVERY SECTION:\n"
    "1. In a card section write 2-3 sentences about THAT card only, in plain words. The heading already names the planets, the sign, "
    "the house, the aspect and the orb: do NOT name any planet, sign, house, aspect or degree there. Start directly with the meaning "
    "(for example: \"Така Иван внася...\").\n"
    "2. Never invent, compute or guess a planet, sign, house, aspect or degree. A fact that is not in a card does not exist for this analysis.\n"
    "3. In the card of a person's own chart write only about that person; in a house card keep the direction: the planet's owner is the "
    "one who brings the energy, the house's owner is the one whose life area it touches.\n"
    "4. In SUMMARY, ADVICE, ANSWER and CONCLUSION name NO planet, sign, house, aspect or degree: write only the meaning and rely only on "
    "the cards. Every recommendation must follow from a card. No predictions of the future, no diagnoses.\n"
    "5. Respect the relationship: never use a romantic or sexual frame unless the relationship is romantic.\n"
    "6. Be direct and concrete. No filler, no mystic phrases.\n"
)

COMPOSE_SYSTEM = (
    "MODE: TWO-PERSON NATAL ANALYSIS, COMPOSITION FROM VERIFIED BLOCKS.\n"
    "THEME: {theme} People: {first} and {second}. Relationship: {relation}.\n"
    "Below are the ONLY verified blocks (fact heading written by the code + a checked interpretation). Write the closing sections "
    "using ONLY them:\n"
    "[[SUMMARY]]  4-6 sentences: what the two people bring to each other and where it is easy or hard between them.\n"
    "[[ADVICE]]  3-5 bullets starting with \"- \": practical advice that follows from the blocks.\n"
    "{answer_tag}"
    "[[CONCLUSION]]  2-3 sentences with a reasoned overall conclusion.\n\n"
    "RULES: name NO planet, sign, house, aspect or degree: write only the meaning. Every recommendation must follow from a block. "
    "Respect the relationship (no romantic or sexual frame unless it is romantic). Direct, concrete style, no filler.\n"
)


NATAL_RESTORE_SYSTEM = (
    "You write ONE short interpretation for ONE pre-written fact card of a natal analysis. Output only the interpretation text, no tags.\n"
    "THEME: {theme}\n"
    "Write 2-3 sentences about THAT card only: what it means for the person (or for the pair), in plain words. The heading already names "
    "the planets, the sign, the house, the aspect and the orb: do NOT name any planet, sign, house, aspect or degree. Start directly with "
    "the meaning. Be direct and concrete, no filler, no predictions. Write in Bulgarian.\n"
)


class NatalPair(CardMode):
    name = "natal_pair"
    stage = "natalpair"
    person_groups: Tuple[str, ...] = ("A", "B")
    groups: Tuple[str, ...] = ("A", "B", "S", "H")
    key_per_group = {"A": 2, "B": 2, "S": 2, "H": 2}
    strict_cards = True
    closing_names_forbidden = True
    advice_title = "Какво да имате предвид"
    facts_stage = "natal"

    def build(self, ctx: Context):
        return build_cards(ctx), {}

    def group_title(self, group: str, ctx: Context) -> str:
        return {"A": f"Собствената карта на {ctx.first}", "B": f"Собствената карта на {ctx.second}", "S": "Взаимодействие между двете карти",
                "H": "Планетите в домовете на другия"}[group]

    def _relation(self, ctx: Context) -> str:
        return ctx.relation or "not specified (do not assume romance)"

    def system_prompt(self, ctx: Context, cards: List[Card], answer_tag: str) -> str:
        tags = "".join(f"[[{c.id}]]  interpretation of card {c.id}\n" for c in cards)
        return SYSTEM_PROMPT.format(theme=ctx.theme, first=ctx.first, second=ctx.second, relation=self._relation(ctx), tags=tags,
                                    answer_tag=answer_tag)

    def compose_system(self, ctx: Context, answer_tag: str) -> str:
        return COMPOSE_SYSTEM.format(theme=ctx.theme, first=ctx.first, second=ctx.second, relation=self._relation(ctx), answer_tag=answer_tag)

    def restore_system(self, ctx: Context) -> str:
        return NATAL_RESTORE_SYSTEM.format(theme=ctx.theme)

    def user_prompt(self, interp, ctx: Context, cards: List[Card], lines: Dict[str, str]) -> str:
        user = f"User Question: {ctx.question or 'Provide a synastry analysis for this relationship.'}\n\n"
        user += factpack.identity_section(ctx.first, ctx.gender, ctx.second, ctx.partner_gender)
        user += f"RELATIONSHIP: {self._relation(ctx)}\n\n"
        user += "FACT CARDS (written by the code; every position, aspect and house is verified):\n"
        user += "".join(f"{c.id}: {c.heading}\n" for c in cards) + "\n"
        return user + "Write the sections now, in Bulgarian."

    def facts_kwargs(self, ctx: Context) -> Dict:
        return dict(mode="natal", user_name=ctx.user_name, natal_chart=ctx.natal_chart, partner_name=ctx.partner_name,
                    partner_chart=ctx.partner_chart, report_date=ctx.utc_today, question=ctx.question or "")


MODE = NatalPair()


async def interpret(interp, *, natal_chart: Dict, partner_chart: Dict, user_name: Optional[str], partner_name: Optional[str], question: str,
                    report_type: str, relation: str, checks: List[Dict], theme: str, utc_today: str, gender: Optional[str] = None,
                    partner_gender: Optional[str] = None) -> str:
    ctx = Context(natal_chart=natal_chart, partner_chart=partner_chart, transit_chart={}, user_name=user_name, partner_name=partner_name,
                  first=factpack.display_name(user_name, factpack.FIRST_PERSON_DEFAULT),
                  second=factpack.display_name(partner_name, factpack.SECOND_PERSON_DEFAULT), question=question or "", target_date="",
                  theme=theme, utc_today=utc_today, gender=gender, partner_gender=partner_gender, report_type=report_type or "general",
                  relation=relation or "")
    return await card_engine.run(interp, MODE, ctx, checks)
