"""
Фаза 9: точен календар и общ преглед на периода. Всичко е офлайн: AI се подменя.

Две независими проверки на календара:
1. срещу контрола от одита (tests/fixtures/phase9_control/*.json; Swiss Ephemeris Moshier, друг код и други файлове
   с ефемериди от производствените) за синтетичните карти A и Иван и периода 01.10-30.11.2026. Допускът за аспектите е по
   ъгъл, не по време: между две ефемериди бавна планета се разминава с минути, а бърза със секунди;
2. срещу груба сила: позиции на всеки 30 минути и собствено определение за „активен“ и „точен“, без обща логика
   със scanner.py. Тя гарантира, че няма пропуснати, излишни или двойни аспекти и преминавания.

Пускане: python -m unittest discover -s tests -p "test_phase9_calendar.py"
"""
import asyncio
import json
import math
import os
import time
import unittest
from datetime import date, datetime, timedelta, timezone
from unittest import mock

import pytz

import testenv  # noqa: F401  (трябва да е преди main)
import swisseph as swe  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

import ai_interpreter  # noqa: E402
import engine  # noqa: E402
import main  # noqa: E402
import period_report  # noqa: E402
from database import Event, Report, SessionLocal  # noqa: E402
from rate_limit import limiter  # noqa: E402
from scanner import (APPLYING_ORB, ASPECTS, NATAL_TARGETS, SEPARATING_ORB, TRANSIT_PLANETS,  # noqa: E402
                     TransitScanner, utc_to_jd)
from testenv import CHART, register_and_login  # noqa: E402

FIXTURES = os.path.join(os.path.dirname(os.path.abspath(__file__)), "fixtures", "phase9_control")
A_BIRTH = dict(date="1990-02-15", time="13:00", lat=42.6977, lon=23.3219)
IVAN_BIRTH = dict(date="1985-07-12", time="18:45", lat=48.2082, lon=16.3738)
PERIOD = ("2026-10-01", "2026-11-30")
FLAGS = swe.FLG_SWIEPH | swe.FLG_SPEED
ASPECT_BY_ANGLE = {0: "Conjunction", 60: "Sextile", 90: "Square", 120: "Trine", 180: "Opposition"}


def load(name):
    with open(os.path.join(FIXTURES, name), encoding="utf-8") as handle:
        return json.load(handle)


def utc(text):
    return datetime.strptime(text[:19], "%Y-%m-%dT%H:%M:%S").replace(tzinfo=timezone.utc)


def tolerance_seconds(when: datetime, planet_id: int, floor: float = 90.0, angle: float = 0.0006) -> float:
    """Допуск за сверка с друга ефемерида: най-малко floor секунди, а за бавна планета времето, за което минава
    `angle` градуса (0,0006° са 2 дъгови секунди, колкото се различават Moshier и файловете на Swiss Ephemeris)."""
    speed = abs(swe.calc_ut(utc_to_jd(when), planet_id, FLAGS)[0][3])          # градуса на ден
    return max(floor, angle / max(speed, 1e-6) * 86400.0)


def utc_jd_to_local(jd, tz):
    return (datetime(1970, 1, 1, tzinfo=timezone.utc) + timedelta(days=jd - 2440587.5)).astimezone(tz).date()


def sse_events(text):
    return [json.loads(line[6:]) for line in text.splitlines() if line.startswith("data: ")]


def section_json(user_prompt, title_prefix):
    """JSON на блока '--- ЗАГЛАВИЕ ---' (данните са на един ред)."""
    lines = user_prompt.splitlines()
    for i, line in enumerate(lines):
        if line.startswith(f"--- {title_prefix}"):
            for candidate in lines[i + 1:]:
                if candidate.startswith(("[", "{")):
                    return json.loads(candidate)
    raise AssertionError(f"няма блок {title_prefix}")


