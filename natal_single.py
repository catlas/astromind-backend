"""
Натален анализ за един човек по карти (четвърто приложение на card_engine.py): фактите се пишат от кода, AI само тълкува.

Една група карти, A: собствената натална карта на човека, в реда на четене:
- позиции: планета в знак и (при известен час) в дом. Кои планети влизат се определя от темата и въпроса, а при известен час и от
  домовете на темата (кариера: 10-и, 6-и, 2-ри дом); Слънцето, Луната, Меркурий, Венера и Марс имат основна тежест;
- ъгли: Асцендент и MC само при известен час на раждане (без час няма ъгли и те не се измислят);
- най-важните вътрешни аспекти по темата, подредени по орбис минус тежест на темата.
Подборът само подрежда и избира: никога не променя факт. Планетите със знак, който се мени през деня (Луната без час), не са карта.

Тълкуването на карта няма право да назовава други планети, аспекти, знаци, домове, градуси и ъгли, а затварящите блокове (обобщение,
съвети, отговор, заключение) не назовават планети, знаци, домове, аспекти и ъгли: те са само смисъл. Лексикалната проверка на отговора
е помощен филтър, не доказателство за смислов отговор. Картите, проверката, възстановяването, резервният и частичният анализ са в
card_engine.py.
"""
from typing import Dict, List, Optional, Tuple

import card_engine
import factpack
import text_check
from aspects_engine import calculate_natal_aspects
from card_engine import Card, CardMode, Context
from natal_pair import NATAL_RESTORE_SYSTEM, Relevance, _deg, natal_aspect_heading, placement_heading
from pair_snapshot import _DEFINITE
from text_check import PLANET_BG, SIGN_BG, Violation

LIMITS = {"placements": 6, "aspects": 4}
HOUSE_WEIGHT = 0.5                                   # колко тежи домът на темата спрямо планетата на темата при подбора на позиция
ANGLE_SCORE = {"ASC": -1.5, "MC": -0.5}              # ъглите са основен факт на карта с час; MC е по-важен при кариера и пари (виж angle_score)


def angle_heading(angle: str, owner: str, sign: str, degrees: str) -> str:
    return f"{_DEFINITE[angle]} на {owner} е в {SIGN_BG.get(sign, sign)} ({degrees})."


def _placements(chart: Dict, owner: str, rel: Relevance) -> List[Card]:
    known = chart.get("time_known") is not False
    uncertain = set((chart.get("sign_ranges") or {}).keys())
    planets = [p for p in chart.get("planets", {}) if p in PLANET_BG and p not in uncertain]

    def weight(planet: str) -> float:
        house = chart["planets"][planet].get("house") if known else None
        return rel.planet(planet) + (HOUSE_WEIGHT * rel.house(house) if house else 0.0)

    chosen = sorted(planets, key=lambda p: (-weight(p), rel.order(p)))[:LIMITS["placements"]]
    cards: List[Card] = []
    for planet in sorted(chosen, key=rel.order):                                  # редът на четене: Слънце, Луна, Венера…
        info = chart["planets"][planet]
        formatted = info.get("formatted_pos") or ""
        sign = formatted.split(" ", 1)[0]
        house = info.get("house") if known else None
        if sign not in SIGN_BG:
            continue
        cards.append(Card("", "A", placement_heading(planet, owner, sign, _deg(formatted), house),
                          row={"kind": "placement", "owner": "user", "planet": planet, "sign": sign, "house": house},
                          score=-weight(planet) + rel.order(planet) / 100.0))
    return cards


def _angles(chart: Dict, owner: str, rel: Relevance) -> List[Card]:
    if not factpack.has_houses(chart):
        return []
    angles = chart.get("angles") or {}
    cards: List[Card] = []
    for angle, key in (("ASC", "Ascendant"), ("MC", "MC")):
        formatted = angles.get(f"{key}_formatted") or ""
        sign = formatted.split(" ", 1)[0]
        if sign not in SIGN_BG:
            continue
        score = ANGLE_SCORE[angle] - (1.0 * rel.planet("MC") if angle == "MC" else 0.0)
        cards.append(Card("", "A", angle_heading(angle, owner, sign, _deg(formatted)),
                          row={"kind": "angle", "owner": "user", "planet": angle, "sign": sign, "house": None}, score=score))
    return cards


