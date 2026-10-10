"""
Фаза 19: часовата зона на прогнозата (AM-N03).

Датата и часът на прогнозата са местни за избрана зона, а не UTC на сървъра и не зоната на мястото на раждане. Рождените данни
остават отделна основа. Несъществуващ час се отказва, повтарящ се изисква избор.
"""
import io
import os
import unittest
import zipfile
from datetime import datetime, timedelta
from unittest import mock

import pytz

import testenv  # noqa: F401  (трябва да е преди main)
from fastapi.testclient import TestClient  # noqa: E402

import ai_interpreter  # noqa: E402
import bg_text  # noqa: E402
import engine  # noqa: E402
import generation  # noqa: E402
import main  # noqa: E402
from rate_limit import limiter  # noqa: E402
from schemas import ChartRequest  # noqa: E402
from scanner import TransitScanner  # noqa: E402
from testenv import CHART, register_and_login  # noqa: E402

SOFIA = (42.6977, 23.3219)
PLOVDIV = (42.1354, 24.7453)
A = dict(date="1990-02-15", time="13:00", lat=SOFIA[0], lon=SOFIA[1])


def request(**extra) -> ChartRequest:
    return ChartRequest(**{**CHART, **extra})


def lons(chart):
    return {n: round(p["longitude"], 6) for n, p in chart["planets"].items()}


class ZoneResolutionTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.eng = engine.get_engine()

    def utc(self, date, time, zone, fold=None):
        resolved = self.eng.resolve_local_time(date, time, 0.0, 0.0, fold, zone=zone)
        return resolved

    def test_the_same_moment_gives_the_same_sky_in_sofia_vienna_and_new_york_with_different_labels(self):
        # 09:00 UTC на 10.10.2026 = 12:00 в София (UTC+3) = 11:00 във Виена (UTC+2) = 05:00 в Ню Йорк (UTC-4)
        charts = [self.eng.calculate_chart(date, time, *SOFIA, strict=True, zone=zone)
                  for date, time, zone in (("2026-10-10", "12:00", "Europe/Sofia"), ("2026-10-10", "11:00", "Europe/Vienna"),
                                           ("2026-10-10", "05:00", "America/New_York"))]
        self.assertEqual({c["datetime_utc"][:19] for c in charts}, {"2026-10-10T09:00:00"})
        self.assertEqual(lons(charts[0]), lons(charts[1]))
        self.assertEqual(lons(charts[0]), lons(charts[2]))
        self.assertEqual([c["timezone"] for c in charts], ["Europe/Sofia", "Europe/Vienna", "America/New_York"])
        self.assertEqual([c["utc_offset_minutes"] for c in charts], [180, 120, -240])
        self.assertEqual([c["datetime_local"][-5:] for c in charts], ["12:00", "11:00", "05:00"])

    def test_the_local_date_does_not_slip_around_midnight(self):
        chart = self.eng.calculate_chart("2026-10-11", "00:30", *SOFIA, strict=True, zone="Europe/Sofia")
        self.assertEqual(chart["datetime_utc"][:19], "2026-10-10T21:30:00")          # локалният 11-и е 10-и в UTC
        self.assertEqual(chart["datetime_local"], "2026-10-11 00:30")

    def test_a_skipped_hour_is_refused_and_a_repeated_hour_needs_a_choice(self):
        with self.assertRaises(engine.TimeResolutionError) as gap:
            self.eng.calculate_chart("2026-03-29", "03:30", *SOFIA, strict=True, zone="Europe/Sofia")
        self.assertEqual(gap.exception.code, "time_nonexistent")
        with self.assertRaises(engine.TimeResolutionError) as repeat:
            self.eng.calculate_chart("2026-10-25", "03:30", *SOFIA, strict=True, zone="Europe/Sofia")
        self.assertEqual(repeat.exception.code, "time_ambiguous")
        first = self.eng.calculate_chart("2026-10-25", "03:30", *SOFIA, strict=True, zone="Europe/Sofia", fold=0)
        second = self.eng.calculate_chart("2026-10-25", "03:30", *SOFIA, strict=True, zone="Europe/Sofia", fold=1)
        self.assertEqual((first["datetime_utc"][:19], second["datetime_utc"][:19]), ("2026-10-25T00:30:00", "2026-10-25T01:30:00"))

    def test_an_unknown_zone_is_an_error(self):
        with self.assertRaises(ValueError):
            self.eng.resolve_local_time("2026-10-10", "12:00", 0.0, 0.0, zone="Europe/Nowhere")
        with self.assertRaises(Exception):
            ChartRequest(**{**CHART, "forecast_timezone": "Europe/Nowhere"})

    def test_the_zone_decides_not_the_coordinates_when_it_is_given(self):
        a = self.eng.resolve_local_time("2026-10-10", "12:00", *SOFIA)
        b = self.eng.resolve_local_time("2026-10-10", "12:00", *SOFIA, zone="America/New_York")
        self.assertEqual((a["timezone"], b["timezone"]), ("Europe/Sofia", "America/New_York"))
        self.assertNotEqual(a["utc"], b["utc"])


