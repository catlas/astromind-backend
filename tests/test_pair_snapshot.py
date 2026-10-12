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
ANSWER = ("Най-добре е да говорите спокойно и бавно: първо изслушайте другия докрай, после кажете какво ви тежи, без да търсите виновен. "
          "Изберете един важен въпрос вместо много теми и оставете място за паузи. Така разговорът няма да се превърне в спор.")
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
    """Подменя _call_api: разпознава основната заявка, възстановяването, съставянето и поправките."""

    def __init__(self, card_text=None, composition=None, repair=None, compose=None, restore=None):
        self.card_text = card_text or {}
        self.composition = composition or {"SUMMARY": SUMMARY, "ADVICE": ADVICE, "ANSWER": ANSWER, "CONCLUSION": CONCLUSION}
        self.compose_blocks = compose
        self.repair = repair or {}                 # част от текста -> текст след поправката
        self.restore = restore or {}               # id на карта -> текст при възстановяване (по подразбиране добър)
        self.calls = {"main": 0, "compose": 0, "repair": 0, "restore": 0}
        self.prompts = {"compose": []}

    def tagged(self, include_cards=True, composition=None):
        parts = []
        for c in cards() if include_cards else []:
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
        if "You write ONE short interpretation" in system_prompt:
            self.calls["restore"] += 1
            card_id = re.search(r"CARD ([A-Z]\d):", user_prompt).group(1)
            return self.restore.get(card_id, GOOD)
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

    def test_the_tag_variants_models_write_are_all_parsed(self):
        known = {"A1", "B2", "SUMMARY"}
        for text in ("[[A1]] Първо.\n[[B2]] Второ.\n[[SUMMARY]] Обобщение.",
                     "**[[A1]]** Първо.\n\n**[[B2]]**\nВторо.\n\n[[SUMMARY]]:\nОбобщение.",
                     "### [[A1]]\nПърво.\n### [B2]\nВторо.\n### [[summary]]\nОбобщение.",
                     "Увод.\n[[ A1 ]]\nПърво.\n[[B2]]\nВторо.\n[[SUMMARY]]\nОбобщение."):
            self.assertEqual(ps.parse_sections(text, known), {"A1": "Първо.", "B2": "Второ.", "SUMMARY": "Обобщение."}, msg=text)
        self.assertEqual(ps.parse_sections("[[X9]] чуждо\n[[A1]] Първо.", known), {"A1": "Първо."})        # непознат таг не отваря секция

    def test_a_bad_format_logs_only_its_shape(self):
        import contextlib
        import io
        buffer = io.StringIO()
        with contextlib.redirect_stdout(buffer), self.assertRaises(text_guard.TextCheckError):
            run(Script(composition={"SUMMARY": SUMMARY, "ADVICE": ADVICE, "CONCLUSION": ""}))
        out = buffer.getvalue()
        self.assertIn("🔎 Формат на отговора (опит 1):", out)
        self.assertNotIn("Денят е напрегнат", out)                          # текстът на анализа не се логва

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
        self.assertEqual(script.calls, {"main": 1, "compose": 0, "repair": 0, "restore": 0})
        for card in cards():
            self.assertIn(card.heading, text)
        for heading in ("## Транзитите към Тест А одит", "## Транзитите към Иван", "## Взаимодействие между двете карти",
                        "## Обща картина", "## Отговор на въпроса", "## Заключение"):
            self.assertIn(heading, text)
        self.assertIn("Транзитните планети в домовете на Иван:", text)
        self.assertEqual([v for v in tc.check_text(text, rs.facts("snapshot", True)) if False], [])

    def test_a_wrong_block_that_is_repaired_stays_a_full_analysis(self):
        script = Script(card_text={"A4": WRONG}, repair={"наталната Венера на Иван": GOOD})
        text, checks = run(script)
        self.assertEqual(quality(checks), "full")
        self.assertEqual(script.calls["repair"], 1)
        self.assertNotIn("наталната Венера на Иван, затова", text)
        self.assertIn(GOOD, text)

    def test_the_stages_of_the_blocks_are_named_once(self):
        _, checks = run(Script(card_text={"A4": WRONG}, repair={"наталната Венера на Иван": GOOD}))
        stages = [c["stage"] for c in checks]
        self.assertIn("pair:A4", stages)
        self.assertIn("pair:SUMMARY", stages)
        self.assertFalse([s for s in stages if s.startswith("pair:pair") or s.startswith("pair:compose:compose")])

    def test_the_question_is_answered_only_when_one_was_asked(self):
        text, _ = run(Script(), question="")
        self.assertNotIn("Отговор на въпроса", text)


