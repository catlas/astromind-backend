#!/usr/bin/env python3
"""
Възпроизводима сверка на прогнозата за период по карти (period_cards.py) с фиксирани тестови данни.

Какво проверява в готов отчет (или в отчет, построен от кода при --offline), без да ползва scanner.py, aspects_engine, factpack и text_check:
- всеки точен аспект: местният час се превръща в UTC през zoneinfo, позицията на транзитната планета се смята наново (Swiss Ephemeris
  през swisseph) и разстоянието до наталната планета на ПОСОЧЕНИЯ човек трябва да е равно на аспекта (до 0,05°); чуждият човек не трябва
  да е на същия аспект; броят на точните преминавания в периода (извадка през 3 часа) съвпада с написаното;
- посоката на движение (директно или ретроградно) в момента;
- най-близкото приближение: отстояние и час, и че наистина няма точен момент в периода;
- интервала на действие: орбис 1,5° при приближаване и 1° при отдалечаване, пресметнат наново за всеки ден (граници на местни дати);
- станции (знак и посока), влизания в знак (знакът след и преди момента), новолуния, пълнолуния и затъмнения (елонгация и знак);
- домовете от куспидите на човека; липса на дом, когато часът на раждане е неизвестен;
- в тълкуванията на картите няма планети, аспекти, знаци, домове, часове и дати; в затварящите блокове няма планети, аспекти, знаци и
  домове, а датите са само от заглавията на картите и границите на периода.

Какво НЕ проверява: самите ефемериди, координатите на градовете и вярността на смисъла на тълкуванията; не доказва, че изборът на карти е
най-добрият (само че всяка показана карта е вярна). Позициите идват от Swiss Ephemeris като дадени: сверката доказва съгласуваност на
фактите с тях, не независимост от тях. Кодът за изчисление на календара (scanner.py) не се ползва.

Употреба (от папка backend):
  python3 scripts/verify_period.py                                        # фиксираните случаи, карти от кода (offline)
  python3 scripts/verify_period.py --report-file report.md --people A,IVAN --start 2026-10-01 --end 2026-11-30
Код за изход 1, ако има несъответствие.
"""
import argparse
import os
import re
import sys
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "scripts"))
os.environ.setdefault("SECRET_KEY", "verify-period-script")

import swisseph as swe  # noqa: E402
import verify_cards as vc  # noqa: E402
from verify_cards import ASPECTS, ORDINALS, PEOPLE, PLANETS, SIGNS, house_of, sep  # noqa: E402

MONTHS = {"януари": 1, "февруари": 2, "март": 3, "април": 4, "май": 5, "юни": 6, "юли": 7, "август": 8, "септември": 9, "октомври": 10,
          "ноември": 11, "декември": 12}
MONTH_RX = "|".join(MONTHS)
PLANET_IDS = {"Sun": swe.SUN, "Moon": swe.MOON, "Mercury": swe.MERCURY, "Venus": swe.VENUS, "Mars": swe.MARS, "Jupiter": swe.JUPITER,
              "Saturn": swe.SATURN, "Uranus": swe.URANUS, "Neptune": swe.NEPTUNE, "Pluto": swe.PLUTO}
APPLYING, SEPARATING = 1.5, 1.0                      # орбис на прозореца на действие (виж текста на отчета)
STEP_HOURS = 3
TIME_TOLERANCE_MINUTES = 2                          # точният момент е закръглен до минута
ASPECT_RX = "съвпад|секстил|квадратура|тригон|опозиция"
TOLERANCE = 0.05                                     # градуса: точност на минутата в заглавието и на извадката
SIGN_BY_NAME = {s: i for i, s in enumerate(SIGNS)}

