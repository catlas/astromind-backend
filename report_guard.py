"""
Финална съгласуваност на целия отчет преди запис, показване като готов и такса (R03, регресионен одит 11.10.2026).

Месечните части и общият преглед се проверяват поотделно (text_guard), но противоречие между тях се вижда само в цялото:
- „няма данни за октомври“ в раздела за ноември, когато октомврийската част е в отчета;
- една и съща дата за едно и също действие е едновременно препоръка и забрана („не финализирайте на 24 октомври“ и „финализирайте на 24 октомври“);
- действие, отложено до по-късна дата, е препоръчано на по-ранна („втората граница след 14 ноември“ и „втората граница на 31 октомври“).

Проверката е детерминирана (без AI заявка). При нарушение частта се връща на поправка през text_guard с extra_check:
поправеният текст се проверява и срещу останалите части. Не се твърди, че това е пълна семантична проверка: то хваща доказаните случаи.
"""
import re
from datetime import date
from typing import Dict, List, Optional, Sequence, Tuple

import safety
import text_check
from text_check import Facts, Violation

# Действията, за които се следят препоръка и забрана на една дата (корен на думата -> име за съобщението)
ACTIONS = {
    r"финализир\w+": "финализиране",
    r"подпис\w+": "подписване",
    r"започ\w+|започв\w+|старт\w+": "започване",
    r"решени\w+": "решение",
    r"границ\w+": "граница",
    r"ангажир\w+|ангажимент\w*": "ангажимент",
    r"разговор\w*|обсъжд\w+": "разговор",
}
_ACTION_RES = [(re.compile(r"(?<![\w])(?:%s)" % pattern, re.IGNORECASE), label) for pattern, label in ACTIONS.items()]
_AVOID_RE = re.compile(r"(?<![\w])(?:не\s+\w+|избягвайте|избягвай|без)(?![\w])", re.IGNORECASE)
# „по-късно“ няма тук: стои в обикновени изречения („думи, които по-късно съжалявате“) и би правило фалшиви противоречия
_DEFER_WORD_RE = re.compile(r"(?<![\w])(?:отложете|отлагайте|изчакайте|изчакай|почакайте)(?![\w])", re.IGNORECASE)
_NO_DATA_RE = re.compile(r"(?<![\w])няма\s+(?:данни|информация|анализ|събития)\s+за\s+(?:месец\s+)?(януари|февруари|март|април|май|юни|юли|август|септември|октомври|ноември|декември)", re.IGNORECASE)


def _who(sentence: str, facts: Facts) -> frozenset:
    """Кои от двамата са наречени в изречението: съвет за единия не противоречи на съвет за другия."""
    named = set()
    for key in ("user", "partner"):
        rx = text_check._name_re(facts.names.get(key, ""))
        if rx and rx.search(sentence):
            named.add(key)
    return frozenset(named)


def _statements(text: str, facts: Facts) -> List[Tuple[str, str, date, str, frozenset]]:
    """(действие, вид, дата, изречение, наречени хора): вид е do (препоръка на дата), avoid (забрана на дата) или from (препоръка/отлагане „след/до“ дата)."""
    out: List[Tuple[str, str, date, str, frozenset]] = []
    for sentence in safety._plain_sentences(text):
        labels = [label for rx, label in _ACTION_RES if rx.search(sentence)]
        if not labels:
            continue
        for item in text_check.parse_dates(sentence, facts):
            if item.is_range:
                continue
            day = item.dates[0]
            before = sentence[max(0, item.start - 12):item.start].lower()
            first_action = min((m.start() for rx, _ in _ACTION_RES for m in [rx.search(sentence)] if m), default=0)
            negated = bool(_AVOID_RE.search(sentence[max(0, first_action - 25):first_action + 1]))
            after = bool(re.search(r"(?<![\w])след\s*$", before))
            until = bool(re.search(r"(?<![\w])до\s*$", before))
            deferred = bool(_DEFER_WORD_RE.search(sentence))
            # „след 14 ноември“ и „изчакайте до 14 ноември“ са отлагане. Но „финализирайте до 24 ноември“ е срок, а не отлагане:
            # срокът не противоречи на препоръка за по-ранна дата и не се следи (иначе нормален текст се отказва).
            if until and not (deferred or negated):
                continue
            kind = "from" if (after or deferred or until) else ("avoid" if negated else "do")
            for label in labels:
                out.append((label, kind, day, sentence[:160], _who(sentence, facts)))
    return out


