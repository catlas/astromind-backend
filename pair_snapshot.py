"""
Анализ „за дата“ за двама души: фактите се пишат от кода, AI само тълкува (решение на собственика, 12.10.2026).

Защо: в живото приемане AI смесваше притежателя на планетите и вида на аспекта, а проверката основателно отказваше
текста (3 от 3 опита). Картите, проверката, възстановяването, резервният и частичният анализ са в card_engine.py (общи за
режимите); тук са само картите и подканите на този режим:
- група A: транзитите към първия човек; B: към втория; S: взаимодействието между двете натални карти;
- ред за домовете на транзитните планети за всеки човек с известен час (изцяло от кода).
"""
from typing import Dict, List, Optional, Tuple

import card_engine
import factpack
from card_engine import (COMPOSITION, MAX_FAILED_CARDS, MIN_KEPT_PER_PERSON, Card, CardMode, Context, minimum_met,  # noqa: F401 (за тестовете)
                         parse_sections)
from text_check import ASPECT_BG, PLANET_BG

CARD_LIMITS = {"A": 6, "B": 6, "S": 4}            # най-много карти в група: най-тесните аспекти

_NEUTER = {"Sun"}
_FEMININE = {"Moon", "Venus"}
_DEFINITE = {"Sun": "Слънцето", "Moon": "Луната", "Mercury": "Меркурий", "Venus": "Венера", "Mars": "Марс", "Jupiter": "Юпитер",
             "Saturn": "Сатурн", "Uranus": "Уран", "Neptune": "Нептун", "Pluto": "Плутон", "Chiron": "Хирон",
             "Node": "Възходящият възел", "ASC": "Асцендентът", "MC": "MC"}
_OBJECT = {**_DEFINITE, "Node": "Възходящия възел", "ASC": "Асцендента"}
_ORDINAL = {1: "1-ви", 2: "2-ри", 3: "3-ти", 4: "4-ти", 5: "5-ти", 6: "6-ти", 7: "7-ми", 8: "8-ми", 9: "9-ти", 10: "10-и",
            11: "11-ти", 12: "12-ти"}


def _gender(planet: str) -> str:
    return "n" if planet in _NEUTER else ("f" if planet in _FEMININE else "m")


_ADJECTIVES = {"транзит": {"m": "транзитният", "f": "транзитната", "n": "транзитното"},
               "натал": {"m": "наталния", "f": "наталната", "n": "наталното"}}


def _adjective(base: str, planet: str) -> str:
    """транзитен/натален с род и определителен член: „транзитният Юпитер“, „наталната Венера“, „наталното Слънце“."""
    return _ADJECTIVES[base][_gender(planet)]


def _orb(value) -> str:
    return f"{float(value):.2f}".replace(".", ",") + "°"


def _applying(row: Dict) -> str:
    flag = row.get("applying")
    return ", приближава се" if flag is True else (", отдалечава се" if flag is False else "")


def transit_heading(row: Dict, owner: str) -> str:
    tp, npl = row["transit_planet"], row["natal_planet"]
    return (f"{_adjective('транзит', tp).capitalize()} {PLANET_BG.get(tp, tp)} е в {ASPECT_BG[row['aspect']]} с "
            f"{_adjective('натал', npl)} {PLANET_BG.get(npl, npl)} на {owner} (орб {_orb(row['orb'])}{_applying(row)}).")


def with_word(word: str) -> str:
    """„с“ или „със“ пред думата: със Слънцето, със Сатурн, с Луната."""
    return "със" if word[:1] in "сСзЗ" else "с"


def synastry_heading(row: Dict) -> str:
    obj = _OBJECT.get(row['planet2'], row['planet2'])
    return (f"{_DEFINITE.get(row['planet1'], row['planet1'])} на {row['person1']} е в {ASPECT_BG[row['aspect']]} {with_word(obj)} "
            f"{obj} на {row['person2']} (орб {_orb(row['orb'])}).")


def transit_card(card_id: str, group: str, row: Dict, owner_name: str, owner_key: str) -> Card:
    return Card(card_id, group, transit_heading(row, owner_name),
                row={"kind": "transit", "owner": owner_key, "p1": row["transit_planet"], "p2": row["natal_planet"],
                     "aspect": row["aspect"], "orb": row["orb"]})


