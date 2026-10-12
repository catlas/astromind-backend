"""
Фаза 23 (регресионен одит 11.10.2026): R05 здраве, R15 пълнота и измислена прецизност, R03 съгласуваност на целия отчет.

Всяко правило има и верен контрол: безопасните съвети (пауза без екрани, консултация с лекар без отлагане) и обикновените думи
(„общ преглед“, „рожден ден“) не са нарушения. Финалната проверка на отчета е детерминирана и връща засегнатата част на поправка.
"""
import asyncio
import dataclasses
import os
import unittest
from unittest import mock

import testenv  # noqa: F401  (трябва да е преди engine)
from testenv import OVERVIEW_OK
import ai_interpreter
import period_report
import regression_support as rs
import report_guard
import safety
import text_check as tc
import text_guard


def hard_codes(text, facts):
    return sorted({v.code for v in tc.check_text(text, facts) if v.severity == "hard"})


class HealthTest(unittest.TestCase):
    def test_delaying_medical_help_for_an_astrological_reason_is_found(self):
        for text in ("Не взимайте важни здравни решения около 24 октомври. Меркурий е ретрограден – възможно е объркване. Изчакайте няколко дни.",
                     "Отложете прегледа при лекар до 14 ноември, когато Венера става директна.",
                     "Не започвайте лечение, докато Меркурий е ретрограден.",
                     "Не вземайте решения за операция около пълнолунието."):
            self.assertIn("health_delay", [c for c, _ in safety.health_findings(text)], msg=text)

    def test_safe_advice_and_ordinary_words_are_not_findings(self):
        for text in ("Ако имате симптоми, потърсете лекар без отлагане, независимо от астрологичния период.",
                     "Не отлагайте прегледа при лекар заради ретрограден Меркурий.",
                     "Не започвайте нова терапия без консултация с лекар.",
                     "Направете пауза без екрани и подредете деня си около 24 октомври.",
                     "Изчакайте няколко дни, преди да подпишете договора около 24 октомври.",
                     "Общ преглед на периода: прегледайте старите договорки.",
                     "Сърцето на проблема е в комуникацията – Слънце в 7-ми дом."):
            self.assertEqual(safety.health_findings(text), [], msg=text)

    def test_organs_as_a_known_problem_are_found_but_symbolic_wording_is_not(self):
        self.assertEqual([c for c, _ in safety.health_findings("Транзитът на Марс ще причини проблеми със стомаха.")], ["health_organ"])
        self.assertEqual([c for c, _ in safety.health_findings("Имате слаб стомах и уязвимо сърце заради Сатурн.")], ["health_organ"])
        self.assertEqual(safety.health_findings("Темата за храносмилането и стреса може да се усети по-силно при натоварен ритъм."), [])

    def test_the_final_net_replaces_the_delay_with_one_safe_sentence(self):
        text, flags = safety.check_output("<p>Не взимайте важни здравни решения около 24 октомври. Изчакайте няколко дни.</p><p>Сън и ритъм.</p>", "health")
        self.assertIn("health_delay", flags)
        self.assertEqual(text.count(safety.HEALTH_DELAY_REPLACEMENT), 1)
        self.assertNotIn("Изчакайте", text)
        self.assertIn("Сън и ритъм.", text)

    def test_the_rule_is_in_the_prompt_and_in_the_checker(self):
        self.assertIn("НИКОГА не съветвай да се отложи консултация", safety.SAFETY_RULES)
        violations = tc.check_text("Не вземайте здравни решения около 24 октомври.", rs.facts("natal", False))
        self.assertIn(("health", "hard"), [(v.code, v.severity) for v in violations])


class EditorBiographyTest(unittest.TestCase):
    def test_an_editor_self_correction_is_found_and_ordinary_vsashnost_is_not(self):
        self.assertTrue(safety.editor_leaks("Юпитер е в ретрограден Сатурн... – всъщност Юпитер е в 2-ри дом."))
        self.assertFalse(safety.editor_leaks("Всъщност това не е външна пречка, а вътрешен конфликт."))

    def test_family_biography_as_a_fact_is_found_unless_it_is_hedged(self):
        for text in ("Майка ви е била строга и недостъпна.", "Това е моделът – родител, който е бил емоционално недостъпен.",
                     "Моделът се предава през родовата линия."):
            self.assertTrue(safety.biography_findings(text), msg=text)
        for text in ("Може да се свързва с модел на недостъпност, без да е факт за вашите родители.",
                     "Тест А е родител на детето, затова темата е грижа.", "Символично темата за родовите модели е за собствените реакции."):
            self.assertEqual(safety.biography_findings(text), [], msg=text)