class CalendarBase(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.eng = engine.AstrologyEngine()
        cls.a = cls.eng.calculate_chart(**A_BIRTH)
        cls.ivan = cls.eng.calculate_chart(**IVAN_BIRTH)
        cls.scanner = TransitScanner(engine_instance=cls.eng)
        cls.cal = cls.scanner.build_calendar(cls.a, PERIOD[0], PERIOD[1], A_BIRTH["lat"], A_BIRTH["lon"],
                                             partner_chart=cls.ivan)

    def series(self, target, planet, natal, aspect, cal=None):
        return [e for e in (cal or self.cal).events if e["type"] == "TRANSIT" and e["target"] == target
                and e["planet"] == planet and e["natal_planet"] == natal and e["aspect"] == aspect]


class ControlTest(CalendarBase):
    """Календарът срещу независимия контрол от одита."""

    POINT_EVENTS = {
        "Venus retrograde": ("Venus", "retrograde"), "Venus direct": ("Venus", "direct"),
        "Mercury retrograde": ("Mercury", "retrograde"), "Mercury direct": ("Mercury", "direct"),
        "Pluto direct": ("Pluto", "direct"),
    }

    def nearest(self, predicate, moment):
        rows = [e for e in self.cal.events if e["type"] != "TRANSIT" and predicate(e)]
        self.assertTrue(rows, "няма такова събитие в календара")
        return min(rows, key=lambda e: abs((utc(e["_utc"]) - moment).total_seconds()))

    def test_global_events_match_the_control(self):
        for ref in load("october-november-reference-events.json"):
            name, moment = ref["event"], utc(ref["utc"])
            with self.subTest(event=name, utc=ref["utc"]):
                if " natal " in name:                                   # личен аспект на Иван (втория човек)
                    planet, verb, _, natal = name.split()
                    aspect = {"square": "Square", "conjunct": "Conjunction"}[verb]
                    rows = self.series("Partner", planet, natal, aspect)
                    self.assertTrue(rows, name)
                    times = [utc(x) for row in rows for x in row["_exact_utc"]]
                    best = min(times, key=lambda t: abs((t - moment).total_seconds()))
                    self.assertLessEqual(abs((best - moment).total_seconds()),
                                         tolerance_seconds(moment, TRANSIT_PLANETS[planet]), name)
                    continue
                if name in self.POINT_EVENTS:
                    planet, direction = self.POINT_EVENTS[name]
                    found = self.nearest(lambda e: e["type"] == "RETROGRADE" and e["planet"] == planet
                                         and e["direction"] == direction, moment)
                elif name in ("New Moon", "Full Moon"):
                    found = self.nearest(lambda e: e["type"] in ("LUNATION", "ECLIPSE")
                                         and e["event"].startswith(name), moment)
                else:                                                   # "Sun Scorpio", "Sun Sagittarius", "Mars Virgo"
                    planet, sign = name.split()
                    found = self.nearest(lambda e: e["type"] == "INGRESS" and e["planet"] == planet
                                         and e["sign"] == sign, moment)
                self.assertLessEqual(abs((utc(found["_utc"]) - moment).total_seconds()), 60.0, name)

    def test_venus_retrograde_ingress_into_libra(self):
        ref = load("venus-libra-ingress-control.json")
        rows = [e for e in self.cal.events if e["type"] == "INGRESS" and e["planet"] == "Venus"]
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["sign"], "Libra")
        self.assertEqual(rows[0]["motion"], "retrograde")
        self.assertIn("moving retrograde", rows[0]["event"])
        self.assertLessEqual(abs((utc(rows[0]["_utc"]) - utc(ref["utc"])).total_seconds()), 60.0)
        self.assertEqual(rows[0]["when"], "2026-10-25 11:10")                  # 11:09:48 -> 11:10, зимно време UTC+2

    def test_personal_series_match_the_control(self):
        for ref in load("pair-personal-event-control.json"):
            label = f"{ref['subject']} {ref['transit']} {ref['angle']} {ref['natal']}"
            with self.subTest(series=label):
                target = "User" if ref["subject"] == "A" else "Partner"
                rows = self.series(target, ref["transit"], ref["natal"], ASPECT_BY_ANGLE[ref["angle"]])
                mine = sorted(utc(x) for row in rows for x in row["_exact_utc"])
                control = sorted(utc(x["utc"]) for x in ref["exact_events"])
                self.assertEqual(len(mine), len(control), f"{label}: {mine} срещу {control}")
                for got, want in zip(mine, control):
                    self.assertLessEqual(abs((got - want).total_seconds()),
                                         tolerance_seconds(want, TRANSIT_PLANETS[ref["transit"]]), label)
                if not control:
                    # без точен момент: или аспектът не е в орбис, или е активен и се казва колко близо стига
                    orb = ref["minimum_sampled_orb_12h"]
                    if orb > APPLYING_ORB:
                        self.assertEqual(rows, [], label)
                    else:
                        self.assertEqual(len(rows), 1, label)
                        self.assertEqual(rows[0]["exact"], [])
                        self.assertAlmostEqual(rows[0]["closest_in_period"]["orb"], orb, delta=0.05, msg=label)


