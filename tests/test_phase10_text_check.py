"""
Фаза 10: проверка на готовия текст преди показване. Всичко е офлайн: AI се подменя.

Какво се доказва:
1. Корпусът от одита (36 запазени реални AI анализа; tests/fixtures/phase10_corpus.json съдържа само изреченията с твърдения):
   известните грешки LIVE-01…32 се хващат, а текстовете без известна грешка не получават тежки нарушения.
2. Верните твърдения се приемат, грешните се хващат: за няколко произволни карти се генерират изречения от изчислените факти
   (домове, знаци, градуси, аспекти, куспиди, управители, наслагвания, транзити, дати на събития). Верните версии нямат
   нито едно тежко нарушение, а всяка поквара (друг дом, друг знак, друг аспект, друга дата) се хваща.
3. Поправката и отхвърлянето: най-много един опит за поправка, провал = без запис и без такса, режимите на TEXT_CHECK_MODE,
   служебните етикети, повреда в самия проверител, крайни точки.
4. Скорост: обичайните текстове и патологичните входове не блокират сървъра.

Пускане: python -m unittest discover -s tests -p "test_phase10_text_check.py"
"""
import asyncio
import json
import os
import time
import unittest
from datetime import date, timedelta
from unittest import mock

import testenv  # noqa: F401  (трябва да е преди main)
from fastapi.testclient import TestClient  # noqa: E402

import ai_interpreter  # noqa: E402
import engine  # noqa: E402
import main  # noqa: E402
import period_report  # noqa: E402
import text_check as tc  # noqa: E402
import text_guard  # noqa: E402
from database import Event, Report, SessionLocal  # noqa: E402
from rate_limit import limiter  # noqa: E402
from scanner import TransitScanner  # noqa: E402
from testenv import CHART, register_and_login  # noqa: E402

FIXTURES = os.path.join(os.path.dirname(os.path.abspath(__file__)), "fixtures")
A_BIRTH = dict(date="1990-02-15", time="13:00", lat=42.6977, lon=23.3219)          # София
IVAN_BIRTH = dict(date="1985-07-12", time="18:45", lat=48.2082, lon=16.3738)       # Виена
PEOPLE = [
    A_BIRTH, IVAN_BIRTH,
    dict(date="1978-11-03", time="06:20", lat=40.7128, lon=-74.0060),               # Ню Йорк
    dict(date="2001-03-27", time="23:50", lat=-33.8688, lon=151.2093),              # Сидни: южно полукълбо
    dict(date="1969-12-21", time="03:05", lat=64.1466, lon=-21.9426),               # Рейкявик: голяма ширина
    dict(date="1995-08-09", time="12:00", lat=35.6762, lon=139.6503),               # Токио
]
HOUSE_SUFFIX = {1: "ви", 2: "ри", 3: "ти", 4: "ти", 5: "ти", 6: "ти", 7: "ми", 8: "ми", 9: "ти", 10: "ти", 11: "ти", 12: "ти"}
ASPECT_PHRASE = {"conjunction": "съвпад", "sextile": "секстил", "square": "квадратура", "trine": "тригон", "opposition": "опозиция"}
BODIES = ["Sun", "Moon", "Mercury", "Venus", "Mars", "Jupiter", "Saturn", "Uranus", "Neptune", "Pluto"]
MONTHS_BG = ["", "януари", "февруари", "март", "април", "май", "юни", "юли", "август", "септември", "октомври", "ноември", "декември"]
SNAPSHOT = "2026-10-08 12:00"


def hb(n):                       # 7 -> "7-ми"
    return f"{n}-{HOUSE_SUFFIX[n]}"


def pl(key):
    return tc.PLANET_BG[key]


def sg(key):
    return tc.SIGN_BG[key]


def dm(iso):                     # "2026-10-03" -> "3 октомври"
    return f"{int(iso[8:10])} {MONTHS_BG[int(iso[5:7])]}"


def hard_codes(violations):
    return sorted({v.code for v in tc.hard(violations)})


_ENGINE = engine.AstrologyEngine()
_CHARTS = {}


def chart(birth):
    key = json.dumps(birth, sort_keys=True)
    if key not in _CHARTS:
        _CHARTS[key] = _ENGINE.calculate_chart(**birth)
    return _CHARTS[key]


def sky(place, when=SNAPSHOT):
    d, t = when.split(" ")
    return _ENGINE.calculate_chart(date=d, time=t, lat=place["lat"], lon=place["lon"])


# --------------------------------------------------------------------------------------------------------------
# 1. Корпусът от одита
# --------------------------------------------------------------------------------------------------------------
_CAL_CACHE = {}


def calendar_for(partner: bool):
    if partner not in _CAL_CACHE:
        _CAL_CACHE[partner] = TransitScanner(engine_instance=_ENGINE).build_calendar(
            chart(A_BIRTH) if partner else chart(IVAN_BIRTH), "2026-10-01", "2026-11-30",
            (A_BIRTH if partner else IVAN_BIRTH)["lat"], (A_BIRTH if partner else IVAN_BIRTH)["lon"],
            partner_chart=chart(IVAN_BIRTH) if partner else None)
    return _CAL_CACHE[partner]


def corpus_facts(target: str, mode: str) -> tc.Facts:
    if target == "single":
        kw = dict(user_name="Иван", natal_chart=chart(IVAN_BIRTH))
        place = IVAN_BIRTH
    else:
        kw = dict(user_name="Потребител", natal_chart=chart(A_BIRTH), partner_name="Иван", partner_chart=chart(IVAN_BIRTH))
        place = A_BIRTH
    if mode == "snapshot":
        kw.update(transit_chart=sky(place), target_date=SNAPSHOT)
    if mode == "period":
        kw.update(calendar=calendar_for(target == "pair"), report_date="2026-10-08")
    return tc.build_facts(mode=mode, **kw)


