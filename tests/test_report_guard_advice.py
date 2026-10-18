"""
Само съвет е препоръка (report_guard, 18.10.2026, второ живо приемане).

От 10 пускания на период 4 бяха отказани. Журналът за пръв път показа изреченията: проверката броеше за „препоръка на дата“ всяко изречение с дума за
действие и дата, също и описателните от тълкуванията на картите („На 24 октомври започва период, в който ежедневните разговори, задачи и
уговорки …“, „От 6 декември … разговори стават …“, „… което ви ангажира“), и те влизаха в противоречие с прозорците на съветите; „избягвате“ не се
четеше като отрицание; „след 25 декември“ в декемврийския блок се сравняваше с препоръки от октомври.

Тук: изреченията са от живите откази и от реалните приети отчети; каноничните противоречия остават; всяка част е проверена със „счупване“.
"""
import contextlib
import io
import os
import re
import unittest
from datetime import date

import testenv  # noqa: F401  (трябва да е преди engine)
import regression_support as rs
import report_guard
import safety
import text_check as tc
import text_guard

MONTHS = ["2026-10", "2026-11", "2026-12"]

# Реални описателни изречения от тълкуванията на картите (текстовете са от продукцията на 18.10.2026): не са съвет
DESCRIPTIVE = (
    "На 24 октомври започва период, в който ежедневните разговори, задачи и уговорки могат да се объркват или да се връщат назад за уточняване.",
    "На 9 ноември започва нов цикъл в темата за ежедневната работа, задълженията и грижата за собственото здраве.",
    "От 6 декември ежедневните ви задачи и начинът, по който говорите за тях, стават по-подвижни и по-лесно се подреждат в думи.",
    "От 25 декември разговорите се насочват към партньорства, договори и близки взаимоотношения.",
    "От 25 декември разговорите ви с партньори, колеги и близки стават по-практични и ориентирани към конкретни договорки.",
    "От 13 ноември разговорът и мисленето в областта на личните ви занимания, творчеството и удоволствията се улесняват.",
    "На 24 октомври започва обратен ход в начина, по който общувате и подреждате ежедневните си задачи.",
    "В интервала от 15 до 27 декември се отваря прозорец за бързи, неочаквани решения, които ви освобождават от застой.",
    "Между 27 октомври и 1 ноември за Тест А одит може да се изостри тема за контрол или за това кой взема решенията в общите ви начинания.",
)
# Изречения със съвет от същите отчети: остават препоръка (повелително, „да + глагол“, „добър момент“, „добре е“)
ADVICE = (
    "На 31 октомври е добър момент да вземете решение за нова стъпка.",
    "От 24 октомври до края на месеца е по-добре да избягвате финализиране на договорки и подписване.",
    "- От 24 октомври до 31 октомври избягвайте финализиране на договорки и подписване.",
    "Около 13 ноември е добре да започнете разговора с близките си.",
    "Добри моменти за разговор и изясняване са около 13 ноември.",
    "От 3 март комуникацията се улеснява, така че използвайте този период за разговори и изясняване.",
    "Подписването планирайте след 14 ноември.",
    "Не финализирайте проекта на 24 октомври.",
)


def statements(sentence, month_start=None):
    return report_guard._statements(sentence, rs.facts("period", False, "2026-12-31"), month_start)