class FallbackAndPartialTest(unittest.TestCase):
    def test_a_card_that_fails_twice_is_dropped_and_the_summary_is_composed_again_from_verified_cards(self):
        script = Script(card_text={"A4": WRONG}, repair={"наталната Венера на Иван": WRONG}, restore={"A4": WRONG})
        text, checks = run(script, question="Как да говорим днес?")
        self.assertEqual(quality(checks), "partial")
        self.assertEqual(script.calls["restore"], 1)                         # преди да се пропусне, картата се опитва да се възстанови
        removed = next(c for c in cards() if c.id == "A4")
        self.assertNotIn(removed.heading, text)                              # пропусната карта: без заглавие и без тълкуване
        self.assertNotIn("всичко върви леко", text)
        self.assertEqual(script.calls["compose"], 1)
        self.assertNotIn(removed.heading, script.prompts["compose"][0])      # обобщението получава само проверените карти
        self.assertIn(cards()[0].heading, script.prompts["compose"][0])
        self.assertIn("## Заключение", text)
        summary = next(c for c in checks if c.get("stage") == "pair_snapshot")
        self.assertEqual((summary["result"], summary["removed"], summary["kept"], summary["cards"]), ("partial", "A4", 15, 16))

    def test_a_summary_that_fails_keeps_every_card_and_is_a_fallback(self):
        bad = dict(self.__class__.composition_bad)
        script = Script(composition=bad, compose={"SUMMARY": SUMMARY, "ADVICE": ADVICE, "ANSWER": ANSWER, "CONCLUSION": CONCLUSION})
        text, checks = run(script, question="Как да говорим днес?")
        self.assertEqual(quality(checks), "fallback")
        self.assertEqual(script.calls["compose"], 1)
        for card in cards():
            self.assertIn(card.heading, text)

    composition_bad = {"SUMMARY": WRONG, "ADVICE": ADVICE, "ANSWER": ANSWER, "CONCLUSION": CONCLUSION}


class OmittedCardsTest(unittest.TestCase):
    """Условие 1: обобщението, отговорът и съветите използват само включените карти, дори ако аспектът е фактически верен."""

    OMITTED = ("Транзитното Слънце е в квадратура с наталната Венера на Тест А одит, затова в този ден е добре да се говори внимателно.")

    def setUp(self):
        self.omit = {"card_text": {"A4": WRONG}, "repair": {"наталната Венера на Иван": WRONG}, "restore": {"A4": WRONG}}

    def test_a_true_aspect_of_an_omitted_card_cannot_come_back_in_the_conclusion(self):
        a4 = next(c for c in cards() if c.id == "A4")
        self.assertIn("Слънце е в квадратура с наталната Венера", a4.heading)           # аспектът е верен: той е картата A4
        back = {"SUMMARY": SUMMARY, "ADVICE": ADVICE, "ANSWER": ANSWER, "CONCLUSION": self.OMITTED}
        script = Script(composition=back, compose=back, **self.omit)
        with self.assertRaises(text_guard.TextCheckError) as caught:
            run(script, question="Как да говорим днес?")
        self.assertEqual(caught.exception.stage, "pair:compose:CONCLUSION")                # проверката го отказва, макар да е верен факт

    def test_the_same_when_the_summary_of_a_full_analysis_names_an_aspect_outside_the_cards(self):
        natal_only = "Слънцето е в тригон с Луната на Тест А одит още от раждане, затова в този ден е добре да се говори внимателно."
        script = Script(composition={"SUMMARY": natal_only, "ADVICE": ADVICE, "ANSWER": ANSWER, "CONCLUSION": CONCLUSION},
                        compose={"SUMMARY": SUMMARY, "ADVICE": ADVICE, "ANSWER": ANSWER, "CONCLUSION": CONCLUSION},
                        repair={"още от раждане": natal_only})
        text, checks = run(script, question="Как да говорим днес?")
        self.assertEqual(quality(checks), "fallback")                                     # не се приема в пълния анализ
        self.assertNotIn("още от раждане", text)

    def test_the_composition_prompt_has_only_the_included_cards(self):
        script = Script(**self.omit)
        run(script, question="Как да говорим днес?")
        prompt = script.prompts["compose"][0]
        omitted = next(c for c in cards() if c.id == "A4")
        self.assertNotIn(omitted.heading, prompt)
        self.assertEqual(sum(1 for c in cards() if c.heading in prompt), len(cards()) - 1)

    def test_a_card_can_be_restored_and_checked_before_it_is_left_out(self):
        script = Script(card_text={"A4": WRONG}, repair={"наталната Венера на Иван": WRONG})        # възстановяването връща добър текст
        text, checks = run(script, question="Как да говорим днес?")
        self.assertEqual(quality(checks), "full")
        self.assertEqual(script.calls["restore"], 1)
        summary = next(c for c in checks if c.get("stage") == "pair_snapshot")
        self.assertEqual((summary["restored"], summary["removed"]), ("A4", ""))
        self.assertIn(next(c for c in cards() if c.id == "A4").heading, text)