class CorpusTest(unittest.TestCase):
    """36-те реални AI анализа от одита (само изреченията с твърдения)."""

    @classmethod
    def setUpClass(cls):
        with open(os.path.join(FIXTURES, "phase10_corpus.json"), encoding="utf-8") as f:
            cls.corpus = json.load(f)
        cls.facts = {}
        cls.results = {}
        for key, segments in cls.corpus.items():
            target, mode, _ = key.split("-")
            if (target, mode) not in cls.facts:
                cls.facts[(target, mode)] = corpus_facts(target, mode)
            cls.results[key] = tc.check_text("\n".join(segments), cls.facts[(target, mode)])

    def violations(self, key, code=None):
        return [v for v in self.results[key] if code is None or v.code == code]

    def assertFlagged(self, key, code, *needles):
        hits = [v for v in self.violations(key, code) if v.severity == "hard"
                and all(n in v.excerpt + " " + v.detail for n in needles)]
        self.assertTrue(hits, f"{key}: няма тежко нарушение {code} с {needles}; има: "
                              f"{[(v.code, v.detail[:90]) for v in self.violations(key)]}")

    def test_the_corpus_is_complete(self):
        self.assertEqual(len(self.corpus), 36)
        self.assertGreater(sum(len(v) for v in self.corpus.values()), 1000)

    # --- известните грешки от одита ---------------------------------------------------------------------------
    def test_live_01_02_03_cusps_and_rulers(self):
        self.assertFlagged("single-natal-health", "cusp", "6-ти дом", "Близнаци 3°22'")                  # LIVE-01
        self.assertFlagged("single-natal-money", "cusp", "8-ти дом", "Рак 29°46'")                        # LIVE-02
        self.assertFlagged("single-natal-money", "ruler", "8-ти дом", "Луна")
        self.assertFlagged("single-natal-karma", "ruler", "4-ти дом", "Марс")                             # LIVE-03

    def test_live_04_05_21_aspects_that_are_wrong(self):
        self.assertFlagged("single-snapshot-love", "aspect", "Нептун и Нептун", "квадратура, не съвпад")  # LIVE-04
        self.assertFlagged("single-snapshot-love", "aspect", "Сатурн и Меркурий")                         # LIVE-05
        self.assertFlagged("pair-snapshot-love", "aspect", "Сатурн", "Слънце")                            # LIVE-21

    def test_live_07_transit_houses_are_not_mixed_with_natal(self):
        for theme in ("career", "general", "health", "karma", "love", "money"):
            self.assertFlagged(f"single-snapshot-{theme}", "house", "транзитният Сатурн е в 3-ти дом")

    def test_live_08_27_station_ingress_and_lunation_dates(self):
        for theme in ("career", "general", "health", "karma", "love", "money"):
            hits = " ".join(v.detail for v in self.violations(f"single-period-{theme}", "event"))
            self.assertIn("3 октомври", hits, theme)                                                        # Венера: 3, не 4 октомври
            self.assertIn("24 октомври", hits, theme)                                                       # Меркурий: 24, не 25
        self.assertFlagged("single-period-general", "event", "Плутон", "16 октомври")
        self.assertFlagged("single-period-general", "event", "Слънце", "23 октомври")
        self.assertFlagged("pair-period-general", "event", "Знакът на пълнолунието", "Близнаци")           # LIVE-27

    def test_live_09_10_15_17_overlays(self):
        for planet in ("Слънце", "Луна", "Меркурий", "Венера", "Марс"):                                    # LIVE-09: пет грешни дома
            self.assertFlagged("pair-natal-general", "house", f"{planet} не е в")
        for planet in ("Слънце", "Меркурий"):                                                               # LIVE-10
            self.assertFlagged("pair-natal-health", "house", f"{planet} не е в")
        for planet in ("Слънце", "Марс", "Сатурн", "Венера"):                                               # LIVE-15
            self.assertFlagged("pair-natal-career", "house", f"{planet} не е в")
        for planet in ("Луна", "Венера", "Марс"):                                                           # LIVE-17
            self.assertFlagged("pair-natal-love", "house", f"{planet} не е в")

    def test_live_16_conjunction_word_for_a_trine(self):
        self.assertFlagged("pair-natal-money", "aspect", "Луна и Юпитер", "не съвпад")

    def test_live_24_25_26_28_period_aspects(self):
        self.assertFlagged("pair-period-health", "aspect", "Марс и Марс")                                   # LIVE-24
        self.assertFlagged("pair-period-health", "exact", "12 януари")                                       # LIVE-25: точният е през януари, не 10–11 ноември
        self.assertFlagged("pair-period-money", "exact", "Точен квадрат Нептун", "няма точен момент")        # LIVE-26: Нептун–Юпитер няма точност
        self.assertFlagged("single-period-general", "date", "22 ноември", "точни 21 ноември")               # LIVE-27: пикът е 21, не 22 ноември
        self.assertFlagged("single-period-general", "house", "Плутон не е в 2-ти дом", "10-ти дом")        # LIVE-28
        self.assertFlagged("single-period-general", "house", "Сатурн не е в 8-ти дом", "11-ти дом")

    def test_live_32_the_retrograde_count_matches_the_list(self):
        self.assertFlagged("pair-snapshot-karma", "retro_count", "четири", "6")

    def test_internal_label_in_a_text_is_found_and_removed(self):
        leaks = self.violations("pair-natal-karma", "english")
        self.assertTrue(leaks)
        self.assertIn("PARTNER PLANETS IN USER'S NATAL HOUSES", leaks[0].excerpt)
        cleaned, removed = tc.clean_labels("\n".join(self.corpus["pair-natal-karma"]))
        self.assertGreaterEqual(removed, 1)
        self.assertNotIn("PARTNER PLANETS", cleaned)
        self.assertFalse(tc.check_leaks(cleaned))

    # --- текстове без известна грешка ---------------------------------------------------------------------------
    def test_texts_without_a_known_error_have_no_hard_violation(self):
        for key in ("single-natal-career", "single-natal-general", "single-natal-love",
                    "pair-natal-karma", "pair-snapshot-career", "pair-snapshot-general", "pair-snapshot-money"):
            self.assertEqual(tc.hard(self.results[key]), [], f"{key}: {[(v.code, v.excerpt) for v in tc.hard(self.results[key])]}")

    def test_hard_counts_are_stable(self):
        """Замразява сегашното поведение: промяна на проверителя, която добавя или маха тежки нарушения, се вижда веднага."""
        counts = {key: len(tc.hard(v)) for key, v in self.results.items()}
        total = sum(counts.values())
        self.assertGreater(total, 150)
        self.assertEqual(counts["single-natal-career"] + counts["single-natal-general"] + counts["single-natal-love"], 0)
        self.assertEqual(counts["pair-natal-general"], 5)
        self.assertEqual(counts["pair-natal-health"], 2)
        self.assertEqual(counts["pair-natal-love"], 3)
        self.assertEqual(counts["single-natal-money"], 2)


# --------------------------------------------------------------------------------------------------------------
# 2. Верните твърдения се приемат, грешните се хващат
# --------------------------------------------------------------------------------------------------------------
def natal_true_sentences(facts, key="user", name="Иван"):
    out = []
    for planet in BODIES + ["Chiron"]:
        d = facts.natal[key].get(planet)
        if not d or not d.get("house"):
            continue
        h, s, deg, mn = d["house"], d["sign"], d["deg"], d["min"]
        out += [
            (f"{pl(planet)} в {sg(s)} {deg}°{mn:02d}' е в {hb(h)} дом.", dict(planet=planet, house=h, sign=s, deg=deg)),
            (f"Натален {pl(planet)} ({sg(s)} {deg}°{mn:02d}'), {hb(h)} дом.", dict(planet=planet, house=h, sign=s, deg=deg)),
            (f"{name}: {pl(planet)} е в {hb(h)} дом.", dict(planet=planet, house=h, sign=s, deg=deg)),
        ]
    return out