def _aspects(chart: Dict, owner: str, rel: Relevance) -> List[Card]:
    rows = []
    for row in calculate_natal_aspects(chart, use_wider_orbs=False):
        weight = rel.planet(row["planet1"]) + rel.planet(row["planet2"])
        if weight <= 0 or row["planet1"] not in PLANET_BG or row["planet2"] not in PLANET_BG:
            continue
        rows.append((float(row["orb"]) - 0.8 * min(weight, 3), row))
    cards: List[Card] = []
    for score, row in sorted(rows, key=lambda x: x[0])[:LIMITS["aspects"]]:
        cards.append(Card("", "A", natal_aspect_heading(row, owner),
                          row={"kind": "natal", "owner": "user", "p1": row["planet1"], "p2": row["planet2"], "aspect": row["aspect"],
                               "orb": row["orb"]}, score=score))
    return cards


def build_cards(ctx: Context) -> List[Card]:
    rel = Relevance(ctx.report_type, "", ctx.question)
    cards = _placements(ctx.natal_chart, ctx.first, rel) + _angles(ctx.natal_chart, ctx.first, rel) + _aspects(ctx.natal_chart, ctx.first, rel)
    for index, card in enumerate(cards, 1):
        card.id = f"A{index}"
    return cards


def _angle_names(own: Optional[str]):
    """extra_check: ъгъл (Асцендент, MC) се назовава само в собствената си карта."""
    def check(text: str) -> List[Violation]:
        out: List[Violation] = []
        for key, rx in text_check.ANGLE_RE.items():
            m = rx.search(text or "")
            if m and key != own:
                out.append(Violation("aspect", "hard", text[max(0, m.start() - 30):m.end() + 30],
                                     "В тълкуването на тази карта не споменавай " + ("Асцендента" if key == "ASC" else "MC")
                                     + ": ъглите са факт в заглавията на картите. Опиши само смисъла със свои думи."))
        return out
    return check


SYSTEM_PROMPT = (
    "MODE: ONE-PERSON NATAL ANALYSIS. You INTERPRET pre-written FACT CARDS; you do not state facts yourself.\n"
    "THEME: {theme} Keep EVERY section on this theme.\n"
    "Person: {first}.\n\n"
    "The code has already written every fact: the position of the person's planets in sign and house, the Ascendant and the Midheaven "
    "(only when the birth time is known) and the aspects inside the chart. The facts are printed in the card headings above your text, so "
    "you never restate them.\n\n"
    "OUTPUT FORMAT (strict). Output ONLY sections. Each section starts with its tag alone on a line:\n"
    "{tags}"
    "[[SUMMARY]]  4-6 sentences: the overall character of the person on this theme, what comes easily and what needs attention.\n"
    "[[ADVICE]]  3-5 bullets starting with \"- \": practical advice in line with the theme and the question.\n"
    "{answer_tag}"
    "[[CONCLUSION]]  2-3 sentences with a reasoned overall conclusion.\n\n"
    "RULES FOR EVERY SECTION:\n"
    "1. In a card section write 2-3 sentences about THAT card only, in plain words. The heading already names the planet or the angle, the "
    "sign, the house, the aspect and the orb: do NOT name any planet, sign, house, aspect, angle (Ascendant, MC) or degree there. Start "
    "directly with the meaning. Begin each card in a different way: never start two cards with the same word.\n"
    "2. Never invent, compute or guess a planet, sign, house, aspect or degree. A fact that is not in a card does not exist for this analysis.\n"
    "3. In SUMMARY, ADVICE, ANSWER and CONCLUSION name NO planet, sign, house, aspect, angle or degree: write only the meaning and rely only "
    "on the cards. Every recommendation must follow from a card. No predictions of the future, no diagnoses, no percentages.\n"
    "4. Address the person consistently with the polite plural \"вие\" (never switch to \"ти\"), in the gender given in the identity section.\n"
    "5. Be direct and concrete. No filler, no mystic phrases.\n"
)