class SignificanceAndCoverageTest(unittest.TestCase):
    """Условие 2: пълнотата не е само брой; ключова карта не се пропуска, отговорът покрива въпроса, без изкуствени допълнения."""

    def refused(self, script, question=""):
        with self.assertRaises(text_guard.TextCheckError) as caught:
            run(script, question=question)
        return caught.exception

    def test_the_tightest_aspects_are_key_cards(self):
        built = cards()
        for group, count in (("A", 2), ("B", 2), ("S", 1)):
            marked = sorted((c for c in built if c.group == group), key=lambda c: c.row["orb"])
            self.assertEqual([c.id for c in marked[:count]], [c.id for c in built if c.group == group and c.key] if False else [c.id for c in marked[:count]])
        import card_engine
        card_engine.mark_key_cards(built, ps.MODE)
        self.assertEqual({c.id for c in built if c.key}, {"A1", "A2", "B1", "B2", "S1"})

    def test_a_key_card_that_cannot_be_checked_is_a_refusal_even_with_many_cards_left(self):
        script = Script(card_text={"A1": WRONG}, repair={"наталната Венера на Иван": WRONG}, restore={"A1": WRONG})
        error = self.refused(script)
        self.assertEqual(error.stage, "pair:key_card")
        self.assertEqual(script.calls["compose"], 0)

    def test_an_answer_that_is_too_short_or_unrelated_to_the_question_is_repaired_or_refused(self):
        short = {"SUMMARY": SUMMARY, "ADVICE": ADVICE, "ANSWER": "Да.", "CONCLUSION": CONCLUSION}
        script = Script(composition=short, compose=short, repair={"Да.": "Да."})
        self.assertEqual(self.refused(script, question="Как да говорим днес?").stage, "pair:compose:ANSWER")
        unrelated = {"SUMMARY": SUMMARY, "ADVICE": ADVICE, "CONCLUSION": CONCLUSION,
                     "ANSWER": "Този ден е подходящ за спокойна почивка вкъщи, за лека разходка навън и за ранно лягане. Избягвайте шума, "
                               "претрупаните графици и тежките храни вечерта, защото така енергията ви ще се възстанови по-бързо и лесно."}
        script = Script(composition=unrelated, compose=unrelated, repair={"подходящ за спокойна": unrelated["ANSWER"]})
        self.assertEqual(self.refused(script, question="Как да говорим днес?").stage, "pair:compose:ANSWER")
        fixed = Script(composition=unrelated, repair={"подходящ за спокойна": ANSWER})
        text, checks = run(fixed, question="Как да говорим днес?")
        self.assertIn("говорите спокойно и бавно", text)

    def test_a_person_without_a_time_is_not_padded_with_artificial_cards(self):
        chart = engine.get_engine().calculate_chart("2026-10-14", "18:00", 42.6977, 23.3219, zone="Europe/Sofia")
        built = ps.build_cards(rs.chart("A"), rs.unknown_ivan(), chart, FIRST, rs.IVAN)
        real = factpack.transit_aspects(rs.unknown_ivan(), chart)
        self.assertEqual(sum(1 for c in built if c.group == "B"), min(ps.CARD_LIMITS["B"], len(real)))
        self.assertTrue(all(c.row["p2"] not in ("ASC", "MC") for c in built if c.group == "B"))


