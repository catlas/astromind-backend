"""
Анализ „за дата“ за двама души: фактите се пишат от кода, AI само тълкува (решение на собственика, 12.10.2026).

Защо: в живото приемане AI смесваше притежателя на планетите и вида на аспекта, а проверката основателно отказваше
текста (3 от 3 опита). Затова:
- кодът изгражда КАРТИ с факти (идентификатор, човек, транзитна или натална планета, вид аспект, орбис, посока) и ги
  изписва като заглавия: AI не ги пресъздава, а пише само тълкуването под всяка карта;
- групите са отделни: транзитите към първия човек (A), транзитите към втория (B) и взаимодействието между двете карти (S);
- всеки блок се проверява отделно (text_guard, най-много 2 поправки). Проверката не е отслабена;
- след 2 неуспешни поправки на блок: ако блокът е самостоятелна карта, тя се пропуска, а обобщението, съветите, отговорът и
  заключението се съставят наново САМО от проверените карти (резервен анализ). Така в тях не остават изводи от пропуснато;
- накрая целият сглобен текст минава пълната проверка още веднъж. Минимум за доставяне: анализ и за двамата, отговор на
  въпроса, ако има такъв, и заключение. Иначе резултатът се отказва.

Видове резултат (виж quality в записа на проверката): full | fallback (само обобщението е съставено наново, всички карти са
запазени) | partial (пропусната е поне една карта: не се таксува) | отказ (TextCheckError).
"""
import re
from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple

import factpack
import report_guard
import text_check
import text_guard
from text_check import ASPECT_BG, PLANET_BG, Violation

CARD_LIMITS = {"A": 6, "B": 6, "S": 4}            # най-много карти в група: най-тесните аспекти
MIN_KEPT_PER_PERSON = 2                           # най-малко проверени транзитни карти за човек (ако е имал поне толкова)
MAX_FAILED_CARDS = 4                              # повече провалени карти значи, че AI е объркан: отказ без още заявки
COMPOSITION = ("SUMMARY", "ADVICE", "ANSWER", "CONCLUSION")
# Таг в началото на ред: [[A1]], **[[A1]]**, ### [[A1]], [A1] или [[A1]]: текст на същия ред (моделите го пишат по различен начин)
TAG_RE = re.compile(r"^[ \t*#>_-]*\[{1,2}\s*([A-Za-z][A-Za-z0-9_]*)\s*\]{1,2}[ \t*:_.-]*", re.MULTILINE)

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


@dataclass
class Card:
    id: str                    # A1, B3, S2
    group: str                 # A | B | S
    heading: str               # написан от кода: факти без AI
    removed: bool = False
    text: str = ""             # проверено тълкуване на AI


def transit_heading(row: Dict, owner: str) -> str:
    tp, npl = row["transit_planet"], row["natal_planet"]
    return (f"{_adjective('транзит', tp).capitalize()} {PLANET_BG.get(tp, tp)} е в {ASPECT_BG[row['aspect']]} с "
            f"{_adjective('натал', npl)} {PLANET_BG.get(npl, npl)} на {owner} (орб {_orb(row['orb'])}{_applying(row)}).")


def synastry_heading(row: Dict) -> str:
    return (f"{_DEFINITE.get(row['planet1'], row['planet1'])} на {row['person1']} е в {ASPECT_BG[row['aspect']]} с "
            f"{_OBJECT.get(row['planet2'], row['planet2'])} на {row['person2']} (орб {_orb(row['orb'])}).")


def build_cards(natal: Dict, partner: Dict, transit: Dict, first: str, second: str) -> List[Card]:
    cards: List[Card] = []
    for group, chart, owner in (("A", natal, first), ("B", partner, second)):
        rows = sorted(factpack.transit_aspects(chart, transit), key=lambda r: r["orb"])[:CARD_LIMITS[group]]
        for index, row in enumerate(rows, 1):
            cards.append(Card(f"{group}{index}", group, transit_heading(row, owner)))
    rows = sorted(factpack.synastry_aspects(natal, partner, first, second), key=lambda r: r["orb"])[:CARD_LIMITS["S"]]
    for index, row in enumerate(rows, 1):
        cards.append(Card(f"S{index}", "S", synastry_heading(row)))
    return cards


