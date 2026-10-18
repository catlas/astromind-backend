"""
„Отговор на въпроса“ в прогнозата за период (18.10.2026, по анализа на ChatGPT; потребителят реши името да остане „Отговор на вашия въпрос“).

Тестерът не е видял отделна секция „Отговор на въпроса“ в период с втори човек. Кодът винаги я пише при въпрос (един и двама), а крайната проверка
не пуска отчет с въпрос без нея. Тези тестове закрепват това поведение, за да не се загуби: с въпрос има секция с отговор (и за един, и за двама, с
периода, имената и връзката в заявката), без въпрос няма празна секция, а отчет с въпрос без секцията се отказва.

Без реален AI, мрежа и текуща дата (подменен AI, фиксиран календар). Пускане: python -m unittest discover -s tests -p "test_period_answer.py"
"""
import dataclasses
import re
import unittest

import testenv  # noqa: F401  (трябва да е преди engine)
import regression_support as rs
import period_cards
import text_check as tc
from test_period_cards import FlowBase, NAME_A, NAME_IVAN

QUESTION = "Кой период би бил подходящ за спокоен разговор за общите ни планове?"
HEADING = "### Отговор на вашия въпрос"


class AnswerSectionTest(FlowBase):
    def run_recorded(self, **kwargs):
        calls = []
        inner = self.ai()

        async def spy(system_prompt, user_prompt, max_tokens=None, temperature=None, **extra):
            calls.append((system_prompt, user_prompt))
            return await inner(system_prompt, user_prompt, max_tokens, temperature, **extra)
        return self.run_report(spy, **kwargs), calls

    @staticmethod
    def answer_of(overview):
        match = re.search(r"### Отговор на вашия въпрос\n(.+?)(?=\n### |\Z)", overview, re.S)
        return match.group(1).strip() if match else None

    @staticmethod
    def overview_call(calls):
        return next((s, u) for s, u in calls if "MODE: PERIOD OVERVIEW" in s)

    def test_a_forecast_for_one_person_with_a_question_has_a_separate_non_empty_answer(self):
        steps, calls = self.run_recorded(question=QUESTION)
        overview = steps[-1]["overview"]
        answer = self.answer_of(overview)
        self.assertIsNotNone(answer)
        self.assertGreater(len(answer), 100)
        order = [m.group(0) for m in re.finditer(r"(?m)^### .+$", overview)]
        self.assertEqual(order, ["### Най-силни моменти", "### Обща картина", "### Съгласуван план", HEADING, "### Заключение"])
        system, user = self.overview_call(calls)
        self.assertIn("[[ANSWER]]", system)
        self.assertIn(f"User question: {QUESTION}", user)

    def test_a_forecast_for_two_with_a_question_has_the_answer_and_asks_for_it_with_both_people_the_relation_and_the_period(self):
        cal = rs.calendar(True)
        steps, calls = self.run_recorded(pair=True, calendar=cal, question=QUESTION, relation="romantic")
        overview = steps[-1]["overview"]
        answer = self.answer_of(overview)
        self.assertIsNotNone(answer)
        self.assertGreater(len(answer), 100)
        system, user = self.overview_call(calls)
        self.assertIn("[[ANSWER]]", system)
        self.assertIn(f"People: {NAME_A} and {NAME_IVAN}", system)                    # и двата профила
        self.assertIn("(romantic)", system)                                               # избраната връзка
        self.assertIn(f"{cal.start} - {cal.end}", system)                                 # избраният период
        self.assertIn(period_cards.interpreter_theme("general"), system)                  # избраната тема
        self.assertIn(f"User question: {QUESTION}", user)
        self.assertIn(NAME_A, user)                                                       # проверените блокове са и за двамата
        self.assertIn(NAME_IVAN, user)

    def test_the_question_reaches_every_month_of_a_pair_forecast(self):
        _steps, calls = self.run_recorded(pair=True, question=QUESTION)
        months = [u for s, u in calls if "MODE: MONTHLY FORECAST BY FACT CARDS" in s]
        self.assertTrue(months)
        for user in months:
            self.assertIn(f"User Question: {QUESTION}", user)

    def test_without_a_question_there_is_no_answer_section_and_no_request_for_one(self):
        for pair in (False, True):
            steps, calls = self.run_recorded(pair=pair)
            overview = steps[-1]["overview"]
            self.assertNotIn("Отговор", overview, pair)
            self.assertIsNone(self.answer_of(overview))
            system, user = self.overview_call(calls)
            self.assertNotIn("[[ANSWER]]", system)
            self.assertIn("User question: none", user)
            self.assertEqual([m.group(0) for m in re.finditer(r"(?m)^### .+$", overview)],
                             ["### Най-силни моменти", "### Обща картина", "### Съгласуван план", "### Заключение"])


class CompletenessTest(unittest.TestCase):
    """Крайната проверка е гаранцията: отчет с въпрос без секцията с отговора не се доставя."""

    TEXT = "### Обща картина\nПериодът е спокоен.\n\n### Заключение\nДобър период за разговори."

    def test_a_period_with_a_question_but_no_answer_section_is_a_hard_violation(self):
        facts = dataclasses.replace(rs.facts("period", True), question=QUESTION)
        found = [v for v in tc.check_completeness(self.TEXT, facts) if v.code == "complete"]
        self.assertEqual(len(found), 1)
        self.assertEqual(found[0].severity, "hard")
        self.assertIn("Отговор на вашия въпрос", found[0].detail)

    def test_the_section_satisfies_it_and_a_period_without_a_question_does_not_need_it(self):
        facts = dataclasses.replace(rs.facts("period", True), question=QUESTION)
        with_answer = self.TEXT.replace("### Заключение", f"{HEADING}\nПървата половина на ноември е най-спокойна.\n\n### Заключение")
        self.assertEqual([v for v in tc.check_completeness(with_answer, facts) if "Отговор" in v.detail], [])
        plain = dataclasses.replace(facts, question="")
        self.assertEqual([v for v in tc.check_completeness(self.TEXT, plain) if "Отговор" in v.detail], [])


if __name__ == "__main__":
    unittest.main()
