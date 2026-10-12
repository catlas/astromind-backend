"""
Прогноза за период по карти: събитията, датите и притежателите идват от кода, AI само тълкува (четвърто приложение на идеята на
card_engine.py; периодът има свой ход, защото е многомесечен и се стриймва месец по месец).

Условията на независимия преглед, които този модул изпълнява:
1. Моментът, зоната, аспектът, движението (директно или ретроградно), смяната на знак и лунациите се пишат от кода, от календара на
   scanner.py. AI не ги възстановява: в тълкуването на карта няма дати, часове, знаци, домове, чужди планети, аспекти и думи за движение.
2. При двама принадлежността е изрична: „транзитният Плутон е в квадратура с наталния Плутон на Иван“. Притежателят е в заглавието и в
   проверката, не в свободния текст.
3. Точен аспект, най-близко приближение (без точен момент в периода) и интервал на действие по орбис са различни неща и се пишат различно.
   Интервалът е прозорец при орбис до 1,5° (приближаване) и 1° (отдалечаване); краят му не е край на житейски процес.
4. Целият отчет се проверява след сглобяването: всеки затварящ блок (съвет за месеца, обща картина, плановете, отговорът, заключението)
   се сверява с вече проверените блокове (една дата не може да е препоръчана и забранена за същото действие) и с датите на включените
   карти; накрая целият текст минава още веднъж.
Картите се подбират по важност (бавни планети, точен аспект, станции, смяна на знак, лунации), темата, отношенията и въпроса. Ключови са
двете най-важни карти на месеца: ако една не мине проверката дори след възстановяване, прогнозата се отказва. Пропусната карта прави
резултата частичен (не се таксува); провален затварящ блок при цели карти го прави резервен (нова композиция от проверените карти).
"""
import asyncio
import dataclasses
import re
from typing import Any, AsyncIterator, Dict, List, Optional, Tuple

import card_engine
import factpack
import natal_pair
import period_report
import report_guard
import safety
import text_check
import text_guard
from card_engine import Card, parse_sections, question_coverage
from pair_snapshot import _ORDINAL, _adjective
from scanner import APPLYING_ORB, SEPARATING_ORB, PeriodCalendar
from text_check import ASPECT_BG, PLANET_BG, SIGN_BG, Violation

CARDS_PER_MONTH = 8
KEY_CARDS_PER_MONTH = 2
MIN_CARDS_PER_MONTH = 3             # по-малко събития в месеца се допълват с аспекти, активни в него
MIN_KEPT_PER_MONTH = 2
MAX_FAILED_CARDS = 4                 # за целия отчет: повече провалени карти значи, че AI не следва картите
_MONTHS = text_check._MONTH_NAMES
_SLOW = {"Pluto": 0.0, "Neptune": 0.0, "Uranus": 0.0, "Saturn": 0.2, "Jupiter": 0.3, "Mars": 0.6, "Venus": 0.7, "Mercury": 0.8,
         "Sun": 1.0, "Moon": 1.2, "Chiron": 0.5, "Node": 0.4}
# „директно“ като наречие („кажете го директно“) е обикновена дума; забранени са ретроградният/директният ход като движение на планета
_MOTION_WORD_RE = re.compile(r"(?<![\w])(?:ретроград\w*|директен|директна|директни|директн\w*\s+(?:ход|движение)|директно\s+(?:движение|ход))(?![\w])",
                             re.IGNORECASE)
_CLOCK_RE = re.compile(r"(?<![\d:])\d{1,2}:\d{2}(?![\d:])")
WINDOW_NOTE = (f"Интервалът на действие е прозорец при орбис до {str(APPLYING_ORB).replace('.', ',')}° при приближаване и до "
               f"{str(SEPARATING_ORB).replace('.', ',')}° при отдалечаване; краят му е край на въздействието при този орбис, не край на "
               "житейски процес.")
_RETRO_MOTION = {"retrograde": "ретроградно", "direct": "директно"}


# --------------------------------------------------------------------------------------------------------------
# Карти от събитията на календара
# --------------------------------------------------------------------------------------------------------------
def _day(iso: str) -> str:
    """„2026-10-24“ или „2026-10-24 09:36“ -> „24 октомври“."""
    return f"{int(iso[8:10])} {_MONTHS[int(iso[5:7])]}"


def _moment(iso: str) -> str:
    return f"{_day(iso)}, {iso[11:16]}"


def _orb(value) -> str:
    return f"{float(value):.2f}".replace(".", ",")


def _houses_clause(row: Dict, first: str, second: str) -> str:
    """„в 4-ти дом на Иван“ за всеки човек с известен час (само от календара)."""
    houses = row.get("natal_house") or {}
    names = {"User": first, "Partner": second}
    parts = [f"в {_ORDINAL[h]} дом на {names[k]}" for k, h in houses.items() if h and k in names and h in _ORDINAL]
    return " (" + " и ".join(parts) + ")" if parts else ""


def _in_sign(sign_bg: str) -> str:
    """„в Телец“, но „във Везни“ и „във Водолей“."""
    return f"във {sign_bg}" if sign_bg[:1] in ("В", "Ф") else f"в {sign_bg}"


def _gendered(planet: str, masculine: str, feminine: str) -> str:
    return feminine if planet == "Venus" or planet == "Moon" else masculine