def cross_part_violations(parts: Sequence[Tuple[str, str]], months: Sequence[str], facts: Facts) -> Dict[str, List[Violation]]:
    """parts: (етап, текст) по реда на отчета; етапите са „month:YYYY-MM“ и „overview“. Връща етап -> нарушения (само тези с нарушения)."""
    out: Dict[str, List[Violation]] = {}
    present = {text_check._MONTH_NAMES[int(m[5:7])] for m in months}

    def add(stage: str, violation: Violation) -> None:
        out.setdefault(stage, []).append(violation)

    # 1. „няма данни за октомври“ при налична октомврийска част
    for stage, text in parts:
        for m in _NO_DATA_RE.finditer(re.sub(r"<[^>]+>", " ", text or "")):
            month = m.group(1).lower()
            if month in present:
                add(stage, Violation("consistency", "hard", m.group(0)[:160],
                                     f"Отчетът съдържа раздел за {month}: махни твърдението, че няма данни за този месец."))

    # 2. препоръка и забрана на една дата; отлагане до по-късна дата и препоръка на по-ранна
    entries: List[Tuple[str, str, str, date, str, frozenset]] = []          # (етап, действие, вид, дата, изречение, наречени хора)
    for stage, text in parts:
        for label, kind, day, sentence, who in _statements(text, facts):
            entries.append((stage, label, kind, day, sentence, who))

    def owner(a: str, b: str) -> str:
        if "overview" in (a, b):
            return "overview"
        return max(a, b)                                          # по-късният месец

    seen = set()
    for i, (stage_a, label_a, kind_a, day_a, text_a, who_a) in enumerate(entries):
        for stage_b, label_b, kind_b, day_b, text_b, who_b in entries[i + 1:]:
            if label_a != label_b or who_a != who_b:
                continue                                          # друго действие или друг човек: не е противоречие
            conflict: Optional[str] = None
            if {kind_a, kind_b} == {"do", "avoid"} and day_a == day_b:
                conflict = f"на {day_a.day} {text_check._MONTH_NAMES[day_a.month]} за „{label_a}“ има и препоръка, и забрана"
            else:
                for (k1, d1, k2, d2) in ((kind_a, day_a, kind_b, day_b), (kind_b, day_b, kind_a, day_a)):
                    if k1 == "from" and k2 == "do" and d2 < d1:
                        conflict = (f"„{label_a}“ е отложено до {d1.day} {text_check._MONTH_NAMES[d1.month]}, "
                                    f"а на {d2.day} {text_check._MONTH_NAMES[d2.month]} е препоръчано")
            if conflict is None:
                continue
            key = (label_a, day_a, day_b, conflict)
            if key in seen:
                continue
            seen.add(key)
            add(owner(stage_a, stage_b), Violation(
                "consistency", "hard", f"{text_a[:80]} ⟷ {text_b[:80]}",
                f"Противоречие между части на отчета: {conflict}. Остави една последователна препоръка за това действие и датите му."))
    return out


def part_check(parts: Sequence[Tuple[str, str]], stage: str, months: Sequence[str], facts: Facts):
    """extra_check за text_guard: проверка на кандидат-текст на една част заедно с останалите."""
    def check(candidate: str) -> List[Violation]:
        replaced = [(s, candidate if s == stage else t) for s, t in parts]
        return cross_part_violations(replaced, months, facts).get(stage, [])
    return check
