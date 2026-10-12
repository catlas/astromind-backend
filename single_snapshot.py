"""
Анализ „за дата“ за един човек: фактите се пишат от кода, AI само тълкува (второ приложение на card_engine.py, след анализа за двама).

- група A: транзитите към наталната карта на човека (най-тесните аспекти);
- ред за домовете на транзитните планети (само при известен час на раждане);
- затварящи блокове: обща картина, съвети, отговор на въпроса и заключение, само от проверените карти.
Картите, проверката, възстановяването, резервният и частичният анализ са в card_engine.py.
"""
from typing import Dict, List, Optional, Tuple

import card_engine
import factpack
from card_engine import Card, CardMode, Context
from pair_snapshot import houses_line, transit_card

CARD_LIMIT = 8

SYSTEM_PROMPT = (
    "MODE: ONE-PERSON SNAPSHOT for ONE chosen date and time. You INTERPRET pre-written FACT CARDS; you do not state facts yourself.\n"
    "THEME: {theme} Keep EVERY section on this theme.\n"
    "Person: {first}. The sky is for the moment {when}. Use ONLY this date.\n\n"
    "The code has already written every fact: whose planet it is, natal or transiting, the aspect, the orb, the houses. "
    "They are printed above your text, so you never restate them.\n\n"
    "OUTPUT FORMAT (strict). Output ONLY sections. Each section starts with its tag alone on a line:\n"
    "{tags}"
    "[[SUMMARY]]  4-6 sentences: the overall state of the person on this date.\n"
    "[[ADVICE]]  3-5 bullets starting with \"- \": what to focus on or avoid on this date, in line with the theme and the question.\n"
    "{answer_tag}"
    "[[CONCLUSION]]  2-3 sentences with a reasoned overall conclusion.\n\n"
    "RULES FOR EVERY SECTION:\n"
    "1. In a card section write 2-3 sentences about THAT card only: what it means for the person on this date, in plain words. "
    "The heading above your text already names the planets, the aspect and the orb: do NOT name any planet, aspect, sign, house or "
    "degree there, start directly with the meaning (for example: \"Мислите ви може да...\").\n"
    "2. Mention a planet, aspect, house, sign, degree or date only if it is in a card, in the houses line or in the natal data. "
    "Never invent, compute or guess one. An aspect that is not in a card does not exist for this analysis.\n"
    "3. In SUMMARY, ADVICE, ANSWER and CONCLUSION rely only on the cards: every recommendation must follow from a card; never mention an "
    "aspect that is not one of the cards. No orb numbers, no times of day, no health diagnoses.\n"
    "4. Refer to the chosen date only as \"в този ден\" or \"на избраната дата\": never \"днес\", \"утре\" or \"вчера\".\n"
    "5. Be direct and concrete. No filler, no mystic phrases, no promises about the future.\n"
)

COMPOSE_SYSTEM = (
    "MODE: ONE-PERSON SNAPSHOT, COMPOSITION FROM VERIFIED BLOCKS.\n"
    "THEME: {theme} Person: {first}. Moment: {when}.\n"
    "Below are the ONLY verified blocks (fact heading written by the code + a checked interpretation). Write the closing sections "
    "using ONLY them:\n"
    "[[SUMMARY]]  4-6 sentences: the overall state of the person on this date.\n"
    "[[ADVICE]]  3-5 bullets starting with \"- \": practical advice that follows from the blocks.\n"
    "{answer_tag}"
    "[[CONCLUSION]]  2-3 sentences with a reasoned overall conclusion.\n\n"
    "RULES: use no aspect, planet position, house, sign, degree or date that is not in the verified blocks; an aspect that is not in one "
    "of them does not exist. Whenever you name a planet in an aspect, say whether it is natal or transiting. No orb numbers, no times of "
    "day. Every recommendation must follow from a block. Refer to the chosen date only as \"в този ден\" or \"на избраната дата\": "
    "never \"днес\", \"утре\" or \"вчера\". Direct, concrete style, no filler.\n"
)


class SingleSnapshot(CardMode):
    name = "single_snapshot"
    stage = "single"
    person_groups: Tuple[str, ...] = ("A",)
    groups: Tuple[str, ...] = ("A",)
    key_per_group = {"A": 2}

    def build(self, ctx: Context):
        rows = sorted(factpack.transit_aspects(ctx.natal_chart, ctx.transit_chart), key=lambda r: r["orb"])[:CARD_LIMIT]
        cards = [transit_card(f"A{i}", "A", row, ctx.first, "user") for i, row in enumerate(rows, 1)]
        return cards, {"A": houses_line(ctx.natal_chart, ctx.transit_chart, ctx.first)}

    def group_title(self, group: str, ctx: Context) -> str:
        return f"Транзитите към {ctx.first}"

    def system_prompt(self, ctx: Context, cards: List[Card], answer_tag: str) -> str:
        tags = "".join(f"[[{c.id}]]  interpretation of card {c.id}\n" for c in cards)
        return SYSTEM_PROMPT.format(theme=ctx.theme, first=ctx.first, when=ctx.target_date, tags=tags, answer_tag=answer_tag)

    def compose_system(self, ctx: Context, answer_tag: str) -> str:
        return COMPOSE_SYSTEM.format(theme=ctx.theme, first=ctx.first, when=ctx.target_date, answer_tag=answer_tag)

    def user_prompt(self, interp, ctx: Context, cards: List[Card], lines: Dict[str, str]) -> str:
        user = f"User Question: {ctx.question or 'Provide a forecast for this specific date.'}\n\n"
        user += factpack.identity_section(ctx.first, ctx.gender)
        user += "FACT CARDS (written by the code; every number and owner is verified):\n" + "".join(f"{c.id}: {c.heading}\n" for c in cards) + "\n"
        if lines.get("A"):
            user += lines["A"] + "\n"
        user += "\n" + interp._natal_block(ctx.first, ctx.natal_chart)
        return user + "Write the sections now, in Bulgarian."

    def facts_kwargs(self, ctx: Context) -> Dict:
        return dict(mode="snapshot", user_name=ctx.user_name, natal_chart=ctx.natal_chart, partner_name=None, partner_chart=None,
                    transit_chart=ctx.transit_chart, target_date=ctx.target_date, report_date=ctx.utc_today, question=ctx.question or "")


MODE = SingleSnapshot()


async def interpret(interp, *, natal_chart: Dict, transit_chart: Dict, user_name: Optional[str], question: str, target_date: str,
                    checks: List[Dict], theme: str, utc_today: str, gender: Optional[str] = None) -> str:
    ctx = Context(natal_chart=natal_chart, partner_chart=None, transit_chart=transit_chart, user_name=user_name, partner_name=None,
                  first=factpack.display_name(user_name, factpack.FIRST_PERSON_DEFAULT), second="", question=question or "",
                  target_date=target_date, theme=theme, utc_today=utc_today, gender=gender)
    return await card_engine.run(interp, MODE, ctx, checks)
