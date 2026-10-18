"""
Финална съгласуваност на целия отчет преди запис, показване като готов и такса (R03, регресионен одит 11.10.2026).

Месечните части и общият преглед се проверяват поотделно (text_guard), но противоречие между тях се вижда само в цялото:
- „няма данни за октомври“ в раздела за ноември, когато октомврийската част е в отчета;
- една и съща дата за едно и също действие е едновременно препоръка и забрана („не финализирайте на 24 октомври“ и „финализирайте на 24 октомври“);
- действие, отложено до по-късна дата, е препоръчано на по-ранна („втората граница след 14 ноември“ и „втората граница на 31 октомври“);
- действие, забранено или отложено за прозорец („от 3 до 25 декември избягвайте решения“), е препоръчано на дата вътре в прозореца.

Прозорец (18.10.2026, след живо приемане: съветите за декември се отказваха, защото „до 25 декември“ се сравняваше с препоръки от октомври):
- „3–25 декември“, „от 3 до 25 декември“, „от 30 октомври до 2 ноември“, „между 3 и 25 декември“, „между 30 октомври и 2 ноември“ са прозорци
  с начало и край;
- прозорец с дума за отлагане („отложете“, „изчакайте“) покрива датите от началото до края, без самия край (отлагане ДО датата);
- прозорец с отрицание („избягвайте“, „не започвайте“) покрива и двата края;
- препоръка на дата преди началото на прозореца не е противоречие; вътре в него е;
- „до 25 декември“ само с крайна дата в месечен блок е прозорец от първия ден на ТОЗИ месец (авторът на блока за декември вижда само
  картите на декември); в общия преглед няма начало и важи за всички по-ранни дати, както преди.

Само съвет е препоръка (18.10.2026, второ живо приемане: описателни изречения от тълкуванията на картите — „На 24 октомври започва период,
в който ежедневните разговори …“ — се четяха като „препоръка за започване / разговор“ и влизаха в противоречие с прозорците на съветите):
- изречението допринася за проверката само ако е съвет: повелително („вземете“, „финализирайте“), „да + глагол“ („да вземете“, „да не подписвате“)
  или оборот за съвет („добре е“, „добър момент“, „подходящо е“, „препоръчително“, „трябва“, „можете“);
- „започва / започват / ангажира“ и другите форми в 3-то лице и причастията не са действие;
- „избягвате“, „да избягвате“, „недейте“ са отрицание като „избягвайте“;
- „след 25 декември“ само с дата в месечен блок важи от първия ден на ТОЗИ месец, както „до 25 декември“.

Груба връзка дума↔дата (18.10.2026, трето живо приемане: 3 отказа и 1 резервен от 10 и след всички уточнения; журналът показа кои механизми остават):
- двойки изречения от ЕДНО и също изречение не се сравняват помежду си: изречението е едно съобщение, а думата за действие в една негова част
  („…, преди да поемете ангажименти“) се връзваше с датата от друга („Около 24 декември …, а след 25 декември …“);
- за общите думи (`GENERIC_ACTIONS`: „разговор“, „решение“, „ангажимент“, „започване“) се следи само препоръка срещу забрана на ЕДНА и СЪЩА дата;
  „след / до дата“, отлагане и прозорец се следят само за конкретните („финализиране“, „подписване“, „граница“): общата дума е в почти всяко
  изречение на съвет и „след 25 декември водете разговорите“ не е забрана на разговори преди тази дата. Така не се хващат истински
  противоречия от вида „изчакайте до 14 ноември с решението“ срещу „на 31 октомври вземете решение“ (одобрено от потребителя на 18.10.2026).

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
_AVOID_RE = re.compile(r"(?<![\w])(?:не\s+\w+|избягвайте|избягвай|избягвате|недейте|недей|без)(?![\w])", re.IGNORECASE)
# Изречение е съвет, ако има повелително или друга форма на 2 л. мн. ч. на „-ете/-йте“ (и „можете“), „да + глагол във 2 л. мн. ч.“ или оборот за съвет. Невярно открит сигнал (напр. „дете“) връща
# изречението към стария ход (следи се), а липсващ сигнал го изключва: затова списъкът е широк.
_ADVICE_RES = [
    re.compile(r"(?<![\w])(?:нека|\w+(?:ете|йте))(?![\w])", re.IGNORECASE),
    re.compile(r"(?<![\w])да\s+(?:не\s+)?\w+(?:ете|ите|ате|яте)(?![\w])", re.IGNORECASE),
    re.compile(r"(?<![\w])(?:препоръч\w+|съветвам|добре\s+е|е\s+добре|по-добре|(?:добър|добр\w+)\s+(?:момент\w*|възможност\w*|прозорец\w*|време|ден|дни)|"
               r"(?:не)?подходящ\w*|(?:не)?благоприятн\w*|важно\s+е|необходимо\s+е|трябва|време\s+е|е\s+време)(?![\w])", re.IGNORECASE),
]
# Форми в 3-то лице и причастия: описват („На 24 октомври започва период“, „…което ви ангажира“), не съветват, затова не са действие
_DESCRIPTIVE_FORMS = frozenset({"започва", "започват", "започна", "започнаха", "започне", "започнат", "започнал", "започнала", "започнало", "започнали", "започваща",
                                "стартира", "стартират", "стартирал", "стартирала", "стартирало", "стартирали",
                                "ангажира", "ангажират", "ангажиран", "ангажирана", "ангажирано", "ангажирани", "ангажирал", "ангажирала"})
# „по-късно“ няма тук: стои в обикновени изречения („думи, които по-късно съжалявате“) и би правило фалшиви противоречия
_DEFER_WORD_RE = re.compile(r"(?<![\w])(?:отложете|отлагайте|изчакайте|изчакай|почакайте)(?![\w])", re.IGNORECASE)
_NO_DATA_RE = re.compile(r"(?<![\w])няма\s+(?:данни|информация|анализ|събития)\s+за\s+(?:месец\s+)?(януари|февруари|март|април|май|юни|юли|август|септември|октомври|ноември|декември)", re.IGNORECASE)

# Прозорец от две пълни дати с „между“: „между 30 октомври и 2 ноември“ (между двете дати стои само „и“). „от 30 октомври до 2 ноември“ и
# „30 октомври – 2 ноември“ вече са един диапазон за text_check.parse_dates (две дати, разделени с „до“ или тире, се слепват).
_BETWEEN_BEFORE_RE = re.compile(r"(?<![\w])между\s*$", re.IGNORECASE)
_AND_BETWEEN_RE = re.compile(r"^\s*и\s*$", re.IGNORECASE)
# Прозорец с общ месец: „от 3 до 25 декември“, „между 3 и 25 декември“ (началото е само число пред втората дата)
_BARE_START_RE = re.compile(r"(?<![\w])(?:от|между)\s+(\d{1,2})\s+(?:до|и)\s*$", re.IGNORECASE)

# Общи думи: следи се само препоръка срещу забрана на една и съща дата (вид do срещу avoid); „след / до дата“, отлагане и прозорец — не
GENERIC_ACTIONS = frozenset({"разговор", "решение", "ангажимент", "започване"})
# (действие, вид, дата, изречение, наречени хора, начало на прозореца, номер на изречението в частта)
Statement = Tuple[str, str, date, str, frozenset, Optional[date], int]


def _who(sentence: str, facts: Facts) -> frozenset:
    """Кои от двамата са наречени в изречението: съвет за единия не противоречи на съвет за другия."""
    named = set()
    for key in ("user", "partner"):
        rx = text_check._name_re(facts.names.get(key, ""))
        if rx and rx.search(sentence):
            named.add(key)
    return frozenset(named)


def _is_advice(sentence: str) -> bool:
    return any(rx.search(sentence) for rx in _ADVICE_RES)


def _action_words(sentence: str) -> List[Tuple[int, str]]:
    """(позиция, действие) на думите за действие в изречението; форми в 3-то лице и причастия („започва“, „ангажира“) не са действие."""
    return [(m.start(), label) for rx, label in _ACTION_RES for m in rx.finditer(sentence) if m.group(0).lower() not in _DESCRIPTIVE_FORMS]


def _month_start(stage: str) -> Optional[date]:
    """Първият ден на месеца на частта „month:YYYY-MM“; за общия преглед няма."""
    m = re.fullmatch(r"month:(\d{4})-(\d{2})", stage or "")
    if not m:
        return None
    try:
        return date(int(m.group(1)), int(m.group(2)), 1)
    except ValueError:
        return None


def _windows(sentence: str, items: List["text_check.DateItem"]) -> Tuple[List[Tuple[date, date]], set]:
    """Прозорците в изречението (начало, край) и индексите на датите, които те заемат. items е подредено по позиция."""
    found: List[Tuple[date, date]] = []
    taken: set = set()
    for i, item in enumerate(items):
        if item.is_range:                                           # „3–25 декември“, „от 3 декември до 25 декември“
            found.append((item.dates[0], item.dates[-1]))
            taken.add(i)
            continue
        if i in taken:
            continue
        before = sentence[:item.start]
        bare = _BARE_START_RE.search(before)                        # „от 3 до 25 декември“, „между 3 и 25 декември“
        if bare:
            end = item.dates[0]
            try:
                start: Optional[date] = date(end.year, end.month, int(bare.group(1)))
            except ValueError:
                start = None
            if start is not None and start <= end:
                found.append((start, end))
                taken.add(i)
                continue
        if i + 1 < len(items) and not items[i + 1].is_range:        # „между 30 октомври и 2 ноември“
            nxt = items[i + 1]
            if _BETWEEN_BEFORE_RE.search(before) and _AND_BETWEEN_RE.match(sentence[item.end:nxt.start]) and item.dates[0] <= nxt.dates[0]:
                found.append((item.dates[0], nxt.dates[0]))
                taken.update((i, i + 1))
    return found, taken


def _statements(text: str, facts: Facts, month_start: Optional[date] = None) -> List[Statement]:
    """(действие, вид, дата, изречение, наречени хора, начало): вид е
    - do — препоръка на дата;
    - avoid — забрана на дата;
    - from — препоръка/отлагане „след/до“ дата (до датата, без нея); началото е първият ден от който важи отлагането (None: от началото на отчета);
      само изречения със съвет (_is_advice) и само думи за действие във всяка форма освен „започва“, „ангажира“ и подобните (_DESCRIPTIVE_FORMS);
    - avoid_window — забрана за прозорец, датата е краят му (включително), началото е началото му.
    month_start е първият ден на месеца на частта: „до 25 декември“ и „след 25 декември“ само с дата в месечен блок важат от този ден.
    Общите думи (GENERIC_ACTIONS) дават само do и avoid; последният елемент е номерът на изречението в частта (двойки от едно изречение не се сравняват)."""
    out: List[Statement] = []
    for sid, sentence in enumerate(safety._plain_sentences(text)):
        words = _action_words(sentence)
        if not words or not _is_advice(sentence):
            continue
        labels = list(dict.fromkeys(label for _, label in words))
        items = sorted(text_check.parse_dates(sentence, facts), key=lambda it: it.start)
        if not items:
            continue
        first_action = min(pos for pos, _ in words)
        negated = bool(_AVOID_RE.search(sentence[max(0, first_action - 25):first_action + 1]))
        deferred = bool(_DEFER_WORD_RE.search(sentence))
        who = _who(sentence, facts)
        if deferred or negated:
            windows, taken = _windows(sentence, items)
            for start, end in windows:
                for label in labels:
                    if label not in GENERIC_ACTIONS:
                        out.append((label, "from" if deferred else "avoid_window", end, sentence[:160], who, start, sid))
        else:
            # прозорец без отлагане и без отрицание е препоръка за периода: не се следи като прозорец, датите му се четат поотделно както преди
            taken = {i for i, item in enumerate(items) if item.is_range}
        for i, item in enumerate(items):
            if i in taken or item.is_range:
                continue
            day = item.dates[0]
            before = sentence[max(0, item.start - 12):item.start].lower()
            after = bool(re.search(r"(?<![\w])след\s*$", before))
            until = bool(re.search(r"(?<![\w])до\s*$", before))
            # „след 14 ноември“ и „изчакайте до 14 ноември“ са отлагане. Но „финализирайте до 24 ноември“ е срок, а не отлагане:
            # срокът не противоречи на препоръка за по-ранна дата и не се следи (иначе нормален текст се отказва).
            if until and not (deferred or negated):
                continue
            kind = "from" if (after or deferred or until) else ("avoid" if negated else "do")
            start = month_start if (kind == "from" and (until or after) and month_start is not None and day >= month_start) else None
            for label in labels:
                if kind in ("do", "avoid") or label not in GENERIC_ACTIONS:
                    out.append((label, kind, day, sentence[:160], who, start, sid))
    return out


def _day(d: date) -> str:
    return f"{d.day} {text_check._MONTH_NAMES[d.month]}"


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

    # 2. препоръка и забрана на една дата; отлагане до по-късна дата или забрана за прозорец и препоръка вътре в него
    entries: List[Tuple[str, str, str, date, str, frozenset, Optional[date], Tuple[int, int]]] = []   # (етап, действие, вид, дата, изречение, хора, начало, (част, изречение))
    for part_index, (stage, text) in enumerate(parts):
        for label, kind, day, sentence, who, start, sid in _statements(text, facts, _month_start(stage)):
            entries.append((stage, label, kind, day, sentence, who, start, (part_index, sid)))

    def owner(a: str, b: str) -> str:
        if "overview" in (a, b):
            return "overview"
        return max(a, b)                                          # по-късният месец

    seen = set()
    for i, (stage_a, label_a, kind_a, day_a, text_a, who_a, start_a, where_a) in enumerate(entries):
        for stage_b, label_b, kind_b, day_b, text_b, who_b, start_b, where_b in entries[i + 1:]:
            if label_a != label_b or who_a != who_b or where_a == where_b:
                continue                                          # друго действие, друг човек или едно и също изречение: не е противоречие
            conflict: Optional[str] = None
            if {kind_a, kind_b} == {"do", "avoid"} and day_a == day_b:
                conflict = f"на {_day(day_a)} за „{label_a}“ има и препоръка, и забрана"
            else:
                for (k1, d1, s1, k2, d2) in ((kind_a, day_a, start_a, kind_b, day_b), (kind_b, day_b, start_b, kind_a, day_a)):
                    if k2 != "do":
                        continue
                    if k1 == "from" and d2 < d1 and (s1 is None or s1 <= d2):
                        conflict = f"„{label_a}“ е отложено до {_day(d1)}, а на {_day(d2)} е препоръчано"
                    elif k1 == "avoid_window" and s1 is not None and s1 <= d2 <= d1:
                        conflict = f"„{label_a}“ не се препоръчва от {_day(s1)} до {_day(d1)}, а на {_day(d2)} е препоръчано"
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