class TrueAndFalseClaimsTest(unittest.TestCase):
    """Генерирани от изчислените факти изречения: верните минават, всяка поквара се хваща."""

    def test_natal_houses_signs_and_degrees_one_person(self):
        for birth in PEOPLE:
            facts = tc.build_facts(mode="natal", user_name="Иван", natal_chart=chart(birth))
            true = natal_true_sentences(facts)
            self.assertGreater(len(true), 20)
            self.assertEqual(tc.hard(tc.check_text("\n".join(t for t, _ in true), facts)), [], birth)
            for sentence, d in true[:60]:
                wrong_house = (d["house"] % 12) + 1
                bad = sentence.replace(f"{hb(d['house'])} дом", f"{hb(wrong_house)} дом")
                self.assertIn("house", hard_codes(tc.check_text(bad, facts)), (birth, bad))
            for sentence, d in [x for x in true if "°" in x[0]][:40]:
                signs = [s for s in tc.SIGN_BG if s != d["sign"]]
                other = signs[(list(tc.SIGN_BG).index(d["sign"]) + 5) % len(signs)]
                bad = sentence.replace(sg(d["sign"]), sg(other), 1)
                self.assertIn("sign", hard_codes(tc.check_text(bad, facts)), (birth, bad))
                bad_deg = sentence.replace(f"{d['deg']}°", f"{(d['deg'] + 7) % 30}°", 1)
                self.assertIn("sign", hard_codes(tc.check_text(bad_deg, facts)), (birth, bad_deg))

    def test_natal_aspects_cusps_and_rulers(self):
        for birth in PEOPLE:
            facts = tc.build_facts(mode="natal", user_name="Иван", natal_chart=chart(birth))
            rows = [a for a in facts.natal_aspects["user"] if a["p1"] in BODIES and a["p2"] in BODIES]
            self.assertGreater(len(rows), 4)
            true = [f"{pl(a['p1'])} е в {ASPECT_PHRASE[a['aspect']]} с {pl(a['p2'])} (орбис {a['orb']:.2f}°)." for a in rows]
            for house, c in facts.cusps["user"].items():
                true.append(f"Куспидата на {hb(house)} дом е в {sg(c['sign'])}.")
                true.append(f"Управител на {hb(house)} дом е {pl(c['ruler'])}.")
            self.assertEqual(tc.hard(tc.check_text("\n".join(true), facts)), [], birth)
            for a in rows[:25]:
                present = {r["aspect"] for r in facts.natal_aspects["user"]
                           if {r["p1"], r["p2"]} == {a["p1"], a["p2"]}}
                wrong = next(x for x in ASPECT_PHRASE if x not in present)
                bad = f"{pl(a['p1'])} е в {ASPECT_PHRASE[wrong]} с {pl(a['p2'])}."
                self.assertIn("aspect", hard_codes(tc.check_text(bad, facts)), (birth, bad))
            for house, c in list(facts.cusps["user"].items())[:12]:
                other_sign = [s for s in tc.SIGN_BG if s != c["sign"]][3]
                self.assertIn("cusp", hard_codes(tc.check_text(f"Куспидата на {hb(house)} дом е в {sg(other_sign)}.", facts)))
                other_ruler = [p for p in BODIES if p != c["ruler"]][2]
                self.assertIn("ruler", hard_codes(tc.check_text(f"Управител на {hb(house)} дом е {pl(other_ruler)}.", facts)))

    def test_two_people_overlays_synastry_and_owners(self):
        for a_birth, b_birth in ((A_BIRTH, IVAN_BIRTH), (PEOPLE[2], PEOPLE[3]), (PEOPLE[4], PEOPLE[5]), (IVAN_BIRTH, PEOPLE[2])):
            facts = tc.build_facts(mode="natal", user_name="Мария", natal_chart=chart(a_birth),
                                   partner_name="Иван", partner_chart=chart(b_birth))
            true, bad = [], []
            for planet, house in facts.overlays[("partner", "user")].items():
                if planet not in BODIES:
                    continue
                true.append(f"{pl(planet)} на Иван е във вашия {hb(house)} дом.")
                bad.append(f"{pl(planet)} на Иван е във вашия {hb((house % 12) + 1)} дом.")
            for planet, house in facts.overlays[("user", "partner")].items():
                if planet not in BODIES:
                    continue
                true.append(f"Вашата {pl(planet)} е в неговия {hb(house)} дом.")
                bad.append(f"Вашата {pl(planet)} е в неговия {hb((house % 12) + 1)} дом.")
            self.assertGreater(len(true), 15)
            self.assertEqual(tc.hard(tc.check_text("\n".join(true), facts)), [], (a_birth, b_birth))
            for sentence in bad:
                self.assertIn("house", hard_codes(tc.check_text(sentence, facts)), (a_birth, b_birth, sentence))
            # обърнат собственик: домът на другия човек се твърди за вашия
            swapped = []
            for planet, house in facts.overlays[("partner", "user")].items():
                other = facts.overlays[("user", "partner")].get(planet)
                if planet in BODIES and other is not None and other != house:
                    swapped.append(f"{pl(planet)} на Иван е във вашия {hb(other)} дом.")
            for sentence in swapped[:8]:
                self.assertIn("house", hard_codes(tc.check_text(sentence, facts)), sentence)
            # синастрия
            syn = [a for a in facts.synastry if a["p1"] in BODIES and a["p2"] in BODIES]
            self.assertTrue(syn)
            true_syn = [f"{pl(a['p1'])} на {a['o1']} е в {ASPECT_PHRASE[a['aspect']]} с {pl(a['p2'])} на {a['o2']}." for a in syn]
            self.assertEqual(tc.hard(tc.check_text("\n".join(true_syn), facts)), [], (a_birth, b_birth))
            for a in syn[:10]:
                present = {r["aspect"] for r in facts.synastry if {r["p1"], r["p2"]} == {a["p1"], a["p2"]}}
                wrong = next(x for x in ASPECT_PHRASE if x not in present)
                bad_syn = f"{pl(a['p1'])} на {a['o1']} е в {ASPECT_PHRASE[wrong]} с {pl(a['p2'])} на {a['o2']}."
                self.assertIn("aspect", hard_codes(tc.check_text(bad_syn, facts)), bad_syn)

    def test_snapshot_transit_houses_signs_and_aspects(self):
        for birth in PEOPLE[:4]:
            facts = tc.build_facts(mode="snapshot", user_name="Иван", natal_chart=chart(birth),
                                   transit_chart=sky(birth), target_date=SNAPSHOT)
            true, bad = [], []
            for planet, house in facts.transit_houses["user"].items():
                s = facts.sky.get(planet)
                if planet not in BODIES or not s:
                    continue
                true.append(f"Транзитният {pl(planet)} е в {sg(s['sign'])} {s['deg']}°{s['min']:02d}' и в {hb(house)} дом.")
                bad.append(f"Транзитният {pl(planet)} е в {hb((house % 12) + 1)} дом.")
            for a in facts.transit_aspects["user"]:
                if a["p1"] in BODIES and a["p2"] in BODIES:
                    true.append(f"Транзитният {pl(a['p1'])} е в {ASPECT_PHRASE[a['aspect']]} с наталния {pl(a['p2'])}.")
            self.assertGreater(len(true), 8)
            self.assertEqual(tc.hard(tc.check_text("\n".join(true), facts)), [], birth)
            for sentence in bad:
                self.assertIn("house", hard_codes(tc.check_text(sentence, facts)), (birth, sentence))
            for a in facts.transit_aspects["user"][:12]:
                if a["p1"] in BODIES and a["p2"] in BODIES:
                    present = {r["aspect"] for r in facts.transit_aspects["user"] if (r["p1"], r["p2"]) == (a["p1"], a["p2"])}
                    wrong = next(x for x in ASPECT_PHRASE if x not in present)
                    sentence = f"Транзитният {pl(a['p1'])} е в {ASPECT_PHRASE[wrong]} с наталния {pl(a['p2'])}."
                    self.assertIn("aspect", hard_codes(tc.check_text(sentence, facts)), (birth, sentence))

    def test_period_dates_of_stations_ingresses_lunations_and_aspects(self):
        cal = calendar_for(partner=False)
        facts = tc.build_facts(mode="period", user_name="Иван", natal_chart=chart(IVAN_BIRTH), calendar=cal, report_date="2026-10-08")
        true, bad = [], []
        for e in cal.public_events():
            t = e["type"]
            if t == "RETROGRADE":
                word = "ретроградна" if e["direction"] == "retrograde" else "директна"
                when = e["when"]
                true.append(f"{pl(e['planet'])} става {word} на {dm(when[:10])} в {when[11:16]}.")
                shifted = (date.fromisoformat(when[:10]) + timedelta(days=3)).isoformat()
                bad.append(f"{pl(e['planet'])} става {word} на {dm(shifted)}.")
            elif t == "INGRESS" and e["planet"] != "Moon":
                when = e["when"]
                true.append(f"{pl(e['planet'])} влиза в {sg(e['sign'])} на {dm(when[:10])} в {when[11:16]}.")
                shifted = (date.fromisoformat(when[:10]) + timedelta(days=3)).isoformat()
                bad.append(f"{pl(e['planet'])} влиза в {sg(e['sign'])} на {dm(shifted)}.")
            elif t in ("LUNATION", "ECLIPSE"):
                new = e["event"].startswith("New")
                sign = next(k for k in tc.SIGN_FORMS if e["event"].endswith(k))
                when = e["when"]
                word = "Новолуние" if new else "Пълнолуние"
                true.append(f"{word} в {sg(sign)} на {dm(when[:10])}.")
                shifted = (date.fromisoformat(when[:10]) + timedelta(days=3)).isoformat()
                bad.append(f"{word} в {sg(sign)} на {dm(shifted)}.")
        self.assertGreater(len(true), 8)
        self.assertEqual(tc.hard(tc.check_text("\n".join(true), facts)), [])
        for sentence in bad:
            self.assertIn("event", hard_codes(tc.check_text(sentence, facts)), sentence)
        exact_rows = [e for e in cal.public_events() if e["type"] == "TRANSIT" and e.get("exact")]
        no_exact = [e for e in cal.public_events() if e["type"] == "TRANSIT" and not e.get("exact")]
        self.assertTrue(exact_rows and no_exact)
        for e in exact_rows[:15]:
            x = e["exact"][0]["when"]
            phrase = ASPECT_PHRASE[e["aspect"].lower()]
            good = f"{pl(e['planet'])} е в {phrase} с наталния {pl(e['natal_planet'])}, точен на {dm(x[:10])} в {x[11:16]}."
            self.assertEqual(tc.hard(tc.check_text(good, facts)), [], good)
            wrong_day = (date.fromisoformat(x[:10]) + timedelta(days=4)).isoformat()
            if wrong_day in {y["when"][:10] for y in e["exact"]}:
                continue
            bad_exact = f"{pl(e['planet'])} е в {phrase} с наталния {pl(e['natal_planet'])}, точен на {dm(wrong_day)}."
            self.assertTrue({"exact", "date"} & set(hard_codes(tc.check_text(bad_exact, facts))), bad_exact)
        for e in no_exact[:10]:
            sentence = f"{pl(e['planet'])} е в точен {ASPECT_PHRASE[e['aspect'].lower()]} с наталния {pl(e['natal_planet'])}."
            self.assertIn("exact", hard_codes(tc.check_text(sentence, facts)), sentence)

    def test_ranges_inside_the_window_and_free_dates_are_not_hard(self):
        cal = calendar_for(partner=False)
        facts = tc.build_facts(mode="period", user_name="Иван", natal_chart=chart(IVAN_BIRTH), calendar=cal, report_date="2026-10-08")
        row = next(e for e in cal.public_events() if e["type"] == "TRANSIT" and e.get("exact"))
        x = date.fromisoformat(row["exact"][0]["when"][:10])
        base = f"{pl(row['planet'])} е в {ASPECT_PHRASE[row['aspect'].lower()]} с наталния {pl(row['natal_planet'])}"
        rng = f"{base} ({x.day - 1}–{x.day + 1} {MONTHS_BG[x.month]})."
        self.assertEqual(tc.hard(tc.check_text(rng, facts)), [], rng)
        lunation = next(e for e in cal.public_events() if e["type"] == "LUNATION" and e["event"].startswith("New"))
        d = date.fromisoformat(lunation["when"][:10])
        sign = next(k for k in tc.SIGN_FORMS if lunation["event"].endswith(k))
        text = f"Новолуние в {sg(sign)} ({d.day}–{d.day + 1} {MONTHS_BG[d.month]})."
        self.assertEqual(tc.hard(tc.check_text(text, facts)), [], text)
        self.assertEqual(tc.hard(tc.check_text("Планирайте разговора след 19 ноември и преди 5 декември.", facts)), [])
        self.assertEqual(tc.hard(tc.check_text("Стартирайте проекти след 4 октомври (ретроградна Венера).", facts)), [])   # граница на препоръка
        self.assertEqual(tc.hard(tc.check_text("Рожденият ви ден е 12 юли 1985 г.", facts)), [])

    def test_a_person_named_like_a_planet_is_not_checked_for_claims(self):
        for name in ("Венера", "Мария Луна", "Марс"):
            facts = tc.build_facts(mode="natal", user_name=name, natal_chart=chart(IVAN_BIRTH))
            sun = facts.natal["user"]["Sun"]
            wrong = f"{name} е родена със Слънце в {hb((sun['house'] % 12) + 1)} дом."
            self.assertEqual(tc.hard(tc.check_text(wrong, facts)), [], name)
            self.assertIn("english", [v.code for v in tc.check_text("Тук е exact_this_month.", facts)])

    def test_entering_a_house_is_an_event_not_a_placement(self):
        facts = tc.build_facts(mode="snapshot", user_name="Иван", natal_chart=chart(IVAN_BIRTH),
                               transit_chart=sky(IVAN_BIRTH), target_date=SNAPSHOT)
        house = facts.transit_houses["user"]["Saturn"]
        text = f"Сатурн навлиза в {hb((house % 12) + 1)} дом през следващите години."
        self.assertEqual(tc.hard(tc.check_text(text, facts)), [])

    def test_a_planet_that_is_not_in_the_data_makes_no_claim(self):
        facts = tc.build_facts(mode="natal", user_name="Иван", natal_chart=chart(IVAN_BIRTH))
        self.assertEqual(tc.hard(tc.check_text("Ако Марс беше в 5-ти дом, би ставало шумно. Например Луна в 11-ти дом е друго.", facts)), [])

    def test_the_period_text_may_speak_about_transit_planets_without_data_for_them(self):
        cal = calendar_for(partner=False)
        facts = tc.build_facts(mode="period", user_name="Иван", natal_chart=chart(IVAN_BIRTH), calendar=cal, report_date="2026-10-08")
        self.assertEqual(tc.hard(tc.check_text("Марс в Лъв (8-ми дом) носи енергия. Юпитер в Лъв прави живо лятото.", facts)), [])