def houses_line(chart: Dict, transit: Dict, owner: str) -> str:
    """Къде са транзитните планети в домовете на човека: изцяло от кода (само при известен час на раждане)."""
    if not factpack.has_houses(chart):
        return ""
    overlay = factpack.overlay(chart, transit)
    parts = [f"{PLANET_BG.get(p, p)} — {_ORDINAL.get(h, str(h) + '-и')} дом" for p, h in overlay.items() if p in PLANET_BG]
    return f"Транзитните планети в домовете на {owner}: " + "; ".join(parts) + "." if parts else ""


def parse_sections(text: str, known=None) -> Dict[str, str]:
    """Секциите на отговора по таговете [[A1]], [[SUMMARY]]… Текст преди първия таг се пренебрегва; тагове извън known (ако е дадено)
    не отварят секция. Текстът на секцията е останалото от реда на тага и следващите редове до следващия таг."""
    found = [m for m in TAG_RE.finditer(text or "") if known is None or m.group(1).upper() in known]
    out: Dict[str, str] = {}
    for i, m in enumerate(found):
        end = found[i + 1].start() if i + 1 < len(found) else len(text)
        body = text[m.end():end].strip()
        tag = m.group(1).upper()
        if body and tag not in out:
            out[tag] = body
    return out


def _shape(raw: str) -> str:
    """Формата на неправилен отговор за журнала: брой тагове и първите знаци, без текст на анализа."""
    first = (raw or "").strip().splitlines()[0][:50] if (raw or "").strip() else ""
    return f"len={len(raw or '')} tags={len(TAG_RE.findall(raw or ''))} brackets={(raw or '').count('[[')} first={first!r}"


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
    "degree there, start directly with the meaning (for example: \"Думите ви може да...\").\n"
    "2. Mention a planet, aspect, house, sign, degree or date only if it is in a card, in the houses lines or in the natal data. "
    "Never invent, compute or guess one. An aspect that is not in a card does not exist for this analysis.\n"
    "3. Every planet belongs to ONE person and is either natal or transiting: whenever you name a planet in an aspect, say whose it is.\n"
    "4. In SUMMARY, ADVICE, ANSWER and CONCLUSION rely only on the cards: every recommendation must follow from a card. No orb numbers, "
    "no times of day, no health diagnoses.\n"
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
    "RULES: use no aspect, planet position, house, sign, degree or date that is not in the verified blocks. Whenever you name a planet "
    "in an aspect, say whose it is and whether it is natal or transiting. No orb numbers, no times of day. Every recommendation must "
    "follow from a block. Refer to the chosen date only as \"в този ден\" or \"на избраната дата\": never \"днес\", \"утре\" or \"вчера\". "
    "Direct, concrete style, no filler.\n"
)


def _fail(stage: str, code: str, detail: str, text: str = "") -> text_guard.TextCheckError:
    outcome = text_guard.GuardOutcome(stage=stage, mode=text_guard.MODE_ENFORCE, text=text,
                                      after=[Violation(code, "hard", "", detail)], rejected=True, reason="violations_remain")
    return text_guard.TextCheckError(stage, outcome)


async def _check_block(interp, facts, block_id: str, text: str, checks: List[Dict]) -> Tuple[Optional[str], str]:
    """Проверява един блок (най-много 2 поправки). Връща (проверен текст или None, кодовете на нарушенията при провал)."""
    text = TAG_RE.sub("", text or "").strip()                    # забъркан таг в текста на блока не е част от анализа
    try:
        stage = block_id if ":" in block_id else f"pair:{block_id}"
        return await interp.guard_text(text, facts, stage=stage, checks=checks), ""
    except text_guard.TextCheckError as failure:
        return None, failure.outcome.codes() or "check"


def _prompt_cards(cards: List[Card]) -> str:
    return "".join(f"{c.id}: {c.heading}\n" for c in cards)


def _answer_tag(question: str) -> str:
    return "[[ANSWER]]  a direct answer to the user's question in 3-5 sentences, based only on the cards.\n" if question else ""