class BruteForceTest(CalendarBase):
    """Календарът срещу груба сила: никакви общи функции или решения със scanner.py."""

    STEP_MINUTES = 30

    @staticmethod
    def wrap(x):
        return (x + 180.0) % 360.0 - 180.0

    def brute(self, natal, zone, start, end):
        tz = pytz.timezone(zone)

        def midnight(day):
            return utc_to_jd(tz.localize(datetime(day.year, day.month, day.day)).astimezone(pytz.utc)
                             .replace(tzinfo=timezone.utc))
        p0, p1 = midnight(start), midnight(end + timedelta(days=1))
        step = self.STEP_MINUTES / 1440.0
        n = int(math.ceil((p1 - p0) / step))
        times = [p0 + i * step for i in range(n + 1)]
        longitudes = {name: [swe.calc_ut(t, pid, FLAGS)[0][0] % 360.0 for t in times]
                      for name, pid in TRANSIT_PLANETS.items()}
        found = {}
        for planet, pid in TRANSIT_PLANETS.items():
            for natal_name in NATAL_TARGETS:
                n_lon = natal["planets"][natal_name]["longitude"] % 360.0
                for aspect, angle in ASPECTS:
                    if angle == 0.0:
                        points = [n_lon]
                    elif angle == 180.0:
                        points = [(n_lon + 180.0) % 360.0]
                    else:
                        points = [(n_lon + angle) % 360.0, (n_lon - angle) % 360.0]
                    for point in points:
                        s = [self.wrap(x - point) for x in longitudes[planet]]
                        exact, active, first, last = [], False, None, None
                        for k in range(len(s) - 1):
                            if (abs(s[k]) < 90 and abs(s[k + 1]) < 90 and s[k] != 0.0
                                    and ((s[k] < 0.0 <= s[k + 1]) or (s[k] > 0.0 >= s[k + 1]))):
                                lo, hi = times[k], times[k + 1]
                                for _ in range(45):
                                    mid = (lo + hi) / 2
                                    if (self.wrap(swe.calc_ut(mid, pid, FLAGS)[0][0] - point) > 0) == (s[k] > 0):
                                        lo = mid
                                    else:
                                        hi = mid
                                exact.append((lo + hi) / 2)
                            limit = APPLYING_ORB if abs(s[k + 1]) < abs(s[k]) else SEPARATING_ORB
                            if abs(s[k]) <= limit:
                                active = True
                                first = times[k] if first is None else first
                                last = times[k]
                        if exact or active:
                            entry = found.setdefault((planet, natal_name, aspect), {"exact": [], "first": None, "last": None})
                            entry["exact"] = sorted(entry["exact"] + exact)
                            if first is not None:
                                entry["first"] = first if entry["first"] is None else min(entry["first"], first)
                                entry["last"] = last if entry["last"] is None else max(entry["last"], last)
        return found, tz, p0, p1

    def check(self, natal, target, zone, start, end, cal):
        found, tz, p0, p1 = self.brute(natal, zone, start, end)
        mine, windows = {}, {}
        for row in cal.events:
            if row["type"] == "TRANSIT" and row["target"] == target:
                key = (row["planet"], row["natal_planet"], row["aspect"])
                mine.setdefault(key, []).extend(utc_to_jd(utc(x)) for x in row["_exact_utc"])
                lo, hi = windows.get(key, (row["active_from"], row["active_to"]))
                windows[key] = (min(lo, row["active_from"]), max(hi, row["active_to"]))
        self.assertEqual(set(found), set(mine), "различни аспекти (планета, натална точка, аспект)")
        local_date = lambda jd: utc_jd_to_local(jd, tz)
        for key, entry in found.items():
            got = sorted(mine[key])
            self.assertEqual(len(entry["exact"]), len(got), f"{key}: брой точни моменти")
            for a, b in zip(entry["exact"], got):
                self.assertLess(abs(a - b) * 86400.0, 5.0, f"{key}: точният момент се разминава с груба сила")
            # прозорец: ако аспектът е влязъл в орбис вътре в периода, началото съвпада (до ден); иначе е преди периода
            window_from, window_to = windows[key]
            first, last = local_date(entry["first"]), local_date(entry["last"])
            if entry["first"] - p0 > 0.05:
                self.assertLessEqual(abs((date.fromisoformat(window_from) - first).days), 1, f"{key}: начало на прозореца")
            else:
                self.assertLessEqual(window_from, start.isoformat(), f"{key}: прозорецът трябва да започва преди периода")
            if p1 - entry["last"] > 0.05:
                self.assertLessEqual(abs((date.fromisoformat(window_to) - last).days), 1, f"{key}: край на прозореца")
            else:
                self.assertGreaterEqual(window_to, end.isoformat(), f"{key}: прозорецът трябва да свършва след периода")
        return len(found)

    def test_user_matches_brute_force(self):
        count = self.check(self.a, "User", "Europe/Sofia", date(2026, 10, 1), date(2026, 11, 30), self.cal)
        self.assertGreaterEqual(count, 10)

    def test_partner_matches_brute_force(self):
        count = self.check(self.ivan, "Partner", "Europe/Sofia", date(2026, 10, 1), date(2026, 11, 30), self.cal)
        self.assertGreaterEqual(count, 10)

    def test_three_months_in_another_zone(self):
        cal = self.scanner.build_calendar(self.ivan, "2026-01-01", "2026-03-31", IVAN_BIRTH["lat"], IVAN_BIRTH["lon"])
        self.assertEqual(cal.timezone, "Europe/Vienna")
        self.check(self.ivan, "User", "Europe/Vienna", date(2026, 1, 1), date(2026, 3, 31), cal)