class AdviceGateTest(unittest.TestCase):
    def test_the_real_descriptive_sentences_are_not_recommendations(self):
        for sentence in DESCRIPTIVE:
            self.assertFalse(report_guard._is_advice(sentence), sentence)
            self.assertEqual(statements(sentence), [], sentence)

    def test_the_real_advice_sentences_stay_recommendations(self):
        for sentence in ADVICE:
            self.assertTrue(report_guard._is_advice(sentence), sentence)
            self.assertTrue(statements(sentence), sentence)

    def test_every_form_of_advice_is_a_signal(self):
        """Всяко изречение има точно един сигнал: така пропускането на който и да е от тях се вижда."""
        for sentence in ("Вземете решение на 5 ноември.", "Нека решението бъде на 5 ноември.", "Решението да вземете на 5 ноември.",
                         "Решението да не вземате на 5 ноември.", "Препоръчително е решение на 5 ноември.", "Съветвам решение на 5 ноември.",
                         "Добре е решението на 5 ноември.", "Решението е добре на 5 ноември.", "По-добре решение на 5 ноември.",
                         "Добър момент за решение е 5 ноември.", "Добри моменти за решения са около 5 ноември.", "Добра възможност за решение е 5 ноември.",
                         "Добър прозорец за решение е 5 ноември.", "Подходящо за решение е 5 ноември.", "Неподходящо за решение е 5 ноември.",
                         "Благоприятно за решение е 5 ноември.", "Неблагоприятно за решение е 5 ноември.", "Важно е решението на 5 ноември.",
                         "Необходимо е решение на 5 ноември.", "Трябва решение на 5 ноември.", "Можете решение на 5 ноември.",
                         "Време е за решение на 5 ноември.", "Е време за решение на 5 ноември.", "Добро време за решение е 5 ноември.",
                         "Добър ден за решение е 5 ноември.", "Добри дни за решения са около 5 ноември."):
            self.assertTrue(report_guard._is_advice(sentence), sentence)
            self.assertTrue(statements(sentence), sentence)
        for sentence in ("На 5 ноември решението се улеснява.", "На 5 ноември започва период на яснота.", "От 5 ноември разговорите стават по-леки.",
                         "На 5 ноември добрите намерения се връщат към решението."):
            self.assertFalse(report_guard._is_advice(sentence), sentence)
            self.assertEqual(statements(sentence), [], sentence)

    def test_third_person_and_participle_forms_are_not_actions_but_advice_forms_are(self):
        for word in ("започва", "започват", "започне", "започнат", "стартира", "стартират", "ангажира", "ангажират", "ангажиран"):
            self.assertEqual(report_guard._action_words(f"Нещо {word} на 5 ноември.", ), [], word)
        for word, label in (("започнете", "започване"), ("започвайте", "започване"), ("стартирайте", "започване"), ("започване", "започване"),
                            ("ангажирайте", "ангажимент"), ("ангажимент", "ангажимент"), ("финализирайте", "финализиране"), ("подписвайте", "подписване")):
            self.assertEqual([lab for _pos, lab in report_guard._action_words(f"Моля {word} на 5 ноември.")], [label], word)

    def test_an_advice_sentence_about_something_else_does_not_become_a_commitment(self):
        # „…което ви ангажира“ е описание на нещо друго, а не препоръка за ангажимент
        sentence = "От 23 октомври насочете енергията си към едно нещо, което ви носи радост и ви ангажира."
        self.assertTrue(report_guard._is_advice(sentence))
        self.assertEqual(statements(sentence), [])