async def _compose(interp, facts, cards: List[Card], *, theme: str, first: str, second: str, when: str, question: str,
                   checks: List[Dict]) -> Dict[str, str]:
    """Обобщение, съвети, отговор и заключение от проверените карти. Връща проверените блокове (хвърля TextCheckError при провал)."""
    kept = [c for c in cards if not c.removed and c.text]
    blocks = "".join(f"[{c.id}] {c.heading}\n{c.text}\n\n" for c in kept)
    system = COMPOSE_SYSTEM.format(theme=theme, first=first, second=second, when=when, answer_tag=_answer_tag(question))
    user = (f"User question: {question or 'none'}\n\nVERIFIED BLOCKS:\n{blocks}"
            "Write the sections now, in Bulgarian.")
    raw = await interp._call_api(system_prompt=system, user_prompt=user, max_tokens=interp.max_output_tokens)
    sections = parse_sections(raw, {c.id for c in cards} | set(COMPOSITION))
    return await _check_composition(interp, facts, sections, question, checks, stage_prefix="compose")


async def _check_composition(interp, facts, sections: Dict[str, str], question: str, checks: List[Dict], stage_prefix: str = "pair") -> Dict[str, str]:
    needed = [t for t in COMPOSITION if t != "ANSWER" or question]
    missing = [t for t in needed if not sections.get(t)]
    if missing:
        raise _fail(f"{stage_prefix}:format", "complete", "Липсват задължителни секции: " + ", ".join(missing))
    out: Dict[str, str] = {}
    for tag in needed:
        checked, codes = await _check_block(interp, facts, f"{stage_prefix}:{tag}", sections[tag], checks)
        if checked is None:
            raise _fail(f"{stage_prefix}:{tag}", codes.split(",")[0] or "check", f"Блокът {tag} не мина проверката ({codes}).")
        out[tag] = checked
    return out


def assemble(cards: List[Card], names: Tuple[str, str], lines: Dict[str, str], blocks: Dict[str, str], question: str) -> str:
    first, second = names
    parts: List[str] = []
    for group, title in (("A", f"Транзитите към {first}"), ("B", f"Транзитите към {second}")):
        parts.append(f"## {title}")
        if lines.get(group):
            parts.append(lines[group])
        for c in (c for c in cards if c.group == group and not c.removed):
            parts.append(f"### {c.heading}\n{c.text}")
    shown = [c for c in cards if c.group == "S" and not c.removed]
    if shown:
        parts.append("## Взаимодействие между двете карти")
        parts.extend(f"### {c.heading}\n{c.text}" for c in shown)
    parts.append(f"## Обща картина\n{blocks['SUMMARY']}")
    parts.append(f"## Какво да имате предвид в този ден\n{blocks['ADVICE']}")
    if question:
        parts.append(f"## Отговор на въпроса\n{blocks['ANSWER']}")
    parts.append(f"## Заключение\n{blocks['CONCLUSION']}")
    return "\n\n".join(parts)


def minimum_met(cards: List[Card], original: Dict[str, int]) -> Optional[str]:
    """None, ако са запазени поне MIN_KEPT_PER_PERSON проверени карти за всеки човек (или всички, ако са имали по-малко)."""
    for group in ("A", "B"):
        kept = sum(1 for c in cards if c.group == group and not c.removed)
        need = min(MIN_KEPT_PER_PERSON, original.get(group, 0))
        if kept < need:
            return f"За {'първия' if group == 'A' else 'втория'} човек са проверени само {kept} от {original[group]} карти."
    return None