class StructureTest(CalendarBase):
    def test_lunations_are_one_row_each_with_the_right_sign(self):
        rows = [e for e in self.cal.events if e["type"] in ("LUNATION", "ECLIPSE")]
        self.assertEqual([e["event"] for e in rows],
                         ["New Moon in Libra", "Full Moon in Taurus", "New Moon in Scorpio", "Full Moon in Gemini"])
        self.assertEqual(len({e["id"] for e in rows}), 4)
        self.assertEqual([e["when"][:10] for e in rows], ["2026-10-10", "2026-10-26", "2026-11-09", "2026-11-24"])
        self.assertTrue(rows[3]["position"].startswith("Gemini 2°"))       # не „Телец“ и „Близнаци“ на два реда

    def test_no_moon_ingresses_and_the_planet_ingresses_are_present(self):
        ingress = [(e["planet"], e["sign"], e["when"][:10]) for e in self.cal.events if e["type"] == "INGRESS"]
        self.assertFalse([e for e in ingress if e[0] == "Moon"])
        self.assertEqual(ingress, [("Sun", "Scorpio", "2026-10-23"), ("Venus", "Libra", "2026-10-25"),
                                   ("Sun", "Sagittarius", "2026-11-22"), ("Mars", "Virgo", "2026-11-26")])

    def test_stations(self):
        stations = [(e["planet"], e["direction"], e["when"]) for e in self.cal.events if e["type"] == "RETROGRADE"]
        self.assertEqual(stations, [
            ("Venus", "retrograde", "2026-10-03 10:16"), ("Pluto", "direct", "2026-10-16 05:40"),
            ("Mercury", "retrograde", "2026-10-24 10:13"), ("Mercury", "direct", "2026-11-13 17:54"),
            ("Venus", "direct", "2026-11-14 02:27")])

    def test_times_follow_daylight_saving_of_the_zone(self):
        # На 25.10.2026 (04:00 местно) времето в България се връща с час: UTC+3 дотогава, UTC+2 след това
        by_id = {e["id"]: e for e in self.cal.events}
        for row, hours in ((by_id["ST:Mercury:retrograde:2026-10-24"], 3), (by_id["IN:Venus:Libra:2026-10-25"], 2)):
            local = datetime.strptime(row["when"], "%Y-%m-%d %H:%M")
            exact = utc(row["_utc"]).replace(tzinfo=None)
            self.assertLess(abs((local - exact - timedelta(hours=hours)).total_seconds()), 31.0, row["id"])

    def test_zone_changes_the_display_not_the_moment(self):
        vienna = self.scanner.build_calendar(self.a, PERIOD[0], PERIOD[1], A_BIRTH["lat"], A_BIRTH["lon"],
                                             partner_chart=self.ivan, tz_name="Europe/Vienna")
        utc_calendar = self.scanner.build_calendar(self.a, PERIOD[0], PERIOD[1], A_BIRTH["lat"], A_BIRTH["lon"],
                                                   tz_name="UTC")

        def venus(c):
            return next(e for e in c.events if e["type"] == "RETROGRADE" and e["planet"] == "Venus")
        self.assertEqual(venus(vienna)["_utc"], venus(self.cal)["_utc"])
        self.assertEqual(venus(self.cal)["when"], "2026-10-03 10:16")         # София, лятно време UTC+3
        self.assertEqual(venus(vienna)["when"], "2026-10-03 09:16")           # Виена, лятно време UTC+2
        self.assertEqual(venus(utc_calendar)["when"], "2026-10-03 07:16")
        self.assertEqual((vienna.timezone, self.cal.timezone), ("Europe/Vienna", "Europe/Sofia"))

    def test_period_boundaries_follow_the_local_date(self):
        def mars(c):
            return [e for e in c.events if e["type"] == "INGRESS" and e["planet"] == "Mars"]
        # Марс влиза в Дева на 25.11 в 23:37 UTC, тоест на 26.11 в 01:37 в София
        sofia_before = self.scanner.build_calendar(self.a, "2026-11-24", "2026-11-25", 42.7, 23.3)
        sofia_after = self.scanner.build_calendar(self.a, "2026-11-26", "2026-11-27", 42.7, 23.3)
        utc_before = self.scanner.build_calendar(self.a, "2026-11-24", "2026-11-25", 42.7, 23.3, tz_name="UTC")
        self.assertEqual(mars(sofia_before), [])
        self.assertEqual([e["when"] for e in mars(sofia_after)], ["2026-11-26 01:37"])
        self.assertEqual([e["when"] for e in mars(utc_before)], ["2026-11-25 23:37"])
        for row in self.cal.events:
            if row["type"] != "TRANSIT":
                self.assertTrue(PERIOD[0] <= row["when"][:10] <= PERIOD[1], row["id"])
            self.assertNotIn("date", row)                                           # излишно за AI: има "when"
            self.assertNotIn("description", row)

    def test_zone_with_daylight_saving_at_midnight_does_not_crash(self):
        # В Хавана времето се сменя в 00:00: на 8.03.2026 такава полунощ не съществува
        cal = self.scanner.build_calendar(self.a, "2026-03-08", "2026-03-09", 23.1, -82.4, tz_name="America/Havana")
        self.assertEqual((cal.timezone, cal.months), ("America/Havana", ["2026-03"]))
        for row in cal.events:
            self.assertTrue("2026-03-08" <= (row["_sort"] if row["type"] == "TRANSIT" else row["when"])[:10] <= "2026-03-09"
                            or row["type"] == "TRANSIT", row["id"])

    def test_today_in_zone_is_an_iso_date(self):
        text = period_report.today_in_zone("Europe/Sofia")
        self.assertEqual(datetime.strptime(text, "%Y-%m-%d").strftime("%Y-%m-%d"), text)
        self.assertEqual(len(period_report.today_in_zone("Nowhere/Invalid")), 10)       # непозната зона: UTC

    def test_invalid_period_is_rejected(self):
        with self.assertRaises(ValueError):
            self.scanner.build_calendar(self.a, "2026-11-30", "2026-10-01", 42.7, 23.3)

    def test_eclipses_are_lunations_with_an_eclipse(self):
        cal = self.scanner.build_calendar(self.a, "2026-08-01", "2026-08-31", 42.7, 23.3)
        rows = [e for e in cal.events if e["type"] in ("LUNATION", "ECLIPSE")]
        self.assertEqual([(e["type"], e["when"][:10]) for e in rows],
                         [("ECLIPSE", "2026-08-12"), ("ECLIPSE", "2026-08-28")])
        self.assertEqual([e["eclipse_kind"] for e in rows], ["total", "partial"])
        self.assertTrue(rows[0]["event"].startswith("Solar Eclipse in Leo"))
        self.assertTrue(rows[1]["event"].startswith("Lunar Eclipse in Pisces"))

    def test_aspect_series_semantics(self):
        neptune = self.series("Partner", "Neptune", "Neptune", "Square")
        self.assertEqual(len(neptune), 1)                                   # един ред, не 61 дневни
        row = neptune[0]
        self.assertEqual([x["when"][:10] for x in row["exact"]], ["2026-11-21"])
        self.assertEqual(row["exact"][0]["motion"], "retrograde")
        self.assertTrue(row["exact_outside_period"][0].startswith("2027-01-0"))     # второто преминаване е след периода
        self.assertLess(row["active_from"], PERIOD[0])                              # прозорецът започва преди периода
        self.assertEqual(row["natal_planet_natal_house"], self.ivan["planets"]["Neptune"]["house"])
        self.assertIn(row["transit_planet_natal_house"], range(1, 13))

        loop = self.series("User", "Uranus", "Mercury", "Trine")[0]                 # ретроградна примка
        self.assertEqual([x["motion"] for x in loop["exact"]], ["retrograde"])
        self.assertTrue(loop["exact_outside_period"][0].startswith("2026-07-2"))
        self.assertLess(loop["active_from"], "2026-07-31")                          # прозорецът обхваща и двете преминавания

        before = self.series("User", "Mars", "Moon", "Square")[0]                   # точен преди периода
        self.assertEqual(before["exact"], [])
        self.assertTrue(before["exact_outside_period"][0].startswith("2026-09-30"))
        self.assertEqual(before["closest_in_period"]["at"], "start_of_period")      # без час: не е събитие
        self.assertNotIn("when", before["closest_in_period"])
        self.assertAlmostEqual(before["closest_in_period"]["orb"], 0.41, delta=0.02)
        self.assertNotIn("turns_back", before)

    def test_aspects_that_turn_back_are_active_but_never_exact(self):
        for target, planet, natal, aspect, orb in (("User", "Neptune", "Jupiter", "Square", 0.7),
                                                   ("Partner", "Saturn", "Venus", "Sextile", 1.14)):
            with self.subTest(aspect=f"{planet} {aspect} {natal}"):
                row = self.series(target, planet, natal, aspect)[0]
                self.assertEqual(row["exact"], [])
                self.assertTrue(row["turns_back"])
                self.assertNotIn("exact_outside_period", row)
                self.assertAlmostEqual(row["closest_in_period"]["orb"], orb, delta=0.05)
        far = self.series("User", "Pluto", "Mercury", "Conjunction")[0]             # активен, но точен чак след периода
        self.assertEqual(far["exact"], [])
        self.assertNotIn("turns_back", far)
        self.assertTrue(far["exact_outside_period"])

    def test_aspect_that_is_not_in_orb_has_no_row(self):
        self.assertEqual(self.series("User", "Mars", "Mars", "Square"), [])        # 40° от квадратура: контролът го отхвърля

    def test_rows_are_compact_and_ids_are_unique(self):
        self.assertLessEqual(len(self.cal.events), 60)                              # преди 407 реда за същия период
        self.assertEqual(len({e["id"] for e in self.cal.events}), len(self.cal.events))
        for row in self.cal.events:
            if row["type"] == "TRANSIT":
                self.assertLessEqual(len(row["exact"]), 3)

    def test_public_rows_hide_the_internal_fields(self):
        for row in self.cal.public_events():
            self.assertFalse([k for k in row if k.startswith("_")], row["id"])
        for month in self.cal.months:
            for row in self.cal.month_events(month):
                self.assertFalse([k for k in row if k.startswith("_")], row["id"])

    def test_months_and_the_state_of_an_aspect_in_each_month(self):
        self.assertEqual(self.cal.months, ["2026-10", "2026-11"])
        self.assertEqual(self.cal.months_with_events(), ["2026-10", "2026-11"])
        october = {e["id"]: e for e in self.cal.month_events("2026-10")}
        november = {e["id"]: e for e in self.cal.month_events("2026-11")}
        for row in october.values():
            if row["type"] != "TRANSIT":
                self.assertTrue(row["when"].startswith("2026-10"), row["id"])
        for row in november.values():
            if row["type"] != "TRANSIT":
                self.assertTrue(row["when"].startswith("2026-11"), row["id"])
        short = "AS:Partner:Mars:Square:Pluto"                                       # точен 01.10, активен до 03.10
        self.assertEqual(october[short]["state_in_month"], "exact_in_month")
        self.assertEqual(october[short]["exact_this_month"], [october[short]["exact"][0]["when"]])
        self.assertNotIn(short, november)
        slow = "AS:Partner:Neptune:Square:Neptune"                                  # точен 21.11
        self.assertEqual(october[slow]["state_in_month"], "approaching")
        self.assertEqual(october[slow]["exact_this_month"], [])
        self.assertEqual(november[slow]["state_in_month"], "exact_in_month")
        loop = "AS:User:Uranus:Trine:Mercury"                                       # точен 31.10, активен до 25.11
        self.assertEqual(october[loop]["state_in_month"], "exact_in_month")
        self.assertEqual(november[loop]["state_in_month"], "separating")
        never = "AS:User:Neptune:Square:Jupiter"                                    # не става точен
        self.assertEqual(november[never]["state_in_month"], "active")

    def test_a_three_month_pair_calendar_is_fast_enough(self):
        started = time.perf_counter()
        cal = self.scanner.build_calendar(self.a, "2026-10-01", "2026-12-31", A_BIRTH["lat"], A_BIRTH["lon"],
                                          partner_chart=self.ivan)
        self.assertLess(time.perf_counter() - started, 10.0)
        self.assertEqual(cal.months, ["2026-10", "2026-11", "2026-12"])