class CompletenessTest(unittest.TestCase):
    def test_the_overview_needs_its_four_parts(self):
        facts = dataclasses.replace(rs.facts("period", False), mode="overview")
        self.assertEqual([v.code for v in tc.check_completeness(OVERVIEW_OK, facts)], [])
        missing = tc.check_completeness("<p><b>Накратко</b>: смесен период.</p>", facts)
        self.assertEqual([v.code for v in missing], ["complete"])
        self.assertIn("Най-силни моменти", missing[0].detail)

    def test_a_month_with_a_question_needs_the_answer_section(self):
        facts = rs.facts("period", False)
        facts.question = "Кога да сменя работата?"
        text = "<p>Месецът е смесен.</p>"
        self.assertEqual([v.code for v in tc.check_completeness(text, facts)], ["complete"])
        with_answer = text + "<h3>Отговор на вашия въпрос: Кога да сменя работата?</h3><p>Изчакайте втората половина на месеца.</p>"
        self.assertEqual(tc.check_completeness(with_answer, facts), [])

    def test_a_cut_off_text_is_found_and_a_list_without_a_final_period_is_not(self):
        facts = rs.facts("natal", False)
        for text in ("<p>Слънцето в Водолей дава самостоятелност и жажда за знание, но понякога и</p>",
                     "<p>Първата стъпка е да подредите финансовите документи преди да вземете решение за</p>",
                     "<p>Това е дълго изречение без край което продължава и продължава и така нататък без точка в края</p>"):
            self.assertEqual([v.code for v in tc.check_completeness(text, facts)], ["complete"], msg=text)
        for text in ("<p>Слънцето в Водолей дава самостоятелност.</p>", "<ul><li>Първо: подредете документите</li><li>Второ: разговор с шефа</li></ul>",
                     "<p>Добре сте.</p>\n<h3>Съвет</h3>",
                     "Слънцето дава самостоятелност.\n\n| Дата | Събитие, което се случва през октомври и важи за целия месец |\n|---|---|\n| 10.10 | Новолуние във Везни за всички |"):
            self.assertEqual(tc.check_completeness(text, facts), [], msg=text)


class PrecisionTest(unittest.TestCase):
    def test_no_invented_time_window_in_a_moment_or_natal_analysis(self):
        snapshot = rs.facts("snapshot", False)
        self.assertIn("time", hard_codes("Планирайте разговор на 10 октомври след 14:00, когато Луната подкрепя диалога.", snapshot))
        self.assertEqual(hard_codes("Анализът е за 10 октомври, 12:00.", snapshot), [])
        self.assertEqual(snapshot.snapshot_time, "12:00")

    def test_today_is_not_said_for_another_selected_date(self):
        facts = rs.facts("snapshot", False)
        facts.report_date = "2026-10-20"
        self.assertIn("date", hard_codes("Днес Луната е в 9-ти дом.", facts))
        facts.report_date = "2026-10-11"          # един ден разлика (часови зони): не се тълкува като друга дата
        self.assertEqual(hard_codes("Днес Луната е във Везни.", facts), [])

    def test_natal_texts_have_no_transit_dates_but_a_birthday_is_fine(self):
        facts = rs.facts("natal", False)
        facts.report_date = "2026-10-11"
        self.assertIn("date", hard_codes("През 24 октомври Сатурн ще подкрепи кариерата ви.", facts))
        self.assertEqual(hard_codes("Роден сте на 12 юли, а темата за корените е важна.", facts), [])

    def test_a_percent_as_a_probability_is_hard_and_a_budget_share_is_soft(self):
        facts = rs.facts("natal", False)
        self.assertIn("percent", hard_codes("В 80% от случаите ще продължите.", facts))
        soft = [v for v in tc.check_text("Отделете 10% от бюджета за обучение.", facts) if v.code == "percent"]
        self.assertEqual([v.severity for v in soft], ["soft"])