TITLE_TRANSIT_RE = re.compile(rf"^(?:(\d{{1,2}}) ({MONTH_RX}), (\d{{2}}:\d{{2}}) – )?(Транзитн\S+) (.+?) е в ({ASPECT_RX}) с (натал\S+) (.+?) на (.+?)$")
TITLE_STATION_RE = re.compile(rf"^(\d{{1,2}}) ({MONTH_RX}), (\d{{2}}:\d{{2}}) – (.+?) става (директен|директна|ретрограден|ретроградна)(?: (?:в|във) (\S+))?(?: \((.+)\))?\.$")
TITLE_INGRESS_RE = re.compile(rf"^(\d{{1,2}}) ({MONTH_RX}), (\d{{2}}:\d{{2}}) – (.+?) навлиза (?:в|във) (\S+)(?:, идвайки от (\S+))?(?: \((.+)\))?\.$")
TITLE_LUNATION_RE = re.compile(rf"^(\d{{1,2}}) ({MONTH_RX}), (\d{{2}}:\d{{2}}) – (Новолуние|Пълнолуние|Слънчево затъмнение|Лунно затъмнение)(?: (?:в|във) (\S+))?(?: \((.+)\))?\.$")
HOUSE_PART_RE = re.compile(r"(?:в|във) (\S+) дом на (.+)")
SEASON_FULL_RE = re.compile(rf"(\d{{1,2}}) ({MONTH_RX}), (\d{{2}}:\d{{2}}) \((лятно|зимно) време\)")
SEASON_RE = re.compile(r"(\d{2}:\d{2}) \((лятно|зимно) време\)")
MOMENT_RE = re.compile(rf"(\d{{1,2}}) ({MONTH_RX}), (\d{{2}}:\d{{2}})")
DAY_RE = re.compile(rf"(\d{{1,2}}) ({MONTH_RX})(?:\s+\d{{4}})?")
DETAIL_EXACT_ONE = re.compile(r"^Точен аспект, движение: (директно|ретроградно)\.")
DETAIL_EXACT_MANY = re.compile(r"^Точен аспект на (.+?)\. ?(?:Интервал|Точен аспект извън)")
DETAIL_CLOSEST = re.compile(rf"^Най-близко приближение (?:на (\d{{1,2}}) ({MONTH_RX}), (\d{{2}}:\d{{2}})|в (началото|края) на периода) \(отстояние ([\d,]+)°\); точен аспект в периода няма\.")
DETAIL_NONE = re.compile(r"^Точен аспект в периода няма\.")
DETAIL_OUTSIDE = re.compile(rf"Точен аспект извън периода: (.+?)\. ?(?:Интервал|$)")
DETAIL_INTERVAL = re.compile(rf"Интервал на действие: от (\d{{1,2}}) ({MONTH_RX}) до (\d{{1,2}}) ({MONTH_RX})( \(интервалът излиза извън периода\))?\.")
FORBIDDEN_NAMES = [(re.compile(rf"(?<![\w]){re.escape(n)}(?![\w])", re.IGNORECASE), n) for n in sorted(PLANETS, key=len, reverse=True)]
ASPECT_DEFINITE = re.compile(r"(?<![\w])(секстил\w*|тригон\w*|квадратур\w*|конюнкци\w*|аспект\w*)", re.IGNORECASE)
ASPECT_AMBIGUOUS = re.compile(r"(?<![\w])(съвпад\w*|опозиц\w*|съединение\w*|квадрат\w*)", re.IGNORECASE)      # и обикновени думи
ASTRO_CUE = re.compile(r"транзит|натал|аспект|орбис|ретроград", re.IGNORECASE)


class _AspectWords:
    """Като останалите регулярни изрази тук: .search(текст) -> съвпадение или None. Двусмислените думи се броят само в изречение с астрологична
    следа (транзит, натален, аспект, орбис, ретрограден или име на планета)."""

    def finditer(self, text):
        for sentence in re.split(r"(?<=[.!?])\s+|\n+", text or ""):
            cue = bool(ASTRO_CUE.search(sentence)) or any(rx.search(sentence) for rx, _n in FORBIDDEN_NAMES)
            yield from ASPECT_DEFINITE.finditer(sentence)
            if cue:
                yield from ASPECT_AMBIGUOUS.finditer(sentence)

    def search(self, text):
        return next(self.finditer(text), None)


ASPECT_WORDS = _AspectWords()
SIGN_WORDS = re.compile(r"(?<![\w])(" + "|".join(SIGNS) + r")(?![\w])")
HOUSE_WORDS = re.compile(r"(?<![\w])(\d{1,2}-?(?:ви|ри|ти|ми|и)\s+дом|\d{1,2}\s*дом|(?:първ|втор|трет|четвърт|пет|шест|седм|осм|девет|десет|единадесет|дванадесет)\w*\s+(?:натален\s+)?дом)(?![\w])", re.IGNORECASE)  # „ви дом“ (вашия дом) е обикновена дума
CLOCK_RE = re.compile(r"(?<![\d:])\d{1,2}:\d{2}(?![\d:])")
DATE_RX = re.compile(rf"(?<![\w.])(\d{{1,2}})(?:\s*[-–]\s*(\d{{1,2}}))?\s+({MONTH_RX})(?![\w])", re.IGNORECASE)
MOTION_WORDS = re.compile(r"(?<![\w])(ретроград\w*|директен|директна|директни|директн\w*\s+(?:ход|движение)|директно\s+(?:движение|ход))(?![\w])", re.IGNORECASE)


