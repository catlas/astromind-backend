"""
Прогноза за период по карти (period_cards.py): датите, часовете, притежателите, движението и смяната на знак пише кодът, AI само тълкува.

Постоянни регресионни тестове за старите грешки на периодния режим (двумесечна прогноза, погрешна опозиция/секстил, ретрограден
Юпитер, Марс в предишен знак, объркан притежател в двойка, период през смяната на часа), независима геометрична сверка на всеки
точен момент (местен час -> UTC през zoneinfo, не през кода на проекта) и контролирани тестове на резервния и частичния път с проверка
на крайния текст и на таксуването.
"""
import asyncio
import os
import re
import unittest
from datetime import datetime, timedelta, timezone
from unittest import mock
from zoneinfo import ZoneInfo

import testenv  # noqa: F401  (трябва да е преди engine)
from testenv import CHART, give_deposit, period_reply, register_and_login
import ai_interpreter
import engine
import factpack
import natal_pair
import period_cards
import period_report
import regression_support as rs
import report_guard
import text_check as tc
from fastapi.testclient import TestClient
from rate_limit import limiter
import main
from database import SessionLocal, User
from scanner import TransitScanner

ZONE = "Europe/Sofia"
BAD_CARD = "Точно в 13:37 ще стане ясно какво да правите."
GOOD_CARD = "Това изисква търпение и ясни граници в разговорите."
BAD_MONTH = "Марс е планетата на конфликтите, затова избягвайте спорове."          # планета в съвета за месеца
SOFIA = ZoneInfo(ZONE)
NAME_A = factpack.display_name(rs.A_NAME, "A")                  # както ги пише генерацията: тире и интервали се чистят
NAME_IVAN = factpack.display_name(rs.IVAN, "B")


def sep(a, b):
    d = abs(a - b) % 360
    return min(d, 360 - d)


def utc_of(local: str) -> datetime:
    """„2026-10-24 09:36“ в зоната на прогнозата -> UTC, независимо от кода на проекта."""
    return datetime.strptime(local, "%Y-%m-%d %H:%M").replace(tzinfo=SOFIA).astimezone(timezone.utc)


def longitudes(moment_utc: datetime, lat: float, lon: float) -> dict:
    raw = engine.get_engine().calculate_chart(moment_utc.strftime("%Y-%m-%d"), moment_utc.strftime("%H:%M:%S"), lat, lon, zone="UTC")
    return {k: v["longitude"] for k, v in raw["planets"].items() if v.get("longitude") is not None}


def natal(key):
    return {k: v["longitude"] for k, v in rs.chart(key)["planets"].items() if v.get("longitude") is not None}


def dst_calendar():
    """Двойка в София: 20–31 октомври 2026 включва смяната на часа (неделя 25 октомври, 04:00 -> 03:00)."""
    key = ("dst-cal",)
    if key not in rs._cache:
        rs._cache[key] = TransitScanner(engine_instance=engine.get_engine()).build_calendar(
            rs.chart("A"), "2026-10-20", "2026-10-31", rs.A_BIRTH["lat"], rs.A_BIRTH["lon"], partner_chart=rs.chart("Ivan"), tz_name=ZONE)
    return rs._cache[key]


def cards_of(calendar, pair, limit=200):
    first, second = (NAME_A, NAME_IVAN) if pair else (NAME_IVAN, "")
    rel = natal_pair.Relevance("general", "", "")
    out = []
    for i, month in enumerate(calendar.months_with_events(), 1):
        out += period_cards.build_month_cards(calendar, month, i, first, second, rel, limit=limit)
    return out


class HeadingsTest(unittest.TestCase):
    """Всяко заглавие, написано от кода, минава през проверката на текста срещу календара."""

    def sweep(self, pair):
        end = "2027-01-31"                                    # октомври–януари: преминава през смяната на часа
        cal, facts = rs.calendar(pair, end), rs.facts("period", pair, end)
        cards = cards_of(cal, pair)
        self.assertGreater(len(cards), 30)
        for card in cards:
            hard = [v for v in tc.check_text(card.heading, period_cards.restrict_events(facts, [card])) if v.severity == "hard"]
            self.assertEqual(hard, [], card.heading)

    def test_every_heading_of_a_pair_report_passes_the_checker(self):
        self.sweep(True)

    def test_every_heading_of_a_single_report_passes_the_checker(self):
        self.sweep(False)

    def test_exact_aspect_nearest_approach_and_interval_are_different_things(self):
        base = {"type": "TRANSIT", "target": "Partner", "planet": "Pluto", "natal_planet": "Pluto", "aspect": "Square",
                "active_from": "2026-09-06", "active_to": "2026-10-16"}
        exact = period_cards.transit_heading({**base, "exact": [{"when": "2026-10-05 10:00", "motion": "direct"}]}, "Иван")
        self.assertIn("точен аспект, движение: директно", exact)
        self.assertNotIn("най-близко", exact)
        nearest = period_cards.transit_heading({**base, "exact": [], "no_exact_pass": True,
                                                "closest_in_period": {"when": "2026-10-16 05:40", "orb": 1.14}}, "Иван")
        self.assertIn("най-близко приближение на 16 октомври, 05:40 (отстояние 1,14°)", nearest)
        self.assertIn("точен аспект в периода няма", nearest)
        self.assertNotIn("движение:", nearest)                       # без точен момент няма и посока на движение
        self.assertNotIn("орб", nearest.replace("орбис", ""))        # приближението не е орбис
        boundary = period_cards.transit_heading({**base, "exact": [], "closest_in_period": {"at": "start_of_period", "orb": 0.41},
                                                 "exact_outside_period": ["2026-09-30 07:14"]}, "Иван")
        self.assertIn("най-близко приближение в началото на периода", boundary)
        self.assertIn("Точен аспект извън периода: 30 септември", boundary)
        self.assertIn("Интервал на действие: от 6 септември до 16 октомври", exact)
        clipped = period_cards.transit_heading({**base, "exact": [], "closest_in_period": {"at": "end_of_period", "orb": 0.8},
                                                "window_clipped": True}, "Иван")
        self.assertIn("интервалът излиза извън периода", clipped)

    def test_the_overview_says_that_a_window_is_not_the_end_of_a_life_process(self):
        cal = rs.calendar(False)
        cards = cards_of(cal, False)
        for c in cards:
            c.text = GOOD_CARD
        text = period_cards.assemble_overview(cards[:6], {"SUMMARY": "а", "ADVICE": "- б", "CONCLUSION": "в"}, cal, "", cal.months_with_events())
        self.assertIn("не край на житейски процес", text)
        self.assertIn("орбис до 1,5°", text)

    def test_stations_ingresses_and_lunations_are_written_by_the_code(self):
        headings = [c.heading for c in cards_of(rs.calendar(True, "2027-01-31"), True)]
        self.assertTrue(any(re.search(r"става (ретрограден|ретроградна|директен|директна)", h) for h in headings))
        self.assertTrue(any("навлиза в" in h and "идвайки от" in h for h in headings))
        self.assertTrue(any(" – Новолуние" in h or " – Пълнолуние" in h for h in headings))