def build_cards(natal: Dict, partner: Dict, transit: Dict, first: str, second: str) -> List[Card]:
    cards: List[Card] = []
    for group, chart, owner, key in (("A", natal, first, "user"), ("B", partner, second, "partner")):
        rows = sorted(factpack.transit_aspects(chart, transit), key=lambda r: r["orb"])[:CARD_LIMITS[group]]
        for index, row in enumerate(rows, 1):
            cards.append(transit_card(f"{group}{index}", group, row, owner, key))
    rows = sorted(factpack.synastry_aspects(natal, partner, first, second), key=lambda r: r["orb"])[:CARD_LIMITS["S"]]
    for index, row in enumerate(rows, 1):
        cards.append(Card(f"S{index}", "S", synastry_heading(row),
                          row={"kind": "synastry", "o1": row["person1"], "p1": row["planet1"], "aspect": row["aspect"],
                               "o2": row["person2"], "p2": row["planet2"], "orb": row["orb"]}))
    return cards


def houses_line(chart: Dict, transit: Dict, owner: str) -> str:
    """Къде са транзитните планети в домовете на човека: изцяло от кода (само при известен час на раждане)."""
    if not factpack.has_houses(chart):
        return ""
    overlay = factpack.overlay(chart, transit)
    parts = [f"{PLANET_BG.get(p, p)} — {_ORDINAL.get(h, str(h) + '-и')} дом" for p, h in overlay.items() if p in PLANET_BG]
    return f"Транзитните планети в домовете на {owner}: " + "; ".join(parts) + "." if parts else ""


SYSTEM_PROMPT = (
    "MODE: TWO-PERSON SNAPSHOT for ONE chosen date and time. You INTERPRET pre-written FACT CARDS; you do not state facts yourself.\n"
    "THEME: {theme} Keep EVERY section on this theme.\n"
    "People: {first} and {second}. The sky is for the moment {when}. Use ONLY this date.\n\n"
    "The code has already written every fact: whose planet it is, natal or transiting, the aspect, the orb, the houses. "
    "They are printed above your text, so you never restate them.\n\n"
    "OUTPUT FORMAT (strict). Output ONLY sections. Each section starts with its tag alone on a line:\n"
    "{tags}"
    "[[SUMMARY]]  4-6 sentences: how the two people's days interact. Say who needs patience with whom.\n"
    "[[ADVICE]]  3-5 bullets starting with \"- \": what to focus on or avoid on this date, in line with the theme and the question.\n"
    "{answer_tag}"
    "[[CONCLUSION]]  2-3 sentences with a reasoned overall conclusion.\n\n"
    "RULES FOR EVERY SECTION:\n"
    "1. In a card section write 2-3 sentences about THAT card only: what it means for the person (or for the pair) on this date, in plain "
    "words. The heading above your text already names the planets, the aspect and the orb: do NOT name any planet, aspect, sign, house or "
    "degree there, start directly with the meaning. Begin each card in a different way: never start two cards with the same word.\n"
    "2. Mention a planet, aspect, house, sign, degree or date only if it is in a card, in the houses lines or in the natal data. "
    "Never invent, compute or guess one. An aspect that is not in a card does not exist for this analysis.\n"
    "3. Every planet belongs to ONE person and is either natal or transiting: whenever you name a planet in an aspect, say whose it is.\n"
    "4. In SUMMARY, ADVICE, ANSWER and CONCLUSION rely only on the cards: every recommendation must follow from a card; never mention an "
    "aspect that is not one of the cards. No orb numbers, no times of day, no health diagnoses.\n"
    "5. Refer to the chosen date only as \"в този ден\" or \"на избраната дата\": never \"днес\", \"утре\" or \"вчера\".\n"
    "6. Be direct and concrete. No filler, no mystic phrases, no promises about the future.\n"
)

COMPOSE_SYSTEM = (
    "MODE: TWO-PERSON SNAPSHOT, COMPOSITION FROM VERIFIED BLOCKS.\n"
    "THEME: {theme} People: {first} and {second}. Moment: {when}.\n"
    "Below are the ONLY verified blocks (fact heading written by the code + a checked interpretation). Write the closing sections "
    "using ONLY them:\n"
    "[[SUMMARY]]  4-6 sentences: how the two people's days interact. Say who needs patience with whom.\n"
    "[[ADVICE]]  3-5 bullets starting with \"- \": practical advice that follows from the blocks.\n"
    "{answer_tag}"
    "[[CONCLUSION]]  2-3 sentences with a reasoned overall conclusion.\n\n"
    "RULES: use no aspect, planet position, house, sign, degree or date that is not in the verified blocks; an aspect that is not in one "
    "of them does not exist. Whenever you name a planet in an aspect, say whose it is and whether it is natal or transiting. No orb "
    "numbers, no times of day. Every recommendation must follow from a block. Refer to the chosen date only as \"в този ден\" or "
    "\"на избраната дата\": never \"днес\", \"утре\" or \"вчера\". Direct, concrete style, no filler.\n"
)