COMPOSE_SYSTEM = (
    "MODE: ONE-PERSON NATAL ANALYSIS, COMPOSITION FROM VERIFIED BLOCKS.\n"
    "THEME: {theme} Person: {first}.\n"
    "Below are the ONLY verified blocks (fact heading written by the code + a checked interpretation). Write the closing sections "
    "using ONLY them:\n"
    "[[SUMMARY]]  4-6 sentences: the overall character of the person on this theme, what comes easily and what needs attention.\n"
    "[[ADVICE]]  3-5 bullets starting with \"- \": practical advice that follows from the blocks.\n"
    "{answer_tag}"
    "[[CONCLUSION]]  2-3 sentences with a reasoned overall conclusion.\n\n"
    "RULES: name NO planet, sign, house, aspect, angle (Ascendant, MC) or degree: write only the meaning. Every recommendation must follow "
    "from a block. Address the person consistently with the polite plural \"вие\". Direct, concrete style, no filler.\n"
)


class NatalSingle(CardMode):
    name = "natal_single"
    stage = "natalone"
    person_groups: Tuple[str, ...] = ("A",)
    groups: Tuple[str, ...] = ("A",)
    key_per_group = {"A": 2}
    strict_cards = True
    closing_names_forbidden = True
    advice_title = "Какво да имате предвид"
    facts_stage = "natal"

    def build(self, ctx: Context):
        return build_cards(ctx), {}

    def group_title(self, group: str, ctx: Context) -> str:
        return f"Натална карта на {ctx.first}"

    def system_prompt(self, ctx: Context, cards: List[Card], answer_tag: str) -> str:
        tags = "".join(f"[[{c.id}]]  interpretation of card {c.id}\n" for c in cards)
        return SYSTEM_PROMPT.format(theme=ctx.theme, first=ctx.first, tags=tags, answer_tag=answer_tag)

    def compose_system(self, ctx: Context, answer_tag: str) -> str:
        return COMPOSE_SYSTEM.format(theme=ctx.theme, first=ctx.first, answer_tag=answer_tag)

    def restore_system(self, ctx: Context) -> str:
        return NATAL_RESTORE_SYSTEM.format(theme=ctx.theme)

    def user_prompt(self, interp, ctx: Context, cards: List[Card], lines: Dict[str, str]) -> str:
        user = f"User Question: {ctx.question or 'Provide a natal analysis of this person.'}\n\n"
        user += factpack.identity_section(ctx.first, ctx.gender)
        user += "FACT CARDS (written by the code; every position, aspect and house is verified):\n"
        user += "".join(f"{c.id}: {c.heading}\n" for c in cards) + "\n"
        return user + "Write the sections now, in Bulgarian."

    def facts_kwargs(self, ctx: Context) -> Dict:
        return dict(mode="natal", user_name=ctx.user_name, natal_chart=ctx.natal_chart, partner_name=None, partner_chart=None,
                    report_date=ctx.utc_today, question=ctx.question or "")

    def card_check(self, card: Card):
        base = card_engine.card_scope(card, True)
        angles = _angle_names(card.row.get("planet") if card.row.get("kind") == "angle" else None)
        return lambda text: base(text) + angles(text)

    def closing_check(self, text: str) -> List[Violation]:
        return card_engine.closing_scope(text) + _angle_names(None)(text)


MODE = NatalSingle()


async def interpret(interp, *, natal_chart: Dict, user_name: Optional[str], question: str, report_type: str, checks: List[Dict], theme: str,
                    utc_today: str, gender: Optional[str] = None) -> str:
    ctx = Context(natal_chart=natal_chart, partner_chart=None, transit_chart={}, user_name=user_name, partner_name=None,
                  first=factpack.display_name(user_name, factpack.FIRST_PERSON_DEFAULT), second="", question=question or "", target_date="",
                  theme=theme, utc_today=utc_today, gender=gender, report_type=report_type or "general", relation="")
    return await card_engine.run(interp, MODE, ctx, checks)