def transit_parts(row: Dict, owner: str) -> Tuple[str, str]:
    """(заглавие, подробности) на карта за транзит. Точният момент е във заглавието; най-близкото приближение и интервалът са подробности."""
    tp, npl = row["planet"], row["natal_planet"]
    aspect = ASPECT_BG[row["aspect"].lower()]
    base = (f"{_adjective('транзит', tp).capitalize()} {PLANET_BG.get(tp, tp)} е в {aspect} с {_adjective('натал', npl)} "
            f"{PLANET_BG.get(npl, npl)} на {owner}")
    exact = row.get("exact") or []
    if len(exact) == 1:
        title = f"{_moment(exact[0]['when'])} – {base}"
        detail = f"точен аспект, движение: {_RETRO_MOTION.get(exact[0]['motion'], exact[0]['motion'])}."
    elif exact:
        title = f"{_moment(exact[0]['when'])} – {base}"
        detail = "точен аспект на " + ", ".join(f"{_moment(x['when'])} ({_RETRO_MOTION.get(x['motion'], x['motion'])})" for x in exact) + "."
    elif row.get("closest_in_period"):
        closest = row["closest_in_period"]
        if closest.get("when"):
            when = f"на {_moment(closest['when'])}"
        else:
            when = "в " + ("началото" if closest.get("at") == "start_of_period" else "края") + " на периода"
        title = base
        detail = f"най-близко приближение {when} (отстояние {_orb(closest['orb'])}°); точен аспект в периода няма."
    else:
        title = base
        detail = "точен аспект в периода няма."
    outside = row.get("exact_outside_period") or []
    if outside:
        detail += " Точен аспект извън периода: " + ", ".join(_day(x) for x in outside) + "."
    clipped = " (интервалът излиза извън периода)" if row.get("window_clipped") else ""
    detail += f" Интервал на действие: от {_day(row['active_from'])} до {_day(row['active_to'])}{clipped}."
    return title, detail


def transit_heading(row: Dict, owner: str) -> str:
    title, detail = transit_parts(row, owner)
    return f"{title}: {detail}"


def station_heading(row: Dict, first: str, second: str) -> str:
    planet = row["planet"]
    word = _gendered(planet, "директен", "директна") if row["direction"] == "direct" else _gendered(planet, "ретрограден", "ретроградна")
    position = row.get("position") or ""
    sign = position.split(" ", 1)[0]
    where = f" {_in_sign(SIGN_BG[sign])}" if sign in SIGN_BG else ""
    return f"{_moment(row['when'])} – {PLANET_BG.get(planet, planet)} става {word}{where}{_houses_clause(row, first, second)}."


def ingress_heading(row: Dict, first: str, second: str) -> str:
    planet = row["planet"]
    origin = f", идвайки от {SIGN_BG[row['from_sign']]}" if row.get("from_sign") in SIGN_BG else ""
    return (f"{_moment(row['when'])} – {_DEFINITE_PLANET(planet)} навлиза {_in_sign(SIGN_BG.get(row['sign'], row['sign']))}"
            f"{origin}{_houses_clause(row, first, second)}.")


def _DEFINITE_PLANET(planet: str) -> str:
    from pair_snapshot import _DEFINITE
    return _DEFINITE.get(planet, PLANET_BG.get(planet, planet))


def lunation_heading(row: Dict, first: str, second: str) -> str:
    event = row.get("event", "")
    if row.get("type") == "ECLIPSE":
        kind = "Слънчево затъмнение" if event.startswith("Solar") else "Лунно затъмнение"
    else:
        kind = "Новолуние" if event.startswith("New Moon") else "Пълнолуние"
    position = row.get("position") or ""
    sign = position.split(" ", 1)[0]
    where = f" {_in_sign(SIGN_BG[sign])}" if sign in SIGN_BG else ""
    return f"{_moment(row['when'])} – {kind}{where}{_houses_clause(row, first, second)}."


def event_planets(row: Dict) -> set:
    if row["type"] == "TRANSIT":
        return {row["planet"], row["natal_planet"]}
    if row["type"] in ("LUNATION", "ECLIPSE"):
        return {"Sun", "Moon"}
    return {row["planet"]}


def importance(row: Dict, rel: natal_pair.Relevance) -> float:
    """По-малко е по-важно. Бавните планети, точният аспект, станциите, смяната на знак и затъмненията водят; темата, отношенията и
    въпросът (Relevance) подреждат отгоре. Само подрежда: фактите не се променят."""
    kind = row["type"]
    if kind == "TRANSIT":
        base = 1.0 + (0.0 if row.get("exact") else 1.5) + _SLOW.get(row["planet"], 1.0) * 0.5
    elif kind == "RETROGRADE":
        base = 1.2 + _SLOW.get(row["planet"], 1.0) * 0.5
    elif kind == "INGRESS":
        base = 1.6 + _SLOW.get(row["planet"], 1.0) * 0.5
    else:
        base = 0.8 if row["type"] == "ECLIPSE" else 1.3
    return base - 0.4 * sum(rel.planet(p) for p in event_planets(row))


