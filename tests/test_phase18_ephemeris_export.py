"""
Фаза 18: ефемериди във всяка работна нишка и износ със същите карти (AM-N02).

Swiss Ephemeris държи пътя до файловете за всяка нишка поотделно (на Linux; на macOS не личи). Тестът го имитира на всяка система:
обвива swe.set_ephe_path и swe.calc_ut така, че Хирон да не се намира в нишка, която не е задала пътя. Старият код няма да мине.
"""
import io
import threading
import unittest
import zipfile
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from unittest import mock

import testenv  # noqa: F401  (трябва да е преди engine)
import swisseph as swe  # noqa: E402

import engine  # noqa: E402
import report_export  # noqa: E402
from database import Report  # noqa: E402
from scanner import TransitScanner  # noqa: E402

A = dict(date="1990-02-15", time="13:00", lat=42.6977, lon=23.3219)
B = dict(date="1985-07-12", time="18:45", lat=48.2082, lon=16.3738)

_real_set = swe.set_ephe_path
_real_calc = swe.calc_ut
_state = threading.local()
_set_calls = []


def fake_set(path):
    _state.has_path = True
    _set_calls.append(threading.get_ident())
    return _real_set(path)


def fake_calc(jd, body, flags=0):
    if body == swe.CHIRON and not getattr(_state, "has_path", False):
        raise swe.Error("SwissEph file 'seas_18.se1' not found in PATH '.:/users/ephe2/:/users/ephe/'")
    if not getattr(_state, "has_path", False):
        flags = (flags & ~swe.FLG_SWIEPH) | swe.FLG_MOSEPH               # както на Linux: тихо Moshier
    return _real_calc(jd, body, flags)


def positions(chart):
    return {name: (None if p["longitude"] is None else round(p["longitude"], 6)) for name, p in chart["planets"].items()}


class ThreadLocalEphemerisTest(unittest.TestCase):
    def setUp(self):
        _set_calls.clear()
        _state.has_path = False
        patcher_set = mock.patch.object(swe, "set_ephe_path", fake_set)
        patcher_calc = mock.patch.object(swe, "calc_ut", fake_calc)
        patcher_set.start()
        patcher_calc.start()
        self.addCleanup(patcher_set.stop)
        self.addCleanup(patcher_calc.stop)
        engine._ephe_thread.path = None                                      # главната нишка почва без път
        self.eng = engine.get_engine()                                       # както в main: задава пътя в главната нишка

    def calc(self, birth):
        return engine.get_engine().calculate_chart(**birth)

    def test_a_new_worker_thread_gets_chiron_and_swiss_data(self):
        main_chart = self.calc(A)
        with ThreadPoolExecutor(max_workers=1) as pool:
            worker_chart = pool.submit(self.calc, A).result()
        self.assertEqual(positions(worker_chart), positions(main_chart))
        self.assertIsNotNone(worker_chart["planets"]["Chiron"]["longitude"])
        self.assertEqual(worker_chart["ephemeris"], {"source": "swiss", "missing": []})

    def test_two_workers_and_the_main_thread_give_the_same_twelve_positions_and_houses_for_both_people(self):
        results = {}
        for label, birth in (("A", A), ("B", B)):
            main_chart = self.calc(birth)
            with ThreadPoolExecutor(max_workers=2) as pool:
                first, second = [pool.submit(self.calc, birth) for _ in range(2)]
                worker_charts = [first.result(), second.result()]
            for chart in worker_charts:
                self.assertEqual(positions(chart), positions(main_chart), label)
                self.assertEqual({n: p["house"] for n, p in chart["planets"].items()},
                                 {n: p["house"] for n, p in main_chart["planets"].items()}, label)
                self.assertEqual(chart["houses"], main_chart["houses"], label)
            results[label] = len(positions(main_chart))
        self.assertEqual(results, {"A": 12, "B": 12})

    def test_the_path_is_set_once_per_thread_not_on_every_calculation(self):
        _set_calls.clear()

        def three_charts():
            for _ in range(3):
                self.calc(A)
            return threading.get_ident()
        with ThreadPoolExecutor(max_workers=1) as pool:
            worker = pool.submit(three_charts).result()
        self.assertEqual(_set_calls.count(worker), 1)

    def test_the_scanner_works_in_a_worker_thread(self):
        def calendar():
            return TransitScanner(engine_instance=engine.get_engine()).build_calendar(
                engine.get_engine().calculate_chart(**A), "2026-10-01", "2026-10-31", A["lat"], A["lon"])
        main_events = calendar().events
        with ThreadPoolExecutor(max_workers=1) as pool:
            worker_events = pool.submit(calendar).result().events
        self.assertEqual([e["id"] for e in worker_events], [e["id"] for e in main_events])
        self.assertTrue(worker_events)

    def test_a_body_that_cannot_be_calculated_is_reported_not_hidden(self):
        with mock.patch.object(swe, "calc_ut", side_effect=lambda jd, body, flags=0: (_ for _ in ()).throw(swe.Error("няма файл"))
                               if body == swe.CHIRON else fake_calc(jd, body, flags)):
            chart = self.calc(A)
        self.assertIsNone(chart["planets"]["Chiron"]["longitude"])
        self.assertEqual(chart["ephemeris"]["missing"], ["Chiron"])

    def test_the_unknown_time_chart_carries_the_ephemeris_info_too(self):
        chart = engine.get_engine().calculate_chart(B["date"], "ignored", B["lat"], B["lon"], time_known=False)
        self.assertEqual(chart["ephemeris"], {"source": "swiss", "missing": []})


