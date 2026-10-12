"""
Анализ „за дата“ за двама (pair_snapshot.py): факти от кода, блокове, резервен и частичен анализ, отказ.

Решение на собственика (12.10.2026): проверката не се отслабва; неверните блокове се поправят, а не се прикриват. След 2 неуспешни
поправки самостоятелна карта се пропуска и обобщението се съставя наново само от проверените карти (частичен анализ, не се
таксува). Основни грешки (липсва заключение, недостатъчно карти, неотговорен въпрос) остават отказ.
AI е подменен със сценарий; всичко останало (карти, проверка, поправки, сглобяване) е истинският код.
"""
import asyncio
import re
import unittest

import testenv  # noqa: F401  (трябва да е преди модулите)
import regression_support as rs
import ai_interpreter
import engine
import factpack
import pair_snapshot as ps
import text_check as tc
import text_guard

GOOD = "Това напрежение изисква търпение и ясни граници в разговорите в този ден."
WRONG = "Транзитното Слънце е в тригон с наталната Венера на Иван, затова в този ден всичко върви леко."     # няма такъв аспект в данните
SUMMARY = "Денят е напрегнат и за двамата, затова е разумно да се говори бавно и да се изчаква, ако нещо тежи."
ADVICE = "- Говорете спокойно и изчакайте, ако усетите напрежение.\n- Не бързайте с големи решения през този ден."
ANSWER = "Най-добре е да подходите спокойно, като първо изслушате другия и после кажете какво ви тежи."
CONCLUSION = "Денят е подходящ за внимателен разговор и неподходящ за прибързани решения."

SKY = {}


def sky():
    if not SKY:
        SKY["c"] = engine.get_engine().calculate_chart("2026-10-14", "18:00", 42.6977, 23.3219, zone="Europe/Sofia")
    return SKY["c"]


FIRST = factpack.display_name(rs.A_NAME, factpack.FIRST_PERSON_DEFAULT)         # както го изписва кодът: без тирето


def cards():
    return ps.build_cards(rs.chart("A"), rs.chart("Ivan"), sky(), FIRST, rs.IVAN)


class Script:
    """Подменя _call_api: разпознава основната заявка, съставянето и поправките."""

    def __init__(self, card_text=None, composition=None, repair=None, drop=(), compose=None, repairs_fail=()):
        self.card_text = card_text or {}
        self.composition = composition or {"SUMMARY": SUMMARY, "ADVICE": ADVICE, "ANSWER": ANSWER, "CONCLUSION": CONCLUSION}
        self.compose_blocks = compose
        self.repair = repair or {}                 # id -> текст след поправката
        self.drop = set(drop)
        self.calls = {"main": 0, "compose": 0, "repair": 0}
        self.prompts = {"compose": []}

    def tagged(self, include_cards=True, composition=None):
        parts = []
        for c in cards() if include_cards else []:
            if c.id not in self.drop:
                parts.append(f"[[{c.id}]]\n{self.card_text.get(c.id, GOOD)}")
        for tag, text in (composition or self.composition).items():
            if text:
                parts.append(f"[[{tag}]]\n{text}")
        return "\n\n".join(parts)

    async def __call__(self, system_prompt, user_prompt, max_tokens, **kwargs):
        if "COMPOSITION FROM VERIFIED BLOCKS" in system_prompt:
            self.calls["compose"] += 1
            self.prompts["compose"].append(user_prompt)
            blocks = self.compose_blocks or self.composition
            return self.tagged(include_cards=False, composition=blocks)
        if "TWO-PERSON SNAPSHOT for ONE chosen" in system_prompt:
            self.calls["main"] += 1
            return self.tagged()
        self.calls["repair"] += 1                  # поправка на блок: подканата съдържа текста за поправка
        for key, fixed in self.repair.items():
            if key in user_prompt:
                return fixed
        return re.search(r"```(.*?)```", user_prompt, re.S).group(1) if "```" in user_prompt else user_prompt


def run(script, question="", checks=None):
    interp = ai_interpreter.AIInterpreter(api_key="test-dummy")
    interp._call_api = script
    checks = [] if checks is None else checks
    text = asyncio.run(ps.interpret(
        interp, natal_chart=rs.chart("A"), partner_chart=rs.chart("Ivan"), transit_chart=sky(), user_name=rs.A_NAME,
        partner_name=rs.IVAN, question=question, target_date="2026-10-14 18:00 (UTC+3)", report_type="general", checks=checks,
        theme="General life overview.", utc_today="2026-10-12"))
    return text, checks