def _closest_when(row: Dict, calendar: PeriodCalendar) -> str:
    """Моментът на най-близкото приближение; ако е на границата на периода (няма час), началото/краят на периода."""
    closest = row.get("closest_in_period") or {}
    if closest.get("when"):
        return closest["when"]
    if closest.get("at") == "end_of_period":
        return calendar.end + " 23:59"
    return calendar.start + " 00:00"


def event_month(row: Dict, calendar: PeriodCalendar) -> str:
    """Към кой месец принадлежи събитието: на първия точен момент, иначе на най-близкото приближение, иначе на началото на интервала."""
    if row["type"] == "TRANSIT":
        if row.get("exact"):
            return row["exact"][0]["when"][:7]
        return _closest_when(row, calendar)[:7]
    return row["when"][:7]


def event_sort_key(row: Dict, calendar: PeriodCalendar) -> str:
    if row["type"] == "TRANSIT":
        if row.get("exact"):
            return row["exact"][0]["when"]
        return _closest_when(row, calendar)
    return row["when"]


def build_month_cards(calendar: PeriodCalendar, month: str, month_index: int, first: str, second: str, rel: natal_pair.Relevance,
                      limit: int = CARDS_PER_MONTH) -> List[Card]:
    kinds = ("TRANSIT", "RETROGRADE", "INGRESS", "LUNATION", "ECLIPSE")
    events = [r for r in calendar.public_events() if r["type"] in kinds]
    rows = [r for r in events if event_month(r, calendar) == month]
    if len(rows) < MIN_CARDS_PER_MONTH:
        # Месец, чиито събития са по-скоро в съседните (бавен аспект, активен в месеца): аспектите, активни в него, допълват картите.
        first_day, last_day = f"{month}-01", f"{month}-31"
        active = [r for r in events if r["type"] == "TRANSIT" and r not in rows and r["active_from"] <= last_day and r["active_to"] >= first_day]
        rows += sorted(active, key=lambda r: importance(r, rel))[:MIN_CARDS_PER_MONTH - len(rows)]
    chosen = sorted(rows, key=lambda r: (importance(r, rel), event_sort_key(r, calendar)))[:limit]
    chosen.sort(key=lambda r: event_sort_key(r, calendar))
    cards: List[Card] = []
    for index, row in enumerate(chosen, 1):
        title, detail = "", ""
        if row["type"] == "TRANSIT":
            owner = first if row["target"] == "User" else second
            title, detail = transit_parts(row, owner)
            heading = f"{title}: {detail}"
        elif row["type"] == "RETROGRADE":
            heading = station_heading(row, first, second)
        elif row["type"] == "INGRESS":
            heading = ingress_heading(row, first, second)
        else:
            heading = lunation_heading(row, first, second)
        if not title:
            title = heading
        other = ""
        if row["type"] == "TRANSIT" and second:
            other = second if row["target"] == "User" else first
        cards.append(Card(f"M{month_index}E{index}", f"M{month_index}", heading, row={"kind": "event", "event": row, "planets": sorted(event_planets(row)),
                                                                                        "aspect": row.get("aspect", "").lower() or None,
                                                                                        "other_person": other, "title": title, "detail": detail,
                                                                                        "owner": (first if row["target"] == "User" else second) if row["type"] == "TRANSIT" else ""},
                          score=importance(row, rel)))
    for card in sorted(cards, key=lambda c: c.score)[:KEY_CARDS_PER_MONTH]:
        card.key = True
    return cards


# --------------------------------------------------------------------------------------------------------------
# Проверки
# --------------------------------------------------------------------------------------------------------------
def restrict_events(facts, cards: List[Card]):
    """Фактите само със събитията на дадените карти: всяко друго събитие (и всеки аспект) в текста е нарушение."""
    if not isinstance(facts, text_check.Facts):
        return facts
    return dataclasses.replace(facts, natal_aspects={}, synastry=[], transit_aspects={}, calendar=[c.row["event"] for c in cards], question="")


def period_card_scope(card: Card):
    """extra_check за тълкуването на карта: само нейните планети и нейният аспект, без знаци, домове, часове и думи за движение; датите
    само от заглавието на картата (те са факт от кода). Всичко това е в заглавието, написано от кода."""
    own = set(card.row.get("planets") or [])
    own_aspect = card.row.get("aspect")
    own_dates = {(int(i[5:7]), int(i[8:10])) for i in re.findall(r"\d{4}-\d{2}-\d{2}", repr(card.row.get("event")))}

    def check(text: str) -> List[Violation]:
        out: List[Violation] = []
        for key, rx in text_check.PLANET_RE.items():
            m = rx.search(text or "")
            if m and key not in own:
                out.append(Violation("event", "hard", text[max(0, m.start() - 30):m.end() + 30],
                                     f"В тълкуването на тази карта не споменавай {PLANET_BG.get(key, key)}: картата е само за "
                                     f"{' и '.join(PLANET_BG.get(p, p) for p in sorted(own))}. Не назовавай други планети и аспекти."))
        for key, rx in text_check.ASPECT_RE.items():
            m = rx.search(text or "")
            if m and key != own_aspect:
                out.append(Violation("event", "hard", text[max(0, m.start() - 30):m.end() + 30],
                                     f"В тълкуването на тази карта не споменавай {ASPECT_BG.get(key, key)}: картата не е такъв аспект."))
        out.extend(card_engine._names_in(text, "в тълкуването на карта", sign_house_only=True))
        other = card.row.get("other_person") or ""
        owner = card.row.get("owner") or ""
        if other:
            words = {other} | {w for w in re.findall(r"[^\W\d_]{3,}", other) if w.lower() not in owner.lower()}
            for word in words:
                m = re.search(r"(?<![\w])" + re.escape(word) + r"(?![\w])", text or "", re.IGNORECASE)
                if m:
                    out.append(Violation("event", "hard", text[max(0, m.start() - 30):m.end() + 30],
                                         f"Тази карта е само за {owner}: не споменавай {other}. Принадлежността е в заглавието, написано от кода."))
                    break
        for rx, what in ((_MOTION_WORD_RE, "посока на движение"), (_CLOCK_RE, "час")):
            m = rx.search(text or "")
            if m:
                out.append(Violation("event", "hard", text[max(0, m.start() - 30):m.end() + 30],
                                     f"В тълкуването на карта не споменавай {what}: тя е в заглавието, написано от кода."))
        for start, end, day, month in _date_mentions(text or ""):
            if (month, day) not in own_dates:
                out.append(Violation("date", "hard", text[max(0, start - 20):end + 20],
                                     "В тълкуването на карта споменавай само датите от заглавието ѝ (те са проверени); не добавяй други дати. "
                                     "По-добре пиши без дата: „през тези дни“."))
                break
        return out
    return check