# --------------------------------------------------------------------------------------------------------------
# 3. Поправка, отхвърляне, режими
# --------------------------------------------------------------------------------------------------------------
def natal_facts():
    return tc.build_facts(mode="natal", user_name="Иван", natal_chart=chart(IVAN_BIRTH))


def good_and_bad_text():
    facts = natal_facts()
    sun = facts.natal["user"]["Sun"]
    wrong = (sun["house"] % 12) + 1
    good = (f"Слънце в {sg(sun['sign'])} {sun['deg']}°{sun['min']:02d}' е в {hb(sun['house'])} дом. "
            "Това дава сила и увереност във всичко, което правите.\n\nВторият абзац е само съвет и няма твърдения.")
    bad = good.replace(f"{hb(sun['house'])} дом", f"{hb(wrong)} дом")
    return facts, good, bad, wrong


class GuardTest(unittest.TestCase):
    def run_guard(self, text, facts, replies, mode=None):
        calls = []

        async def repair(system_prompt, user_prompt):
            calls.append((system_prompt, user_prompt))
            reply = replies.pop(0)
            if isinstance(reply, Exception):
                raise reply
            return reply

        outcome = asyncio.run(text_guard.guard_text(text, facts, stage="natal", repair_call=repair, mode=mode))
        return outcome, calls

    def test_a_correct_text_is_left_alone_and_no_repair_is_made(self):
        facts, good, _, _ = good_and_bad_text()
        outcome, calls = self.run_guard(good, facts, [])
        self.assertEqual(outcome.text, good)
        self.assertEqual(calls, [])
        self.assertEqual(outcome.result, "ok")

    def test_one_repair_fixes_the_text(self):
        facts, good, bad, wrong = good_and_bad_text()
        outcome, calls = self.run_guard(bad, facts, [good])
        self.assertEqual(outcome.text, good)
        self.assertTrue(outcome.repaired)
        self.assertEqual(outcome.result, "repaired")
        self.assertEqual(len(calls), 1)
        system_prompt, user_prompt = calls[0]
        self.assertIn("FACT CORRECTION", system_prompt)
        self.assertIn(bad.strip(), user_prompt)                       # текстът отива към поправката
        self.assertIn("Вярно:", user_prompt)                          # заедно с верните стойности
        self.assertIn(f"не е в {wrong}-ти дом", user_prompt)
        self.assertEqual(outcome.summary()["hard_before"], 1)
        self.assertEqual(outcome.summary()["hard_after"], 0)
        self.assertNotIn("Слънце", json.dumps(outcome.summary(), ensure_ascii=False))          # телеметрията няма текст

    def test_a_repair_that_still_has_a_hard_violation_rejects_the_text(self):
        facts, _, bad, _ = good_and_bad_text()
        with self.assertRaises(text_guard.TextCheckError) as ctx:
            self.run_guard(bad, facts, [bad])
        self.assertEqual(ctx.exception.stage, "natal")
        self.assertTrue(ctx.exception.outcome.rejected)
        self.assertEqual(ctx.exception.outcome.codes(), "house")

    def test_only_one_repair_is_made(self):
        facts, _, bad, _ = good_and_bad_text()
        replies = [bad, bad, bad]
        with self.assertRaises(text_guard.TextCheckError):
            self.run_guard(bad, facts, replies)
        self.assertEqual(len(replies), 2)                              # втората и третата не са пипнати

    def test_a_failed_empty_or_truncated_repair_rejects_the_text(self):
        facts, good, bad, _ = good_and_bad_text()
        for reply in (RuntimeError("провайдърът не отговори"), "", "   ", good[:20]):
            with self.assertRaises(text_guard.TextCheckError, msg=repr(reply)):
                self.run_guard(bad, facts, [reply])

    def test_code_fences_around_the_repair_are_removed(self):
        facts, good, bad, _ = good_and_bad_text()
        outcome, _ = self.run_guard(bad, facts, ["```markdown\n" + good + "\n```"])
        self.assertEqual(outcome.text, good)

    def test_soft_violations_alone_do_not_cause_a_repair(self):
        facts, good, _, _ = good_and_bad_text()
        text = good + "\n\nВероятността е 85% през октомври."
        outcome, calls = self.run_guard(text, facts, [])
        self.assertEqual(calls, [])
        self.assertEqual(outcome.text, text)
        self.assertEqual({v.code for v in outcome.before}, {"percent"})

    def test_modes_warn_and_off(self):
        facts, _, bad, _ = good_and_bad_text()
        outcome, calls = self.run_guard(bad, facts, [], mode="warn")
        self.assertEqual((outcome.text, calls, outcome.result), (bad, [], "warned"))
        outcome, calls = self.run_guard(bad, facts, [], mode="off")
        self.assertEqual((outcome.text, calls, outcome.before), (bad, [], []))

    def test_mode_comes_from_the_environment_and_a_bad_value_means_enforce(self):
        with mock.patch.dict(os.environ, {"TEXT_CHECK_MODE": "WARN"}):
            self.assertEqual(text_guard.check_mode(), "warn")
        with mock.patch.dict(os.environ, {"TEXT_CHECK_MODE": "нещо"}):
            self.assertEqual(text_guard.check_mode(), "enforce")
        with mock.patch.dict(os.environ, {}, clear=False):
            os.environ.pop("TEXT_CHECK_MODE", None)
            self.assertEqual(text_guard.check_mode(), "enforce")

    def test_ignored_codes_are_not_counted(self):
        facts, _, bad, _ = good_and_bad_text()
        with mock.patch.dict(os.environ, {"TEXT_CHECK_IGNORE": "house, event"}):
            outcome, calls = self.run_guard(bad, facts, [])
        self.assertEqual((outcome.text, calls), (bad, []))

    def test_labels_are_removed_even_without_facts_and_in_every_mode(self):
        text = "Любов (ЗАДЪЛЖИТЕЛНА СЕКЦИЯ)\nТекст с `state_in_month: approaching` вътре."
        for mode in ("enforce", "warn", "off"):
            outcome, _ = self.run_guard(text, None, [], mode=mode)
            self.assertNotIn("ЗАДЪЛЖИТЕЛНА", outcome.text)
            self.assertNotIn("state_in_month", outcome.text)
            self.assertEqual(outcome.cleaned, 2)

    def test_a_crash_in_the_checker_never_stops_the_report(self):
        facts, _, bad, _ = good_and_bad_text()
        with mock.patch.object(text_check_module(), "check_text", side_effect=ValueError("повреда")):
            outcome, calls = self.run_guard(bad, facts, [])
        self.assertEqual((outcome.text, calls, outcome.result), (bad, [], "error"))

    def test_a_checker_that_takes_too_long_is_cut_off(self):
        facts, _, bad, _ = good_and_bad_text()

        def slow(*args, **kwargs):
            time.sleep(0.5)
            return []
        with mock.patch.object(text_check_module(), "check_text", slow), mock.patch.object(text_guard, "CHECK_TIMEOUT_SECONDS", 0.05):
            outcome, calls = self.run_guard(bad, facts, [])
        self.assertEqual((outcome.text, outcome.result), (bad, "error"))

    def test_the_repair_list_puts_hard_violations_first_and_drops_duplicates(self):
        v = [tc.Violation("percent", "soft", "а", "x"), tc.Violation("house", "hard", "б", "y"), tc.Violation("house", "hard", "в", "y")]
        _, user_prompt = text_guard.build_repair_prompts("Текст", v)
        self.assertLess(user_prompt.index("[house]"), user_prompt.index("[percent]"))
        self.assertEqual(user_prompt.count("[house]"), 1)