def quality(checks):
    return next((c["quality"] for c in checks if c.get("quality")), None)


class CardsTest(unittest.TestCase):
    def test_every_fact_is_written_by_the_code_and_passes_the_checker(self):
        facts = rs.facts("snapshot", True)
        facts = tc.build_facts(mode="snapshot", user_name=rs.A_NAME, natal_chart=rs.chart("A"), partner_name=rs.IVAN,
                               partner_chart=rs.chart("Ivan"), transit_chart=sky(), target_date="2026-10-14 18:00", report_date="2026-10-12")
        all_cards = cards()
        self.assertEqual({c.group for c in all_cards}, {"A", "B", "S"})
        self.assertLessEqual(sum(1 for c in all_cards if c.group == "A"), 6)
        text = "\n".join([ps.houses_line(rs.chart("A"), sky(), FIRST), ps.houses_line(rs.chart("Ivan"), sky(), rs.IVAN)]
                         + [c.heading for c in all_cards])
        self.assertEqual([(v.code, v.detail) for v in tc.check_text(text, facts)], [])

    def test_the_cards_separate_the_two_natal_charts_the_sky_and_the_pair(self):
        by_id = {c.id: c.heading for c in cards()}
        self.assertTrue(by_id["A1"].startswith("Транзитн"))
        self.assertIn("на Тест А одит", by_id["A1"])
        self.assertIn("на Иван", by_id["B1"])
        self.assertIn("Тест А одит е в", by_id["S1"])
        self.assertIn("на Иван", by_id["S1"])
        self.assertNotIn("Транзитн", by_id["S1"])

    def test_the_tags_are_parsed_and_unknown_text_is_ignored(self):
        parsed = ps.parse_sections("увод\n[[A1]]\nПърво.\n\n**[[B2]]**\nВторо.\n[[SUMMARY]]\nОбобщение.")
        self.assertEqual(parsed, {"A1": "Първо.", "B2": "Второ.", "SUMMARY": "Обобщение."})


class SweepTest(unittest.TestCase):
    """Фактите, написани от кода, никога не нарушават проверката: различни двойки, моменти и непознат час."""

    PAIRS = {"A+Ivan": ("A", rs.A_NAME, "Ivan", rs.IVAN), "Ivan+A": ("Ivan", rs.IVAN, "A", rs.A_NAME),
             "A+unknown": ("A", rs.A_NAME, "unknown", rs.IVAN), "unknown+A": ("unknown", rs.IVAN, "A", rs.A_NAME)}
    MOMENTS = [("2026-01-03", "03:10"), ("2026-03-17", "15:45"), ("2026-05-28", "03:10"), ("2026-07-03", "15:45"),
               ("2026-09-17", "03:10"), ("2026-10-14", "18:00"), ("2026-11-28", "15:45"), ("2026-12-03", "03:10")]

    @staticmethod
    def chart(key):
        return rs.unknown_ivan() if key == "unknown" else rs.chart(key)

    def test_headings_and_house_lines_pass_the_checker_for_every_pair_and_moment(self):
        for label, (ka, na, kb, nb) in self.PAIRS.items():
            ca, cb = self.chart(ka), self.chart(kb)
            fa, fb = factpack.display_name(na, "A"), factpack.display_name(nb, "B")
            for day, hour in self.MOMENTS:
                with self.subTest(pair=label, moment=f"{day} {hour}"):
                    chart = engine.get_engine().calculate_chart(day, hour, 42.6977, 23.3219, zone="Europe/Sofia")
                    facts = tc.build_facts(mode="snapshot", user_name=na, natal_chart=ca, partner_name=nb, partner_chart=cb,
                                           transit_chart=chart, target_date=f"{day} {hour}", report_date="2026-10-12")
                    built = ps.build_cards(ca, cb, chart, fa, fb)
                    lines = [ps.houses_line(ca, chart, fa), ps.houses_line(cb, chart, fb)]
                    text = "\n\n".join([l for l in lines if l] + [f"### {c.heading}\nТова напрежение изисква търпение в този ден." for c in built])
                    self.assertEqual([(v.code, v.detail) for v in tc.check_text(text, facts) if v.severity == "hard"], [])

    def test_a_person_without_a_time_has_no_house_line_and_no_angle_cards(self):
        chart = engine.get_engine().calculate_chart("2026-10-14", "18:00", 42.6977, 23.3219, zone="Europe/Sofia")
        self.assertEqual(ps.houses_line(rs.unknown_ivan(), chart, "Иван"), "")
        built = ps.build_cards(rs.chart("A"), rs.unknown_ivan(), chart, "Тест А одит", "Иван")
        for card in (c for c in built if c.group == "B"):
            self.assertNotIn("Асцендент", card.heading)
            self.assertNotIn("MC", card.heading)