# --------------------------------------------------------------------------------------------------------------
# Небето: само позициите (swisseph), всичко останало се смята тук
# --------------------------------------------------------------------------------------------------------------
class Sky:
    def __init__(self, zone):
        import engine
        engine.ensure_ephemeris()
        self.tz = ZoneInfo(zone)
        self.cache = {}

    def utc(self, year, month, day, clock, season=""):
        """season: „лятно“ (първото появяване на повтарящия се час, fold=0) или „зимно“ (второто, fold=1); без означение - fold=0."""
        local = datetime(year, month, day, int(clock[:2]), int(clock[3:5]), tzinfo=self.tz, fold=1 if season == "зимно" else 0)
        return local.astimezone(timezone.utc)

    def ambiguous(self, year, month, day, clock):
        a = datetime(year, month, day, int(clock[:2]), int(clock[3:5]), tzinfo=self.tz, fold=0)
        b = a.replace(fold=1)
        return a.utcoffset() != b.utcoffset()

    def jd(self, moment):
        return swe.julday(moment.year, moment.month, moment.day, moment.hour + moment.minute / 60 + moment.second / 3600)

    def lon(self, planet, moment):
        key = (planet, moment)
        if key not in self.cache:
            self.cache[key] = swe.calc_ut(self.jd(moment), PLANET_IDS[planet], swe.FLG_SWIEPH)[0][0] % 360
        return self.cache[key]

    def direction(self, planet, moment, hours=12):
        d = (self.lon(planet, moment + timedelta(hours=hours)) - self.lon(planet, moment) + 180) % 360 - 180
        return "директно" if d > 0 else "ретроградно"


def wrap180(x):
    return (x + 180) % 360 - 180


def signed_offset(angle, transit_lon, natal_lon):
    """0 при точен аспект. За съвпад и опозиция - със знак (за преминаването), за останалите - разликата на разстоянието."""
    if angle == 0:
        return wrap180(transit_lon - natal_lon)
    if angle == 180:
        return wrap180(transit_lon - natal_lon - 180)
    return sep(transit_lon, natal_lon) - angle


# --------------------------------------------------------------------------------------------------------------
# Разчитане на отчета
# --------------------------------------------------------------------------------------------------------------
def year_for(month, months_years, start, end):
    if month in months_years:
        return months_years[month]
    for y in range(int(start[:4]), int(end[:4]) + 1):
        if start <= f"{y}-{month:02d}-31" and f"{y}-{month:02d}-01" <= end:
            return y
    return int(start[:4])


def zone_from_label(label):
    """„Europe/Vienna“ или българското име на града („Виена“: езиковата поправка след генерирането заменя имената на зоните)."""
    if "/" in label:
        return label
    import zoneinfo
    from bg_text import ZONE_CITIES
    for city, bg in ZONE_CITIES.items():
        if bg == label:
            for name in sorted(zoneinfo.available_timezones()):
                if name.endswith("/" + city):
                    return name
    raise SystemExit(f"непозната зона в отчета: {label}")


def parse_report(text):
    """-> (cards, closing, zone, months_years). Картата е {title, detail, ai: [редове], line}; затварящите блокове са {name, text}."""
    zone_match = re.search(r"местно време \(([^)]+)\)", text)
    zone = zone_from_label(zone_match.group(1)) if zone_match else "Europe/Sofia"
    months_years = {}
    for m in re.finditer(rf"<h2>({MONTH_RX.title().replace('|', '|')}|{MONTH_RX}) (\d{{4}})</h2>", text, re.IGNORECASE):
        months_years[MONTHS[m.group(1).lower()]] = int(m.group(2))
    cards, closing = [], []
    current = None
    in_cards = False
    for raw in text.splitlines():
        line = raw.strip()
        if line.startswith("<h2>"):
            current = None
            in_cards = not line.lower().startswith("<h2>общ преглед")
            continue
        if line.startswith("### "):
            title = line[4:].strip()
            closing_names = ("Най-силни моменти", "Обща картина", "Съгласуван план", "Отговор на вашия въпрос", "Заключение", "Съвет за месеца")
            if title in closing_names:
                current = {"name": title, "text": []}
                closing.append(current)
            else:
                current = {"title": title, "detail": "", "ai": []}
                cards.append(current)
            continue
        if current is None or not line:
            continue
        if "name" in current:
            current["text"].append(line)
        elif not current["detail"] and line.startswith("*") and line.endswith("*") and len(line) > 2:
            current["detail"] = line[1:-1]
        else:
            current["ai"].append(line)
    return cards, closing, zone, months_years


# --------------------------------------------------------------------------------------------------------------
# Сверка
# --------------------------------------------------------------------------------------------------------------
def moment_of(sky, card, year, month, day, clock, problems):
    """UTC на посочения местен час; в повтарящия се час при смяна на часа е нужно означение „(лятно време)“ или „(зимно време)“."""
    season = card.get("smap", {}).get((day, month, clock), "")
    if not season and sky.ambiguous(year, month, day, clock):
        problems.append(f"повтарящ се час без означение за лятно или зимно време: {day}.{month} {clock} ({card['title'][:50]})")
    return sky.utc(year, month, day, clock, season)