def text_check_module():
    return text_guard.text_check


# --------------------------------------------------------------------------------------------------------------
# 4. Интерпретаторът и крайните точки
# --------------------------------------------------------------------------------------------------------------
class InterpreterIntegrationTest(unittest.TestCase):
    def setUp(self):
        self.interp = ai_interpreter.AIInterpreter(api_key="test-dummy")

    def test_a_wrong_house_is_repaired_with_a_second_call(self):
        facts, good, bad, _ = good_and_bad_text()
        checks = []
        fake = mock.AsyncMock(side_effect=[bad, good])
        with mock.patch.object(self.interp, "_call_api", fake):
            text = asyncio.run(self.interp.interpret_chart(natal_chart=chart(IVAN_BIRTH), user_name="Иван", checks=checks))
        self.assertEqual(text, good)
        self.assertEqual(fake.await_count, 2)
        self.assertEqual(fake.await_args_list[1].kwargs["temperature"], text_guard.REPAIR_TEMPERATURE)
        self.assertEqual([c["result"] for c in checks], ["repaired"])

    def test_an_unrepairable_text_raises_and_nothing_is_returned(self):
        _, _, bad, _ = good_and_bad_text()
        checks = []
        with mock.patch.object(self.interp, "_call_api", mock.AsyncMock(side_effect=[bad, bad])):
            with self.assertRaises(text_guard.TextCheckError):
                asyncio.run(self.interp.interpret_chart(natal_chart=chart(IVAN_BIRTH), user_name="Иван", checks=checks))
        self.assertEqual([c["result"] for c in checks], ["rejected"])

    def test_provider_errors_are_still_wrapped_as_before(self):
        with mock.patch.object(self.interp, "_call_api", mock.AsyncMock(side_effect=RuntimeError("мрежа"))):
            with self.assertRaises(RuntimeError) as ctx:
                asyncio.run(self.interp.interpret_chart(natal_chart=chart(IVAN_BIRTH), user_name="Иван"))
        self.assertNotIsInstance(ctx.exception, text_guard.TextCheckError)

    def test_snapshot_and_pair_texts_are_checked_with_their_own_facts(self):
        transit = sky(IVAN_BIRTH)
        facts = tc.build_facts(mode="snapshot", user_name="Иван", natal_chart=chart(IVAN_BIRTH), transit_chart=transit, target_date=SNAPSHOT)
        planet, house = next((p, h) for p, h in facts.transit_houses["user"].items() if p == "Saturn")
        good = f"Транзитният Сатурн е в {hb(house)} дом и иска търпение."
        bad = f"Транзитният Сатурн е в {hb((house % 12) + 1)} дом и иска търпение."
        with mock.patch.object(self.interp, "_call_api", mock.AsyncMock(side_effect=[bad, good])):
            text = asyncio.run(self.interp.interpret_chart(
                natal_chart=chart(IVAN_BIRTH), transit_chart=transit, target_date=SNAPSHOT, user_name="Иван"))
        self.assertEqual(text, good)
        pair_facts = tc.build_facts(mode="natal", user_name="Мария", natal_chart=chart(A_BIRTH), partner_name="Иван", partner_chart=chart(IVAN_BIRTH))
        h = pair_facts.overlays[("partner", "user")]["Mars"]
        good_pair = f"Марс на Иван е във вашия {hb(h)} дом."
        bad_pair = f"Марс на Иван е във вашия {hb((h % 12) + 1)} дом."
        with mock.patch.object(self.interp, "_call_api", mock.AsyncMock(side_effect=[bad_pair, good_pair])):
            text = asyncio.run(self.interp.interpret_chart(
                natal_chart=chart(A_BIRTH), partner_chart=chart(IVAN_BIRTH), user_name="Мария", partner_name="Иван"))
        self.assertEqual(text, good_pair)

    def test_a_text_in_another_language_is_not_checked(self):
        _, _, bad, _ = good_and_bad_text()
        fake = mock.AsyncMock(return_value=bad)
        with mock.patch.object(self.interp, "_call_api", fake):
            text = asyncio.run(self.interp.interpret_chart(natal_chart=chart(IVAN_BIRTH), user_name="Иван", language="en"))
        self.assertEqual(text, bad)
        self.assertEqual(fake.await_count, 1)

    def test_facts_that_cannot_be_built_do_not_stop_the_report(self):
        _, _, bad, _ = good_and_bad_text()
        with mock.patch.object(self.interp, "_call_api", mock.AsyncMock(return_value=bad)), \
                mock.patch.object(ai_interpreter.text_check, "build_facts", side_effect=KeyError("карта")):
            text = asyncio.run(self.interp.interpret_chart(natal_chart=chart(IVAN_BIRTH), user_name="Иван"))
        self.assertEqual(text, bad)