async def interpret(interp, *, natal_chart: Dict, partner_chart: Dict, transit_chart: Dict, user_name: Optional[str],
                    partner_name: Optional[str], question: str, target_date: str, report_type: str, checks: List[Dict],
                    theme: str, utc_today: str, gender: Optional[str] = None, partner_gender: Optional[str] = None) -> str:
    first = factpack.display_name(user_name, factpack.FIRST_PERSON_DEFAULT)
    second = factpack.display_name(partner_name, factpack.SECOND_PERSON_DEFAULT)
    facts = interp.build_text_facts(mode="snapshot", user_name=user_name, natal_chart=natal_chart, partner_name=partner_name,
                                    partner_chart=partner_chart, transit_chart=transit_chart, target_date=target_date,
                                    report_date=utc_today, question=question or "")
    interp.require_text_facts(facts, stage="snapshot", checks=checks)

    cards = build_cards(natal_chart, partner_chart, transit_chart, first, second)
    original = {g: sum(1 for c in cards if c.group == g) for g in ("A", "B", "S")}
    lines = {"A": houses_line(natal_chart, transit_chart, first), "B": houses_line(partner_chart, transit_chart, second)}

    tags = "".join(f"[[{c.id}]]  interpretation of card {c.id}\n" for c in cards)
    system = SYSTEM_PROMPT.format(theme=theme, first=first, second=second, when=target_date, tags=tags,
                                  answer_tag=_answer_tag(question))
    user = f"User Question: {question or 'Provide a relationship forecast for this specific date.'}\n\n"
    user += factpack.identity_section(first, gender, second, partner_gender)
    user += "FACT CARDS (written by the code; every number and owner is verified):\n" + _prompt_cards(cards) + "\n"
    for owner, line in ((first, lines["A"]), (second, lines["B"])):
        if line:
            user += line + "\n"
    user += "\n" + interp._natal_block(first, natal_chart) + interp._natal_block(second, partner_chart)
    user += "Write the sections now, in Bulgarian."

    known = {c.id for c in cards} | set(COMPOSITION)
    sections: Dict[str, str] = {}
    for attempt in (1, 2):                                      # неправилен формат: най-много една нова заявка
        raw = await interp._call_api(system_prompt=system, user_prompt=user, max_tokens=interp.max_output_tokens)
        sections = parse_sections(raw, known)
        if not (sum(1 for c in cards if sections.get(c.id)) >= max(1, len(cards) // 2) and sections.get("CONCLUSION")):
            print(f"🔎 Формат на отговора (опит {attempt}): {_shape(raw)}")
        if sum(1 for c in cards if sections.get(c.id)) >= max(1, len(cards) // 2) and sections.get("CONCLUSION"):
            break
    else:
        raise _fail("pair:format", "complete", "Отговорът на AI не е в изискания формат.")

    # 1. Проверка на всяка карта поотделно (най-много 2 поправки на блок)
    failed: Dict[str, str] = {}
    for card in cards:
        raw_text = sections.get(card.id)
        if not raw_text:
            card.removed = True
            failed[card.id] = "missing"
            continue
        checked, codes = await _check_block(interp, facts, card.id, raw_text, checks)
        if checked is None:
            card.removed = True
            failed[card.id] = codes
            if len(failed) > MAX_FAILED_CARDS:
                raise _fail("pair:too_many", "complete", f"{len(failed)} карти не минаха проверката: AI не следва картите.")
        else:
            card.text = checked

    quality = "full"
    blocks: Dict[str, str] = {}
    if not failed:
        try:
            blocks = await _check_composition(interp, facts, sections, question, checks)
        except text_guard.TextCheckError as failure:
            checks.append({"stage": "pair:composition", "mode": "enforce", "result": "fallback", "codes": failure.outcome.codes()[:60]})
            blocks = {}
    if not blocks:
        # 2. Резервен анализ: обобщението се съставя наново САМО от проверените карти
        problem = minimum_met(cards, original)
        if problem:
            raise _fail("pair:minimum", "complete", problem)
        blocks = await _compose(interp, facts, cards, theme=theme, first=first, second=second, when=target_date,
                                question=question or "", checks=checks)
        quality = "partial" if failed else "fallback"

    text = assemble(cards, (first, second), lines, blocks, question or "")

    # 3. Цялостният текст минава проверката още веднъж: факти, пълнота и противоречия между блоковете.
    # В „warn“ и „off“ (или без факти) проверката е изключена по настройка, както при всички останали анализи.
    final: List[Violation] = []
    if text_guard.check_mode() == text_guard.MODE_ENFORCE and isinstance(facts, text_check.Facts):
        final = [v for v in text_check.check_text(text, facts) if v.severity == "hard"]
        final += report_guard.cross_part_violations([("overview", text)], [], facts).get("overview", [])
    if final:
        outcome = text_guard.GuardOutcome(stage="pair:final", mode=text_guard.MODE_ENFORCE, text=text, after=final,
                                          rejected=True, reason="violations_remain")
        checks.append(outcome.summary())
        raise text_guard.TextCheckError("pair:final", outcome)

    removed = [c.id for c in cards if c.removed]
    checks.append({"stage": "pair_snapshot", "mode": "enforce", "result": "ok" if quality == "full" else quality, "quality": quality,
                   "cards": len(cards), "kept": len(cards) - len(removed), "removed": ",".join(removed)[:60],
                   "codes": ",".join(f"{k}:{v}" for k, v in failed.items())[:60]})
    return text