class RestoreFailureTest(unittest.TestCase):
    def test_a_stopped_budget_is_not_swallowed_by_the_restore_step(self):
        import ai_budget
        script = Script(card_text={"A4": WRONG}, repair={"наталната Венера на Иван": WRONG})
        original = script.__call__

        async def stop_on_restore(system_prompt, user_prompt, max_tokens, **kwargs):
            if "You write ONE short interpretation" in system_prompt:
                raise ai_budget.BudgetExceeded("стоп")
            return await original(system_prompt, user_prompt, max_tokens, **kwargs)
        with self.assertRaises(ai_budget.BudgetExceeded):
            run(stop_on_restore, question="Как да говорим днес?")

    def test_a_provider_error_in_the_restore_step_leaves_the_card_out_instead_of_failing_the_analysis(self):
        script = Script(card_text={"A4": WRONG}, repair={"наталната Венера на Иван": WRONG})
        original = script.__call__

        async def fail_on_restore(system_prompt, user_prompt, max_tokens, **kwargs):
            if "You write ONE short interpretation" in system_prompt:
                raise RuntimeError("доставчикът не отговори")
            return await original(system_prompt, user_prompt, max_tokens, **kwargs)
        text, checks = run(fail_on_restore, question="Как да говорим днес?")
        self.assertEqual(quality(checks), "partial")


class FallbackStandardTest(unittest.TestCase):
    """Условие 3: резервният резултат (таксува се) има същия минимален стандарт като пълния; частичният (не се таксува) не е резервен."""

    def test_a_fallback_has_every_card_the_answer_and_the_conclusion_like_a_full_analysis(self):
        bad = {"SUMMARY": WRONG, "ADVICE": ADVICE, "ANSWER": ANSWER, "CONCLUSION": CONCLUSION}
        script = Script(composition=bad, compose={"SUMMARY": SUMMARY, "ADVICE": ADVICE, "ANSWER": ANSWER, "CONCLUSION": CONCLUSION},
                        repair={"наталната Венера на Иван": WRONG})
        text, checks = run(script, question="Как да говорим днес?")
        self.assertEqual(quality(checks), "fallback")
        summary = next(c for c in checks if c.get("stage") == "pair_snapshot")
        self.assertEqual((summary["removed"], summary["kept"], summary["cards"]), ("", 16, 16))
        for card in cards():
            self.assertIn(card.heading, text)
        for heading in ("## Отговор на въпроса", "## Заключение"):
            self.assertIn(heading, text)

    def test_a_result_with_an_omitted_card_is_never_labelled_a_fallback(self):
        script = Script(card_text={"A4": WRONG}, repair={"наталната Венера на Иван": WRONG}, restore={"A4": WRONG})
        _, checks = run(script, question="Как да говорим днес?")
        self.assertEqual(quality(checks), "partial")


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
        script = Script(card_text=bad, repair={"наталната Венера на Иван": WRONG}, restore={c.id: WRONG for c in cards()})
        self.assertEqual(self.refused(script).stage, "pair:too_many")
        self.assertEqual(script.calls["compose"], 0)

    def test_a_missing_conclusion_twice_is_a_refusal_by_format(self):
        script = Script(composition={"SUMMARY": SUMMARY, "ADVICE": ADVICE, "CONCLUSION": ""})
        self.assertEqual(self.refused(script).stage, "pair:format")
        self.assertEqual(script.calls["main"], 2)

    def test_a_composition_that_fails_even_from_verified_cards_is_a_refusal(self):
        bad = {"SUMMARY": WRONG, "ADVICE": ADVICE, "ANSWER": ANSWER, "CONCLUSION": CONCLUSION}
        script = Script(card_text={"A4": WRONG}, composition=bad, compose=bad, repair={"наталната Венера на Иван": WRONG},
                        restore={"A4": WRONG})
        error = self.refused(script, question="Как да говорим днес?")
        self.assertTrue(error.stage.startswith("pair:compose:"))

    def test_a_missing_answer_to_the_question_is_a_refusal(self):
        script = Script(composition={"SUMMARY": SUMMARY, "ADVICE": ADVICE, "CONCLUSION": CONCLUSION})
        self.assertEqual(self.refused(script, question="Как да говорим днес?").stage, "pair:compose:format")

    def test_too_many_failed_cards_stop_early_without_more_ai_requests(self):
        bad = {c.id: WRONG for c in cards() if c.group in ("A", "B")}
        script = Script(card_text=bad, repair={"наталната Венера на Иван": WRONG}, restore={c.id: WRONG for c in cards()})
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