class BulgariaRuleTest(unittest.TestCase):
    """Фиксираният UTC+2 важи само за доказания период 1945–1979; преди него е историческата зона от IANA."""

    @classmethod
    def setUpClass(cls):
        cls.eng = engine.get_engine()

    def offset(self, date, time):
        resolved = self.eng.resolve_local_time(date, time, *SOFIA)
        return resolved["status"], resolved["utc_offset_minutes"]

    def test_1974_is_the_fixed_utc_plus_two(self):
        self.assertEqual(self.offset("1974-07-01", "12:00"), ("ok", 120))
        self.assertEqual(self.offset("1974-01-15", "12:00"), ("ok", 120))

    def test_1880_follows_the_iana_zone_not_the_modern_rule(self):
        status, minutes = self.offset("1880-06-01", "12:00")
        expected = int(pytz.timezone("Europe/Sofia").localize(datetime(1880, 6, 1, 12, 0)).utcoffset().total_seconds() // 60)
        self.assertEqual((status, minutes), ("ok", expected))
        self.assertNotEqual(minutes, 120)

    def test_the_wartime_years_before_1945_follow_the_iana_zone_too(self):
        _, minutes = self.offset("1944-06-01", "12:00")
        expected = int(pytz.timezone("Europe/Sofia").localize(datetime(1944, 6, 1, 12, 0)).utcoffset().total_seconds() // 60)
        self.assertEqual(minutes, expected)

    def test_the_boundaries_of_the_period(self):
        self.assertEqual(self.offset("1945-04-02", "12:00"), ("ok", 120))
        self.assertEqual(self.offset("1978-12-31", "23:00"), ("ok", 120))


class GenerationMomentTest(unittest.TestCase):
    def test_the_moment_is_local_to_the_forecast_zone_and_the_birth_data_do_not_change(self):
        plain = request(lat=PLOVDIV[0], lon=PLOVDIV[1], target_date="2026-10-10", target_time="12:00")
        zoned = request(lat=PLOVDIV[0], lon=PLOVDIV[1], target_date="2026-10-10", target_time="12:00",
                        forecast_timezone="Europe/Vienna")
        self.assertEqual(lons(generation.natal_chart(plain)), lons(generation.natal_chart(zoned)))
        self.assertEqual(generation.natal_chart(plain)["datetime_utc"], generation.natal_chart(zoned)["datetime_utc"])
        sofia_moment = generation.target_moment(plain)
        vienna_moment = generation.target_moment(zoned)
        self.assertEqual((sofia_moment["zone"], vienna_moment["zone"]), ("Europe/Sofia", "Europe/Vienna"))
        self.assertEqual(sofia_moment["chart"]["datetime_utc"][:19], "2026-10-10T09:00:00")
        self.assertEqual(vienna_moment["chart"]["datetime_utc"][:19], "2026-10-10T10:00:00")

    def test_without_a_time_the_current_time_in_the_zone_is_used_not_the_server_clock(self):
        with_zone = generation.target_moment(request(target_date="2026-10-10", forecast_timezone="Pacific/Auckland"))
        now_there = datetime.now(pytz.timezone("Pacific/Auckland"))
        used = datetime.strptime(with_zone["time"], "%H:%M")
        gap = abs((now_there.hour * 60 + now_there.minute) - (used.hour * 60 + used.minute))
        self.assertLessEqual(min(gap, 1440 - gap), 2)

    def test_the_default_start_of_a_period_is_today_in_the_forecast_zone(self):
        auckland = generation.period_start(request(is_dynamic=True, end_date="2027-01-01", forecast_timezone="Pacific/Auckland"))
        pago = generation.period_start(request(is_dynamic=True, end_date="2027-01-01", forecast_timezone="Pacific/Pago_Pago"))
        self.assertEqual(auckland, datetime.now(pytz.timezone("Pacific/Auckland")).strftime("%Y-%m-%d"))
        self.assertEqual(pago, datetime.now(pytz.timezone("Pacific/Pago_Pago")).strftime("%Y-%m-%d"))

    def test_validate_refuses_a_skipped_hour_and_asks_for_the_fold_of_a_repeated_one(self):
        with self.assertRaises(generation.GenerationFailure) as gap:
            generation.validate(request(target_date="2026-03-29", target_time="03:30", forecast_timezone="Europe/Sofia"))
        self.assertEqual(gap.exception.code, "invalid_input")
        self.assertIn("не съществува", gap.exception.message)
        with self.assertRaises(generation.GenerationFailure) as repeat:
            generation.validate(request(target_date="2026-10-25", target_time="03:30", forecast_timezone="Europe/Sofia"))
        self.assertIn("се повтаря", repeat.exception.message)
        generation.validate(request(target_date="2026-10-25", target_time="03:30", forecast_timezone="Europe/Sofia", target_fold=1))

    def test_the_calendar_of_a_period_uses_the_forecast_zone(self):
        eng = engine.get_engine()
        natal = eng.calculate_chart(**A)
        scanner = TransitScanner(engine_instance=eng)
        sofia = scanner.build_calendar(natal, "2026-10-01", "2026-10-31", *SOFIA, tz_name="Europe/Sofia")
        vienna = scanner.build_calendar(natal, "2026-10-01", "2026-10-31", *SOFIA, tz_name="Europe/Vienna")
        self.assertEqual((sofia.timezone, vienna.timezone), ("Europe/Sofia", "Europe/Vienna"))
        pluto_s = next(e for e in sofia.events if e["type"] == "RETROGRADE" and e["planet"] == "Pluto")
        pluto_v = next(e for e in vienna.events if e["type"] == "RETROGRADE" and e["planet"] == "Pluto")
        delta = datetime.strptime(pluto_s["when"], "%Y-%m-%d %H:%M") - datetime.strptime(pluto_v["when"], "%Y-%m-%d %H:%M")
        self.assertEqual(delta, timedelta(hours=1))
        self.assertEqual(pluto_s["_utc"][:16], pluto_v["_utc"][:16])              # един и същ UTC момент (до минута)

    def test_the_prompt_data_has_no_zone_identifier_only_the_offset(self):
        moment = generation.target_moment(request(target_date="2026-10-10", target_time="12:00"))
        offset = engine.utc_offset_label(moment["chart"]["utc_offset_minutes"])
        self.assertEqual(offset, "UTC+3")


class EndpointTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.client = TestClient(main.app)

    def setUp(self):
        limiter.reset()

    def run_job(self, headers, body):
        with mock.patch.object(main.ai_interpreter, "interpret_chart", mock.AsyncMock(return_value="<p>текст</p>")):
            return self.client.post("/jobs?wait=60", json=body, headers=headers)

    def test_the_saved_report_keeps_the_zone_the_time_and_the_utc_moment_and_the_export_uses_them(self):
        h = register_and_login(self.client, "p19-save@test.bg")
        body = {**CHART, "name": "Аз", "target_date": "2026-10-10", "target_time": "11:00", "forecast_timezone": "Europe/Vienna"}
        r = self.run_job(h, body)
        self.assertEqual(r.status_code, 200, r.text)
        job = r.json()["job"]
        self.assertEqual(job["status"], "succeeded", job)
        report = self.client.get(f"/reports/{job['report_id']}", headers=h).json()
        self.assertEqual((report["params"]["forecast_timezone"], report["params"]["target_time"]), ("Europe/Vienna", "11:00"))
        self.assertTrue(report["params"]["target_datetime_utc"].startswith("2026-10-10T09:00:00"))
        docx = self.client.get(f"/reports/{job['report_id']}/export?format=docx", headers=h)
        self.assertEqual(docx.status_code, 200)
        with zipfile.ZipFile(io.BytesIO(docx.content)) as z:
            xml = z.read("word/document.xml").decode("utf-8")
        self.assertIn("Небе на 10.10.2026 11:00 (UTC+2, транзити)", xml)

    def test_a_skipped_hour_is_a_clear_400_and_nothing_is_started(self):
        h = register_and_login(self.client, "p19-gap@test.bg")
        r = self.run_job(h, {**CHART, "target_date": "2026-03-29", "target_time": "03:30", "forecast_timezone": "Europe/Sofia"})
        self.assertEqual(r.status_code, 400, r.text)
        self.assertIn("не съществува", r.json()["detail"])

    def test_an_unknown_zone_is_rejected_by_the_schema(self):
        h = register_and_login(self.client, "p19-zone@test.bg")
        r = self.run_job(h, {**CHART, "target_date": "2026-10-10", "target_time": "11:00", "forecast_timezone": "Mars/Olympus"})
        self.assertEqual(r.status_code, 422)

    def test_time_check_accepts_a_zone_instead_of_coordinates(self):
        ok = self.client.get("/time-check", params={"date": "2026-10-10", "time": "12:00", "timezone": "America/New_York"}).json()
        self.assertEqual((ok["status"], ok["timezone"], ok["utc_offset"]), ("ok", "America/New_York", "UTC-4"))
        gap = self.client.get("/time-check", params={"date": "2026-03-29", "time": "03:30", "timezone": "Europe/Sofia"}).json()
        self.assertEqual(gap["status"], "nonexistent")
        repeat = self.client.get("/time-check", params={"date": "2026-10-25", "time": "03:30", "timezone": "Europe/Sofia"}).json()
        self.assertEqual((repeat["status"], len(repeat["options"])), ("ambiguous", 2))
        self.assertEqual(self.client.get("/time-check", params={"date": "2026-10-10", "time": "12:00"}).status_code, 400)
        self.assertEqual(self.client.get("/time-check", params={"date": "2026-10-10", "time": "12:00", "timezone": "Nowhere/City"}).status_code, 400)
        by_place = self.client.get("/time-check", params={"date": "2026-10-10", "time": "12:00", "lat": SOFIA[0], "lon": SOFIA[1]}).json()
        self.assertEqual(by_place["timezone"], "Europe/Sofia")


class ZoneWordsTest(unittest.TestCase):
    def test_zone_identifiers_become_a_bulgarian_place_or_local_time(self):
        self.assertEqual(bg_text.bulgarianize("Прогноза за 21:00 (Europe/Sofia)."), "Прогноза за 21:00 (София).")
        self.assertEqual(bg_text.bulgarianize("часовата зона America/New_York и Europe/Nowhere"), "часовата зона Ню Йорк и местно време")
        self.assertEqual(bg_text.bulgarianize("http://a/b и Europe е континент"), "http://a/b и Europe е континент")

    def test_the_rules_forbid_writing_zone_identifiers(self):
        self.assertIn("never write a time zone identifier", ai_interpreter.AIInterpreter._get_bulgarian_language_rules())


if __name__ == "__main__":
    unittest.main()