def _date_mentions(text: str) -> List[Tuple[int, int, int, int]]:
    """(начало, край, ден, месец) на всяка дата в текста: „24 октомври“, „1–5 октомври“, „24.10“, „2026-10-24“."""
    out: List[Tuple[int, int, int, int]] = []
    for m in text_check.DATE_TEXT_RE.finditer(text):
        month = text_check.MONTHS[m.group(3).lower()]
        for g in (m.group(1), m.group(2)):
            if g:
                out.append((m.start(), m.end(), int(g), month))
    for m in text_check.DATE_NUM_RE.finditer(text):
        out.append((m.start(), m.end(), int(m.group(1)), int(m.group(2))))
    for m in text_check.DATE_ISO_RE.finditer(text):
        out.append((m.start(), m.end(), int(m.group(3)), int(m.group(2))))
    return out


def _any_date(text: str) -> Optional[Tuple[int, int]]:
    found = _date_mentions(text)
    return (found[0][0], found[0][1]) if found else None


def allowed_dates(cards: List[Card], calendar: PeriodCalendar) -> set:
    """Датите (месец, ден) от заглавията на включените карти и границите на периода: само те могат да се споменават в затварящите блокове."""
    out = {(int(calendar.start[5:7]), int(calendar.start[8:10])), (int(calendar.end[5:7]), int(calendar.end[8:10]))}
    for card in cards:
        for iso in re.findall(r"\d{4}-\d{2}-\d{2}", repr(card.row.get("event"))):
            out.add((int(iso[5:7]), int(iso[8:10])))
    return out


def dates_from_cards(allowed: set):
    """extra_check за затварящите блокове: всяка дата в текста е дата на включена карта (или граница на периода)."""
    def check(text: str) -> List[Violation]:
        out: List[Violation] = []
        for start, end, day, month in _date_mentions(text or ""):
            if (month, day) not in allowed:
                out.append(Violation("date", "hard", (text or "")[max(0, start - 20):end + 20],
                                     f"Датата {day} {_MONTHS[month] if 1 <= month <= 12 else month} не е дата на включена карта. Споменавай само "
                                     "дати от картите (или пиши без дата: „през месеца“, „в началото/края на периода“)."))
        return out
    return check


_END_VERB_RE = re.compile(r"(?<![\w])(свършва\w*|приключва\w*|завършва\w*|изтича\w*|отшумява\w*|затихва\w*|отминава\w*|край на|краят на|до края на)", re.IGNORECASE)


def window_ends(cards: List[Card]) -> set:
    """(месец, ден) на края на интервала на действие на всеки включен аспект."""
    out = set()
    for card in cards:
        event = card.row.get("event") or {}
        if event.get("type") == "TRANSIT" and event.get("active_to"):
            out.add((int(event["active_to"][5:7]), int(event["active_to"][8:10])))
    return out


def window_end_claims(ends: set):
    """extra_check: краят на интервала на действие не е край на житейски процес. Изречение с глагол за край и дата, която е край на
    интервал, се връща за поправка (датата може да се спомене, но не като край на събитие или състояние)."""
    def check(text: str) -> List[Violation]:
        out: List[Violation] = []
        for sentence in re.split(r"(?<=[.!?])\s+|\n+", text or ""):
            if not _END_VERB_RE.search(sentence):
                continue
            for start, end, day, month in _date_mentions(sentence):
                if (month, day) in ends:
                    out.append(Violation("date", "hard", sentence[:160],
                                         f"{day} {_MONTHS[month] if 1 <= month <= 12 else month} е краят на интервал на действие по орбис, не краят на "
                                         "житейски процес: не пиши, че нещо свършва или приключва на тази дата."))
                    break
        return out
    return check