class RefusalScenariosTest(unittest.TestCase):
    """Живите откази от 18.10.2026, пресъздадени с реалните изречения: вече не са противоречие."""

    def setUp(self):
        self.facts = rs.facts("period", False, "2026-12-31")

    def found(self, parts):
        return report_guard.cross_part_violations(parts, MONTHS, self.facts)

    def test_the_descriptive_card_sentence_does_not_collide_with_the_advice_window_of_the_overview(self):
        october = DESCRIPTIVE[0] + " Полезно е да проверявате два пъти написаното, преди да го изпратите."
        overview = "- От 24 октомври до 31 октомври избягвайте да подписвате, финализирате или стартирате нови договорки."
        self.assertEqual(self.found([("month:2026-10", october), ("overview", overview)]), {})
        overview = "- От 24 октомври до 13 ноември не стартирайте и не финализирайте важни договорки."
        self.assertEqual(self.found([("month:2026-10", october), ("month:2026-11", DESCRIPTIVE[1]), ("overview", overview)]), {})

    def test_the_same_avoidance_written_twice_with_avoid_in_the_second_person_plural_is_consistent(self):
        october_advice = "От 24 октомври до края на месеца е по-добре да избягвате финализиране на договорки и подписване."
        overview = "- От 24 октомври до 31 октомври избягвайте финализиране на договорки и подписване."
        self.assertEqual(self.found([("month:2026-10", october_advice), ("overview", overview)]), {})

    def test_a_later_month_after_date_is_not_compared_with_an_earlier_month(self):
        december = "След 25 декември финализирайте договорките с конкретика и ясни срокове."
        october = DESCRIPTIVE[0] + " Около 24 октомври е добър момент да финализирате проекта."
        self.assertEqual(self.found([("month:2026-10", october), ("month:2026-12", december)]), {})
        # но вътре в същия месец е противоречие
        found = self.found([("month:2026-12", december + " На 13 декември е добър момент да финализирате проекта.")])
        self.assertEqual(list(found), ["month:2026-12"])
        self.assertIn("отложено до 25 декември, а на 13 декември е препоръчано", found["month:2026-12"][0].detail)

    def test_after_a_date_in_the_overview_still_covers_every_earlier_date(self):
        found = self.found([("month:2026-10", "Около 24 октомври е добър момент да финализирате проекта."), ("overview", "Финализирането планирайте след 25 декември.")])
        self.assertEqual(list(found), ["overview"])

    def test_the_commitment_that_the_card_only_describes_is_not_a_recommendation(self):
        card = "От 23 октомври насочете енергията си към едно нещо, което ви носи радост и ви ангажира."
        december = "Изчакайте до 11 декември с големите ангажименти."
        self.assertEqual(self.found([("month:2026-10", card), ("month:2026-12", december)]), {})


class CanonicalContradictionsTest(unittest.TestCase):
    """Нищо не е отслабено: препоръка и забрана на една дата, отлагане и препоръка по-рано, прозорец и препоръка вътре в него."""

    def setUp(self):
        self.facts = rs.facts("period", False, "2026-12-31")

    def found(self, parts):
        return report_guard.cross_part_violations(parts, MONTHS, self.facts)

    def test_a_recommendation_and_a_prohibition_on_the_same_date_in_every_form_of_advice(self):
        pairs = (("Финализирайте проекта на 24 октомври.", "Не финализирайте проекта на 24 октомври."),
                 ("На 24 октомври е добре да финализирате проекта.", "На 24 октомври по-добре да не финализирате проекта."),
                 ("На 24 октомври е добър момент да финализирате проекта.", "На 24 октомври избягвайте финализиране на проекта."),
                 ("На 24 октомври можете да финализирате проекта.", "На 24 октомври недейте да финализирате проекта."),
                 ("На 24 октомври финализирайте проекта.", "На 24 октомври е по-добре да избягвате финализирането на проекта."))
        for first, second in pairs:
            found = self.found([("month:2026-10", first), ("overview", second)])
            self.assertEqual(list(found), ["overview"], (first, second))

    def test_postponed_to_a_later_date_and_recommended_earlier(self):
        for first, second in (("Изчакайте до 14 ноември с финализирането на проекта.", "На 31 октомври е добър момент да финализирате проекта."),
                              ("Подписването планирайте след 14 ноември.", "На 31 октомври подписвайте договора."),
                              ("Не финализирайте проекта до 14 ноември.", "На 31 октомври финализирайте проекта."),
                              ("Отложете подписването до 14 ноември.", "На 31 октомври подписвайте договора.")):
            found = self.found([("month:2026-10", second), ("overview", first)])
            self.assertEqual(list(found), ["overview"], (first, second))

    def test_the_avoid_in_the_second_person_plural_and_the_negative_imperative_are_negations(self):
        for avoid in ("На 24 октомври по-добре да избягвате решения.", "На 24 октомври е добре да избягвате решения.", "На 24 октомври недейте да вземате решения."):
            kinds = [row[1] for row in report_guard._statements(avoid, self.facts)]
            self.assertEqual(kinds, ["avoid"], avoid)
        found = self.found([("month:2026-10", "На 24 октомври е добър момент да вземете решение."), ("overview", "На 24 октомври е по-добре да избягвате решения.")])
        self.assertEqual(list(found), ["overview"])

    def test_a_window_with_avoid_in_the_second_person_plural(self):
        found = self.found([("month:2026-12", "На 13 декември е добър момент да финализирате проекта."),
                            ("overview", "От 3 до 25 декември по-добре да избягвате финализирането на проекта.")])
        self.assertEqual(list(found), ["overview"])

    def test_a_descriptive_sentence_does_not_hide_a_real_contradiction_next_to_it(self):
        text = DESCRIPTIVE[0] + " На 24 октомври е добър момент да финализирате проекта."
        found = self.found([("month:2026-10", text), ("overview", "На 24 октомври не финализирайте проекта.")])
        self.assertEqual(list(found), ["overview"])