class PairSnapshot(CardMode):
    name = "pair_snapshot"
    stage = "pair"
    person_groups: Tuple[str, ...] = ("A", "B")

    def build(self, ctx: Context):
        cards = build_cards(ctx.natal_chart, ctx.partner_chart, ctx.transit_chart, ctx.first, ctx.second)
        lines = {"A": houses_line(ctx.natal_chart, ctx.transit_chart, ctx.first),
                 "B": houses_line(ctx.partner_chart, ctx.transit_chart, ctx.second)}
        return cards, lines

    def group_title(self, group: str, ctx: Context) -> str:
        return {"A": f"Транзитите към {ctx.first}", "B": f"Транзитите към {ctx.second}", "S": "Взаимодействие между двете карти"}[group]

    def system_prompt(self, ctx: Context, cards: List[Card], answer_tag: str) -> str:
        tags = "".join(f"[[{c.id}]]  interpretation of card {c.id}\n" for c in cards)
        return SYSTEM_PROMPT.format(theme=ctx.theme, first=ctx.first, second=ctx.second, when=ctx.target_date, tags=tags, answer_tag=answer_tag)

    def compose_system(self, ctx: Context, answer_tag: str) -> str:
        return COMPOSE_SYSTEM.format(theme=ctx.theme, first=ctx.first, second=ctx.second, when=ctx.target_date, answer_tag=answer_tag)

    def user_prompt(self, interp, ctx: Context, cards: List[Card], lines: Dict[str, str]) -> str:
        user = f"User Question: {ctx.question or 'Provide a relationship forecast for this specific date.'}\n\n"
        user += factpack.identity_section(ctx.first, ctx.gender, ctx.second, ctx.partner_gender)
        user += "FACT CARDS (written by the code; every number and owner is verified):\n" + "".join(f"{c.id}: {c.heading}\n" for c in cards) + "\n"
        for group in ("A", "B"):
            if lines.get(group):
                user += lines[group] + "\n"
        user += "\n" + interp._natal_block(ctx.first, ctx.natal_chart) + interp._natal_block(ctx.second, ctx.partner_chart)
        return user + "Write the sections now, in Bulgarian."

    def facts_kwargs(self, ctx: Context) -> Dict:
        return dict(mode="snapshot", user_name=ctx.user_name, natal_chart=ctx.natal_chart, partner_name=ctx.partner_name,
                    partner_chart=ctx.partner_chart, transit_chart=ctx.transit_chart, target_date=ctx.target_date,
                    report_date=ctx.utc_today, question=ctx.question or "")


MODE = PairSnapshot()


async def interpret(interp, *, natal_chart: Dict, partner_chart: Dict, transit_chart: Dict, user_name: Optional[str],
                    partner_name: Optional[str], question: str, target_date: str, report_type: str, checks: List[Dict],
                    theme: str, utc_today: str, gender: Optional[str] = None, partner_gender: Optional[str] = None) -> str:
    ctx = Context(natal_chart=natal_chart, partner_chart=partner_chart, transit_chart=transit_chart, user_name=user_name,
                  partner_name=partner_name, first=factpack.display_name(user_name, factpack.FIRST_PERSON_DEFAULT),
                  second=factpack.display_name(partner_name, factpack.SECOND_PERSON_DEFAULT), question=question or "",
                  target_date=target_date, theme=theme, utc_today=utc_today, gender=gender, partner_gender=partner_gender)
    return await card_engine.run(interp, MODE, ctx, checks)


def assemble(cards, names, lines, blocks, question):                      # съвместимост с тестовете
    ctx = Context(natal_chart={}, partner_chart={}, transit_chart={}, user_name=None, partner_name=None, first=names[0],
                  second=names[1], question=question, target_date="", theme="", utc_today="")
    return card_engine.assemble(MODE, ctx, cards, lines, blocks)