class FakeInterpreter:
    """Подменя само двата AI вика; календарът, редът на стъпките и повторните опити са истински."""

    def __init__(self, fail_month=None, month_failures=0, overview_failures=0, empty_overview=False):
        self.fail_month, self.month_failures = fail_month, month_failures
        self.overview_failures, self.empty_overview = overview_failures, empty_overview
        self.month_calls, self.overview_calls = [], []

    async def _process_monthly_chunk(self, **kwargs):
        self.month_calls.append(kwargs["month"])
        if kwargs["month"] == self.fail_month and self.month_failures > 0:
            self.month_failures -= 1
            raise RuntimeError("провайдърът не отговори")
        return f"Текст за {kwargs['month']}"

    async def compose_period_overview(self, **kwargs):
        self.overview_calls.append(kwargs)
        if self.overview_failures > 0:
            self.overview_failures -= 1
            raise RuntimeError("провайдърът не отговори")
        return "" if self.empty_overview else "Общият преглед"


class OrchestrationTest(CalendarBase):
    def run_report(self, interpreter, calendar=None):
        async def go():
            steps = []
            async for step in period_report.run_period_report(
                    interpreter, calendar=calendar or self.cal, natal_chart=self.a, partner_chart=self.ivan,
                    report_type="love", user_name="A", partner_name="Иван", question="Общ проект",
                    partner_gender="male", report_date="2026-10-09", retry_pause=0):
                steps.append(step)
            return steps
        return asyncio.run(go())

    def test_steps_come_in_order_and_the_overview_gets_everything(self):
        fake = FakeInterpreter()
        steps = self.run_report(fake)
        self.assertEqual([s["type"] for s in steps], ["month_start", "month_complete", "month_start", "month_complete",
                                                      "overview_start", "overview_complete", "finished"])
        self.assertEqual([s["month"] for s in steps if s["type"] == "month_start"], ["Октомври 2026", "Ноември 2026"])
        overview_args = fake.overview_calls[0]
        self.assertEqual([t for t, _ in overview_args["month_texts"]], ["Октомври 2026", "Ноември 2026"])
        self.assertEqual(overview_args["zone"], "Europe/Sofia")
        self.assertEqual(overview_args["period"], PERIOD)
        self.assertTrue(overview_args["has_partner"])
        self.assertFalse([k for row in overview_args["calendar_rows"] for k in row if k.startswith("_")])
        self.assertEqual(steps[-1]["overview"], "Общият преглед")

    def test_one_automatic_retry_saves_the_month(self):
        fake = FakeInterpreter(fail_month="2026-11", month_failures=1)
        steps = self.run_report(fake)
        self.assertEqual(steps[-1]["type"], "finished")
        self.assertEqual(fake.month_calls, ["2026-10", "2026-11", "2026-11"])

    def test_a_month_that_fails_twice_fails_the_report(self):
        fake = FakeInterpreter(fail_month="2026-11", month_failures=2)
        with self.assertRaises(period_report.ForecastGenerationError) as ctx:
            self.run_report(fake)
        self.assertEqual(ctx.exception.stage, "month:2026-11")
        self.assertEqual(fake.overview_calls, [])               # без месец няма общ преглед

    def test_the_overview_gets_one_retry_and_an_empty_one_fails(self):
        self.assertEqual(self.run_report(FakeInterpreter(overview_failures=1))[-1]["type"], "finished")
        with self.assertRaises(period_report.ForecastGenerationError) as ctx:
            self.run_report(FakeInterpreter(empty_overview=True))
        self.assertEqual(ctx.exception.stage, "overview")
        with self.assertRaises(period_report.ForecastGenerationError):
            self.run_report(FakeInterpreter(overview_failures=2))

    def test_saved_content_and_markdown_put_the_overview_first(self):
        months = [("Октомври 2026", "<p>о</p>"), ("Ноември 2026", "<p>н</p>")]
        saved = period_report.saved_content("<p>Преглед</p>", months)
        self.assertTrue(saved.startswith("<h2>Общ преглед на периода</h2>\n<p>Преглед</p>\n\n<h2>Октомври 2026</h2>"))
        text = period_report.markdown_report("Преглед", months, has_partner=True, question="Въпрос",
                                             user_display="A", partner_display="Иван")
        self.assertTrue(text.startswith("# Прогноза за Връзка (Октомври 2026 - Ноември 2026)"))
        self.assertLess(text.index("## Общ преглед на периода"), text.index("## Прогноза за Октомври 2026"))
        self.assertIn("**Анализ за A и Иван**", text)