class RepeatedHourTest(unittest.TestCase):
    """В нощта на връщането на часовника (25.10.2026 в София: 04:00 -> 03:00) „03:30“ се случва два пъти: часът се означава."""

    def notes(self):
        return period_cards.TimeNotes(ZONE, {"2026-10-25 03:30": "2026-10-25T00:30:00Z", "2026-10-25 03:31": "2026-10-25T01:31:00Z",
                                             "2026-10-24 12:00": "2026-10-24T09:00:00Z", "2026-10-25 12:00": "2026-10-25T10:00:00Z",
                                             "2026-10-25 02:30": "2026-10-24T23:30:00Z"})

    def test_the_repeated_hour_is_marked_summer_or_winter_and_nothing_else_is(self):
        n = self.notes()
        self.assertEqual(n.season("2026-10-25 03:30"), " (лятно време)")          # 00:30 UTC = 03:30 EEST (първото)
        self.assertEqual(n.season("2026-10-25 03:31"), " (зимно време)")          # 01:31 UTC = 03:31 EET (второто)
        for plain in ("2026-10-24 12:00", "2026-10-25 12:00", "2026-10-25 02:30", "2026-10-25 09:09"):
            self.assertEqual(n.season(plain), "", plain)

    def test_the_marker_is_in_the_heading_and_the_two_moments_are_one_hour_apart_in_utc(self):
        row = {"type": "TRANSIT", "target": "User", "planet": "Mars", "natal_planet": "Pluto", "aspect": "Square",
               "exact": [{"when": "2026-10-25 03:31", "motion": "direct"}], "active_from": "2026-10-23", "active_to": "2026-10-27"}
        heading = period_cards.transit_heading(row, "Иван", self.notes())
        self.assertIn("25 октомври, 03:31 (зимно време) – Транзитният Марс", heading)
        self.assertNotIn("(лятно време)", heading)
        self.assertEqual(utc_of("2026-10-25 03:30").hour, 0)                          # лятно (fold=0)
        second = datetime(2026, 10, 25, 3, 30, tzinfo=SOFIA, fold=1).astimezone(timezone.utc)
        self.assertEqual(second.hour, 1)                                              # зимно (fold=1): точно един час по-късно

    def test_the_checker_accepts_a_heading_with_the_marker(self):
        facts = rs.facts("period", True, "2026-10-31")
        cal = rs.calendar(True, "2026-10-31")
        card = next(c for c in cards_of(cal, True, limit=8) if re.match(r"\d{1,2} \S+, \d{2}:\d{2} – ", c.heading))
        time = re.search(r"\d{2}:\d{2}", card.heading).group(0)
        marked = card.heading.replace(time, f"{time} (зимно време)", 1)
        self.assertNotEqual(marked, card.heading)
        self.assertEqual([v for v in tc.check_text(marked, period_cards.restrict_events(facts, [card])) if v.severity == "hard"], [])


class EclipseTest(unittest.TestCase):
    def test_eclipses_are_cards_and_not_lost_among_the_lunations(self):
        cal = TransitScanner(engine_instance=engine.get_engine()).build_calendar(
            rs.chart("A"), "2026-08-01", "2026-08-31", rs.A_BIRTH["lat"], rs.A_BIRTH["lon"], partner_chart=rs.chart("Ivan"), tz_name=ZONE)
        facts = tc.build_facts(mode="period", user_name=rs.A_NAME, natal_chart=rs.chart("A"), partner_name=rs.IVAN, partner_chart=rs.chart("Ivan"),
                               calendar=cal, report_date="2026-07-30")
        cards = cards_of(cal, True, limit=period_cards.CARDS_PER_MONTH)
        eclipses = [c for c in cards if c.row["event"]["type"] == "ECLIPSE"]
        self.assertEqual(len(eclipses), 2)                                    # 12 август (слънчево) и 28 август (лунно)
        self.assertTrue(any("Слънчево затъмнение в Лъв" in c.heading for c in eclipses))
        self.assertTrue(any("Лунно затъмнение в Риби" in c.heading for c in eclipses))
        for c in eclipses:
            self.assertEqual([v for v in tc.check_text(c.heading, period_cards.restrict_events(facts, [c])) if v.severity == "hard"], [])