def closing_checks(question: str, tag: str, allowed: set, facts, prior_parts: List[Tuple[str, str]], stage: str, months: List[str],
                   ends: frozenset = frozenset()):
    """Всичко за един затварящ блок: без планети/аспекти/знаци/домове, само дати от картите, покритие на въпроса и съгласуваност с
    вече проверените блокове (препоръка и забрана на една дата за едно действие)."""
    coverage = question_coverage(question) if tag == "ANSWER" else None
    dates = dates_from_cards(allowed)
    ends_check = window_end_claims(set(ends))

    def check(text: str) -> List[Violation]:
        out = card_engine.closing_scope(text) + dates(text) + ends_check(text) + (coverage(text) if coverage else [])
        if isinstance(facts, text_check.Facts):
            found = report_guard.cross_part_violations(prior_parts + [(stage, text)], months, facts)
            out += found.get(stage, [])
        return out
    return check


# --------------------------------------------------------------------------------------------------------------
# Подкани
# --------------------------------------------------------------------------------------------------------------
MONTH_SYSTEM = (
    "MODE: MONTHLY FORECAST BY FACT CARDS. You INTERPRET pre-written FACT CARDS; you do not state facts yourself.\n"
    "THEME: {theme} Keep EVERY section on this theme{relation_line}.\n"
    "People: {people}. Month: {month}. All times are local time ({zone}).\n\n"
    "The code has already written every fact: the exact moment, the planet and whose natal planet it touches, the aspect, the direction of "
    "movement, the sign changes, the lunations and the window of action. They are printed above your text, so you never restate them.\n\n"
    "OUTPUT FORMAT (strict). Output ONLY sections. Each section starts with its tag alone on a line:\n"
    "{tags}"
    "[[MONTH]]  3-5 sentences: what to focus on and what to avoid this month, only from the cards.\n\n"
    "RULES FOR EVERY SECTION:\n"
    "1. In a card section write 2-3 sentences about THAT card only, in plain words, for the person named in the heading. The heading already "
    "names the planets, the aspect, the date, the time, the movement and the sign: do NOT name any planet, aspect, sign, house, time "
    "or movement there; avoid dates (if you need one, use only a date from the heading), and no degrees or orb numbers. Start directly with the meaning (for example: \"През тези дни...\"). A card about a "
    "station, a sign change or a lunation concerns every person named in its heading (or the one person, if only one is named).\n"
    "2. Never invent, compute or guess a planet, sign, house, aspect, date or time. A fact that is not in a card does not exist.\n"
    "3. In MONTH name NO planet, aspect, sign or house. Name a date only if it is a date from a card heading. Every recommendation must "
    "follow from a card. No predictions of the future, no diagnoses.\n"
    "4. The window of action is not the end of a life process: never say that something \"ends\" on the last day of a window.\n"
    "5. Respect the relationship: never use a romantic or sexual frame unless it is romantic.\n"
    "6. Be direct and concrete. No filler, no mystic phrases.\n"
    "7. Address the reader consistently with the polite plural \"вие\" (ваш, ви, вас); never switch to \"ти\" (твой, те) in any section.\n"
)

OVERVIEW_SYSTEM = (
    "MODE: PERIOD OVERVIEW FROM VERIFIED BLOCKS.\n"
    "THEME: {theme}{relation_line}. People: {people}. Period: {period}. All times are local time ({zone}).\n"
    "Below are the ONLY verified blocks of the whole period (the month cards with their checked interpretations and the month advice). "
    "Write the closing sections using ONLY them:\n"
    "[[SUMMARY]]  4-6 sentences: the overall character of the period and how the months follow each other.\n"
    "[[ADVICE]]  4-6 bullets starting with \"- \": one consistent plan for the period. A date may be named only if it is a date from a card "
    "heading.\n"
    "{answer_tag}"
    "[[CONCLUSION]]  2-3 sentences with a reasoned overall conclusion.\n\n"
    "RULES: name NO planet, aspect, sign or house: write only the meaning. Every recommendation must follow from a block. The same date "
    "may not be recommended and forbidden for the same action, and a task postponed until a later date may not be recommended earlier. "
    "The window of a transit is not the end of a life process. Respect the relationship (no romantic or sexual frame unless it is "
    "romantic). Direct, concrete style, no filler. Address the reader consistently with the polite plural \"вие\", never \"ти\".\n"
)

MONTH_COMPOSE_SYSTEM = (
    "MODE: MONTH ADVICE FROM VERIFIED CARDS.\n"
    "THEME: {theme}{relation_line}. People: {people}. Month: {month}. All times are local time ({zone}).\n"
    "Below are the ONLY verified cards of this month. Write ONE section: [[MONTH]]  3-5 sentences on what to focus on and what to avoid "
    "this month, only from them. Name NO planet, aspect, sign or house; name a date only if it is a date from a card heading; every "
    "recommendation must follow from a card. Direct, concrete style. Address the reader consistently with the polite plural \"вие\", never \"ти\".\n"
)

RESTORE_SYSTEM = (
    "You write ONE short interpretation for ONE pre-written fact card of a monthly forecast. Output only the interpretation text, no tags.\n"
    "THEME: {theme}\n"
    "Write 2-3 sentences about THAT card only, in plain words, for the person named in the heading. The heading already names the planets, the "
    "aspect, the date, the time, the movement and the sign: do NOT name any planet, aspect, sign, house, date, time or movement. Start "
    "directly with the meaning. Be direct and concrete, no filler, no predictions. Address the reader with the polite plural \"вие\", never \"ти\". Write in Bulgarian.\n"
)