def person_by_name(people, name):
    return next((p for p in people if p["name"] == name), None)


def check_houses(clause, longitude, people, title, problems):
    if not clause:
        for p in people:
            if p["chart"].known:
                problems.append(f"липсва домът на {p['name']}: {title[:60]}")
        return
    seen = set()
    for part in clause.split(" и "):
        m = HOUSE_PART_RE.match(part.strip())
        person = person_by_name(people, m.group(2)) if m else None
        if not (m and person and person["chart"].known and m.group(1) in ORDINALS):
            problems.append(f"дом не се разчита или няма час: {part[:50]} ({title[:50]})")
            continue
        seen.add(person["name"])
        if house_of(longitude, person["chart"].cusps) != ORDINALS[m.group(1)]:
            problems.append(f"дом {title[:60]}: {part} (изчислен {house_of(longitude, person['chart'].cusps)})")
    for p in people:
        if p["chart"].known and p["name"] not in seen:
            problems.append(f"липсва домът на {p['name']}: {title[:60]}")


def root_near(func, moment, hours=6, step_minutes=10):
    """Моментът (UTC), в който func сменя знака около moment (±hours), уточнен до секунди; None, ако няма смяна на знака."""
    t0 = moment - timedelta(hours=hours)
    steps = int(hours * 2 * 60 / step_minutes)
    prev_t, prev = t0, func(t0)
    for i in range(1, steps + 1):
        t = t0 + timedelta(minutes=step_minutes * i)
        cur = func(t)
        if prev * cur < 0 and abs(prev) < 5 and abs(cur) < 5:
            lo, hi, flo = prev_t, t, prev
            for _ in range(20):
                mid = lo + (hi - lo) / 2
                fm = func(mid)
                if flo * fm <= 0:
                    hi = mid
                else:
                    lo, flo = mid, fm
            return lo + (hi - lo) / 2
        prev_t, prev = t, cur
    return None


def minutes_apart(a, b):
    return abs((a - b).total_seconds()) / 60


def aspect_points(angle, natal):
    """Точките на зодиака, в които транзитната планета е в този аспект към наталната: два за секстил, квадратура и тригон."""
    if angle == 0:
        return [natal % 360]
    if angle == 180:
        return [(natal + 180) % 360]
    return [(natal + angle) % 360, (natal - angle) % 360]


def nearest_point(points, longitude):
    return min(points, key=lambda ap: abs(wrap180(longitude - ap)))