class PeriodGuardTest(unittest.TestCase):
    """run_period_report с истинския интерпретатор: подменени са само вътрешните AI викове."""

    def setUp(self):
        self.interp = ai_interpreter.AIInterpreter(api_key="test-dummy")
        self.cal = calendar_for(partner=False)

    def run_report(self, month_texts, overview="<p>Общ преглед</p>", repair=None):
        async def monthly(**kwargs):
            return month_texts[kwargs["month"]].pop(0)

        async def go():
            steps = []
            async for step in period_report.run_period_report(
                    self.interp, calendar=self.cal, natal_chart=chart(IVAN_BIRTH), partner_chart=None, report_type="general",
                    user_name="Иван", partner_name=None, question="", report_date="2026-10-08", retry_pause=0):
                steps.append(step)
            return steps
        with mock.patch.object(self.interp, "_process_monthly_chunk", side_effect=monthly), \
                mock.patch.object(self.interp, "compose_period_overview", mock.AsyncMock(return_value=overview)), \
                mock.patch.object(self.interp, "_call_api", repair or mock.AsyncMock(side_effect=AssertionError("няма поправка"))):
            return asyncio.run(go())

    GOOD_OCT = "Венера става ретроградна на 3 октомври в 09:16. Новолуние в Везни на 10 октомври."
    BAD_OCT = "Венера става ретроградна на 4 октомври. Новолуние в Везни на 10 октомври."
    NOV = "Меркурий става директен на 13 ноември в 16:54."

    def test_a_correct_month_text_passes_without_a_repair(self):
        steps = self.run_report({"2026-10": [self.GOOD_OCT], "2026-11": [self.NOV]})
        self.assertEqual(steps[-1]["type"], "finished")
        self.assertEqual(steps[-1]["month_texts"][0][1], self.GOOD_OCT)
        self.assertEqual([c["result"] for c in steps[-1]["checks"]], ["ok", "ok", "ok"])        # два месеца и общият преглед

    def test_a_wrong_station_date_is_repaired_once(self):
        repair = mock.AsyncMock(return_value=self.GOOD_OCT)
        steps = self.run_report({"2026-10": [self.BAD_OCT], "2026-11": [self.NOV]}, repair=repair)
        self.assertEqual(steps[-1]["month_texts"][0][1], self.GOOD_OCT)
        self.assertEqual(repair.await_count, 1)
        self.assertEqual(steps[-1]["checks"][0]["result"], "repaired")
        self.assertEqual(steps[-1]["checks"][0]["stage"], "month:2026-10")
        self.assertIn("event", steps[-1]["checks"][0]["codes"])

    def test_a_month_that_cannot_be_repaired_is_generated_again_and_then_fails_the_report(self):
        repair = mock.AsyncMock(return_value=self.BAD_OCT)
        with self.assertRaises(period_report.ForecastGenerationError) as ctx:
            self.run_report({"2026-10": [self.BAD_OCT, self.BAD_OCT], "2026-11": [self.NOV]}, repair=repair)
        self.assertEqual(ctx.exception.stage, "month:2026-10")
        self.assertIsInstance(ctx.exception.cause, text_guard.TextCheckError)
        self.assertEqual(repair.await_count, 2)                                                  # по една поправка на опит
        self.assertEqual([c["result"] for c in ctx.exception.checks], ["rejected", "rejected"])

    def test_the_second_attempt_can_save_the_month(self):
        repair = mock.AsyncMock(return_value=self.BAD_OCT)
        steps = self.run_report({"2026-10": [self.BAD_OCT, self.GOOD_OCT], "2026-11": [self.NOV]}, repair=repair)
        self.assertEqual(steps[-1]["month_texts"][0][1], self.GOOD_OCT)
        self.assertEqual([c["result"] for c in steps[-1]["checks"]][:2], ["rejected", "ok"])

    def test_the_overview_is_checked_too(self):
        bad = "Най-важното: Плутон става директен на 17 октомври."
        good = "Най-важното: Плутон става директен на 16 октомври."
        repair = mock.AsyncMock(return_value=good)
        steps = self.run_report({"2026-10": [self.GOOD_OCT], "2026-11": [self.NOV]}, overview=bad, repair=repair)
        self.assertEqual(steps[-1]["overview"], good)
        self.assertEqual(steps[-1]["checks"][-1]["stage"], "overview")

    def test_labels_are_cleaned_in_period_texts(self):
        text = self.GOOD_OCT + " Състояние state_in_month: approaching."
        steps = self.run_report({"2026-10": [text], "2026-11": [self.NOV]})
        self.assertNotIn("state_in_month", steps[-1]["month_texts"][0][1])