class PromptTest(CalendarBase):
    """Подканите към AI с истинския интерпретатор: подменен е само мрежовият вик."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.interp = ai_interpreter.AIInterpreter(api_key="test-dummy")

    def run_calls(self, calendar, pair=True, question="Общ проект"):
        calls = []

        async def fake_call(_self, system_prompt, user_prompt, max_tokens, **kwargs):
            calls.append((system_prompt, user_prompt))
            return "<p>Текст</p>"

        async def go():
            async for _ in period_report.run_period_report(
                    self.interp, calendar=calendar, natal_chart=self.a, partner_chart=self.ivan if pair else None,
                    report_type="love", user_name="A", partner_name="Иван" if pair else None, question=question,
                    partner_gender="male", report_date="2026-10-09", retry_pause=0):
                pass
        with mock.patch.object(ai_interpreter.AIInterpreter, "_call_api", new=fake_call):
            asyncio.run(go())
        return calls

    def test_month_prompts_carry_zone_report_date_and_the_calendar_rules(self):
        calls = self.run_calls(self.cal)
        self.assertEqual(len(calls), 3)                                             # два месеца и общ преглед
        for (system, user), month in zip(calls[:2], ("2026-10", "2026-11")):
            self.assertIn("REPORT DATE: 2026-10-09 (Europe/Sofia)", user)
            self.assertIn("WHOLE REPORT PERIOD: 2026-10-01 to 2026-11-30 (Europe/Sofia)", user)
            self.assertIn(f"TIMELINE EVENTS FOR {month}", user)
            self.assertIn("ONE row per aspect", system)
            self.assertIn("NEVER write that such an aspect is exact", system)
            self.assertIn("Never shift a date, never convert to UTC", system)
            self.assertNotIn("'orb')", system)                                       # старото поле „orb“ го няма
            events = section_json(user, f"TIMELINE EVENTS FOR {month}")
            self.assertTrue(events)
            for row in events:
                self.assertFalse([k for k in row if k.startswith("_")], row["id"])
                if row["type"] == "TRANSIT":
                    self.assertIn("state_in_month", row)
                    self.assertIn("exact_this_month", row)
                else:
                    self.assertTrue(row["when"].startswith(month))
        self.assertIn("Partner", {r["target"] for r in section_json(calls[0][1], "TIMELINE EVENTS FOR 2026-10")
                                  if r["type"] == "TRANSIT"})
        for text in (calls[0][1], calls[1][1], calls[2][1]):
            for internal in ("_utc", "_sort", "_exact_utc", "_pass_count"):
                self.assertNotIn(internal, text)

    def test_overview_prompt_has_calendar_months_question_and_no_natal_data(self):
        system, user = self.run_calls(self.cal)[2]
        self.assertIn("MODE: PERIOD OVERVIEW", system)
        self.assertIn("450 words", system)
        self.assertIn("Never describe an aspect as exact", system)
        self.assertIn("what each of them should do", system)
        self.assertIn("REPORT DATE: 2026-10-09 (Europe/Sofia)", user)
        self.assertIn("--- PERIOD CALENDAR (CALCULATED) ---", user)
        self.assertIn("### Октомври 2026\n<p>Текст</p>", user)
        self.assertIn("### Ноември 2026\n<p>Текст</p>", user)
        self.assertIn("User Question: Общ проект", user)
        self.assertNotIn("NATAL CHART", user)
        calendar_rows = section_json(user, "PERIOD CALENDAR (CALCULATED)")
        self.assertEqual(len(calendar_rows), len(self.cal.events))
        neptune = next(r for r in calendar_rows if r["id"] == "AS:Partner:Neptune:Square:Neptune")
        self.assertEqual(len(neptune["exact"]), 1)

    def test_single_person_overview_is_shorter_and_has_no_pair_line(self):
        single = self.scanner.build_calendar(self.a, PERIOD[0], PERIOD[1], A_BIRTH["lat"], A_BIRTH["lon"])
        system, user = self.run_calls(single, pair=False)[2]
        self.assertIn("350 words", system)
        self.assertNotIn("what each of them should do", system)
        self.assertNotIn("second_person", user)


class EndpointTest(unittest.TestCase):
    """Крайните точки: стрийм и обикновена заявка, успех и провал."""

    @classmethod
    def setUpClass(cls):
        cls.client = TestClient(main.app)

    def setUp(self):
        limiter.reset()

    def body(self, end="2026-11-30"):
        return {**CHART, "name": "Аз", "is_dynamic": True, "target_date": "2026-10-01", "end_date": end}

    def month_ok(self):
        return mock.patch.object(main.ai_interpreter, "_process_monthly_chunk", mock.AsyncMock(return_value="<p>Месец</p>"))

    def overview_ok(self):
        return mock.patch.object(main.ai_interpreter, "compose_period_overview",
                                 mock.AsyncMock(return_value="<p>Преглед</p>"))

    def user_id(self, headers):
        return self.client.get("/me", headers=headers).json()["id"]

    def test_stream_sends_months_then_overview_and_saves_it_first(self):
        h = register_and_login(self.client, "p9-stream@test.bg")
        with self.month_ok(), self.overview_ok():
            r = self.client.post("/interpret-stream", json=self.body(), headers=h)
        steps = sse_events(r.text)
        self.assertEqual([s["type"] for s in steps], ["start", "month_start", "month_complete", "month_start",
                                                      "month_complete", "overview_start", "overview_complete", "complete"])
        self.assertEqual(steps[0]["total_months"], 2)
        self.assertEqual(steps[6]["text"], "<p>Преглед</p>")
        reports = self.client.get("/reports", headers=h).json()
        self.assertEqual(len(reports), 1)
        db = SessionLocal()
        try:
            content = db.get(Report, reports[0]["id"]).content
        finally:
            db.close()
        self.assertTrue(content.startswith("<h2>Общ преглед на периода</h2>\n<p>Преглед</p>"))
        self.assertLess(content.index("Общ преглед"), content.index("Октомври 2026"))

    @mock.patch.dict(os.environ, {"COINS_ENFORCED": "1"})
    def test_failed_month_means_no_report_and_no_charge(self):
        h = register_and_login(self.client, "p9-month-fail@test.bg")
        before = self.client.get("/me", headers=h).json()["coins"]
        broken = mock.AsyncMock(side_effect=RuntimeError("провайдърът не отговори"))
        with mock.patch.object(period_report, "RETRY_PAUSE_SECONDS", 0.0), \
                mock.patch.object(main.ai_interpreter, "_process_monthly_chunk", broken), self.overview_ok():
            r = self.client.post("/interpret-stream", json=self.body(), headers=h)
        steps = sse_events(r.text)
        self.assertEqual(steps[-1], {"type": "error", "code": 502, "message": period_report.USER_MESSAGE})
        self.assertNotIn("complete", [s["type"] for s in steps])
        self.assertNotIn("провайдърът", r.text)                                    # техническото съобщение не стига до клиента
        self.assertEqual(broken.await_count, 2)                                    # опит и един повторен опит
        self.assertEqual(self.client.get("/reports", headers=h).json(), [])
        me = self.client.get("/me", headers=h).json()
        self.assertEqual(me["coins"], before)
        db = SessionLocal()
        try:
            failed = db.query(Event).filter(Event.name == "analysis_failed", Event.user_id == me["id"]).all()
        finally:
            db.close()
        self.assertEqual(len(failed), 1)
        self.assertEqual(failed[0].props["stage"], "month:2026-10")

    @mock.patch.dict(os.environ, {"COINS_ENFORCED": "1"})
    def test_failed_overview_means_no_report_and_no_charge(self):
        h = register_and_login(self.client, "p9-overview-fail@test.bg")
        before = self.client.get("/me", headers=h).json()["coins"]
        broken = mock.AsyncMock(side_effect=RuntimeError("провайдърът не отговори"))
        with mock.patch.object(period_report, "RETRY_PAUSE_SECONDS", 0.0), self.month_ok(), \
                mock.patch.object(main.ai_interpreter, "compose_period_overview", broken):
            r = self.client.post("/interpret-stream", json=self.body(), headers=h)
        steps = sse_events(r.text)
        self.assertEqual(steps[-1]["type"], "error")
        self.assertEqual(steps[-1]["message"], period_report.USER_MESSAGE)
        self.assertEqual(self.client.get("/reports", headers=h).json(), [])
        self.assertEqual(self.client.get("/me", headers=h).json()["coins"], before)

    def test_plain_endpoint_returns_one_text_with_the_overview(self):
        h = register_and_login(self.client, "p9-plain@test.bg")
        with self.month_ok(), self.overview_ok():
            r = self.client.post("/interpret", json=self.body("2026-10-31"), headers=h)
        self.assertEqual(r.status_code, 200, r.text)
        text = r.json()["interpretation"]
        self.assertTrue(text.startswith("# Астрологична Прогноза (Октомври 2026 - Октомври 2026)"))
        self.assertLess(text.index("## Общ преглед на периода"), text.index("## Прогноза за Октомври 2026"))
        self.assertEqual(len(self.client.get("/reports", headers=h).json()), 1)

    def test_plain_endpoint_failure_is_502_and_saves_nothing(self):
        h = register_and_login(self.client, "p9-plain-fail@test.bg")
        broken = mock.AsyncMock(side_effect=RuntimeError("провайдърът не отговори"))
        with mock.patch.object(period_report, "RETRY_PAUSE_SECONDS", 0.0), \
                mock.patch.object(main.ai_interpreter, "_process_monthly_chunk", broken), self.overview_ok():
            r = self.client.post("/interpret", json=self.body("2026-10-31"), headers=h)
        self.assertEqual(r.status_code, 502)
        self.assertEqual(r.json()["detail"], period_report.USER_MESSAGE)
        self.assertEqual(self.client.get("/reports", headers=h).json(), [])


if __name__ == "__main__":
    unittest.main()
