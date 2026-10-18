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

    def test_the_live_natal_single_reports_of_18_10_2026_match_the_recomputation_and_pass_the_checker(self):
        """Реални натални анализи за един човек с истински AI от продукцията: фактите в заглавията са сверени с преизчисление, а под
        картите и в затварящите блокове няма факти; без час на раждане няма Асцендент, MC и домове."""
        import engine
        import regression_support as rs
        import text_check as tc
        for name, key in (("live_2026_10_18_natal_single_A.txt", "A"), ("live_2026_10_18_natal_single_A_no_time.txt", "A_NO_TIME"),
                          ("live_2026_10_18_natal_single_A_after_prompt.txt", "A"), ("live_2026_10_18_natal_single_A_final_prompt.txt", "A"),):
            with open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "fixtures", name), encoding="utf-8") as f:
                live = f.read()
            known = vc.PEOPLE[key]["known"]
            people = vc.build_people((key,))
            for person in people:
                person["name"] = vc.PEOPLE[key]["name"]
            problems, counts = vc.verify(live, people, None, "natal_single")
            self.assertEqual(problems, [], name)
            self.assertGreater(counts["interpretations"], 0, name)
            self.assertEqual(counts["closing"], 3, name)
            headings = [l for l in live.splitlines() if l.startswith("### ")]
            if not known:
                self.assertFalse([h for h in headings if "Асцендент" in h or "MC" in h or " дом" in h], name)
            natal = rs.chart("A") if known else engine.get_engine().calculate_chart("1990-02-15", "", rs.A_BIRTH["lat"], rs.A_BIRTH["lon"], time_known=False)
            facts = tc.build_facts(mode="natal", user_name=(rs.A_NAME if known else vc.PEOPLE[key]["name"]), natal_chart=natal,
                                   report_date="2026-10-18", question="")
            self.assertEqual([v for v in tc.check_text(live, facts) if v.severity == "hard"], [], name)

    def test_the_live_pair_and_snapshot_reports_of_18_10_2026_match_the_recomputation(self):
        """Реални отчети с истински AI от продукцията след последната промяна на подканите: натален за двама, „за дата“ за един и за двама
        (момент 18.10.2026 12:00, София). Фактите в заглавията са сверени с преизчисление; това не доказва ефемеридите и смисъла на текста."""
        import re
        moment = ("2026-10-18", "12:00", "Europe/Sofia")
        cases = (("live_2026_10_18_natal_pair_A_IVAN.txt", "natal_pair", ("A", "IVAN"), None, None, {"aspect": 9, "placement": 6, "overlay": 6, "closing": 3}),
                 ("live_2026_10_18_single_snapshot_A.txt", "single_snapshot", ("A",), moment, 8, {"transit": 8, "house_line": 1}),
                 ("live_2026_10_18_pair_snapshot_A_IVAN.txt", "pair_snapshot", ("A", "IVAN"), moment, 6, {"transit": 12, "aspect": 4, "house_line": 2}))
        for name, case, keys, when, limit, expected in cases:
            with open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "fixtures", name), encoding="utf-8") as f:
                live = f.read()
            people = vc.build_people(keys)
            for person, key in zip(people, keys):
                person["name"] = re.sub(r"\s*[—-]\s*", " ", vc.PEOPLE[key]["name"]).strip()
            sky = vc.Chart(date=when[0], time=when[1], lat=vc.PEOPLE[keys[0]]["lat"], lon=vc.PEOPLE[keys[0]]["lon"], zone=when[2]) if when else None
            problems, counts = vc.verify(live, people, sky, case, limit)
            self.assertEqual(problems, [], name)
            for kind, number in expected.items():
                self.assertEqual(counts[kind], number, (name, kind))

    def test_the_live_reports_of_the_final_acceptance_of_18_10_2026_match_the_recomputation(self):
        """Краят на Фаза 29: по един реален отчет на всеки режим с кода на 210a3ca (същият код като 04cb153, след него са само документи),
        записани точно както са доставени. Фактите в заглавията са сверени с преизчисление; това не доказва ефемеридите и смисъла на текста.
        Бележка: в два от текстовете AI е оставил по една английска дума („spontaneity“, „comfortable“): проверката не гледа за тях (виж ACCEPTANCE_2026_10_18.md)."""
        import re
        moment = ("2026-10-24", "18:00", "Europe/Sofia")
        cases = (("live_2026_10_18_final_natal_single_A.txt", "natal_single", ("A",), None, None, {"aspect": 4, "placement": 8, "interpretations": 12, "closing": 3}),
                 ("live_2026_10_18_final_natal_single_A_no_time.txt", "natal_single", ("A_NO_TIME",), None, None, {"aspect": 4, "placement": 6, "interpretations": 10, "closing": 3}),
                 ("live_2026_10_18_final_natal_pair_A_IVAN_love.txt", "natal_pair", ("A", "IVAN"), None, None,
                  {"aspect": 9, "placement": 6, "overlay": 6, "interpretations": 21, "closing": 4}),
                 ("live_2026_10_18_final_single_snapshot_A.txt", "single_snapshot", ("A",), moment, 8, {"transit": 8, "house_line": 1}),
                 ("live_2026_10_18_final_pair_snapshot_A_IVAN_love.txt", "pair_snapshot", ("A", "IVAN"), moment, 6, {"transit": 12, "aspect": 4, "house_line": 2}))
        for name, case, keys, when, limit, expected in cases:
            with open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "fixtures", name), encoding="utf-8") as f:
                live = f.read()
            self.assertNotIn("Резервен анализ", live, name)
            self.assertNotIn("Частичен анализ", live, name)                              # пълен резултат, без бележка за резервен или частичен
            people = vc.build_people(keys)
            for person, key in zip(people, keys):
                person["name"] = re.sub(r"\s*[—-]\s*", " ", vc.PEOPLE[key]["name"]).strip()
            sky = vc.Chart(date=when[0], time=when[1], lat=vc.PEOPLE[keys[0]]["lat"], lon=vc.PEOPLE[keys[0]]["lon"], zone=when[2]) if when else None
            problems, counts = vc.verify(live, people, sky, case, limit)
            self.assertEqual(problems, [], name)
            for kind, number in expected.items():
                self.assertEqual(counts[kind], number, (name, kind))

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

    def test_natal_single_cards_and_their_interpretations_are_verified(self):
        text, people, names = vc.offline_text("natal_single", ("A",), None, ("career", "", "Как да избера работа?"))
        for person, display in zip(people, names):
            person["name"] = display
        problems, counts = vc.verify(text, people, None, "natal_single")
        self.assertEqual(problems, [])
        for kind in ("placement", "aspect"):
            self.assertGreater(counts[kind], 0, kind)
        self.assertGreater(counts["interpretations"], 5)
        self.assertEqual(counts["closing"], 4)                                           # обща картина, съвети, отговор, заключение
        self.assertIn("Асцендентът на", text)                                             # ъглите са карти при известен час
        unknown_text, unknown_people, unknown_names = vc.offline_text("natal_single", ("IVAN_NO_TIME",), None, ("general", "", ""))
        for person, display in zip(unknown_people, unknown_names):
            person["name"] = display
        self.assertEqual(vc.verify(unknown_text, unknown_people, None, "natal_single")[0], [])
        self.assertNotIn("Асцендент", unknown_text)                                       # без час няма ъгли и домове
        self.assertNotIn(" дом", unknown_text.split("## Обща картина")[0])

    def test_a_tampered_natal_single_heading_is_detected(self):
        text, people, names = vc.offline_text("natal_single", ("A",), None, ("love", "", ""))
        for person, display in zip(people, names):
            person["name"] = display
        sun = next(l for l in text.splitlines() if l.startswith("### Слънцето на"))
        for old, new in ((" е в Водолей ", " е в Телец "), ("(26°30')", "(25°30')"), (", в 9-ти дом", ", в 10-и дом")):
            if old in sun:
                self.assertTrue(vc.verify(text.replace(sun, sun.replace(old, new, 1), 1), people, None, "natal_single")[0], old)
        angle = next(l for l in text.splitlines() if l.startswith("### Асцендентът на"))
        self.assertTrue(vc.verify(text.replace(angle, angle.replace("Близнаци", "Лъв"), 1), people, None, "natal_single")[0])            # друг знак
        self.assertTrue(vc.verify(text.replace(angle, angle[:-1] + ", в 1-ви дом.", 1), people, None, "natal_single")[0])           # ъгълът няма дом

    def test_facts_inside_the_interpretations_of_a_natal_single_report_are_detected(self):
        text, people, names = vc.offline_text("natal_single", ("A",), None, ("general", "", "Как да избера работа?"))
        for person, display in zip(people, names):
            person["name"] = display
        filler = "Това изисква търпение и ясни граници в разговорите."
        for dirty in ("Марс помага тук.", "Тук е важно да сте в Телец.", "В 7-ми дом е по-лесно.", "Градусът 12°30 е важен.", "Тригонът носи лекота."):
            self.assertTrue(vc.verify(text.replace(filler, dirty, 1), people, None, "natal_single")[0], dirty)
        summary = "Общата картина е спокойна и изисква търпение."
        for dirty in ("Венера е в Козирог.", "Асцендентът дава увереност.", "Квадратурата тежи."):
            self.assertTrue(vc.verify(text.replace(summary, dirty, 1), people, None, "natal_single")[0], dirty)
        for fine in ("Очакванията ви съвпадат повече, отколкото си мислите.", "Не влизайте в опозиция с близките си."):
            self.assertEqual(vc.verify(text.replace(filler, fine, 1), people, None, "natal_single")[0], [], fine)


if __name__ == "__main__":
    unittest.main()
