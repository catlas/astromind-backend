#!/usr/bin/env python3
"""
Възпроизводима сверка на картите с факти (pair_snapshot, single_snapshot, natal_pair) с фиксирани тестови данни.

Какво проверява: заглавията на картите в готов анализ (или построени от кода при --offline) срещу ИЗЧИСЛЕНИЕ, направено наново и
по друг път: позициите идват от Swiss Ephemeris през engine.calculate_chart, но аспектите, орбисите, знаците, градусите, домовете
(по куспидите) и притежателите се смятат тук с проста геометрия, без aspects_engine, factpack и text_check. Транзитните карти се
сравняват и по пълнота: наборът е точно най-тесните аспекти (орбис до 3°) на всеки човек.

Какво НЕ проверява: самите астрономически позиции (ефемеридите), координатите на градовете и преобразуването на местния час в UTC.
Тези неща се вземат от engine като дадени; сверката доказва съгласуваност на фактите с тях, не независимост от тях. Смисълът на
тълкуванията и качеството на стила не се проверяват автоматично.

Употреба (от папка backend):
  python3 scripts/verify_cards.py                      # всички фиксирани случаи, карти от кода (offline)
  python3 scripts/verify_cards.py --case natal_pair --report-file report.md [--theme love --relation romantic]
Код за изход 1, ако има несъответствие.
"""
import argparse
import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.environ.setdefault("SECRET_KEY", "verify-cards-script")

ASPECTS = {"съвпад": 0, "секстил": 60, "квадратура": 90, "тригон": 120, "опозиция": 180}
MAX_TRANSIT_ORB = 3.0
PLANETS = {"Слънце": "Sun", "Слънцето": "Sun", "Луна": "Moon", "Луната": "Moon", "Меркурий": "Mercury", "Венера": "Venus", "Марс": "Mars",
           "Юпитер": "Jupiter", "Сатурн": "Saturn", "Уран": "Uranus", "Нептун": "Neptune", "Плутон": "Pluto", "Хирон": "Chiron",
           "Възходящ възел": "Node", "Възходящия възел": "Node", "Възходящият възел": "Node", "Асцендент": "ASC", "Асцендента": "ASC",
           "Асцендентът": "ASC", "MC": "MC"}
SIGNS = ["Овен", "Телец", "Близнаци", "Рак", "Лъв", "Дева", "Везни", "Скорпион", "Стрелец", "Козирог", "Водолей", "Риби"]
ORDINALS = {"1-ви": 1, "2-ри": 2, "3-ти": 3, "4-ти": 4, "5-ти": 5, "6-ти": 6, "7-ми": 7, "8-ми": 8, "9-ти": 9, "10-и": 10, "11-ти": 11, "12-ти": 12}

# Фиксирани синтетични данни (както в одита): име, дата, час, ширина, дължина, известен ли е часът
PEOPLE = {
    "A": dict(name="Тест А одит", date="1990-02-15", time="13:00", lat=42.6977, lon=23.3219, known=True),
    "IVAN": dict(name="Иван", date="1985-07-12", time="18:45", lat=48.2082, lon=16.3738, known=True),
    "IVAN_NO_TIME": dict(name="Иван", date="1985-07-12", time="12:00", lat=48.2082, lon=16.3738, known=False),
    "CHILD": dict(name="Мария", date="2014-05-20", time="09:15", lat=42.1354, lon=24.7453, known=True),
}
MOMENTS = [("2026-10-14", "18:00", "Europe/Sofia"), ("2026-11-03", "09:30", "Europe/Sofia"), ("2026-01-03", "03:10", "Europe/Sofia"),
           ("2026-07-03", "15:45", "Europe/Sofia")]
CASES = {
    "pair_snapshot": [("A", "IVAN"), ("IVAN", "A"), ("A", "IVAN_NO_TIME"), ("A", "CHILD")],
    "single_snapshot": [("IVAN",), ("A",), ("IVAN_NO_TIME",)],
    "natal_pair": [("A", "IVAN"), ("IVAN", "A"), ("A", "IVAN_NO_TIME"), ("A", "CHILD")],
}
VARIANTS = [("general", "", ""), ("love", "romantic", "Как да говорим за очакванията си?"), ("career", "work", "Как да си разпределим задачите?"),
            ("money", "family", "Как да планираме общ разход?"), ("health", "friend", ""), ("karmic", "parent_child", "Как да поставям граници?")]