class RealReportsAdviceTest(unittest.TestCase):
    def test_the_real_sentences_of_the_reports_are_classified_as_expected(self):
        """Всяко от реалните описателни изречения във фикстурите е без сигнал за съвет, всяко от реалните със съвет — със сигнал."""
        base = os.path.join(os.path.dirname(os.path.abspath(__file__)), "fixtures")
        sentences = []
        for name in sorted(os.listdir(base)):
            if name.startswith("live_2026_10_1") and "period" in name:
                with open(os.path.join(base, name), encoding="utf-8") as f:
                    sentences += safety._plain_sentences(f.read())
        self.assertGreater(len(sentences), 200)
        index = {s.strip() for s in sentences}
        for expected in (DESCRIPTIVE[0], DESCRIPTIVE[1], DESCRIPTIVE[3], DESCRIPTIVE[4]):
            self.assertIn(expected, index)                                           # изреченията наистина са в отчетите
        facts = rs.facts("period", False, "2026-12-31")
        bullets = []
        for name in ("live_2026_10_18_period_single_today_after_changes.txt", "live_2026_10_18_period_single_two_months.txt",
                     "live_2026_10_18_period_single_windows_a.txt", "live_2026_10_18_period_single_windows_b.txt", "live_2026_10_18_period_single_windows_c.txt"):
            with open(os.path.join(base, name), encoding="utf-8") as f:
                bullets += [b.strip() for b in re.findall(r"(?m)^- (.+)$", f.read())]
        counted = [b for b in bullets if report_guard._action_words(b) and tc.parse_dates(b, facts)]
        self.assertGreater(len(counted), 12)
        for bullet in counted:                                                      # всяка точка на плана е съвет и не се губи от проверката
            self.assertTrue(report_guard._is_advice(bullet), bullet)


class RefusalLogDateTest(unittest.TestCase):
    def logged(self, violations, facts):
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            text_guard._log_remaining("period:overview:ADVICE", violations, facts)
        return out.getvalue()

    def test_a_date_violation_shows_its_sentence_with_masked_names(self):
        facts = rs.facts("period", True)
        sentence = f"{rs.IVAN} има прозорец до 25 ноември, когато напрежението отшумява."
        line = self.logged([tc.Violation("date", "hard", sentence, "25 ноември е краят на интервал на действие по орбис, не краят на житейски процес.")], facts)
        self.assertIn("date: 25 ноември е краят на интервал", line)
        self.assertIn("||", line)
        self.assertIn("има прозорец до 25 ноември, когато напрежението отшумява", line)
        self.assertIn("[име]", line)
        self.assertNotIn(rs.IVAN, line)

    def test_other_codes_still_log_no_text(self):
        facts = rs.facts("period", False)
        line = self.logged([tc.Violation("sign", "hard", "Марс е в Овен според текста", "Марс е в Телец.")], facts)
        self.assertNotIn("||", line)
        self.assertNotIn("според текста", line)


if __name__ == "__main__":
    unittest.main()