class TitleTest(unittest.TestCase):
    def test_the_monthly_pair_title_names_both_people_in_the_format_and_in_the_instruction(self):
        interp = ai_interpreter.AIInterpreter(api_key="test-dummy")
        pair = interp._build_dynamic_system_prompt(
            report_type="love", language="bg", natal_chart=chart(A_BIRTH), partner_chart=chart(IVAN_BIRTH),
            user_display_name="Мария", partner_display_name="Иван", has_partner=True)
        self.assertEqual(pair.count("[ИМЕ НА ПАРТНЬОРА]"), 2)                 # формата и указанието казват едно и също
        single = interp._build_dynamic_system_prompt(
            report_type="love", language="bg", natal_chart=chart(A_BIRTH), user_display_name="Мария", has_partner=False)
        self.assertNotIn("[ИМЕ НА ПАРТНЬОРА]", single)


def sse_events(text):
    return [json.loads(line[6:]) for line in text.splitlines() if line.startswith("data: ")]


class EndpointTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.client = TestClient(main.app)

    def setUp(self):
        limiter.reset()

    def natal_sun(self):
        c = chart(dict(date=CHART["date"], time=CHART["time"], lat=CHART["lat"], lon=CHART["lon"]))
        facts = tc.build_facts(mode="natal", user_name="Аз", natal_chart=c)
        sun = facts.natal["user"]["Sun"]
        return sun, f"Слънце в {sg(sun['sign'])} {sun['deg']}°{sun['min']:02d}' е в {hb(sun['house'])} дом."

    def bad_and_good(self):
        sun, good = self.natal_sun()
        return good.replace(f"{hb(sun['house'])} дом", f"{hb((sun['house'] % 12) + 1)} дом"), good

    def events(self, user_id, name):
        db = SessionLocal()
        try:
            return db.query(Event).filter(Event.name == name, Event.user_id == user_id).all()
        finally:
            db.close()

    def test_natal_is_repaired_saved_and_the_repair_is_recorded_without_text(self):
        h = register_and_login(self.client, "p10-repair@test.bg")
        uid = self.client.get("/me", headers=h).json()["id"]
        bad, good = self.bad_and_good()
        with mock.patch.object(main.ai_interpreter, "_call_api", mock.AsyncMock(side_effect=[bad, good])):
            r = self.client.post("/interpret", json={**CHART, "name": "Аз"}, headers=h)
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual(r.json()["interpretation"], good)
        saved = self.client.get("/reports", headers=h).json()
        self.assertEqual(len(saved), 1)
        recorded = self.events(uid, "text_check")
        self.assertEqual(len(recorded), 1)
        self.assertEqual(recorded[0].props["result"], "repaired")
        self.assertEqual(recorded[0].props["stage"], "natal")
        self.assertNotIn("Слънце", json.dumps(recorded[0].props, ensure_ascii=False))

    @mock.patch.dict(os.environ, {"COINS_ENFORCED": "1"})
    def test_natal_that_cannot_be_repaired_is_502_nothing_saved_nothing_charged(self):
        h = register_and_login(self.client, "p10-reject@test.bg")
        me = self.client.get("/me", headers=h).json()
        bad, _ = self.bad_and_good()
        with mock.patch.object(main.ai_interpreter, "_call_api", mock.AsyncMock(side_effect=[bad, bad])):
            r = self.client.post("/interpret", json={**CHART, "name": "Аз"}, headers=h)
        self.assertEqual(r.status_code, 502)
        self.assertEqual(r.json()["detail"], text_guard.USER_MESSAGE)
        self.assertNotIn("house", r.text)                                                     # без технически подробности
        self.assertEqual(self.client.get("/reports", headers=h).json(), [])
        self.assertEqual(self.client.get("/me", headers=h).json()["coins"], me["coins"])
        failed = self.events(me["id"], "analysis_failed")
        self.assertEqual(len(failed), 1)
        self.assertEqual(failed[0].props["reason"], "text_check")
        self.assertEqual(failed[0].props["codes"], "house")

    def test_warn_mode_returns_the_text_as_it_is(self):
        h = register_and_login(self.client, "p10-warn@test.bg")
        bad, _ = self.bad_and_good()
        fake = mock.AsyncMock(return_value=bad)
        with mock.patch.dict(os.environ, {"TEXT_CHECK_MODE": "warn"}), mock.patch.object(main.ai_interpreter, "_call_api", fake):
            r = self.client.post("/interpret", json={**CHART, "name": "Аз"}, headers=h)
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual(r.json()["interpretation"], bad)
        self.assertEqual(fake.await_count, 1)
        uid = self.client.get("/me", headers=h).json()["id"]
        self.assertEqual(self.events(uid, "text_check")[0].props["result"], "warned")

    def body(self, end="2026-11-30"):
        return {**CHART, "name": "Аз", "is_dynamic": True, "target_date": "2026-10-01", "end_date": end}

    GOOD_OCT = "<p>Венера става ретроградна на 3 октомври.</p>"
    BAD_OCT = "<p>Венера става ретроградна на 4 октомври.</p>"

    def test_stream_repairs_a_month_and_saves_the_repaired_text(self):
        h = register_and_login(self.client, "p10-stream-ok@test.bg")
        uid = self.client.get("/me", headers=h).json()["id"]
        monthly = mock.AsyncMock(side_effect=[self.BAD_OCT, "<p>Ноември</p>"])
        with mock.patch.object(main.ai_interpreter, "_process_monthly_chunk", monthly), \
                mock.patch.object(main.ai_interpreter, "compose_period_overview", mock.AsyncMock(return_value="<p>Преглед</p>")), \
                mock.patch.object(main.ai_interpreter, "_call_api", mock.AsyncMock(return_value=self.GOOD_OCT)):
            r = self.client.post("/interpret-stream", json=self.body(), headers=h)
        steps = sse_events(r.text)
        self.assertEqual(steps[-1]["type"], "complete")
        self.assertEqual(steps[2]["text"], self.GOOD_OCT)
        db = SessionLocal()
        try:
            content = db.get(Report, self.client.get("/reports", headers=h).json()[0]["id"]).content
        finally:
            db.close()
        self.assertIn(self.GOOD_OCT, content)
        self.assertNotIn(self.BAD_OCT, content)
        self.assertEqual([e.props["result"] for e in self.events(uid, "text_check")], ["repaired"])

    @mock.patch.dict(os.environ, {"COINS_ENFORCED": "1"})
    def test_stream_with_a_month_that_cannot_be_repaired_fails_without_saving_or_charging(self):
        h = register_and_login(self.client, "p10-stream-bad@test.bg")
        me = self.client.get("/me", headers=h).json()
        monthly = mock.AsyncMock(return_value=self.BAD_OCT)
        with mock.patch.object(period_report, "RETRY_PAUSE_SECONDS", 0.0), \
                mock.patch.object(main.ai_interpreter, "_process_monthly_chunk", monthly), \
                mock.patch.object(main.ai_interpreter, "compose_period_overview", mock.AsyncMock(return_value="<p>Преглед</p>")), \
                mock.patch.object(main.ai_interpreter, "_call_api", mock.AsyncMock(return_value=self.BAD_OCT)):
            r = self.client.post("/interpret-stream", json=self.body(), headers=h)
        steps = sse_events(r.text)
        self.assertEqual(steps[-1], {"type": "error", "code": 502, "message": period_report.USER_MESSAGE})
        self.assertNotIn("complete", [s["type"] for s in steps])
        self.assertEqual(self.client.get("/reports", headers=h).json(), [])
        self.assertEqual(self.client.get("/me", headers=h).json()["coins"], me["coins"])
        failed = self.events(me["id"], "analysis_failed")
        self.assertEqual((failed[0].props["reason"], failed[0].props["stage"]), ("text_check", "month:2026-10"))
        self.assertEqual({e.props["result"] for e in self.events(me["id"], "text_check")}, {"rejected"})


