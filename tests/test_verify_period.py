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
        for dirty in ("Марс е планетата на конфликтите.", "В Скорпион е по-трудно.", "Около 14:30 ще се реши.", "На 24 октомври ще се реши.",
                      "Ретроградният период изисква търпение."):
            text = self.text.replace("Това изисква търпение и ясни граници в разговорите.", dirty, 1)
            self.assertTrue(self.verify(text)[0], dirty)

    def test_a_date_that_is_not_in_a_card_is_detected_in_the_closing_blocks(self):
        text = self.text.replace("- Говорете спокойно.", "- Говорете спокойно на 3 ноември.")
        self.assertTrue(any("дата" in p for p in self.verify(text)[0]))

    def test_all_fixed_cases_match_the_recomputation(self):
        self.assertEqual(vp.run_offline(), 0)


if __name__ == "__main__":
    unittest.main()