def verify_transit(card, m, sky, people, year_of, start, end, problems, counts):
    tname = m.group(5)
    nname = m.group(8)
    tp = vc.find_planet(tname)[0]
    npl = vc.find_planet(nname)[0]
    owner = person_by_name(people, m.group(9))
    if not (tp in PLANET_IDS and npl and owner and npl in owner["chart"].points):
        problems.append(f"не се разчита: {card['title'][:80]}")
        return
    natal = owner["chart"].points[npl]
    angle = ASPECTS[m.group(6)]
    points = aspect_points(angle, natal)
    other = next((p for p in people if p is not owner), None)
    detail = card["detail"]
    start_dt = sky.utc(int(start[:4]), int(start[5:7]), int(start[8:10]), "00:00")
    end_dt = sky.utc(int(end[:4]), int(end[5:7]), int(end[8:10]), "00:00") + timedelta(days=1)

    # какво казва картата: точни моменти или най-близко приближение
    stated, motions, closest = [], [], None
    if m.group(1):
        stated.append((int(m.group(1)), MONTHS[m.group(2)], m.group(3)))
    one = DETAIL_EXACT_ONE.match(detail)
    many = DETAIL_EXACT_MANY.match(detail)
    if one:
        motions = [one.group(1)]
    elif many:
        stated = []
        for mm, motion in re.findall(rf"(\d{{1,2}} (?:{MONTH_RX}), \d{{2}}:\d{{2}}) \((директно|ретроградно)\)", many.group(1)):
            mo = MOMENT_RE.match(mm)
            stated.append((int(mo.group(1)), MONTHS[mo.group(2)], mo.group(3)))
            motions.append(motion)
    anchor = None
    if stated:
        day, month, clock = stated[0]
        anchor = moment_of(sky, card, year_for(month, year_of, start, end), month, day, clock, problems)
    else:
        closest = DETAIL_CLOSEST.match(detail)
        if closest and closest.group(1):
            month = MONTHS[closest.group(2)]
            anchor = moment_of(sky, card, year_for(month, year_of, start, end), month, int(closest.group(1)), closest.group(3), problems)
        elif closest:
            anchor = start_dt if closest.group(4) == "началото" else end_dt - timedelta(minutes=1)
    if anchor is None:
        problems.append(f"подробностите не се разчитат: {detail[:70]}")
        return
    ap = nearest_point(points, sky.lon(tp, anchor))

    def f(t):
        return wrap180(sky.lon(tp, t) - ap)

    # прозорецът на действие около върха (1,5° при приближаване, 1° при отдалечаване): серията на картата
    run = interval_run(sky, f, anchor, start_dt - timedelta(days=1100), end_dt + timedelta(days=1100))
    # точните преминавания на ТОЗИ аспект в периода и в серията на картата (извадка през STEP_HOURS)
    passes = []
    lo = max(start_dt, run[0]) if run else start_dt
    hi = min(end_dt, run[1]) if run else end_dt
    t = lo
    prev = f(t)
    while t < hi:
        nxt = t + timedelta(hours=STEP_HOURS)
        cur = f(nxt)
        if prev * cur < 0 and abs(prev) < 5 and abs(cur) < 5:
            passes.append(t)
        t, prev = nxt, cur
    if stated:
        if len(stated) != len(passes):
            problems.append(f"брой точни моменти {card['title'][:60]}: написани {len(stated)}, изчислени {len(passes)}")
        for i, (day, month, clock) in enumerate(stated):
            moment = moment_of(sky, card, year_for(month, year_of, start, end), month, day, clock, problems)
            off = abs(f(moment))
            if off > TOLERANCE:
                problems.append(f"аспект {card['title'][:70]}: на {day} {month} {clock} разстоянието е {off:.3f}° от аспекта")
            else:
                real = root_near(f, moment)
                if real is None or minutes_apart(real, moment) > TIME_TOLERANCE_MINUTES:
                    problems.append(f"час {card['title'][:70]}: изчисленият точен момент е "
                                    f"{'няма' if real is None else real.astimezone(sky.tz).strftime('%d.%m %H:%M')}, написан {day}.{month} {clock}")
            if other and npl in other["chart"].points and sep(owner["chart"].points[npl], other["chart"].points[npl]) > 0.5:
                if min(abs(wrap180(sky.lon(tp, moment) - op)) for op in aspect_points(angle, other["chart"].points[npl])) < TOLERANCE:
                    problems.append(f"притежател {card['title'][:70]}: аспектът е и към {other['name']}")
            if i < len(motions) and sky.direction(tp, moment) != motions[i]:
                problems.append(f"движение {card['title'][:70]}: изчислено {sky.direction(tp, moment)}")
        counts["exact"] += len(stated)
    else:
        if passes:
            problems.append(f"има точен момент в периода, а картата казва, че няма: {card['title'][:70]}")
        if closest:
            off = abs(f(anchor))
            if abs(off - float(closest.group(5).replace(",", "."))) > 0.03:
                problems.append(f"отстояние {card['title'][:70]}: изчислено {off:.2f}°")
            if closest.group(1):
                neighbours = [abs(f(anchor + timedelta(hours=h))) for h in (-12, 12)]
                if off > min(neighbours) + 0.03:
                    problems.append(f"приближението не е най-близко: {card['title'][:70]}")
        elif not DETAIL_NONE.match(detail):
            problems.append(f"подробностите не се разчитат: {detail[:70]}")
        counts["nearest"] += 1

    # интервалът на действие: 1,5° при приближаване и 1° при отдалечаване около върха, по местни дати
    interval = DETAIL_INTERVAL.search(detail)
    if not interval:
        problems.append(f"няма интервал на действие: {card['title'][:70]}")
    else:
        d1 = (int(interval.group(1)), MONTHS[interval.group(2)])
        d2 = (int(interval.group(3)), MONTHS[interval.group(4)])
        if run is None:
            problems.append(f"няма изчислен интервал около картата: {card['title'][:70]}")
        elif not interval.group(5):
            lo, hi = run[0].astimezone(sky.tz), run[1].astimezone(sky.tz)
            if (lo.day, lo.month) != d1 or (hi.day, hi.month) != d2:
                problems.append(f"интервал {card['title'][:60]}: написан {d1[0]}.{d1[1]}–{d2[0]}.{d2[1]}, изчислен {lo.day}.{lo.month}–{hi.day}.{hi.month}")
        counts["interval"] += 1
    counts["transit"] += 1


