"""
Тестове за Фаза 12: местният час на раждане и часовата зона.

- час, който не съществува (лятно време) или се повтаря (връщане на часовника), не се поправя тихо: потребителят избира;
- за транзити и "сега" старото поведение остава (нестрого);
- GET /time-check казва зоната, отместването и състоянието на часа.
Очакваните моменти са от независимия zoneinfo, не от кода на двигателя.

Пускане: python -m unittest discover -s tests -p "test_time_resolution.py"
"""
import unittest
from datetime import datetime, timedelta
from unittest import mock
from zoneinfo import ZoneInfo

import testenv  # noqa: F401  (трябва да е преди main)
import pytz  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

import engine  # noqa: E402
import main  # noqa: E402
from rate_limit import limiter  # noqa: E402
from testenv import CHART, register_and_login  # noqa: E402

SOFIA = (42.6977, 23.3219)
UTC = ZoneInfo("UTC")


def balances(email):
    from database import SessionLocal, User
    db = SessionLocal()
    try:
        row = db.query(User.paid_cents, User.gift_cents).filter(User.email == email).one()
        return int(row[0]), int(row[1])
    finally:
        db.close()


def expected_utc(date, time, zone, fold=0):
    """Моментът в UTC по независимия zoneinfo (fold 0 е първото преминаване)."""
    year, month, day = (int(p) for p in date.split("-"))
    hour, minute = (int(p) for p in time.split(":"))
    local = datetime(year, month, day, hour, minute, tzinfo=ZoneInfo(zone), fold=fold)
    return local.astimezone(UTC).replace(tzinfo=None)


class ResolveTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.eng = engine.get_engine()

    def resolve(self, date, time, fold=None, place=SOFIA):
        return self.eng.resolve_local_time(date, time, place[0], place[1], fold)

    def test_an_ordinary_time_has_a_zone_and_an_offset(self):
        for date, time, minutes in (("2026-07-01", "12:00", 180), ("2026-01-15", "12:00", 120)):
            with self.subTest(date=date):
                resolved = self.resolve(date, time)
                self.assertEqual((resolved["status"], resolved["timezone"], resolved["zone_found"]), ("ok", "Europe/Sofia", True))
                self.assertEqual(resolved["utc_offset_minutes"], minutes)
                self.assertEqual(resolved["utc"].replace(tzinfo=None), expected_utc(date, time, "Europe/Sofia"))

    def test_the_skipped_hour_does_not_exist(self):
        for time in ("03:00", "03:30", "03:59"):
            with self.subTest(time=time):
                resolved = self.resolve("2026-03-29", time)
                self.assertEqual(resolved["status"], "nonexistent")
                self.assertEqual(resolved["next_valid_time"], "04:00")
        # границите са обикновени часове
        self.assertEqual(self.resolve("2026-03-29", "02:59")["status"], "ok")
        self.assertEqual(self.resolve("2026-03-29", "04:00")["status"], "ok")

    def test_the_repeated_hour_is_ambiguous_until_a_pass_is_chosen(self):
        for time in ("03:00", "03:30", "03:59"):
            with self.subTest(time=time):
                resolved = self.resolve("2026-10-25", time)
                self.assertEqual(resolved["status"], "ambiguous")
                self.assertEqual([(o["fold"], o["utc_offset_minutes"]) for o in resolved["options"]], [(0, 180), (1, 120)])
                self.assertTrue(all(o["label"] and o["utc_offset"] for o in resolved["options"]))
        self.assertEqual(self.resolve("2026-10-25", "02:59")["status"], "ok")
        self.assertEqual(self.resolve("2026-10-25", "04:00")["status"], "ok")

    def test_a_chosen_pass_gives_the_matching_moment(self):
        for fold, offset in ((0, 180), (1, 120)):
            with self.subTest(fold=fold):
                resolved = self.resolve("2026-10-25", "03:30", fold)
                self.assertEqual(resolved["status"], "ok")
                self.assertEqual(resolved["utc_offset_minutes"], offset)
                self.assertEqual(resolved["utc"].replace(tzinfo=None), expected_utc("2026-10-25", "03:30", "Europe/Sofia", fold))
        # избраният fold не променя час, който не се повтаря
        self.assertEqual(self.resolve("2026-07-01", "12:00", 1)["utc"], self.resolve("2026-07-01", "12:00")["utc"])

    def test_bulgaria_before_1979_is_a_fixed_utc_plus_two(self):
        for date, time in (("1975-07-01", "12:00"), ("1978-03-26", "03:30"), ("1978-09-24", "03:30")):
            with self.subTest(date=date):
                resolved = self.resolve(date, time)
                self.assertEqual((resolved["status"], resolved["utc_offset_minutes"]), ("ok", 120))
                self.assertEqual(resolved["utc"].replace(tzinfo=None), datetime.fromisoformat(f"{date}T{time}") - timedelta(hours=2))

    def test_a_point_at_sea_is_flagged(self):
        resolved = self.resolve("2026-06-01", "12:00", place=(0.0, -30.0))
        self.assertTrue(resolved["at_sea"])
        self.assertEqual(resolved["status"], "ok")
        self.assertFalse(self.resolve("2026-06-01", "12:00")["at_sea"])

    def test_a_place_without_a_zone_falls_back_to_utc(self):
        with mock.patch.object(self.eng, "tf", mock.Mock(timezone_at=mock.Mock(return_value=None))):
            resolved = self.resolve("2026-06-01", "12:00")
        self.assertEqual((resolved["timezone"], resolved["zone_found"], resolved["status"]), ("UTC", False, "ok"))
        self.assertEqual(resolved["utc"].replace(tzinfo=None), datetime(2026, 6, 1, 12, 0))

    def test_invalid_input_is_a_value_error(self):
        for date, time in (("2026-02-30", "12:00"), ("2026-06-01", "25:00"), ("abc", "12:00"), ("2026-06-01", "xx")):
            with self.subTest(date=date, time=time):
                with self.assertRaises(ValueError):
                    self.resolve(date, time)


class StrictTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.eng = engine.get_engine()

    def utc(self, date, time, **kw):
        return self.eng._datetime_to_utc(date, time, SOFIA[0], SOFIA[1], **kw)

    def test_strict_refuses_a_skipped_time(self):
        with self.assertRaises(engine.TimeResolutionError) as caught:
            self.utc("2026-03-29", "03:30", strict=True)
        self.assertEqual(caught.exception.code, "time_nonexistent")
        self.assertIn("не съществува", caught.exception.message)
        self.assertIn("04:00", caught.exception.message)

    def test_strict_refuses_a_repeated_time_without_a_choice(self):
        with self.assertRaises(engine.TimeResolutionError) as caught:
            self.utc("2026-10-25", "03:30", strict=True)
        self.assertEqual(caught.exception.code, "time_ambiguous")
        self.assertEqual(len(caught.exception.options), 2)

    def test_strict_with_a_chosen_pass_is_exact(self):
        for fold in (0, 1):
            with self.subTest(fold=fold):
                moment, zone = self.utc("2026-10-25", "03:30", strict=True, fold=fold)
                self.assertEqual(zone, "Europe/Sofia")
                self.assertEqual(moment.replace(tzinfo=None), expected_utc("2026-10-25", "03:30", "Europe/Sofia", fold))

    def test_lenient_keeps_the_old_behaviour(self):
        # Старото поведение: tz.localize(naive) с зимното време. За "сега" и транзитите не се иска избор.
        tz = pytz.timezone("Europe/Sofia")
        for date, time in (("2026-03-29", "03:30"), ("2026-10-25", "03:30")):
            with self.subTest(date=date):
                moment, _ = self.utc(date, time)
                naive = datetime.fromisoformat(f"{date}T{time}")
                self.assertEqual(moment, tz.localize(naive).astimezone(pytz.UTC))

    def test_strict_and_lenient_agree_for_every_ordinary_time(self):
        places = {"Europe/Sofia": SOFIA, "America/New_York": (40.7128, -74.006), "Asia/Kolkata": (28.6139, 77.209),
                  "Australia/Sydney": (-33.8688, 151.2093), "Europe/London": (51.5074, -0.1278)}
        samples = [datetime(1950, 1, 1, 0, 30) + timedelta(days=17, hours=5, minutes=13) * n for n in range(0, 1400, 7)]
        checked = 0
        for zone, (lat, lon) in places.items():
            for naive in samples:
                date, time = naive.strftime("%Y-%m-%d"), naive.strftime("%H:%M")
                resolved = self.eng.resolve_local_time(date, time, lat, lon)
                if resolved["status"] != "ok":
                    continue
                strict = self.eng._datetime_to_utc(date, time, lat, lon, strict=True)
                lenient = self.eng._datetime_to_utc(date, time, lat, lon)
                self.assertEqual(strict, lenient, (zone, date, time))
                checked += 1
        self.assertGreater(checked, 500)

    def test_pre_1979_sofia_matches_zoneinfo_where_it_is_unambiguous(self):
        moment, _ = self.utc("1975-07-01", "12:00", strict=True)
        self.assertEqual(moment.replace(tzinfo=None), datetime(1975, 7, 1, 10, 0))


class ChartTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.eng = engine.get_engine()

    def chart(self, time, **kw):
        return self.eng.calculate_chart(date="2026-10-25", time=time, lat=SOFIA[0], lon=SOFIA[1], **kw)

    def test_the_two_passes_are_two_different_moments(self):
        first = self.chart("03:30", fold=0, strict=True)
        second = self.chart("03:30", fold=1, strict=True)
        self.assertTrue(first["datetime_utc"].startswith("2026-10-25T00:30"), first["datetime_utc"])
        self.assertTrue(second["datetime_utc"].startswith("2026-10-25T01:30"), second["datetime_utc"])
        self.assertNotEqual(first["julian_day"], second["julian_day"])
        self.assertNotEqual(first["angles"], second["angles"])

    def test_a_strict_chart_without_a_choice_is_refused(self):
        with self.assertRaises(engine.TimeResolutionError):
            self.chart("03:30", strict=True)
        with self.assertRaises(engine.TimeResolutionError):
            self.eng.calculate_chart(date="2026-03-29", time="03:30", lat=SOFIA[0], lon=SOFIA[1], strict=True)

    def test_a_lenient_chart_still_works(self):
        self.assertTrue(self.chart("03:30")["datetime_utc"])

    def test_the_module_level_function_passes_the_options_through(self):
        chart = engine.calculate_chart("2026-10-25", "03:30", SOFIA[0], SOFIA[1], fold=1, strict=True)
        self.assertTrue(chart["datetime_utc"].startswith("2026-10-25T01:30"))
        with self.assertRaises(engine.TimeResolutionError):
            engine.calculate_chart("2026-10-25", "03:30", SOFIA[0], SOFIA[1], strict=True)


class TimeCheckTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.client = TestClient(main.app)

    def setUp(self):
        limiter.reset()

    def check(self, date, time, lat=SOFIA[0], lon=SOFIA[1], **extra):
        params = {"date": date, "time": time, "lat": lat, "lon": lon, **extra}
        return self.client.get("/time-check", params=params)

    def test_an_ordinary_time_shows_the_zone_and_offset(self):
        r = self.check("2026-07-01", "12:00")
        self.assertEqual(r.status_code, 200, r.text)
        body = r.json()
        self.assertEqual((body["status"], body["timezone"], body["utc_offset"], body["utc_offset_minutes"]),
                         ("ok", "Europe/Sofia", "UTC+3", 180))
        self.assertIn("Europe/Sofia", body["message"])
        self.assertIn("UTC+3", body["message"])
        self.assertFalse(body["at_sea"])

    def test_a_skipped_time_names_the_next_valid_one(self):
        body = self.check("2026-03-29", "03:30").json()
        self.assertEqual((body["status"], body["next_valid_time"]), ("nonexistent", "04:00"))
        self.assertIn("не съществува", body["message"])
        self.assertIn("04:00", body["message"])

    def test_a_repeated_time_offers_both_passes_and_accepts_a_choice(self):
        body = self.check("2026-10-25", "03:30").json()
        self.assertEqual(body["status"], "ambiguous")
        self.assertEqual([(o["fold"], o["utc_offset"]) for o in body["options"]], [(0, "UTC+3"), (1, "UTC+2")])
        self.assertIn("повтаря", body["message"])
        chosen = self.check("2026-10-25", "03:30", fold=1).json()
        self.assertEqual((chosen["status"], chosen["utc_offset"]), ("ok", "UTC+2"))

    def test_a_point_at_sea_gets_a_warning(self):
        body = self.check("2026-06-01", "12:00", lat=0.0, lon=-30.0).json()
        self.assertTrue(body["at_sea"])
        self.assertIn("открито море", body["message"])

    def test_bad_input_is_refused(self):
        self.assertEqual(self.check("2026-02-30", "12:00").status_code, 400)
        self.assertEqual(self.check("2026-06-01", "25:00").status_code, 400)
        self.assertEqual(self.check("abc", "12:00").status_code, 400)
        self.assertEqual(self.check("0001-01-01", "00:00").status_code in (200, 400), True)   # без 500 за краен случай
        self.assertEqual(self.check("2026-06-01", "12:00", lat=91).status_code, 422)
        self.assertEqual(self.check("2026-06-01", "12:00", lon=181).status_code, 422)
        self.assertEqual(self.check("2026-06-01", "12:00", fold=2).status_code, 422)
        self.assertEqual(self.client.get("/time-check", params={"date": "2026-06-01"}).status_code, 422)

    def test_it_needs_no_login_but_is_rate_limited(self):
        for _ in range(120):
            self.assertEqual(self.check("2026-07-01", "12:00").status_code, 200)
        limited = self.check("2026-07-01", "12:00")
        self.assertEqual(limited.status_code, 429)
        self.assertIn("Retry-After", limited.headers)


class JobTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.client = TestClient(main.app)

    def setUp(self):
        limiter.reset()

    def post(self, headers, body, **params):
        query = "&".join(f"{k}={v}" for k, v in {"wait": 60, **params}.items())
        return self.client.post(f"/jobs?{query}", json=body, headers=headers)

    def seen_charts(self):
        seen = []

        async def fake(*args, **kwargs):
            seen.append((kwargs.get("natal_chart"), kwargs.get("partner_chart"), kwargs.get("transit_chart")))
            return "<p>текст</p>"
        return seen, mock.patch.object(main.ai_interpreter, "interpret_chart", fake)

    GAP = {"date": "2026-03-29", "time": "03:30", "lat": SOFIA[0], "lon": SOFIA[1]}
    REPEAT = {"date": "2026-10-25", "time": "03:30", "lat": SOFIA[0], "lon": SOFIA[1]}
    PAIR = {"partner_name": "Втори", "partner_date": "1992-07-20", "partner_time": "09:30", "partner_lat": 43.2141,
            "partner_lon": 27.9147}

    def test_a_skipped_birth_time_is_refused_before_anything_is_reserved(self):
        h = register_and_login(self.client, "tr-gap@test.bg")
        r = self.post(h, self.GAP)
        self.assertEqual(r.status_code, 400, r.text)
        self.assertIn("не съществува", r.json()["detail"])
        self.assertEqual(self.client.get("/jobs", headers=h).json()["jobs"], [])
        self.assertEqual(balances("tr-gap@test.bg"), (0, 500))              # подаръкът е недокоснат, нищо не е резервирано

    def test_a_repeated_birth_time_needs_a_choice(self):
        h = register_and_login(self.client, "tr-repeat@test.bg")
        r = self.post(h, self.REPEAT)
        self.assertEqual(r.status_code, 400, r.text)
        self.assertIn("повтаря", r.json()["detail"])
        self.assertEqual(self.client.get("/jobs", headers=h).json()["jobs"], [])

    def test_the_chosen_pass_is_used_and_saved(self):
        h = register_and_login(self.client, "tr-fold@test.bg")
        for fold, hour_utc in ((0, "00:30"), (1, "01:30")):
            with self.subTest(fold=fold):
                seen, patch = self.seen_charts()
                with patch:
                    job = self.post(h, {**self.REPEAT, "birth_fold": fold}).json()["job"]
                self.assertEqual(job["status"], "succeeded", job)
                self.assertTrue(seen[0][0]["datetime_utc"].startswith(f"2026-10-25T{hour_utc}"), seen[0][0]["datetime_utc"])
                params = self.client.get(f"/reports/{job['report_id']}", headers=h).json()["params"]
                self.assertEqual(params["birth_fold"], fold)

    def test_the_second_person_has_the_same_rule(self):
        h = register_and_login(self.client, "tr-partner@test.bg")
        gap = {**CHART, **self.PAIR, "partner_date": "2026-03-29", "partner_time": "03:30",
               "partner_lat": SOFIA[0], "partner_lon": SOFIA[1]}
        r = self.post(h, gap)
        self.assertEqual(r.status_code, 400, r.text)
        self.assertIn("не съществува", r.json()["detail"])
        repeat = {**CHART, **self.PAIR, "partner_date": "2026-10-25", "partner_time": "03:30",
                  "partner_lat": SOFIA[0], "partner_lon": SOFIA[1]}
        self.assertEqual(self.post(h, repeat).status_code, 400)
        seen, patch = self.seen_charts()
        with patch:
            job = self.post(h, {**repeat, "partner_fold": 1}).json()["job"]
        self.assertEqual(job["status"], "succeeded", job)
        self.assertTrue(seen[0][1]["datetime_utc"].startswith("2026-10-25T01:30"), seen[0][1]["datetime_utc"])

    def test_a_transit_time_in_the_skipped_hour_still_works(self):
        h = register_and_login(self.client, "tr-transit@test.bg")
        seen, patch = self.seen_charts()
        with patch:
            job = self.post(h, {**CHART, "target_date": "2026-03-29", "target_time": "03:30"}).json()["job"]
        self.assertEqual(job["status"], "succeeded", job)
        self.assertIsNotNone(seen[0][2])

    def test_a_fold_outside_zero_or_one_is_rejected(self):
        h = register_and_login(self.client, "tr-badfold@test.bg")
        self.assertEqual(self.post(h, {**self.REPEAT, "birth_fold": 2}).status_code, 422)
        self.assertEqual(self.post(h, {**self.REPEAT, "birth_fold": -1}).status_code, 422)


if __name__ == "__main__":
    unittest.main()