class CrossPartTest(unittest.TestCase):
    def setUp(self):
        self.facts = rs.facts("period", False)
        self.months = ["2026-10", "2026-11"]

    def parts(self, october, november="<p>Ноември е спокоен.</p>", overview=OVERVIEW_OK):
        return [("month:2026-10", october), ("month:2026-11", november), ("overview", overview)]

    def test_no_data_for_a_month_that_is_in_the_report(self):
        found = report_guard.cross_part_violations(self.parts("<p>Октомври е натоварен.</p>", "<p>Няма данни за октомври, затова ноември е спокоен.</p>"),
                                                   self.months, self.facts)
        self.assertEqual(list(found), ["month:2026-11"])
        self.assertEqual(found["month:2026-11"][0].code, "consistency")

    def test_a_recommendation_and_a_prohibition_on_the_same_date(self):
        found = report_guard.cross_part_violations(
            self.parts("<p>Финализирайте проекта на 24 октомври.</p>", overview=OVERVIEW_OK + "<p>Не финализирайте проекта на 24 октомври.</p>"),
            self.months, self.facts)
        self.assertIn("overview", found)
        self.assertIn("и препоръка, и забрана", found["overview"][0].detail)

    def test_something_postponed_to_a_later_date_is_not_recommended_earlier(self):
        found = report_guard.cross_part_violations(
            self.parts("<p>Втората граница поставете след 14 ноември.</p>", overview=OVERVIEW_OK + "<p>Поставете втората граница на 31 октомври.</p>"),
            self.months, self.facts)
        self.assertIn("overview", found)
        self.assertIn("отложено до 14 ноември", found["overview"][0].detail)

    def test_a_consistent_report_has_no_findings(self):
        found = report_guard.cross_part_violations(
            self.parts("<p>Финализирайте проекта на 24 октомври.</p>", overview=OVERVIEW_OK + "<p>Финализирайте проекта на 24 октомври.</p>"),
            self.months, self.facts)
        self.assertEqual(found, {})

    def test_advice_for_two_different_people_is_not_a_contradiction(self):
        pair = rs.facts("period", True)
        found = report_guard.cross_part_violations(
            [("month:2026-10", "<p>Иван: финализирайте проекта на 24 октомври.</p><p>Тест А — одит: не финализирайте проекта на 24 октомври.</p>"),
             ("overview", OVERVIEW_OK)], ["2026-10"], pair)
        self.assertEqual(found, {})

    def test_a_distant_negation_is_not_read_as_a_prohibition(self):
        found = report_guard.cross_part_violations(
            self.parts("<p>Не бързайте с останалото, но финализирайте проекта на 24 октомври.</p>",
                       overview=OVERVIEW_OK + "<p>Финализирайте проекта на 24 октомври.</p>"), self.months, self.facts)
        self.assertEqual(found, {})

    def test_a_deadline_with_until_is_not_a_postponement(self):
        # Живо приемане 12.10.2026: „до 24 ноември“ е срок, а не отлагане. Препоръка на по-ранна дата не го опровергава.
        for sentence in ("До 24 ноември финализирайте решението за връзката. На 13 ноември обсъдете решението спокойно.",
                         "Разговорът за бъдещето да е завършен до 24 ноември. На 13 ноември е добър момент за разговор.",
                         "До 24 ноември подпишете решението, а на 13 ноември е най-добрият момент да вземете решение.",
                         "Започнете до 20 ноември подготовката. На 12 ноември започнете с първата стъпка."):
            found = report_guard.cross_part_violations(self.parts("<p>Октомври е спокоен.</p>", overview="<p>%s</p>" % sentence), self.months, self.facts)
            self.assertEqual(found, {}, msg=sentence)

    def test_waiting_until_a_date_and_after_a_date_are_still_postponements(self):
        for sentence in ("Изчакайте до 14 ноември с решението. На 31 октомври вземете решение.",
                         "Не финализирайте проекта до 14 ноември. На 31 октомври финализирайте проекта.",
                         "Решението вземете след 14 ноември. На 31 октомври вземете решение."):
            found = report_guard.cross_part_violations(self.parts("<p>Октомври е спокоен.</p>", overview="<p>%s</p>" % sentence), self.months, self.facts)
            self.assertIn("overview", found, msg=sentence)

    def test_a_plain_later_in_a_sentence_is_not_a_postponement(self):
        found = report_guard.cross_part_violations(
            self.parts("<p>На 31 октомври е добър момент за разговор.</p>",
                       overview="<p>Около 2 и 7 ноември Марс е напрегнат – думи, които по-късно съжалявате, и решение на 14 ноември.</p>"),
            self.months, self.facts)
        self.assertEqual(found, {})

    def test_the_real_october_and_november_texts_from_the_live_run_are_consistent(self):
        base = os.path.join(os.path.dirname(os.path.abspath(__file__)), "fixtures")
        october = open(os.path.join(base, "live_2026_10_12_love_october.txt"), encoding="utf-8").read()
        november = open(os.path.join(base, "live_2026_10_12_love_november.txt"), encoding="utf-8").read()
        found = report_guard.cross_part_violations([("month:2026-10", october), ("month:2026-11", november)], self.months, self.facts)
        self.assertEqual(found, {})

    def test_a_candidate_text_is_checked_against_the_other_parts(self):
        parts = self.parts("<p>Финализирайте проекта на 24 октомври.</p>")
        check = report_guard.part_check(parts, "overview", self.months, self.facts)
        self.assertEqual(check(OVERVIEW_OK + "<p>Финализирайте на 24 октомври.</p>"), [])
        self.assertTrue(check(OVERVIEW_OK + "<p>Не финализирайте на 24 октомври.</p>"))