def interval_run(sky, f, anchor, lo, hi):
    """Непрекъснатият прозорец около върха: напред докато отклонението надхвърли SEPARATING след върха, назад докато надхвърли APPLYING.
    Върхът е точният момент или най-близкото приближение. Ходът през ретрограден цикъл се обхожда със стъпка STEP_HOURS и се уточнява до минута."""
    def active(t):
        off = abs(f(t))
        applying = abs(f(t + timedelta(minutes=30))) < abs(f(t - timedelta(minutes=30)))
        return (applying and off <= APPLYING) or (not applying and off <= SEPARATING)

    def refine(inside, outside):
        for _ in range(14):
            mid = inside + (outside - inside) / 2
            if active(mid):
                inside = mid
            else:
                outside = mid
        return inside

    # върхът на близкото приближение може да не е „активен“ по посоката си; започваме от най-малкото отклонение около котвата
    best = min((anchor + timedelta(hours=h) for h in range(-36, 37)), key=lambda t: abs(f(t)))
    if abs(f(best)) > APPLYING:
        return None
    step = timedelta(hours=STEP_HOURS)
    t = best
    while t > lo and active(t - step):
        t -= step
    start = refine(t, t - step)
    t = best
    while t < hi and active(t + step):
        t += step
    end = refine(t, t + step)
    return start, end