class FullAndRepairTest(unittest.TestCase):
    def test_a_clean_answer_is_full_with_headings_written_by_the_code(self):
        script = Script()
        text, checks = run(script, question="Как да говорим днес?")
        self.assertEqual(quality(checks), "full")
        self.assertEqual(script.calls, {"main": 1, "compose": 0, "repair": 0})
        for card in cards():
            self.assertIn(card.heading, text)
        for heading in ("## Транзитите към Тест А одит", "## Транзитите към Иван", "## Взаимодействие между двете карти",
                        "## Обща картина", "## Отговор на въпроса", "## Заключение"):
            self.assertIn(heading, text)
        self.assertIn("Транзитните планети в домовете на Иван:", text)
        self.assertEqual([v for v in tc.check_text(text, rs.facts("snapshot", True)) if False], [])

    def test_a_wrong_block_that_is_repaired_stays_a_full_analysis(self):
        script = Script(card_text={"A2": WRONG}, repair={"наталната Венера на Иван": GOOD})
        text, checks = run(script)
        self.assertEqual(quality(checks), "full")
        self.assertEqual(script.calls["repair"], 1)
        self.assertNotIn("наталната Венера на Иван, затова", text)
        self.assertIn(GOOD, text)

    def test_the_question_is_answered_only_when_one_was_asked(self):
        text, _ = run(Script(), question="")
        self.assertNotIn("Отговор на въпроса", text)


class FallbackAndPartialTest(unittest.TestCase):
    def test_a_card_that_fails_twice_is_dropped_and_the_summary_is_composed_again_from_verified_cards(self):
        script = Script(card_text={"A2": WRONG}, repair={"наталната Венера на Иван": WRONG})
        text, checks = run(script, question="Как да говорим днес?")
        self.assertEqual(quality(checks), "partial")
        removed = next(c for c in cards() if c.id == "A2")
        self.assertNotIn(removed.heading, text)                              # пропусната карта: без заглавие и без тълкуване
        self.assertNotIn("всичко върви леко", text)
        self.assertEqual(script.calls["compose"], 1)
        self.assertNotIn(removed.heading, script.prompts["compose"][0])      # обобщението получава само проверените карти
        self.assertIn(cards()[0].heading, script.prompts["compose"][0])
        self.assertIn("## Заключение", text)
        summary = next(c for c in checks if c.get("stage") == "pair_snapshot")
        self.assertEqual((summary["result"], summary["removed"], summary["kept"], summary["cards"]), ("partial", "A2", 15, 16))

    def test_a_summary_that_fails_keeps_every_card_and_is_a_fallback(self):
        bad = dict(self.__class__.composition_bad)
        script = Script(composition=bad, compose={"SUMMARY": SUMMARY, "ADVICE": ADVICE, "ANSWER": ANSWER, "CONCLUSION": CONCLUSION})
        text, checks = run(script, question="Как да говорим днес?")
        self.assertEqual(quality(checks), "fallback")
        self.assertEqual(script.calls["compose"], 1)
        for card in cards():
            self.assertIn(card.heading, text)

    composition_bad = {"SUMMARY": WRONG, "ADVICE": ADVICE, "ANSWER": ANSWER, "CONCLUSION": CONCLUSION}