def _relation_line(relation: str) -> str:
    return f" and on the relationship ({relation})" if relation else ""


def _month_user(question: str, first: str, second: Optional[str], gender, partner_gender, cards: List[Card]) -> str:
    user = f"User Question: {question or 'Provide a forecast for this month.'}\n\n"
    user += factpack.identity_section(first, gender, second, partner_gender) if second else factpack.identity_section(first, gender)
    user += "FACT CARDS (written by the code; every date, time, movement and owner is verified):\n"
    user += "".join(f"{c.id}: {c.heading}\n" for c in cards) + "\n"
    return user + "Write the sections now, in Bulgarian."


# --------------------------------------------------------------------------------------------------------------
# Ходът на отчета
# --------------------------------------------------------------------------------------------------------------
def _failure(stage: str, failure: text_guard.TextCheckError, checks: List[Dict]) -> period_report.ForecastGenerationError:
    return period_report.ForecastGenerationError(stage, failure, checks)


async def _ai(interpreter, system: str, user: str, stage: str, retry_pause: float, max_tokens: Optional[int] = None) -> str:
    """Заявка към AI с един автоматичен повторен опит при грешка на доставчика (както при старите месеци)."""
    async def call() -> str:
        return await interpreter._call_api(system_prompt=system, user_prompt=user, max_tokens=max_tokens or interpreter.max_output_tokens)
    return await period_report._attempt(stage, call, retry_pause)


async def _check_cards(interpreter, facts, cards: List[Card], sections: Dict[str, str], checks: List[Dict], failed: Dict[str, str],
                       month: str) -> None:
    for card in cards:
        raw = sections.get(card.id)
        if not raw:
            card.removed, failed[card.id] = True, "missing"
        else:
            checked, codes = await card_engine._check_block(interpreter, restrict_events(facts, [card]), f"period:{month}:{card.id}", raw,
                                                            checks, period_card_scope(card))
            if checked is None:
                card.removed, failed[card.id] = True, codes
            else:
                card.text = checked
        if len(failed) > MAX_FAILED_CARDS:
            raise text_guard.TextCheckError(f"period:{month}:too_many", _outcome(f"period:{month}:too_many", "complete",
                                                                                  f"{len(failed)} карти не минаха проверката."))


def _outcome(stage: str, code: str, detail: str) -> text_guard.GuardOutcome:
    return text_guard.GuardOutcome(stage=stage, mode=text_guard.MODE_ENFORCE, text="", after=[Violation(code, "hard", "", detail)],
                                   rejected=True, reason="violations_remain")


async def _restore(interpreter, facts, card: Card, theme: str, checks: List[Dict], month: str, retry_pause: float) -> bool:
    system = RESTORE_SYSTEM.format(theme=theme)
    user = f"CARD {card.id}: {card.heading}\nWrite the interpretation now, in Bulgarian."
    try:
        raw = await interpreter._call_api(system_prompt=system, user_prompt=user, max_tokens=800)
    except Exception as exc:
        import ai_budget
        if isinstance(exc, ai_budget.BudgetExceeded):
            raise
        return False
    checked, _codes = await card_engine._check_block(interpreter, restrict_events(facts, [card]), f"period:{month}:{card.id}:restore", raw,
                                                     checks, period_card_scope(card))
    if checked is None:
        return False
    card.text, card.removed = checked, False
    return True


def month_text(cards: List[Card], advice: str) -> str:
    kept = [c for c in cards if not c.removed]
    parts = [f"### {c.row['title']}\n" + (f"*{c.row['detail'][0].upper()}{c.row['detail'][1:]}*\n" if c.row["detail"] else "") + c.text for c in kept]
    parts.append(f"### Съвет за месеца\n{advice}")
    return "\n\n".join(parts)