def sep(a, b):
    d = abs(a - b) % 360
    return min(d, 360 - d)


def nearest(distance):
    best = min(ASPECTS.items(), key=lambda kv: abs(distance - kv[1]))
    return best[0], abs(distance - best[1])


def house_of(longitude, cusps):
    for i in range(12):
        a, b = cusps[i], cusps[(i + 1) % 12]
        if (longitude - a) % 360 < (b - a) % 360:
            return i + 1
    return None


class Chart:
    """Позиции и куспиди на човек или на небето: само числата от engine; всичко останало се смята отново."""

    def __init__(self, person=None, date=None, time=None, lat=None, lon=None, zone=None, known=True):
        import engine
        p = person or {}
        raw = engine.get_engine().calculate_chart(date or p["date"], time or p["time"], lat if lat is not None else p["lat"],
                                                  lon if lon is not None else p["lon"], zone=zone, time_known=known if person is None else p["known"])
        self.known = raw.get("time_known") is not False
        self.planets = {k: v["longitude"] for k, v in raw["planets"].items() if v.get("longitude") is not None}
        self.points = dict(self.planets)
        if self.known and raw.get("angles", {}).get("Ascendant") is not None:
            self.points["ASC"], self.points["MC"] = raw["angles"]["Ascendant"], raw["angles"]["MC"]
        self.cusps = [raw["houses"][f"House{i}"] for i in range(1, 13)] if self.known and raw.get("houses") else None
        self.sign_ranges = set((raw.get("sign_ranges") or {}).keys())


def find_planet(text):
    for name in sorted(PLANETS, key=len, reverse=True):
        if text.startswith(name):
            return PLANETS[name], name
    return None, None


TRANSIT_RE = re.compile(r"^(?:Транзитн\S+) (.+?) е в (съвпад|секстил|квадратура|тригон|опозиция) с (?:натал\S+) (.+?) на (.+?) \(орб ([\d,]+)°")
ASPECT_RE = re.compile(r"^(.+?) на (.+?) е в (съвпад|секстил|квадратура|тригон|опозиция) (?:със|с) (.+?) на (.+?) \(орб ([\d,]+)°")
PLACEMENT_RE = re.compile(r"^(.+?) на (.+?) е (?:в|във) (\S+) \((\d+)°(\d+)'\)(?:, в (\S+) дом)?\.$")      # „във“ пред Водолей, Връзки: поправка на езика
OVERLAY_RE = re.compile(r"^(.+?) на (.+?) попада в (\S+) дом на (.+?)\.$")
HOUSE_LINE_RE = re.compile(r"^Транзитните планети в домовете на (.+?): (.+)\.$")