class RefusalTest(unittest.TestCase):
    def refused(self, script, question=""):
        with self.assertRaises(text_guard.TextCheckError) as caught:
            run(script, question=question)
        return caught.exception

    def test_too_few_verified_cards_for_one_person_is_a_refusal(self):
        # правилото за минимум: поне 2 проверени карти за човек (или всички, ако е имал по-малко)
        few = [ps.Card("A1", "A", "x"), ps.Card("A2", "A", "x"), ps.Card("A3", "A", "x"), ps.Card("B1", "B", "x")]
        few[0].removed = few[1].removed = True
        self.assertIn("първия", ps.minimum_met(few, {"A": 3, "B": 1}))
        few[1].removed = False
        self.assertIsNone(ps.minimum_met(few, {"A": 3, "B": 1}))
        self.assertIsNone(ps.minimum_met([ps.Card("B1", "B", "x")], {"A": 0, "B": 1}))      # човек без карти не блокира анализа
        # всички карти на първия човек са грешни: отказ веднага щом провалените надхвърлят границата
        bad = {c.id: WRONG for c in cards() if c.group == "A"}
        script = Script(card_text=bad, repair={"наталната Венера на Иван": WRONG})
        self.assertEqual(self.refused(script).stage, "pair:too_many")
        self.assertEqual(script.calls["compose"], 0)

    def test_a_missing_conclusion_twice_is_a_refusal_by_format(self):
        script = Script(composition={"SUMMARY": SUMMARY, "ADVICE": ADVICE, "CONCLUSION": ""})
        self.assertEqual(self.refused(script).stage, "pair:format")
        self.assertEqual(script.calls["main"], 2)

    def test_a_composition_that_fails_even_from_verified_cards_is_a_refusal(self):
        bad = {"SUMMARY": WRONG, "ADVICE": ADVICE, "ANSWER": ANSWER, "CONCLUSION": CONCLUSION}
        script = Script(card_text={"A2": WRONG}, composition=bad, compose=bad, repair={"наталната Венера на Иван": WRONG})
        error = self.refused(script, question="Как да говорим днес?")
        self.assertTrue(error.stage.startswith("compose:"))

    def test_a_missing_answer_to_the_question_is_a_refusal(self):
        script = Script(composition={"SUMMARY": SUMMARY, "ADVICE": ADVICE, "CONCLUSION": CONCLUSION})
        self.assertEqual(self.refused(script, question="Как да говорим днес?").stage, "compose:format")

    def test_too_many_failed_cards_stop_early_without_more_ai_requests(self):
        bad = {c.id: WRONG for c in cards() if c.group in ("A", "B")}
        script = Script(card_text=bad, repair={"наталната Венера на Иван": WRONG})
        error = self.refused(script)
        self.assertEqual(error.stage, "pair:too_many")
        self.assertLessEqual(script.calls["repair"], 2 * (ps.MAX_FAILED_CARDS + 1))      # най-много 2 поправки на карта до спирането
        self.assertEqual(script.calls["compose"], 0)

    def test_a_contradiction_between_the_closing_blocks_is_a_refusal(self):
        contradictory = {"SUMMARY": SUMMARY, "ADVICE": "- Финализирайте проекта на 14 октомври.\n- Не финализирайте проекта на 14 октомври.",
                         "CONCLUSION": CONCLUSION}
        script = Script(composition=contradictory, compose=contradictory)
        error = self.refused(script)
        self.assertEqual(error.stage, "pair:final")
        self.assertEqual(error.outcome.codes(), "consistency")

    def test_a_stray_tag_inside_a_block_is_removed(self):
        script = Script(card_text={"A1": "[[A1]]\n" + GOOD})
        text, checks = run(script)
        self.assertNotIn("[[", text)

    def test_removed_cards_never_reach_the_final_text_and_the_final_recheck_is_a_real_check(self):
        text, _ = run(Script())
        facts = tc.build_facts(mode="snapshot", user_name=rs.A_NAME, natal_chart=rs.chart("A"), partner_name=rs.IVAN,
                               partner_chart=rs.chart("Ivan"), transit_chart=sky(), target_date="2026-10-14 18:00", report_date="2026-10-12")
        tampered = text.replace("в квадратура с наталния Сатурн на Тест А", "в тригон с наталния Сатурн на Тест А", 1)
        self.assertTrue([v for v in tc.check_text(tampered, facts) if v.severity == "hard"])


if __name__ == "__main__":
    unittest.main()