async def run_period_report(
    interpreter, *, calendar: PeriodCalendar, natal_chart: Dict, partner_chart: Optional[Dict], report_type: str, user_name: Optional[str],
    partner_name: Optional[str], question: str, gender: Optional[str] = None, partner_gender: Optional[str] = None, language: str = "bg",
    report_date: Optional[str] = None, retry_pause: Optional[float] = None, relation: str = "",
) -> AsyncIterator[Dict[str, Any]]:
    """Същите събития като period_report.run_period_report: month_start, month_complete, part_replaced (по изключение), overview_start,
    overview_complete и finished. Хвърля period_report.ForecastGenerationError."""
    retry_pause = period_report.RETRY_PAUSE_SECONDS if retry_pause is None else retry_pause
    first = factpack.display_name(user_name, factpack.FIRST_PERSON_DEFAULT)
    second = factpack.display_name(partner_name, factpack.SECOND_PERSON_DEFAULT) if partner_chart is not None else ""
    people = f"{first} and {second}" if second else first
    report_date = report_date or period_report.today_in_zone(calendar.timezone)
    theme = interpreter_theme(report_type)
    rel = natal_pair.Relevance(report_type, relation, question)
    months = calendar.months_with_events()

    facts = interpreter.build_text_facts(mode="period", user_name=user_name, natal_chart=natal_chart, partner_name=partner_name if second else None,
                                         partner_chart=partner_chart, calendar=calendar, report_date=report_date, question=question)
    checks: List[Dict] = []
    try:
        interpreter.require_text_facts(facts, stage="facts", checks=checks)
    except text_guard.TextCheckError as failure:
        raise period_report.ForecastGenerationError("facts", failure, checks) from failure

    all_cards: List[Card] = []
    month_blocks: Dict[str, str] = {}
    verified_parts: List[Tuple[str, str]] = []
    failed: Dict[str, str] = {}
    restored: List[str] = []
    fallback = False
    month_titles: List[Tuple[str, str]] = []             # (заглавие, текст) по реда на месеците, които имат карти
    month_keys: List[str] = []                           # месецът („YYYY-MM“) на всеки запис в month_titles
    flags: List[str] = []
    try:
        for index, month in enumerate(months, 1):
            title = period_report.month_title(month)
            yield {"type": "month_start", "month": title, "index": index - 1, "total": len(months)}
            cards = build_month_cards(calendar, month, index, first, second, rel)
            if not cards:
                continue
            known = {c.id for c in cards} | {"MONTH"}
            system = MONTH_SYSTEM.format(theme=theme, relation_line=_relation_line(relation), people=people, month=title, zone=calendar.timezone,
                                         tags="".join(f"[[{c.id}]]  interpretation of card {c.id}\n" for c in cards))
            user = _month_user(question, first, second or None, gender, partner_gender, cards)
            sections: Dict[str, str] = {}
            for attempt in (1, 2):
                raw = await _ai(interpreter, system, user, f"month:{month}", retry_pause)
                sections = parse_sections(raw, known)
                if sum(1 for c in cards if sections.get(c.id)) >= len(cards) // 2 and sections.get("MONTH"):
                    break
                print(f"🔎 Формат на отговора (месец {month}, опит {attempt}): {card_engine._shape(raw)}")
            else:
                raise text_guard.TextCheckError(f"period:{month}:format", _outcome(f"period:{month}:format", "complete", "Неправилен формат."))
            await _check_cards(interpreter, facts, cards, sections, checks, failed, month)
            for card in [c for c in cards if c.removed]:
                if await _restore(interpreter, facts, card, theme, checks, month, retry_pause):
                    restored.append(card.id)
                    failed.pop(card.id, None)
            lost_key = [c.id for c in cards if c.removed and c.key]
            if lost_key:
                raise text_guard.TextCheckError(f"period:{month}:key_card", _outcome(f"period:{month}:key_card", "complete",
                                                                                      "Не мина проверката ключова карта: " + ", ".join(lost_key)))
            kept = [c for c in cards if not c.removed]
            need = min(MIN_KEPT_PER_MONTH, len(cards))
            if len(kept) < need:
                raise text_guard.TextCheckError(f"period:{month}:minimum", _outcome(f"period:{month}:minimum", "complete",
                                                                                     f"Проверени са само {len(kept)} от {len(cards)} карти."))
            all_cards += cards
            # Съветът за месеца: от всички карти (ако нищо не е пропуснато) или наново само от включените
            allowed = allowed_dates(kept, calendar)
            ends = frozenset(window_ends(kept))
            stage = f"month:{month}"
            advice = None
            if len(kept) == len(cards) and sections.get("MONTH"):
                checked, _codes = await card_engine._check_block(
                    interpreter, restrict_events(facts, kept), f"period:{month}:MONTH", sections["MONTH"], checks,
                    closing_checks("", "MONTH", allowed, facts, verified_parts, stage, months, ends))
                advice = checked
            if advice is None:
                fallback = True
                blocks = "".join(f"[{c.id}] {c.heading}\n{c.text}\n\n" for c in kept)
                composed = await _ai(interpreter, MONTH_COMPOSE_SYSTEM.format(theme=theme, relation_line=_relation_line(relation), people=people,
                                                                              month=title, zone=calendar.timezone),
                                     f"VERIFIED CARDS:\n{blocks}Write the section now, in Bulgarian.", f"month:{month}:compose", retry_pause)
                parsed = parse_sections(composed, {"MONTH"})
                checked, codes = await card_engine._check_block(
                    interpreter, restrict_events(facts, kept), f"period:{month}:compose:MONTH", parsed.get("MONTH", ""), checks,
                    closing_checks("", "MONTH", allowed, facts, verified_parts, stage, months, ends))
                if checked is None:
                    raise text_guard.TextCheckError(f"period:{month}:compose:MONTH", _outcome(f"period:{month}:compose:MONTH", "complete",
                                                                                              f"Съветът за месеца не мина проверката ({codes})."))
                advice = checked
            month_blocks[month] = advice
            verified_parts.append((stage, advice))
            text, month_flags = safety.check_output(month_text(cards, advice), report_type)
            flags.extend(month_flags)
            month_titles.append((title, text))
            month_keys.append(month)
            yield {"type": "month_complete", "month": title, "text": text, "index": index - 1, "total": len(months)}
            await asyncio.sleep(0.05)

        # Общ преглед: само от проверените блокове
        yield {"type": "overview_start", "title": period_report.OVERVIEW_TITLE}
        kept_all = [c for c in all_cards if not c.removed]
        allowed_all = allowed_dates(kept_all, calendar)
        ends_all = frozenset(window_ends(kept_all))
        blocks_text = "".join(f"[{c.id}] {c.heading}\n{c.text}\n\n" for c in kept_all)
        advice_text = "".join(f"MONTH ADVICE {m}: {t}\n" for m, t in month_blocks.items())
        answer_tag = "[[ANSWER]]  a direct answer to the user's question in 3-5 sentences, based only on the blocks.\n" if question else ""
        system = OVERVIEW_SYSTEM.format(theme=theme, relation_line=_relation_line(relation), people=people, period=f"{calendar.start} - {calendar.end}",
                                        zone=calendar.timezone, answer_tag=answer_tag)
        user = f"User question: {question or 'none'}\n\nVERIFIED BLOCKS:\n{blocks_text}{advice_text}Write the sections now, in Bulgarian."
        needed = ["SUMMARY", "ADVICE"] + (["ANSWER"] if question else []) + ["CONCLUSION"]
        overview_blocks: Dict[str, str] = {}
        for attempt in (1, 2):
            raw = await _ai(interpreter, system, user, "overview", retry_pause, 4000)
            sections = parse_sections(raw, set(needed))
            if all(sections.get(t) for t in needed):
                break
            print(f"🔎 Формат на отговора (общ преглед, опит {attempt}): {card_engine._shape(raw)}")
        else:
            raise text_guard.TextCheckError("period:overview:format", _outcome("period:overview:format", "complete", "Неправилен формат."))
        prior = list(verified_parts)
        for tag in needed:
            checked, codes = await card_engine._check_block(
                interpreter, restrict_events(facts, kept_all), f"period:overview:{tag}", sections[tag], checks,
                closing_checks(question, tag, allowed_all, facts, prior, "overview", months, ends_all))
            if checked is None:
                raise text_guard.TextCheckError(f"period:overview:{tag}", _outcome(f"period:overview:{tag}", codes.split(",")[0] or "check",
                                                                                   f"Блокът {tag} не мина проверката ({codes})."))
            overview_blocks[tag] = checked
            prior.append(("overview", checked))
    except text_guard.TextCheckError as failure:
        raise _failure(failure.stage, failure, checks) from failure
    except period_report.ForecastGenerationError as failure:
        failure.checks = checks
        raise

    removed = [c.id for c in all_cards if c.removed]
    quality = "partial" if removed else ("fallback" if fallback else "full")
    overview = assemble_overview(kept_all, overview_blocks, calendar, question, months)
    if quality != "full":
        overview = f"{card_engine.QUALITY_NOTES[quality]}\n\n{overview}"
    overview, overview_flags = safety.check_output(overview, report_type)
    flags.extend(overview_flags)
    # Цялостна проверка на сглобения отчет: факти (само включените събития), пълнота и противоречия между всички части
    final: List[Violation] = []
    if text_guard.check_mode() == text_guard.MODE_ENFORCE and isinstance(facts, text_check.Facts):
        parts = [(f"month:{m}", t) for m, (_title, t) in zip(month_keys, month_titles)] + [("overview", overview)]
        final_facts = dataclasses.replace(restrict_events(facts, kept_all), question=facts.question)      # пълнотата (отговор на въпроса) се иска само тук
        final = [v for v in text_check.check_text("\n\n".join(t for _, t in parts), final_facts) if v.severity == "hard"]
        for stage_name, violations in report_guard.cross_part_violations(parts, months, facts).items():
            final += violations
    if final:
        outcome = text_guard.GuardOutcome(stage="period:final", mode=text_guard.MODE_ENFORCE, text="", after=final, rejected=True,
                                          reason="violations_remain")
        checks.append(outcome.summary())
        raise period_report.ForecastGenerationError("period:final", text_guard.TextCheckError("period:final", outcome), checks)

    checks.append({"stage": "period_cards", "mode": "enforce", "result": "ok" if quality == "full" else quality, "quality": quality,
                   "cards": len(all_cards), "kept": len(all_cards) - len(removed), "removed": ",".join(removed)[:60],
                   "restored": ",".join(restored)[:30], "codes": ",".join(f"{k}:{v}" for k, v in failed.items())[:60]})
    yield {"type": "overview_complete", "title": period_report.OVERVIEW_TITLE, "text": overview}
    yield {"type": "finished", "overview": overview, "month_texts": month_titles, "flags": sorted(set(flags)), "months": months, "checks": checks}


def interpreter_theme(report_type: str) -> str:
    import ai_interpreter
    return ai_interpreter.THEME_FOCUS.get(report_type, ai_interpreter.THEME_FOCUS["general"])


def assemble_overview(cards: List[Card], blocks: Dict[str, str], calendar: PeriodCalendar, question: str, months: List[str]) -> str:
    strongest = sorted(cards, key=lambda c: c.score)[:6]
    strongest.sort(key=lambda c: event_sort_key(c.row["event"], calendar))
    parts = [f"Всички часове са по местно време ({calendar.timezone}). {WINDOW_NOTE}", "### Най-силни моменти\n" + "\n".join(f"- {c.row['title']}" for c in strongest),
             f"### Обща картина\n{blocks['SUMMARY']}", f"### Съгласуван план\n{blocks['ADVICE']}"]
    if question:
        parts.append(f"### Отговор на вашия въпрос\n{blocks['ANSWER']}")
    parts.append(f"### Заключение\n{blocks['CONCLUSION']}")
    return "\n\n".join(parts)