class FinalGuardFlowTest(unittest.TestCase):
    """run_period_report с истинския интерпретатор: подменени са само вътрешните AI викове."""

    def setUp(self):
        self.interp = ai_interpreter.AIInterpreter(api_key="test-dummy")
        self.cal = rs.calendar(False)

    def run_report(self, month, overview, repair):
        async def monthly(**kwargs):
            return month

        async def go():
            steps = []
            async for step in period_report.run_period_report(
                    self.interp, calendar=self.cal, natal_chart=rs.chart("Ivan"), partner_chart=None, report_type="general",
                    user_name="Иван", partner_name=None, question="", report_date="2026-10-11", retry_pause=0):
                steps.append(step)
            return steps
        with mock.patch.object(self.interp, "_process_monthly_chunk", side_effect=monthly), \
                mock.patch.object(self.interp, "compose_period_overview", mock.AsyncMock(return_value=overview)), \
                mock.patch.object(self.interp, "_call_api", repair):
            return asyncio.run(go())

    MONTH = "<p>Финализирайте проекта на 24 октомври, когато Меркурий е директен.</p>"

    def test_a_contradicting_overview_is_repaired_before_it_is_shown(self):
        bad = OVERVIEW_OK + "<p>Не финализирайте проекта на 24 октомври.</p>"
        good = OVERVIEW_OK + "<p>Финализирайте проекта на 24 октомври.</p>"
        repair = mock.AsyncMock(return_value=good)
        steps = self.run_report(self.MONTH, bad, repair)
        self.assertEqual(repair.await_count, 1)
        self.assertEqual(steps[-1]["overview"], good)
        self.assertEqual([c["stage"] for c in steps[-1]["checks"]][-1], "final:overview")
        self.assertEqual([s["type"] for s in steps].index("overview_complete") < [s["type"] for s in steps].index("finished"), True)

    def test_a_consistent_report_costs_no_extra_ai_call(self):
        repair = mock.AsyncMock(side_effect=AssertionError("поправка не трябва да се вика"))
        steps = self.run_report(self.MONTH, OVERVIEW_OK + "<p>Финализирайте проекта на 24 октомври.</p>", repair)
        self.assertEqual(steps[-1]["type"], "finished")
        self.assertEqual(repair.await_count, 0)

    def test_an_unresolved_contradiction_fails_the_report_and_nothing_is_finished(self):
        bad = OVERVIEW_OK + "<p>Не финализирайте проекта на 24 октомври.</p>"
        repair = mock.AsyncMock(return_value=bad)               # поправката не поправя
        steps = []
        with self.assertRaises(period_report.ForecastGenerationError) as caught:
            steps = self.run_report(self.MONTH, bad, repair)
        self.assertEqual(caught.exception.stage, "final:overview")
        self.assertIsInstance(caught.exception.cause, text_guard.TextCheckError)
        self.assertEqual(steps, [])

    def test_a_month_with_a_missing_month_claim_is_replaced_and_announced(self):
        month = "<p>Няма данни за октомври, затова започвайте спокойно.</p>"
        fixed = "<p>Започвайте спокойно през октомври.</p>"
        repair = mock.AsyncMock(return_value=fixed)
        steps = self.run_report(month, OVERVIEW_OK, repair)
        replaced = [s for s in steps if s["type"] == "part_replaced"]
        self.assertEqual(len(replaced), 1)
        self.assertEqual(replaced[0]["text"], fixed)
        self.assertEqual(steps[-1]["month_texts"][0][1], fixed)


class RepairPromptTest(unittest.TestCase):
    def test_the_repair_prompt_allows_adding_a_missing_section_without_new_facts(self):
        system_prompt, _ = text_guard.build_repair_prompts("текст", [tc.Violation("complete", "hard", "край", "Липсва секция")])
        self.assertIn("ONLY facts that are already stated in the text", system_prompt)
        self.assertIn("never delay medical help", system_prompt)


if __name__ == "__main__":
    unittest.main()
