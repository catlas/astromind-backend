"""
Сверката scripts/verify_period.py е част от проекта: фиксирани тестови данни, преизчисление на точните моменти (местен час -> UTC през
zoneinfo), на посоката, на най-близкото приближение и на интервала на действие, и откриване на подправени заглавия.
Тя доказва съгласуваност на фактите с позициите от Swiss Ephemeris, не независимост от ефемеридите и координатите.
"""
import os
import re
import sys
import unittest

import testenv  # noqa: F401  (трябва да е преди модулите)

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "scripts"))
import verify_period as vp  # noqa: E402

DST_CASE = (("IVAN", "A"), "2026-10-20", "2026-10-31")                   # смяната на часа е на 25 октомври


class OfflineVerificationTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.text, cls.people = vp.offline_text(*DST_CASE)

    def verify(self, text):
        return vp.verify_period(text, self.people, DST_CASE[1], DST_CASE[2])

    def test_the_period_across_the_change_of_the_clock_matches_the_recomputation(self):
        problems, counts = self.verify(self.text)
        self.assertEqual(problems, [])
        for kind in ("exact", "interval", "station", "ingress", "lunation"):
            self.assertGreater(counts[kind], 0, kind)

    def test_a_wrong_time_is_detected_even_across_the_change_of_the_clock(self):
        # един час разлика е точно грешката от смяната на часа (UTC+3 до 04:00 на 25 октомври, после UTC+2)
        m = re.search(r"### (\d{1,2}) октомври, (\d{2}):(\d{2}) – Транзитн", self.text)
        shifted = self.text.replace(m.group(0), f"### {m.group(1)} октомври, {(int(m.group(2)) + 1) % 24:02d}:{m.group(3)} – Транзитн", 1)
        problems, _ = self.verify(shifted)
        self.assertTrue(any(p.startswith("час ") or "разстоянието" in p or "брой" in p for p in problems), problems)

    def test_a_confused_owner_is_detected(self):
        a, ivan = self.people[0]["name"], self.people[1]["name"]
        line = next(l for l in self.text.splitlines() if l.startswith("### ") and f" на {ivan}" in l and "Транзитн" in l)
        swapped = self.text.replace(line, line.replace(f" на {ivan}", f" на {a}"), 1)
        problems, _ = self.verify(swapped)
        self.assertTrue(problems)

    def test_a_wrong_motion_a_wrong_interval_and_a_wrong_sign_are_detected(self):
        wrong_motion = self.text.replace("движение: директно", "движение: ретроградно", 1)
        self.assertTrue(any("движение" in p for p in self.verify(wrong_motion)[0]))
        wrong_interval = re.sub(r"Интервал на действие: от (\d+) ", lambda mm: f"Интервал на действие: от {int(mm.group(1)) + 3} ", self.text, count=1)
        self.assertTrue(any("интервал" in p for p in self.verify(wrong_interval)[0]))
        wrong_sign = re.sub(r"(### [^\n]*?навлиза (?:в|във) )[А-Яа-я]+", r"\1Овен", self.text, count=1)
        self.assertTrue(any("знак" in p for p in self.verify(wrong_sign)[0]))

    def test_facts_inside_an_interpretation_are_detected(self):
        self.assertEqual(self.verify(self.text.replace("Това изисква търпение и ясни граници в разговорите.", "Очакванията към общия ви дом излизат напред.", 1))[0], [])
        for dirty in ("Марс е планетата на конфликтите.", "Планетата е в 7-ми дом.", "В Скорпион е по-трудно.", "Около 14:30 ще се реши.", "На 3 януари ще се реши.",
                      "Ретроградният период изисква търпение."):
            text = self.text.replace("Това изисква търпение и ясни граници в разговорите.", dirty, 1)
            self.assertTrue(self.verify(text)[0], dirty)

    def test_a_date_that_is_not_in_a_card_is_detected_in_the_closing_blocks(self):
        text = self.text.replace("- Говорете спокойно.", "- Говорете спокойно на 3 ноември.")
        self.assertTrue(any("дата" in p for p in self.verify(text)[0]))

    def test_the_live_report_of_12_10_2026_matches_the_recomputation_and_passes_the_checker(self):
        """Реален отчет от продукцията (двойка, 20–31 октомври, през смяната на часа), написан от AI върху картите на кода."""
        with open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "fixtures", "live_2026_10_12_period_pair_dst.txt"), encoding="utf-8") as f:
            live = f.read()
        problems, counts = vp.verify_period(live, self.people[::-1], "2026-10-20", "2026-10-31")
        self.assertEqual(problems, [])
        self.assertEqual((counts["exact"], counts["nearest"], counts["ingress"], counts["lunation"]), (5, 1, 1, 1))
        import regression_support as rs
        import text_check as tc
        import period_cards
        from test_period_cards import dst_calendar
        cal = dst_calendar()
        facts = tc.build_facts(mode="period", user_name=rs.A_NAME, natal_chart=rs.chart("A"), partner_name=rs.IVAN, partner_chart=rs.chart("Ivan"),
                               calendar=cal, report_date="2026-10-12", question="Как да говорим спокойно за очакванията си?")
        hard = [v for v in tc.check_text(live, facts) if v.severity == "hard"]
        self.assertEqual(hard, [])

    def test_the_live_single_report_in_vienna_matches_the_recomputation_and_passes_the_checker(self):
        """Реален отчет за един човек (Виена, 20–31 октомври): зоната е записана с българското име на града."""
        with open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "fixtures", "live_2026_10_12_period_single_dst.txt"), encoding="utf-8") as f:
            live = f.read()
        ivan = [p for p in self.people if p["name"] == "Иван"]
        problems, counts = vp.verify_period(live, ivan, "2026-10-20", "2026-10-31")
        self.assertEqual(problems, [])
        self.assertEqual((counts["exact"], counts["nearest"], counts["station"], counts["ingress"], counts["lunation"]), (3, 1, 1, 2, 1))
        import engine
        import regression_support as rs
        import text_check as tc
        from scanner import TransitScanner
        cal = TransitScanner(engine_instance=engine.get_engine()).build_calendar(
            rs.chart("Ivan"), "2026-10-20", "2026-10-31", rs.IVAN_BIRTH["lat"], rs.IVAN_BIRTH["lon"], tz_name="Europe/Vienna")
        facts = tc.build_facts(mode="period", user_name=rs.IVAN, natal_chart=rs.chart("Ivan"), calendar=cal, report_date="2026-10-12",
                               question="Кога е подходящо да започна разговор за промяна в работата?")
        self.assertEqual([v for v in tc.check_text(live, facts) if v.severity == "hard"], [])

    def test_the_verifier_demands_the_marker_in_the_repeated_hour_and_uses_it(self):
        sky = vp.Sky("Europe/Sofia")
        self.assertTrue(sky.ambiguous(2026, 10, 25, "03:30"))
        self.assertFalse(sky.ambiguous(2026, 10, 25, "12:00"))
        self.assertEqual((sky.utc(2026, 10, 25, "03:30", "зимно") - sky.utc(2026, 10, 25, "03:30", "лятно")).total_seconds(), 3600)
        problems = []
        vp.moment_of(sky, {"title": "x", "smap": {}}, 2026, 10, 25, "03:30", problems)
        self.assertTrue(any("повтарящ се час" in p for p in problems))
        problems = []
        vp.moment_of(sky, {"title": "x", "smap": {(25, 10, "03:30"): "зимно"}}, 2026, 10, 25, "03:30", problems)
        self.assertEqual(problems, [])

    def test_the_other_live_reports_of_12_10_2026_match_the_recomputation(self):
        """Работна двойка (20–31 октомври) и двойка за два месеца (20 октомври – 30 ноември): реални текстове от продукцията."""
        import regression_support as rs
        import text_check as tc
        from scanner import TransitScanner
        import engine
        for name, end, question in (("live_2026_10_12_period_pair_work.txt", "2026-10-31", "Как да си разпределим задачите, за да не се дразним?"),
                                    ("live_2026_10_12_period_pair_dst_second.txt", "2026-10-31", "Как да говорим спокойно за очакванията си?"),
                                    ("live_2026_10_12_period_pair_two_months.txt", "2026-11-30", "")):
            with open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "fixtures", name), encoding="utf-8") as f:
                live = f.read()
            problems, counts = vp.verify_period(live, self.people, "2026-10-20", end)
            self.assertEqual(problems, [], name)
            cal = TransitScanner(engine_instance=engine.get_engine()).build_calendar(
                rs.chart("A"), "2026-10-20", end, rs.A_BIRTH["lat"], rs.A_BIRTH["lon"], partner_chart=rs.chart("Ivan"), tz_name="Europe/Sofia")
            facts = tc.build_facts(mode="period", user_name=rs.A_NAME, natal_chart=rs.chart("A"), partner_name=rs.IVAN, partner_chart=rs.chart("Ivan"),
                                   calendar=cal, report_date="2026-10-12", question=question)
            self.assertEqual([v for v in tc.check_text(live, facts) if v.severity == "hard"], [], name)

    def test_all_fixed_cases_match_the_recomputation(self):
        self.assertEqual(vp.run_offline(), 0)


if __name__ == "__main__":
    unittest.main()