def verify_period(text, people, start, end):
    cards, closing, zone, year_of = parse_report(text)
    sky = Sky(zone)
    problems = []
    counts = {"cards": len(cards), "transit": 0, "exact": 0, "nearest": 0, "interval": 0, "station": 0, "ingress": 0, "lunation": 0, "closing": len(closing)}
    # Текст, копиран от екрана, не е записаният: екранът слива редовете на абзаца и маха курсива, така че подробностите на картата
    # („Точен аспект, движение: …“) се сливат с тълкуването и сверката дава десетки лъжливи несъответствия (случаят от 18.10.2026).
    merged = [c for c in cards if TITLE_TRANSIT_RE.match(c["title"]) and not c["detail"] and any("Интервал на действие:" in line for line in c["ai"])]
    if merged:
        return [f"текстът не е записаният: подробностите на {len(merged)} карти са слети с тълкуването (така ги показва екранът: слива редовете и маха курсива). "
                f"Сверката не е валидна. Вземете записания текст (История → Преглед, или props.text на ReportText) и я пуснете пак."], counts
    allowed_dates = set()
    for d in (start, end):
        allowed_dates.add((int(d[5:7]), int(d[8:10])))
    for card in cards:
        title, detail = card["title"], card["detail"]
        card["smap"] = {(int(d), MONTHS[mo], clock): season for d, mo, clock, season in SEASON_FULL_RE.findall(title + " " + detail)}
        title, detail = SEASON_RE.sub(r"\1", title), SEASON_RE.sub(r"\1", detail)
        card["title"], card["detail"] = title, detail
        for day, month in re.findall(rf"(\d{{1,2}}) ({MONTH_RX})", title + " " + detail):
            allowed_dates.add((MONTHS[month], int(day)))
        m = TITLE_TRANSIT_RE.match(title)
        if m:
            verify_transit(card, m, sky, people, year_of, start, end, problems, counts)
        else:
            moment_m = None
            for rx, kind in ((TITLE_STATION_RE, "station"), (TITLE_INGRESS_RE, "ingress"), (TITLE_LUNATION_RE, "lunation")):
                moment_m = rx.match(title)
                if moment_m:
                    break
            if not moment_m:
                problems.append(f"заглавието не се разчита: {title[:80]}")
                continue
            day, month, clock = int(moment_m.group(1)), MONTHS[moment_m.group(2)], moment_m.group(3)
            moment = moment_of(sky, card, year_for(month, year_of, start, end), month, day, clock, problems)
            if kind == "station":
                planet = vc.find_planet(moment_m.group(4))[0]
                if planet not in PLANET_IDS:
                    problems.append(f"планета не се разчита: {title[:70]}")
                    continue
                direction = "директно" if moment_m.group(5).startswith("директ") else "ретроградно"
                if sky.direction(planet, moment + timedelta(hours=12), 12) != direction or sky.direction(planet, moment - timedelta(hours=36), 12) == direction:
                    problems.append(f"станция {title[:70]}: посоката след {sky.direction(planet, moment + timedelta(hours=12), 12)}")
                if moment_m.group(6) and SIGN_BY_NAME[moment_m.group(6)] != int(sky.lon(planet, moment) // 30):
                    problems.append(f"знак на станция {title[:70]}")
                check_houses(moment_m.group(7), sky.lon(planet, moment), people, title, problems)
            elif kind == "ingress":
                planet = vc.find_planet(moment_m.group(4))[0]
                if planet not in PLANET_IDS:
                    problems.append(f"планета не се разчита: {title[:70]}")
                    continue
                boundary = SIGN_BY_NAME[moment_m.group(5)] * 30.0
                if moment_m.group(6):
                    # влизане напред: границата е началото на новия знак; назад (ретроградно): краят му
                    forward = (SIGN_BY_NAME[moment_m.group(5)] - SIGN_BY_NAME[moment_m.group(6)]) % 12 == 1
                    boundary = SIGN_BY_NAME[moment_m.group(5)] * 30.0 + (0.0 if forward else 30.0)
                    real = root_near(lambda x: wrap180(sky.lon(planet, x) - boundary), moment, hours=12)
                    if real is None or minutes_apart(real, moment) > TIME_TOLERANCE_MINUTES:
                        problems.append(f"час на влизане {title[:70]}: "
                                        f"{'няма смяна на знака' if real is None else real.astimezone(sky.tz).strftime('%d.%m %H:%M')}")
                after = int(sky.lon(planet, moment + timedelta(minutes=30)) // 30)
                before = int(sky.lon(planet, moment - timedelta(minutes=30)) // 30)
                if SIGN_BY_NAME[moment_m.group(5)] != after:
                    problems.append(f"знак след влизането {title[:70]}: изчислен {SIGNS[after]}")
                if moment_m.group(6) and SIGN_BY_NAME[moment_m.group(6)] != before:
                    problems.append(f"знак преди влизането {title[:70]}: изчислен {SIGNS[before]}")
                check_houses(moment_m.group(7), sky.lon(planet, moment), people, title, problems)
            else:
                elong = wrap180(sky.lon("Moon", moment) - sky.lon("Sun", moment))
                new = moment_m.group(4) in ("Новолуние", "Слънчево затъмнение")
                if abs(elong if new else wrap180(elong - 180)) > 0.2:
                    problems.append(f"фаза {title[:70]}: елонгация {elong:.2f}°")
                else:
                    target = 0.0 if new else 180.0
                    real = root_near(lambda x: wrap180(wrap180(sky.lon("Moon", x) - sky.lon("Sun", x)) - target), moment, hours=3)
                    if real is None or minutes_apart(real, moment) > TIME_TOLERANCE_MINUTES + 1:
                        problems.append(f"час на лунация {title[:70]}: "
                                        f"{'няма' if real is None else real.astimezone(sky.tz).strftime('%d.%m %H:%M')}")
                point = sky.lon("Sun", moment) if new else sky.lon("Moon", moment)
                if moment_m.group(5) and SIGN_BY_NAME[moment_m.group(5)] != int(point // 30):
                    problems.append(f"знак на лунация {title[:70]}")
                check_houses(moment_m.group(6), point, people, title, problems)
            counts[kind] += 1
        # тълкуването на картата: без планети, аспекти, знаци, домове, часове, дати и посока на движение
        ai = " ".join(card["ai"])
        station = TITLE_STATION_RE.match(title)
        own = None
        if station:      # станция: думата за собствената ѝ посока е смисълът на картата
            own = re.compile(r"ретроград\w*" if station.group(5).startswith("ретроград") else r"директ\w*|директн\w*", re.IGNORECASE)
        for rx, what in ((ASPECT_WORDS, "аспект"), (SIGN_WORDS, "знак"), (HOUSE_WORDS, "дом"), (CLOCK_RE, "час"), (MOTION_WORDS, "посока")):
            for found in rx.finditer(ai):
                if rx is MOTION_WORDS and own is not None and own.fullmatch(found.group(0)):
                    continue
                problems.append(f"в тълкуването на картата има {what}: „{found.group(0)}“ ({title[:50]})")
                break
        own_dates = {(MONTHS[mo], int(d)) for d, mo in re.findall(rf"(\d{{1,2}}) ({MONTH_RX})", title + " " + detail)}
        for dm in DATE_RX.finditer(ai):
            month = MONTHS[dm.group(3).lower()]
            for g in (dm.group(1), dm.group(2)):
                if g and (month, int(g)) not in own_dates:
                    problems.append(f"в тълкуването на картата има дата, която не е от заглавието ѝ: {dm.group(0)} ({title[:50]})")
        planets_in = [name for rx, name in FORBIDDEN_NAMES if rx.search(ai)]
        if planets_in:
            problems.append(f"в тълкуването на картата има планета: {planets_in[0]} ({title[:50]})")
        for p in people:
            if len(people) > 1 and TITLE_TRANSIT_RE.match(title):
                owner = TITLE_TRANSIT_RE.match(title).group(9)
                if p["name"] != owner and re.search(rf"(?<![\w]){re.escape(p['name'])}(?![\w])", ai):
                    problems.append(f"в тълкуването е назован друг човек ({p['name']}): {title[:50]}")
        if not card["ai"]:
            problems.append(f"картата няма тълкуване: {title[:70]}")
    # затварящите блокове
    for block in closing:
        body = " ".join(block["text"])
        if block["name"] == "Най-силни моменти":
            for line in block["text"]:
                if line.lstrip("- ").strip() not in {c["title"] for c in cards}:
                    problems.append(f"в най-силните моменти има заглавие, което не е на карта: {line[:70]}")
            continue
        for rx, what in ((ASPECT_WORDS, "аспект"), (SIGN_WORDS, "знак"), (HOUSE_WORDS, "дом"), (CLOCK_RE, "час")):
            found = rx.search(body)
            if found:
                problems.append(f"в „{block['name']}“ има {what}: „{found.group(0)}“")
        planets_in = [name for rx, name in FORBIDDEN_NAMES if rx.search(body)]
        if planets_in:
            problems.append(f"в „{block['name']}“ има планета: {planets_in[0]}")
        for dm in DATE_RX.finditer(body):
            month = MONTHS[dm.group(3).lower()]
            for g in (dm.group(1), dm.group(2)):
                if g and (month, int(g)) not in allowed_dates:
                    problems.append(f"в „{block['name']}“ има дата, която не е от заглавие на карта: {dm.group(0)}")
    return problems, counts


# --------------------------------------------------------------------------------------------------------------
# Offline: картите, които строи кодът, със неутрални тълкувания
# --------------------------------------------------------------------------------------------------------------
CASES = [(("A", "IVAN"), "2026-10-01", "2026-11-30"), (("IVAN", "A"), "2026-10-20", "2026-10-31"), (("A", "IVAN"), "2026-12-01", "2027-01-31"),
         (("IVAN",), "2026-10-01", "2026-12-31"), (("A", "IVAN_NO_TIME"), "2026-10-01", "2026-10-31"), (("A", "IVAN"), "2026-08-01", "2026-08-31")]
FILLER = "Това изисква търпение и ясни граници в разговорите."


def offline_text(keys, start, end):
    import engine
    import factpack
    import natal_pair
    import period_cards
    from scanner import TransitScanner
    eng = engine.get_engine()
    charts = [eng.calculate_chart(PEOPLE[k]["date"], PEOPLE[k]["time"], PEOPLE[k]["lat"], PEOPLE[k]["lon"], time_known=PEOPLE[k]["known"]) for k in keys]
    names = [factpack.display_name(PEOPLE[k]["name"], "A" if i == 0 else "B") for i, k in enumerate(keys)]
    first = PEOPLE[keys[0]]
    cal = TransitScanner(engine_instance=eng).build_calendar(charts[0], start, end, first["lat"], first["lon"],
                                                              partner_chart=charts[1] if len(charts) > 1 else None, tz_name="Europe/Sofia")
    rel = natal_pair.Relevance("general", "", "")
    cards_all, parts = [], []
    for i, month in enumerate(cal.months_with_events(), 1):
        cards = period_cards.build_month_cards(cal, month, i, names[0], names[1] if len(names) > 1 else "", rel)
        for c in cards:
            c.text = FILLER
        cards_all += cards
        parts.append(f"<h2>{period_cards.period_report.month_title(month)}</h2>\n" + period_cards.month_text(cards, "Този месец е добре да подбирате внимателно думите."))
    overview = period_cards.assemble_overview(cards_all, {"SUMMARY": "Периодът е променлив.", "ADVICE": "- Говорете спокойно.", "CONCLUSION": "Изчакайте."}, cal, "", [])
    text = f"<h2>Общ преглед на периода</h2>\n{overview}\n\n" + "\n\n".join(parts)
    people = []
    for k, name, chart in zip(keys, names, charts):
        people.append({"name": name, "chart": vc.Chart(PEOPLE[k]), "key": k})
    return text, people


def run_offline():
    failures = 0
    for keys, start, end in CASES:
        text, people = offline_text(keys, start, end)
        problems, counts = verify_period(text, people, start, end)
        status = "OK" if not problems else f"НЕСЪОТВЕТСТВИЯ {len(problems)}"
        print(f"{keys} {start}..{end}: {status} {counts}")
        for line in problems[:8]:
            print("  НЕСЪОТВЕТСТВИЕ:", line)
        failures += bool(problems)
    return failures


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--report-file")
    parser.add_argument("--people", default="A,IVAN")
    parser.add_argument("--start")
    parser.add_argument("--end")
    args = parser.parse_args(argv)
    if not args.report_file:
        return 1 if run_offline() else 0
    keys = tuple(args.people.split(","))
    people = vc.build_people(keys)
    for person, key in zip(people, keys):
        person["name"] = re.sub(r"\s*[—-]\s*", " ", PEOPLE[key]["name"]).strip()
    text = open(args.report_file, encoding="utf-8").read()
    problems, counts = verify_period(text, people, args.start, args.end)
    print("Проверени:", counts)
    for line in problems:
        print("НЕСЪОТВЕТСТВИЕ:", line)
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