class GeometryTest(unittest.TestCase):
    """Независима сверка: местният час се превръща в UTC през zoneinfo и позициите се смятат наново."""

    def exact_passes(self, calendar):
        for row in calendar.public_events():
            if row["type"] == "TRANSIT":
                for p in row.get("exact", []):
                    yield row, p

    def test_the_period_really_crosses_the_change_of_the_clock(self):
        offsets = {utc_of(p["when"]).hour - int(p["when"][11:13]) for _row, p in self.exact_passes(dst_calendar())}
        self.assertEqual(utc_of("2026-10-24 12:00").utcoffset(), timedelta(0))
        self.assertEqual(SOFIA.utcoffset(datetime(2026, 10, 24, 12, 0)), timedelta(hours=3))
        self.assertEqual(SOFIA.utcoffset(datetime(2026, 10, 26, 12, 0)), timedelta(hours=2))
        before = [p for _r, p in self.exact_passes(dst_calendar()) if p["when"] < "2026-10-25 03:00"]
        after = [p for _r, p in self.exact_passes(dst_calendar()) if p["when"] > "2026-10-25 04:00"]
        self.assertTrue(before and after, "няма точни моменти от двете страни на смяната на часа")
        self.assertEqual(len(offsets) >= 1, True)

    def test_every_exact_moment_matches_the_aspect_and_the_owner_in_utc(self):
        cal = dst_calendar()
        a, ivan = natal("A"), natal("Ivan")
        checked = 0
        sides = set()
        for row, p in self.exact_passes(cal):
            moment = utc_of(p["when"])
            sky = longitudes(moment, rs.A_BIRTH["lat"], rs.A_BIRTH["lon"])
            owner, other = (a, ivan) if row["target"] == "User" else (ivan, a)
            if row["planet"] not in sky or row["natal_planet"] not in owner:
                continue
            angle = row["angle_deg"]
            self.assertAlmostEqual(sep(sky[row["planet"]], owner[row["natal_planet"]]), angle, delta=0.05, msg=f"{row['id']} {p['when']}")
            # притежателят: ако двете натални планети не са почти на едно място, чуждата не е на този аспект
            if sep(owner[row["natal_planet"]], other.get(row["natal_planet"], 999)) > 0.5:
                self.assertGreater(abs(sep(sky[row["planet"]], other[row["natal_planet"]]) - angle), 0.05, msg=f"притежател {row['id']}")
            checked += 1
            sides.add(p["when"] > "2026-10-25 04:00")
        self.assertGreaterEqual(checked, 5)
        self.assertEqual(sides, {True, False}, "сверени са моменти и от двете страни на смяната на часа")

    def test_the_motion_of_every_exact_moment_matches_the_sky(self):
        cal = dst_calendar()
        for row, p in self.exact_passes(cal):
            if row["planet"] in ("Sun", "Moon"):
                continue
            moment = utc_of(p["when"])
            before = longitudes(moment - timedelta(hours=6), rs.A_BIRTH["lat"], rs.A_BIRTH["lon"]).get(row["planet"])
            after = longitudes(moment + timedelta(hours=6), rs.A_BIRTH["lat"], rs.A_BIRTH["lon"]).get(row["planet"])
            if before is None or after is None:
                continue
            delta = (after - before + 180) % 360 - 180
            self.assertEqual(p["motion"], "direct" if delta > 0 else "retrograde", msg=f"{row['id']} {p['when']}")

    def test_the_motion_words_in_the_headings_follow_the_calendar_for_a_retrograde_planet(self):
        retro = [c for c in cards_of(rs.calendar(True, "2027-01-31"), True)
                 if c.row["event"]["type"] == "TRANSIT" and any(x["motion"] == "retrograde" for x in c.row["event"].get("exact", []))]
        self.assertTrue(retro)
        for c in retro:
            self.assertIn("ретроградно", c.heading)

    def test_a_planet_enters_the_sign_the_heading_names_and_leaves_the_previous_one(self):
        cal = rs.calendar(True, "2027-01-31")
        signs = ["Овен", "Телец", "Близнаци", "Рак", "Лъв", "Дева", "Везни", "Скорпион", "Стрелец", "Козирог", "Водолей", "Риби"]
        english = ["Aries", "Taurus", "Gemini", "Cancer", "Leo", "Virgo", "Libra", "Scorpio", "Sagittarius", "Capricorn", "Aquarius", "Pisces"]
        checked = 0
        for card in cards_of(cal, True):
            row = card.row["event"]
            if row["type"] != "INGRESS" or row["planet"] not in ("Mars", "Venus", "Mercury", "Jupiter", "Saturn", "Sun"):
                continue
            moment = utc_of(row["when"])
            kw = (rs.A_BIRTH["lat"], rs.A_BIRTH["lon"])
            now = longitudes(moment + timedelta(minutes=30), *kw)[row["planet"]]
            before = longitudes(moment - timedelta(minutes=30), *kw)[row["planet"]]
            self.assertEqual(english[int(now // 30)], row["sign"], row["id"])
            self.assertEqual(english[int(before // 30)], row["from_sign"], row["id"])
            self.assertRegex(card.heading, rf"навлиза (в|във) {signs[english.index(row['sign'])]}")
            self.assertIn(f"идвайки от {signs[english.index(row['from_sign'])]}", card.heading)
            checked += 1
        self.assertGreater(checked, 2)

    def test_pluto_of_one_person_is_never_named_as_the_pluto_of_the_other(self):
        cards = [c for c in cards_of(rs.calendar(True, "2027-01-31"), True) if c.row["event"]["type"] == "TRANSIT"]
        self.assertTrue(cards)
        for c in cards:
            row = c.row["event"]
            owner = NAME_A if row["target"] == "User" else NAME_IVAN
            other = NAME_IVAN if row["target"] == "User" else NAME_A
            self.assertIn(f" на {owner}:", c.heading)
            self.assertNotIn(f" на {other}:", c.heading)
            self.assertEqual(c.row["owner"], owner)
            self.assertEqual(c.row["other_person"], other)


class CardScopeTest(unittest.TestCase):
    """Тълкуването на карта няма право на нищо, което е факт от заглавието, и на нищо, което е за друга карта или друг човек."""

    @classmethod
    def setUpClass(cls):
        cards = cards_of(rs.calendar(True), True)
        cls.sextile = next(c for c in cards if c.row["aspect"] == "sextile" and c.row["event"]["type"] == "TRANSIT")
        cls.any = next(c for c in cards if c.row["event"]["type"] == "TRANSIT")

    def codes(self, card, text):
        return period_cards.period_card_scope(card)(text)

    def test_plain_text_passes(self):
        self.assertEqual(self.codes(self.sextile, GOOD_CARD), [])

    def test_another_aspect_is_refused_a_sextile_is_not_an_opposition(self):
        self.assertTrue(self.codes(self.sextile, "Това е опозиция, която изисква търпение."))
        self.assertTrue(self.codes(self.sextile, "Имате квадратура, затова бавете."))

    def test_a_foreign_planet_is_refused_pluto_does_not_belong_to_a_mars_card(self):
        planets = self.sextile.row["planets"]
        foreign = "Плутон" if "Pluto" not in planets else "Нептун"
        self.assertTrue(self.codes(self.sextile, f"{foreign} променя нещата."))

    def test_an_ordinary_word_that_looks_like_an_aspect_is_not_refused_in_a_card(self):
        self.assertEqual(self.codes(self.sextile, "Очакванията ви съвпадат повече, отколкото си мислите."), [])
        self.assertEqual(self.codes(self.sextile, "Това съвпада с желанието ви за спокойствие."), [])
        self.assertTrue(self.codes(self.sextile, "Транзитният аспект тук е опозиция."))

    def test_motion_signs_houses_dates_and_times_are_refused(self):
        for text in ("Ретроградният период изисква търпение.", "Директно движение помага.", "В Скорпион е по-трудно.", "В 7-ми дом е важно.",
                     "На 3 януари ще се реши.", "Около 3.1 бъдете внимателни.", BAD_CARD):
            self.assertTrue(self.codes(self.sextile, text), text)

    def test_a_date_from_the_heading_of_the_card_is_allowed_and_no_other_date(self):
        card = self.any
        iso = re.findall(r"\d{4}-\d{2}-\d{2}", repr(card.row["event"]))[0]
        own = f"{int(iso[8:10])} {tc._MONTH_NAMES[int(iso[5:7])]}"
        self.assertEqual(self.codes(card, f"През {own} е добре да изчакате."), [])
        self.assertTrue(self.codes(card, "През 3 януари е добре да изчакате."))

    def test_the_adverb_directly_is_an_ordinary_word_and_the_planet_movement_is_not(self):
        self.assertEqual(self.codes(self.sextile, "Кажете го директно и спокойно, без да мерите кой е по-прав."), [])
        self.assertTrue(self.codes(self.sextile, "Директният ход помага."))
        self.assertTrue(self.codes(self.sextile, "Планетата е ретроградна."))

    def test_a_station_card_may_use_the_word_of_its_own_direction_and_not_the_opposite_one(self):
        card = next(c for c in cards_of(rs.calendar(True, "2027-01-31"), True) if c.row["event"]["type"] == "RETROGRADE" and c.row["event"]["direction"] == "retrograde")
        self.assertEqual(self.codes(card, "Ретроградният период е време за повторно преглеждане."), [])
        self.assertTrue(self.codes(card, "Скоро идва директният ход."))
        self.assertTrue(self.codes(self.sextile, "Ретроградният период е време за повторно преглеждане."))          # не е станция

    def test_the_other_person_is_not_named_in_a_card_of_the_first_one(self):
        card = self.any
        self.assertTrue(self.codes(card, f"Това е важно за {card.row['other_person']}."))
        self.assertEqual(self.codes(card, f"Това е важно за {card.row['owner']}."), [])


class ClosingBlocksTest(unittest.TestCase):
    """Затварящите блокове са само от проверените карти: без планети, само дати от картите и без противоречие с вече проверените."""

    def setUp(self):
        self.cal = rs.calendar(False)
        self.facts = rs.facts("period", False)
        self.months = ["2026-10", "2026-11"]
        self.allowed = {(10, 24), (10, 1), (10, 31)}

    def check(self, tag, text, prior=(), question=""):
        return period_cards.closing_checks(question, tag, self.allowed, self.facts, list(prior), "overview", self.months)(text)

    def test_planets_aspects_signs_and_houses_are_refused(self):
        for text in ("Марс помага.", "Тригонът е хубав.", "В Скорпион е добре.", "В 7-ми дом е важно."):
            self.assertTrue(self.check("SUMMARY", text), text)

    def test_ordinary_words_that_look_like_aspects_are_allowed_and_real_terms_are_not(self):
        for fine in ("Очакванията ви съвпадат, когато говорите бавно.", "Това съвпада с желанието ви за спокойствие.",
                     "Ако няма съвпадение на мненията, не настоявайте.", "Не влизайте в опозиция един на друг."):
            self.assertEqual(self.check("SUMMARY", fine), [], fine)
        for term in ("Тригонът дава лекота.", "Квадратурата носи напрежение.", "Транзитната опозиция тежи.", "Марс в съвпад помага.",
                     "Натален секстил ви подкрепя."):
            self.assertTrue(self.check("SUMMARY", term), term)

    def test_only_dates_of_the_included_cards_may_be_named(self):
        self.assertEqual(self.check("ADVICE", "На 24 октомври финализирайте проекта."), [])
        self.assertTrue(self.check("ADVICE", "На 29 октомври финализирайте проекта."))

    def test_a_date_both_recommended_and_forbidden_for_the_same_action_is_refused(self):
        prior = [("month:2026-10", "Финализирайте проекта на 24 октомври.")]
        found = self.check("ADVICE", "Не финализирайте проекта на 24 октомври.", prior)
        self.assertTrue(found)
        self.assertIn("и препоръка, и забрана", found[0].detail)
        self.assertEqual(self.check("ADVICE", "Финализирайте проекта на 24 октомври.", prior), [])

    def test_postponed_until_a_later_date_is_not_recommended_earlier(self):
        prior = [("month:2026-10", "Втората граница поставете след 31 октомври.")]
        self.assertTrue(self.check("ADVICE", "Поставете втората граница на 24 октомври.", prior))

    def test_the_end_of_a_window_of_action_is_not_the_end_of_a_life_process(self):
        ends = frozenset({(10, 24)})
        check = period_cards.closing_checks("", "ADVICE", self.allowed, self.facts, [], "overview", self.months, ends)
        found = check("Напрежението свършва на 24 октомври и тогава всичко се уталожва.")
        self.assertTrue(found)
        self.assertIn("не краят на житейски процес", found[0].detail)
        self.assertEqual(check("На 24 октомври финализирайте проекта."), [])             # датата може да се спомене, но не като край
        self.assertEqual(check("В края на периода, около 24 октомври, отделете време за почивка."), [])        # „край на периода“ не е край на интервал

    def test_the_answer_must_cover_the_question(self):
        self.assertTrue(self.check("ANSWER", "Всичко ще е наред.", question="Как да говорим спокойно в разговор?"))
        long_answer = ("Най-добре е да говорите спокойно и бавно: първо изслушайте другия докрай, после кажете какво ви тежи, без да търсите виновен. "
                       "Изберете един важен въпрос вместо много теми и оставете място за паузи. Така разговорът няма да се превърне в спор.")
        self.assertEqual(self.check("ANSWER", long_answer, question="Как да говорим спокойно в разговор?"), [])


class FlowBase(unittest.TestCase):
    def setUp(self):
        self.interp = ai_interpreter.AIInterpreter(api_key="test-dummy")
        self.log = []

    def run_report(self, fake, pair=False, calendar=None, question="", relation=""):
        cal = calendar or rs.calendar(pair)
        kwargs = dict(calendar=cal, natal_chart=rs.chart("A" if pair else "Ivan"), partner_chart=rs.chart("Ivan") if pair else None,
                      report_type="general", user_name=rs.A_NAME if pair else rs.IVAN, partner_name=rs.IVAN if pair else None,
                      question=question, report_date="2026-10-11", retry_pause=0, relation=relation)

        async def go():
            return [step async for step in period_cards.run_period_report(self.interp, **kwargs)]
        with mock.patch.object(self.interp, "_call_api", fake):
            return asyncio.run(go())

    @staticmethod
    def ai(card=None, month=None, repair=None, overview=None):
        """Подменен AI: card/month/repair/overview са функции (id/подкана -> текст) или None за стандартния отговор."""
        async def fake(system_prompt, user_prompt, max_tokens=None, temperature=None, **kwargs):
            if temperature is not None:
                return repair(user_prompt) if repair else GOOD_CARD
            if system_prompt.startswith("You write ONE short interpretation"):
                return repair(user_prompt) if repair else GOOD_CARD
            reply = period_reply(system_prompt, user_prompt)
            if "MODE: MONTHLY FORECAST BY FACT CARDS" in system_prompt:
                if card:
                    reply = re.sub(r"\[\[(M\d+E\d+)\]\]\n[^\n]*", lambda m: f"[[{m.group(1)}]]\n{card(m.group(1)) or GOOD_CARD}", reply)
                if month:
                    reply = re.sub(r"\[\[MONTH\]\]\n[^\n]*", lambda m: f"[[MONTH]]\n{month(system_prompt) or ''}", reply)
            elif "MODE: PERIOD OVERVIEW" in system_prompt and overview:
                reply = overview(reply)
            return reply
        return fake


class FlowTest(FlowBase):
    def test_a_two_month_forecast_for_a_pair_is_complete_and_every_fact_is_written_by_the_code(self):
        cal = rs.calendar(True)                                    # октомври 2026, двойка
        steps = self.run_report(self.ai(), pair=True, calendar=cal)
        types = [s["type"] for s in steps]
        self.assertEqual(types[0], "month_start")
        self.assertEqual(types[-1], "finished")
        self.assertEqual(types.count("month_complete"), len(cal.months_with_events()))
        final = steps[-1]
        entry = next(c for c in final["checks"] if c["stage"] == "period_cards")
        self.assertEqual((entry["quality"], entry["removed"]), ("full", ""))
        month_text = final["month_texts"][0][1]
        first = cards_of(cal, True, limit=period_cards.CARDS_PER_MONTH)[0]
        self.assertIn(f"### {first.row['title']}\n", month_text)    # заглавието е точно това на кода
        if first.row["detail"]:
            self.assertIn(f"*{first.row['detail'][0].upper()}{first.row['detail'][1:]}*", month_text)
        self.assertIn("### Съвет за месеца", month_text)
        self.assertEqual([v for v in tc.check_text("\n\n".join(t for _t, t in final["month_texts"]) + "\n\n" + final["overview"],
                                                   rs.facts("period", True)) if v.severity == "hard"], [])
        self.assertIn("не край на житейски процес", final["overview"])

    def test_a_forecast_for_a_single_person_with_a_question_answers_it(self):
        steps = self.run_report(self.ai(), question="Как да говорим спокойно в разговор?")
        self.assertIn("### Отговор на вашия въпрос", steps[-1]["overview"])

    def test_the_period_across_the_change_of_the_clock_keeps_the_local_times(self):
        cal = dst_calendar()
        steps = self.run_report(self.ai(), pair=True, calendar=cal)
        text = steps[-1]["month_texts"][0][1]
        times = re.findall(r"(\d{1,2}) октомври, (\d{2}:\d{2}) –", text)
        self.assertTrue(times)
        wanted = set()
        for row in cal.public_events():
            moments = [p["when"] for p in row.get("exact", [])] + [row["when"]] if row["type"] != "TRANSIT" else [p["when"] for p in row.get("exact", [])]
            wanted |= {(int(m[8:10]), m[11:16]) for m in moments}
        for day, clock in times:
            self.assertIn((int(day), clock), wanted)
        # преди и след смяната на часа: разликата с UTC е 3, после 2 часа (сверено през zoneinfo)
        self.assertEqual(utc_of("2026-10-24 12:00").hour, 9)
        self.assertEqual(utc_of("2026-10-26 12:00").hour, 10)
        self.assertIn("(Europe/Sofia)", steps[-1]["overview"])

    def test_the_checks_record_the_result_without_text(self):
        checks = self.run_report(self.ai())[-1]["checks"]
        self.assertTrue(all(isinstance(c, dict) for c in checks))
        self.assertNotIn(GOOD_CARD, repr(checks))


class RepairFallbackPartialTest(FlowBase):
    def non_key_ids(self, calendar, pair):
        return [c.id for c in cards_of(calendar, False if not pair else True, limit=period_cards.CARDS_PER_MONTH) if not c.key]

    def test_a_card_that_breaks_the_rules_is_repaired_once_and_the_result_stays_full(self):
        first_bad = {"done": False}

        def card(card_id):
            if card_id == "M1E1" and not first_bad["done"]:
                first_bad["done"] = True
                return BAD_CARD
        steps = self.run_report(self.ai(card=card))
        final = steps[-1]
        entry = next(c for c in final["checks"] if c["stage"] == "period_cards")
        self.assertEqual(entry["quality"], "full")
        self.assertNotIn("13:37", final["month_texts"][0][1])
        self.assertIn("repaired", [c.get("result") for c in final["checks"]])

    def test_a_secondary_card_that_cannot_be_fixed_is_dropped_the_result_is_partial_and_the_bad_text_never_appears(self):
        cal = rs.calendar(False)
        drop = self.non_key_ids(cal, False)[0]
        steps = self.run_report(self.ai(card=lambda i: BAD_CARD if i == drop else None, repair=lambda _p: BAD_CARD))
        final = steps[-1]
        entry = next(c for c in final["checks"] if c["stage"] == "period_cards")
        self.assertEqual(entry["quality"], "partial")
        self.assertIn(drop, entry["removed"])
        whole = final["overview"] + "".join(t for _t, t in final["month_texts"])
        self.assertNotIn("13:37", whole)
        self.assertIn("Частичен анализ", final["overview"])
        dropped = next(c for c in cards_of(cal, False, limit=8) if c.id == drop)
        self.assertNotIn(f"### {dropped.row['title']}\n", whole)

    def test_a_key_card_that_cannot_be_fixed_refuses_the_report(self):
        cal = rs.calendar(False)
        key = next(c.id for c in cards_of(cal, False, limit=8) if c.key)
        with self.assertRaises(period_report.ForecastGenerationError) as caught:
            self.run_report(self.ai(card=lambda i: BAD_CARD if i == key else None, repair=lambda _p: BAD_CARD))
        self.assertTrue(caught.exception.stage.endswith("key_card"), caught.exception.stage)

    def test_too_many_failed_cards_refuse_the_report(self):
        with self.assertRaises(period_report.ForecastGenerationError) as caught:
            self.run_report(self.ai(card=lambda _i: BAD_CARD, repair=lambda _p: BAD_CARD))
        self.assertIsNotNone(caught.exception.cause)

    def test_a_month_advice_that_breaks_the_rules_is_recomposed_from_the_verified_cards_the_result_is_a_fallback(self):
        steps = self.run_report(self.ai(month=lambda _s: BAD_MONTH if "Месец" else "", repair=lambda p: BAD_MONTH if "конфликтите" in p else GOOD_CARD))
        # композицията „от проверените карти“ се връща с нормален отговор (period_reply), т.е. не съдържа планета
        final = steps[-1]
        entry = next(c for c in final["checks"] if c["stage"] == "period_cards")
        self.assertEqual((entry["quality"], entry["removed"]), ("fallback", ""))
        self.assertIn("Резервен анализ", final["overview"])
        self.assertNotIn("Марс", final["month_texts"][0][1].split("### Съвет за месеца")[1])
        cards = [c for c in final["month_texts"][0][1].split("### ") if " – " in c.split("\n")[0]]
        self.assertEqual(len(cards), min(period_cards.CARDS_PER_MONTH, len(cards_of(rs.calendar(False), False, limit=8))))

    def test_wrong_aspect_retrograde_sign_and_foreign_planet_never_reach_the_final_text(self):
        bad = {"M1E1": "Това е опозиция.", "M1E2": "Ретроградният Юпитер променя нещата.", "M1E3": "Марс е бил в Овен и сега е в Телец.",
               "M1E4": "Плутон на другия човек влияе."}
        steps = self.run_report(self.ai(card=lambda i: bad.get(i), repair=lambda _p: GOOD_CARD))
        whole = steps[-1]["overview"] + "".join(t for _t, t in steps[-1]["month_texts"])
        for phrase in ("опозиция.", "Ретроградният Юпитер", "в Овен", "Плутон на другия"):
            self.assertNotIn(phrase, whole)

    def test_a_contradicting_overview_is_repaired_before_the_report_is_finished(self):
        cal = rs.calendar(False)
        card = next(c for c in cards_of(cal, False, limit=8) if c.row["event"]["type"] == "TRANSIT" and c.row["event"].get("exact"))
        iso = card.row["event"]["exact"][0]["when"]
        day = f"{int(iso[8:10])} {tc._MONTH_NAMES[int(iso[5:7])]}"
        month_ok = lambda _s: f"Финализирайте проекта на {day}."
        bad_overview = lambda reply: reply.replace("- Говорете спокойно и изчакайте, ако усетите напрежение.", f"- Не финализирайте проекта на {day}.")
        steps = self.run_report(self.ai(month=month_ok, overview=bad_overview, repair=lambda p: f"- Финализирайте проекта на {day}.\n- Говорете спокойно и изчакайте, ако усетите напрежение."))
        whole = steps[-1]["overview"] + "".join(t for _t, t in steps[-1]["month_texts"])
        self.assertNotIn(f"Не финализирайте проекта на {day}", whole)
        parts = [(f"month:{m}", t) for m, (_ti, t) in zip(cal.months_with_events(), steps[-1]["month_texts"])] + [("overview", steps[-1]["overview"])]
        self.assertEqual(report_guard.cross_part_violations(parts, cal.months_with_events(), rs.facts("period", False)), {})

    def test_two_cards_of_the_report_cannot_recommend_and_forbid_the_same_action_on_the_same_date(self):
        cal = rs.calendar(False)
        cards = [c for c in cards_of(cal, False, limit=8) if c.row["event"]["type"] == "TRANSIT" and c.row["event"].get("exact")]
        by_day = {}
        for c in cards:
            by_day.setdefault(c.row["event"]["exact"][0]["when"][:10], []).append(c)
        first, second = next(v for v in by_day.values() if len(v) >= 2)[:2]
        iso = first.row["event"]["exact"][0]["when"]
        day = f"{int(iso[8:10])} {tc._MONTH_NAMES[int(iso[5:7])]}"
        texts = {first.id: f"Финализирайте проекта на {day}.", second.id: f"Не финализирайте проекта на {day}."}
        steps = self.run_report(self.ai(card=lambda i: texts.get(i), repair=lambda _p: GOOD_CARD))
        whole = steps[-1]["overview"] + "".join(t for _t, t in steps[-1]["month_texts"])
        self.assertIn(f"Финализирайте проекта на {day}.", whole)
        self.assertNotIn(f"Не финализирайте проекта на {day}", whole)
        parts = [(f"month:{m}", t) for m, (_ti, t) in zip(cal.months_with_events(), steps[-1]["month_texts"])] + [("overview", steps[-1]["overview"])]
        self.assertEqual(report_guard.cross_part_violations(parts, cal.months_with_events(), rs.facts("period", False)), {})

    def test_an_unresolved_contradiction_refuses_the_report(self):
        cal = rs.calendar(False)
        card = next(c for c in cards_of(cal, False, limit=8) if c.row["event"]["type"] == "TRANSIT" and c.row["event"].get("exact"))
        iso = card.row["event"]["exact"][0]["when"]
        day = f"{int(iso[8:10])} {tc._MONTH_NAMES[int(iso[5:7])]}"
        bad_overview = lambda reply: reply.replace("- Говорете спокойно и изчакайте, ако усетите напрежение.", f"- Не финализирайте проекта на {day}.")
        with self.assertRaises(period_report.ForecastGenerationError):
            self.run_report(self.ai(month=lambda _s: f"Финализирайте проекта на {day}.", overview=bad_overview,
                                    repair=lambda p: f"Не финализирайте проекта на {day}."))


class OrderIndependenceTest(FlowBase):
    """Резултатът не зависи от реда, в който AI връща секциите, и от това коя от две противоречиви карти е първа; крайната проверка на целия
    отчет остава и хваща противоречие, което поетапната проверка е пропуснала."""

    WORDS = ["първо", "второ", "трето", "четвърто", "пето", "шесто", "седмо", "осмо"]

    def distinct(self):
        return lambda card_id: f"Тук е важно да има {self.WORDS[(int(card_id.split('E')[1]) - 1) % 8]} внимание към разговора."

    def reversed_ai(self, card):
        base = self.ai(card=card)

        async def fake(system_prompt, user_prompt, max_tokens=None, temperature=None, **kwargs):
            reply = await base(system_prompt, user_prompt, max_tokens, temperature, **kwargs)
            if "MODE: MONTHLY FORECAST BY FACT CARDS" in system_prompt:
                blocks = re.split(r"(?m)^(?=\[\[)", reply)
                reply = "".join(b if b.endswith("\n") else b + "\n" for b in reversed([b for b in blocks if b.strip()]))
            return reply
        return fake

    def test_the_report_is_the_same_when_the_model_returns_the_cards_in_another_order(self):
        card = self.distinct()
        normal = self.run_report(self.ai(card=card))[-1]
        reverse = self.run_report(self.reversed_ai(card))[-1]
        self.assertEqual(normal["month_texts"], reverse["month_texts"])
        self.assertEqual(normal["overview"], reverse["overview"])
        titles = re.findall(r"(?m)^### (.+)$", normal["month_texts"][0][1])
        dated = [t for t in titles if re.match(r"\d{1,2} \S+, \d{2}:\d{2}", t)]
        self.assertEqual(dated, sorted(dated, key=lambda t: (tc.MONTHS[t.split()[1].rstrip(",")], int(t.split()[0]), t.split()[2])))   # редът е хронологичен, не този на AI

    def contradictory_pair(self):
        cal = rs.calendar(False)
        cards = [c for c in cards_of(cal, False, limit=8) if c.row["event"]["type"] == "TRANSIT" and c.row["event"].get("exact")]
        by_day = {}
        for c in cards:
            by_day.setdefault(c.row["event"]["exact"][0]["when"][:10], []).append(c)
        first, second = next(v for v in by_day.values() if len(v) >= 2)[:2]
        iso = first.row["event"]["exact"][0]["when"]
        return cal, first, second, f"{int(iso[8:10])} {tc._MONTH_NAMES[int(iso[5:7])]}"

    def test_a_contradiction_between_two_cards_is_found_whichever_comes_first(self):
        cal, first, second, day = self.contradictory_pair()
        for recommend, forbid in ((first, second), (second, first)):
            texts = {recommend.id: f"Финализирайте проекта на {day}.", forbid.id: f"Не финализирайте проекта на {day}."}
            steps = self.run_report(self.ai(card=lambda i: texts.get(i), repair=lambda _p: GOOD_CARD))
            whole = steps[-1]["overview"] + "".join(t for _t, t in steps[-1]["month_texts"])
            self.assertFalse(f"Финализирайте проекта на {day}." in whole and f"Не финализирайте проекта на {day}." in whole)   # остава най-много едното
            parts = [(f"month:{m}", t) for m, (_ti, t) in zip(cal.months_with_events(), steps[-1]["month_texts"])] + [("overview", steps[-1]["overview"])]
            self.assertEqual(report_guard.cross_part_violations(parts, cal.months_with_events(), rs.facts("period", False)), {})

    def test_the_final_check_of_the_whole_report_still_refuses_what_the_step_checks_missed(self):
        cal, first, second, day = self.contradictory_pair()
        texts = {first.id: f"Финализирайте проекта на {day}.", second.id: f"Не финализирайте проекта на {day}."}
        with mock.patch.object(period_cards, "cross_check", lambda *a, **k: (lambda text: [])):                # поетапната проверка е изключена
            with self.assertRaises(period_report.ForecastGenerationError) as caught:
                self.run_report(self.ai(card=lambda i: texts.get(i)))
        self.assertEqual(caught.exception.stage, "period:final")


class RetryTest(FlowBase):
    """Един автоматичен повторен опит на заявка към AI (период_report._attempt); втори провал е неуспешен отчет."""

    @staticmethod
    def flaky(marker, failures, empty=False):
        state = {"failed": 0, "calls": {}}

        async def fake(system_prompt, user_prompt, max_tokens=None, temperature=None, **kwargs):
            key = marker if marker in system_prompt else "other"
            state["calls"][key] = state["calls"].get(key, 0) + 1
            if marker in system_prompt and state["failed"] < failures:
                state["failed"] += 1
                if empty:
                    return ""
                raise RuntimeError("провайдърът не отговори")
            return period_reply(system_prompt, user_prompt)
        fake.state = state
        return fake

    def test_one_automatic_retry_saves_the_month(self):
        fake = self.flaky("MODE: MONTHLY FORECAST BY FACT CARDS", 1)
        steps = self.run_report(fake)
        self.assertEqual(steps[-1]["type"], "finished")
        self.assertEqual(fake.state["calls"]["MODE: MONTHLY FORECAST BY FACT CARDS"], len(rs.calendar(False).months_with_events()) + 1)

    def test_a_month_that_fails_twice_fails_the_report_and_there_is_no_overview(self):
        fake = self.flaky("MODE: MONTHLY FORECAST BY FACT CARDS", 2)
        with self.assertRaises(period_report.ForecastGenerationError) as caught:
            self.run_report(fake)
        self.assertEqual(caught.exception.stage, "month:2026-10")
        self.assertNotIn("MODE: PERIOD OVERVIEW FROM VERIFIED BLOCKS", fake.state["calls"])                 # без месец няма общ преглед

    def test_the_overview_gets_one_retry_and_an_empty_one_fails(self):
        marker = "MODE: PERIOD OVERVIEW FROM VERIFIED BLOCKS"
        self.assertEqual(self.run_report(self.flaky(marker, 1))[-1]["type"], "finished")
        with self.assertRaises(period_report.ForecastGenerationError) as caught:
            self.run_report(self.flaky(marker, 2, empty=True))
        self.assertEqual(caught.exception.stage, "overview")
        with self.assertRaises(period_report.ForecastGenerationError):
            self.run_report(self.flaky(marker, 2))

    def test_the_saved_content_puts_the_overview_first(self):
        months = [("Октомври 2026", "<p>о</p>"), ("Ноември 2026", "<p>н</p>")]
        saved = period_report.saved_content("<p>Преглед</p>", months)
        self.assertTrue(saved.startswith("<h2>Общ преглед на периода</h2>\n<p>Преглед</p>\n\n<h2>Октомври 2026</h2>"))
        self.assertTrue(period_report.saved_content("<p>Преглед</p>", months, note="Бележка").startswith("<h2>Общ преглед на периода</h2>\nБележка\n\n"))


class PromptTest(FlowBase):
    """Подканите към AI с истинския интерпретатор и подменен само мрежовият вик: картите са от кода, а общият преглед е само от проверените
    блокове."""

    def prompts(self, pair, question="Как да говорим спокойно в разговор?", relation="", gender=None, partner_gender=None):
        calls = []

        async def fake(system_prompt, user_prompt, max_tokens=None, temperature=None, **kwargs):
            calls.append((system_prompt, user_prompt))
            return period_reply(system_prompt, user_prompt)
        cal = rs.calendar(pair)

        async def go():
            return [s async for s in period_cards.run_period_report(
                self.interp, calendar=cal, natal_chart=rs.chart("A" if pair else "Ivan"), partner_chart=rs.chart("Ivan") if pair else None,
                report_type="love", user_name=rs.A_NAME if pair else rs.IVAN, partner_name=rs.IVAN if pair else None, question=question,
                gender=gender, partner_gender=partner_gender, report_date="2026-10-11", retry_pause=0, relation=relation)]
        with mock.patch.object(self.interp, "_call_api", fake):
            asyncio.run(go())
        return calls, cal

    def test_the_month_prompt_carries_the_zone_the_theme_the_question_the_people_and_the_cards_with_owners(self):
        calls, cal = self.prompts(True, relation="romantic", gender="female", partner_gender="male")
        system, user = calls[0]
        self.assertEqual(len(calls), len(cal.months_with_events()) + 1)                    # месеците и общият преглед
        self.assertIn("Month: Октомври 2026", system)
        self.assertIn(f"All times are local time ({cal.timezone})", system)
        self.assertIn("THEME: " + ai_interpreter.THEME_FOCUS["love"], system)
        self.assertIn("relationship (romantic)", system)
        self.assertIn("User Question: Как да говорим спокойно в разговор?", user)
        self.assertIn("--- PEOPLE ---", user)
        self.assertIn('"gender":"male"', user)
        cards = [l for l in user.splitlines() if re.match(r"^M1E\d+: ", l)]
        self.assertTrue(cards)
        self.assertTrue(any(f" на {NAME_IVAN}:" in c for c in cards) and any(f" на {NAME_A}:" in c for c in cards))
        for text in (system, user):
            for internal in ("_utc", "_sort", "_exact_utc", "_pass_count", "state_in_month", "TIMELINE EVENTS", "NATAL CHART"):
                self.assertNotIn(internal, text)
        self.assertIn("never switch to", system)                                          # едно обръщение: „вие“

    def test_the_overview_prompt_has_only_verified_blocks_the_question_and_the_people(self):
        calls, cal = self.prompts(True, gender="female", partner_gender="male")
        system, user = calls[-1]
        self.assertIn("MODE: PERIOD OVERVIEW FROM VERIFIED BLOCKS", system)
        self.assertIn("User question: Как да говорим спокойно в разговор?", user)
        self.assertIn("--- PEOPLE ---", user)
        self.assertIn("MONTH ADVICE 2026-10:", user)
        self.assertIn("[M1E1]", user)
        for raw in ("PERIOD CALENDAR", "TIMELINE EVENTS", "NATAL CHART", "_utc"):
            self.assertNotIn(raw, user)
        self.assertIn("The window of a transit is not the end of a life process", system)
        self.assertIn("[[ANSWER]]", system)

    def test_a_single_person_prompt_has_no_second_person_and_no_answer_without_a_question(self):
        calls, _cal = self.prompts(False, question="")
        for system, user in calls:
            self.assertNotIn("second_person", user)
        system, user = calls[-1]
        self.assertNotIn("[[ANSWER]]", system)
        self.assertNotIn(" and ", system.split("People:")[1].split(".")[0])


class LabelsTest(FlowBase):
    def test_an_internal_label_in_a_card_text_is_repaired_and_never_reaches_the_report(self):
        label = "Състоянието (state_in_month: approaching) е известно."
        steps = self.run_report(self.ai(card=lambda i: label if i == "M1E2" else None, repair=lambda _p: GOOD_CARD))
        whole = steps[-1]["overview"] + "".join(t for _t, t in steps[-1]["month_texts"])
        self.assertNotIn("state_in_month", whole)
        self.assertEqual(next(c for c in steps[-1]["checks"] if c["stage"] == "period_cards")["quality"], "full")


ENFORCED = {"BALANCE_ENFORCED": "1"}
FORECAST = {"is_dynamic": True, "target_date": "2026-01-01", "end_date": "2026-01-31"}


def balance(email):
    db = SessionLocal()
    try:
        row = db.query(User.paid_cents, User.gift_cents).filter(User.email == email).one()
        return int(row[0]), int(row[1])
    finally:
        db.close()


class BillingTest(unittest.TestCase):
    """Резервният резултат се таксува като пълния; частичният не се таксува. Проверява се и крайният текст на запазения отчет."""

    @classmethod
    def setUpClass(cls):
        cls.client = TestClient(main.app)

    def setUp(self):
        limiter.reset()

    def post(self, email, fake, wrap=None):
        headers = register_and_login(self.client, email)
        give_deposit(email, 1000)
        patches = [mock.patch.object(main.ai_interpreter, "_call_api", fake), mock.patch.dict(os.environ, ENFORCED)]
        if wrap:
            patches.append(wrap)
        for p in patches:
            p.start()
        try:
            response = self.client.post("/jobs?wait=60", json={**CHART, "name": "Иван", **FORECAST}, headers=headers)
        finally:
            for p in reversed(patches):
                p.stop()
        self.assertEqual(response.status_code, 200, response.text)
        job = response.json()["job"]
        content = self.client.get(f"/reports/{job['report_id']}", headers=headers).json()["content"] if job.get("report_id") else ""
        return job, content

    def test_a_full_result_is_charged(self):
        job, content = self.post("pc-full@test.bg", FlowBase.ai())
        self.assertEqual((job["status"], job["charged_cents"]), ("succeeded", 75))
        self.assertEqual(balance("pc-full@test.bg"), (925, 500))
        self.assertNotIn("Резервен анализ", content)
        self.assertNotIn("Частичен анализ", content)

    def test_a_fallback_result_is_charged_like_a_full_one_and_says_so(self):
        fake = FlowBase.ai(month=lambda _s: BAD_MONTH, repair=lambda p: BAD_MONTH if "конфликтите" in p else GOOD_CARD)
        job, content = self.post("pc-fallback@test.bg", fake)
        self.assertEqual((job["status"], job["charged_cents"]), ("succeeded", 75))
        self.assertEqual(balance("pc-fallback@test.bg"), (925, 500))
        self.assertIn("Резервен анализ", content)
        self.assertNotIn("Марс е планетата", content)

    def test_a_partial_result_is_not_charged_and_says_so(self):
        captured = []
        original = period_cards.build_month_cards

        def wrapped(*args, **kwargs):
            cards = original(*args, **kwargs)
            captured.extend(cards)
            return cards
        # картата, която ще се провали, е втората по важност „обикновена“: тя не е ключова
        drop = {"id": None}

        def card(card_id):
            if drop["id"] is None:
                drop["id"] = next(c.id for c in captured if not c.key)
            return BAD_CARD if card_id == drop["id"] else None
        fake = FlowBase.ai(card=card, repair=lambda _p: BAD_CARD)
        job, content = self.post("pc-partial@test.bg", fake, wrap=mock.patch.object(period_cards, "build_month_cards", wrapped))
        self.assertEqual((job["status"], job["charged_cents"]), ("succeeded", 0))
        self.assertEqual(balance("pc-partial@test.bg"), (1000, 500))
        self.assertIn("Частичен анализ", content)
        self.assertNotIn("13:37", content)

    def test_a_refused_report_costs_nothing_and_saves_nothing(self):
        job, content = self.post("pc-refused@test.bg", FlowBase.ai(card=lambda _i: BAD_CARD, repair=lambda _p: BAD_CARD))
        self.assertEqual(job["status"], "failed")
        self.assertEqual(balance("pc-refused@test.bg"), (1000, 500))
        self.assertEqual(content, "")


if __name__ == "__main__":
    unittest.main()
