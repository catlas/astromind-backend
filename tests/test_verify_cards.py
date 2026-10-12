"""
Сверката scripts/verify_cards.py е част от проекта: фиксирани тестови данни, преизчисление по друг път и откриване на подправени заглавия.
Тя доказва съгласуваност на фактите с позициите от engine, не независимост от ефемеридите, координатите и часовото преобразуване.
"""
import os
import sys
import unittest

import testenv  # noqa: F401  (трябва да е преди модулите)

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "scripts"))
import verify_cards as vc  # noqa: E402


class OfflineVerificationTest(unittest.TestCase):
    def test_all_fixed_cases_of_the_three_modes_match_the_recomputation(self):
        self.assertEqual(vc.run_offline(), 0)

    def case(self, name, keys, moment=None, variant=("love", "romantic", "")):
        text, people, names = vc.offline_text(name, keys, moment, variant)
        for person, display in zip(people, names):
            person["name"] = display
        sky = vc.Chart(date=moment[0], time=moment[1], lat=vc.PEOPLE[keys[0]]["lat"], lon=vc.PEOPLE[keys[0]]["lon"], zone=moment[2]) if moment else None
        return text, people, sky

    def test_the_verifier_really_parses_every_kind_of_heading(self):
        text, people, sky = self.case("natal_pair", ("A", "IVAN"))
        problems, counts = vc.verify(text, people, None, "natal_pair")
        self.assertEqual(problems, [])
        for kind in ("aspect", "placement", "overlay"):
            self.assertGreater(counts[kind], 0, kind)
        text, people, sky = self.case("pair_snapshot", ("A", "IVAN"), vc.MOMENTS[0])
        problems, counts = vc.verify(text, people, sky, "pair_snapshot", 6)
        self.assertEqual(problems, [])
        self.assertEqual((counts["transit"], counts["house_line"]), (12, 2))
        self.assertGreater(counts["aspect"], 0)                                        # синастрични карти

    def test_a_tampered_heading_is_detected(self):
        text, people, _ = self.case("natal_pair", ("A", "IVAN"))
        for old, new in (("попада в 10-и дом", "попада в 11-ти дом"), ("(орб 0,02°)", "(орб 1,02°)"), (" на Иван попада", " на Тест А одит попада")):
            if old not in text:
                continue
            problems, _ = vc.verify(text.replace(old, new, 1), people, None, "natal_pair")
            self.assertTrue(problems, old)

    def test_a_missing_or_extra_transit_card_is_detected_and_unknown_time_has_no_houses(self):
        text, people, sky = self.case("single_snapshot", ("IVAN",), vc.MOMENTS[0])
        lines = text.splitlines()
        victim = next(i for i, l in enumerate(lines) if l.startswith("### "))
        problems, _ = vc.verify("\n".join(lines[:victim] + lines[victim + 1:]), people, sky, "single_snapshot", 8)
        self.assertTrue(any("липсват" in p for p in problems))
        text, people, sky = self.case("single_snapshot", ("IVAN_NO_TIME",), vc.MOMENTS[0])
        problems, counts = vc.verify(text, people, sky, "single_snapshot", 8)
        self.assertEqual((problems, counts["house_line"]), ([], 0))                     # без час няма ред за домове


if __name__ == "__main__":
    unittest.main()