def verify(text, people, sky=None, mode="natal_pair", limit=None, require_complete=True):
    """people: [{name, chart: Chart, known}] в реда на хората; sky: Chart на небето за режимите „за дата“. Връща списък от несъответствия."""
    problems = []
    by_name = {p["name"]: p for p in people}
    transit_sets = {p["name"]: set() for p in people}
    counts = {"transit": 0, "aspect": 0, "placement": 0, "overlay": 0, "house_line": 0}
    for line in (l.strip() for l in text.splitlines()):
        heading = line[4:] if line.startswith("### ") else line
        m = TRANSIT_RE.match(heading)
        if m and sky:
            tp, _ = find_planet(m.group(1))
            np_, _ = find_planet(m.group(3))
            owner = by_name.get(m.group(4))
            if not (tp and np_ and owner) or np_ not in owner["chart"].points:
                problems.append(f"не се разчита: {heading[:80]}")
                continue
            calc = abs(sep(sky.planets[tp], owner["chart"].points[np_]) - ASPECTS[m.group(2)])
            if abs(calc - float(m.group(5).replace(",", "."))) > 0.02:
                problems.append(f"орбис {heading[:70]}: изчислен {calc:.2f}")
            transit_sets[m.group(4)].add((tp, np_, m.group(2)))
            counts["transit"] += 1
            continue
        m = ASPECT_RE.match(heading)
        if m:
            p1, _ = find_planet(m.group(1))
            p2, _ = find_planet(m.group(4))
            a, b = by_name.get(m.group(2)), by_name.get(m.group(5))
            if not (p1 and p2 and a and b and p1 in a["chart"].points and p2 in b["chart"].points):
                problems.append(f"не се разчита: {heading[:80]}")
                continue
            kind, orb = nearest(sep(a["chart"].points[p1], b["chart"].points[p2]))
            if kind != m.group(3) or abs(orb - float(m.group(6).replace(",", "."))) > 0.02:
                problems.append(f"аспект {heading[:70]}: изчислен {kind} {orb:.2f}")
            counts["aspect"] += 1
            continue
        m = PLACEMENT_RE.match(heading)
        if m:
            planet, _ = find_planet(m.group(1))
            owner = by_name.get(m.group(2))
            if not (planet and owner and planet in owner["chart"].planets):
                problems.append(f"не се разчита: {heading[:80]}")
                continue
            lon = owner["chart"].planets[planet]
            total = round(lon * 60)                                         # както в заглавията: минутите са закръглени
            sign, deg, minute = SIGNS[(total // 1800) % 12], (total % 1800) // 60, total % 60
            if m.group(3) != sign or m.group(4) != str(deg) or int(m.group(5)) != minute:
                problems.append(f"позиция {heading[:70]}: изчислена {sign} {deg}°{minute}'")
            if m.group(6):
                if not owner["chart"].known or house_of(lon, owner["chart"].cusps) != ORDINALS[m.group(6)]:
                    problems.append(f"дом {heading[:70]}: изчислен {house_of(lon, owner['chart'].cusps) if owner['chart'].known else 'няма час'}")
            elif owner["chart"].known:
                problems.append(f"липсва домът: {heading[:70]}")
            if not owner["chart"].known and planet in owner["chart"].sign_ranges:
                problems.append(f"неуверен знак без час: {heading[:70]}")
            counts["placement"] += 1
            continue
        m = OVERLAY_RE.match(heading)
        if m:
            planet, _ = find_planet(m.group(1))
            giver, owner = by_name.get(m.group(2)), by_name.get(m.group(4))
            if not (planet and giver and owner and planet in giver["chart"].planets) or not owner["chart"].known:
                problems.append(f"не се разчита или няма домове: {heading[:80]}")
                continue
            if house_of(giver["chart"].planets[planet], owner["chart"].cusps) != ORDINALS[m.group(3)]:
                problems.append(f"посока/дом {heading[:70]}: изчислен {house_of(giver['chart'].planets[planet], owner['chart'].cusps)}")
            counts["overlay"] += 1
            continue
        m = HOUSE_LINE_RE.match(line)
        if m and sky:
            owner = by_name.get(m.group(1))
            if not owner or not owner["chart"].known:
                problems.append(f"ред за домове без час: {line[:60]}")
                continue
            for part in m.group(2).split("; "):
                mm = re.match(r"^(.+?) — (\S+) дом$", part)
                planet, _ = find_planet(mm.group(1)) if mm else (None, None)
                if planet and house_of(sky.planets[planet], owner["chart"].cusps) != ORDINALS[mm.group(2)]:
                    problems.append(f"{m.group(1)}: {part} (изчислен {house_of(sky.planets[planet], owner['chart'].cusps)})")
            counts["house_line"] += 1
    if sky and limit:
        for person in people:
            chart = person["chart"]
            found = []
            for tp, tl in sky.planets.items():
                for npoint, nl in chart.points.items():
                    kind, orb = nearest(sep(tl, nl))
                    if orb <= MAX_TRANSIT_ORB:
                        found.append((orb, (tp, npoint, kind)))
            expect = {k for _, k in sorted(found)[:limit]}
            got = transit_sets[person["name"]]
            if require_complete and (expect - got or got - expect):
                problems.append(f"{person['name']}: липсват {sorted(expect - got)} излишни {sorted(got - expect)}")
    return problems, counts


def build_people(keys):
    return [{"name": PEOPLE[k]["name"], "chart": Chart(PEOPLE[k]), "key": k} for k in keys]


def offline_text(case, keys, moment, variant):
    """Заглавията, които кодът строи за случая (без AI): за offline сверката."""
    import card_engine
    import factpack
    theme, relation, question = variant
    people = build_people(keys)
    names = [factpack.display_name(PEOPLE[k]["name"], "A") for k in keys]
    import engine
    eng = engine.get_engine()
    charts = [eng.calculate_chart(PEOPLE[k]["date"], PEOPLE[k]["time"], PEOPLE[k]["lat"], PEOPLE[k]["lon"], time_known=PEOPLE[k]["known"]) for k in keys]
    sky_raw = None
    if moment:
        date, time, zone = moment
        place = PEOPLE[keys[0]]
        sky_raw = eng.calculate_chart(date, time, place["lat"], place["lon"], zone=zone)
    ctx = card_engine.Context(natal_chart=charts[0], partner_chart=charts[1] if len(charts) > 1 else None, transit_chart=sky_raw or {},
                              user_name=names[0], partner_name=names[1] if len(names) > 1 else None, first=names[0],
                              second=names[1] if len(names) > 1 else "", question=question, target_date="", theme="x", utc_today="2026-10-12",
                              report_type=theme, relation=relation)
    lines = []
    if case == "pair_snapshot":
        import pair_snapshot as ps
        cards = ps.build_cards(charts[0], charts[1], sky_raw, names[0], names[1])
        lines = [ps.houses_line(charts[0], sky_raw, names[0]), ps.houses_line(charts[1], sky_raw, names[1])]
    elif case == "single_snapshot":
        import single_snapshot as ss
        cards, extra = ss.MODE.build(ctx)
        lines = [extra.get("A", "")]
    else:
        import natal_pair
        cards = natal_pair.build_cards(ctx)
    return "\n".join([l for l in lines if l] + [f"### {c.heading}" for c in cards]), people, names


def run_offline():
    failures = 0
    total = 0
    for case, groups in CASES.items():
        for keys in groups:
            for variant in (VARIANTS if case == "natal_pair" else VARIANTS[:1]):
                for moment in (MOMENTS if case != "natal_pair" else [None]):
                    text, people, names = offline_text(case, keys, moment, variant)
                    for person, name in zip(people, names):
                        person["name"] = name
                    sky = Chart(date=moment[0], time=moment[1], lat=PEOPLE[keys[0]]["lat"], lon=PEOPLE[keys[0]]["lon"], zone=moment[2]) if moment else None
                    limit = None if case == "natal_pair" else (8 if case == "single_snapshot" else 6)
                    problems, counts = verify(text, people, sky, case, limit)
                    total += 1
                    if problems:
                        failures += 1
                        print(f"НЕСЪОТВЕТСТВИЕ {case} {keys} {variant[:2]} {moment}: {problems[:4]}")
    print(f"Сверени случаи: {total}, с несъответствия: {failures}")
    return failures


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--case", choices=sorted(CASES))
    parser.add_argument("--report-file")
    parser.add_argument("--people", help="ключове от PEOPLE, например A,IVAN (по подразбиране първата двойка на случая)")
    parser.add_argument("--moment", help="дата,час,зона за режимите „за дата“, например 2026-10-14,18:00,Europe/Sofia")
    args = parser.parse_args(argv)
    if not args.report_file:
        return 1 if run_offline() else 0
    keys = tuple((args.people or ",".join(CASES[args.case][0])).split(","))
    text = open(args.report_file, encoding="utf-8").read()
    people = build_people(keys)
    for person, key in zip(people, keys):
        person["name"] = re.sub(r"\s*[—-]\s*", " ", PEOPLE[key]["name"]).strip()
    sky = None
    limit = None
    if args.case != "natal_pair":
        date, time, zone = args.moment.split(",")
        sky = Chart(date=date, time=time, lat=PEOPLE[keys[0]]["lat"], lon=PEOPLE[keys[0]]["lon"], zone=zone)
        limit = 8 if args.case == "single_snapshot" else 6
    problems, counts = verify(text, people, sky, args.case, limit)
    print("Проверени:", counts)
    for line in problems:
        print("НЕСЪОТВЕТСТВИЕ:", line)
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