# --------------------------------------------------------------------------------------------------------------
# 5. Скорост и устойчивост
# --------------------------------------------------------------------------------------------------------------
class PerformanceTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        with open(os.path.join(FIXTURES, "phase10_corpus.json"), encoding="utf-8") as f:
            cls.corpus = json.load(f)
        cls.facts = corpus_facts("pair", "period")

    def timed(self, text, limit):
        started = time.perf_counter()
        tc.check_text(text, self.facts)
        self.assertLess(time.perf_counter() - started, limit)

    def test_a_large_realistic_text_is_fast(self):
        text = "\n".join(seg for key, segs in self.corpus.items() if key.startswith("pair-period") for seg in segs)
        self.assertGreater(len(text), 30000)
        self.timed(text, 4.0)

    def test_pathological_inputs_do_not_hang(self):
        for text in ("Слънце " * 5000, "Слънце, " * 3000 + "в 5-ти дом.", "1-ви дом " * 5000, "(" * 5000 + "Слънце в Овен",
                     "Слънце (Овен 1°) и " * 2000 + "Луна в 3-ти дом", "тригон Слънце " * 2000,
                     "Марс и Венера в тригон с Юпитер и Сатурн, " * 1500, "15 октомври – 20 октомври, " * 3000,
                     "а" * 100000, "1234567890" * 2000):
            self.timed(text, 6.0)

    def test_a_very_long_sentence_is_cut_into_pieces(self):
        pieces = tc.segments("Слънце, " * 2000)
        self.assertGreater(len(pieces), 5)
        self.assertLessEqual(max(len(p) for p in pieces), tc.MAX_SEGMENT + 1)


if __name__ == "__main__":
    unittest.main()