class ExportTest(unittest.TestCase):
    BASE = {"name": "Тест А", "date": A["date"], "time": A["time"], "lat": A["lat"], "lon": A["lon"],
            "report_type": "general", "birth_time_known": True}

    def report(self, **extra):
        r = Report(user_id=1, profile_name="Тест А", report_type="general", label="Общ анализ", status="completed",
                   content="<h2>Общ анализ</h2><p>Текст.</p>", cost_cents=0, params={**self.BASE, **extra})
        r.created_at = datetime(2026, 10, 10, 20, 0)
        return r

    def titles(self, report):
        return [c["title"] for c in report_export.context(report)["charts"]]

    def test_a_snapshot_export_contains_the_transit_sky_with_the_used_date_and_time(self):
        report = self.report(target_date="2026-10-10", target_time="12:00")
        charts = report_export.context(report)["charts"]
        self.assertEqual([c["title"] for c in charts], ["Карта: Тест А", "Небе на 10.10.2026 12:00 (транзити)"])
        sky = charts[1]
        self.assertEqual(sky["chart"]["datetime_local"][:16], "2026-10-10 12:00")
        self.assertTrue(sky["aspects"])
        self.assertTrue(all(a["planet1"].startswith("транзитен ") and a["planet2"].startswith("натален ") for a in sky["aspects"]))

    def test_an_old_report_without_the_saved_time_gets_no_invented_sky_and_a_period_report_none(self):
        self.assertEqual(self.titles(self.report(target_date="2026-10-10")), ["Карта: Тест А"])
        self.assertEqual(self.titles(self.report(target_date="2026-10-01", end_date="2026-10-31", is_dynamic=True,
                                                 target_time="12:00")), ["Карта: Тест А"])

    def test_the_export_in_a_worker_thread_has_chiron_and_the_same_content_as_in_the_main_thread(self):
        report = self.report(target_date="2026-10-10", target_time="12:00")

        def docx():
            return report_export.docx_bytes(report)
        main_docx = docx()
        with ThreadPoolExecutor(max_workers=1) as pool:
            worker_docx = pool.submit(docx).result()

        def text(blob):
            with zipfile.ZipFile(io.BytesIO(blob)) as z:
                return z.read("word/document.xml").decode("utf-8")
        for blob in (main_docx, worker_docx):
            xml = text(blob)
            self.assertIn("Хирон", xml)
            self.assertIn("Небе на 10.10.2026 12:00 (транзити)", xml)
        self.assertEqual(text(main_docx).count("Хирон"), text(worker_docx).count("Хирон"))

    def test_the_target_place_is_used_when_the_report_saved_one(self):
        base = report_export.context(self.report(target_date="2026-10-10", target_time="12:00"))["charts"][1]["chart"]
        other = report_export.context(self.report(target_date="2026-10-10", target_time="12:00",
                                                  target_lat=40.7128, target_lon=-74.006))["charts"][1]["chart"]
        self.assertNotEqual(base["location"], other["location"])
        self.assertEqual(round(other["location"]["latitude"], 4), 40.7128)


if __name__ == "__main__":
    unittest.main()
