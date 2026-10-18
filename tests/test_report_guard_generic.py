"""
Общи думи и едно изречение (report_guard, 18.10.2026, трето живо приемане; одобрено от потребителя: опции 1 и 3).

След всички уточнения на проверката отказите на периода останаха 3 от 10 и 1 резервен. Журналът показа механизмите на груба връзка дума↔дата:
- двойки от ЕДНО изречение се сравняват помежду си („Около 24 декември преценете нуждите си, преди да поемете ангажименти, а след 25 декември
  водете разговорите“);
- общата дума („разговор“, „решение“, „ангажимент“, „започване“) е в почти всяко изречение на съвет: „след 25 декември водете разговорите“ се
  четеше като „не преди 25 декември“, а препоръчан прозорец — като препоръка на крайната си дата.

Сега: двойки от едно и също изречение не се сравняват; за общите думи се следи само препоръка срещу забрана на ЕДНА и СЪЩА дата, а „след / до дата“,
отлагане и прозорец — само за конкретните („финализиране“, „подписване“, „граница“). Известна загуба (одобрена): „изчакайте до 14 ноември с
решението“ срещу „на 31 октомври вземете решение“ вече не е откритo противоречие.
"""
import unittest
from datetime import date

import testenv  # noqa: F401  (трябва да е преди engine)
import regression_support as rs
import report_guard

MONTHS = ["2026-10", "2026-11", "2026-12"]
GENERIC = {"разговор": ("разговорите", "разговор"), "решение": ("решението", "решение"), "ангажимент": ("ангажиментите", "ангажимент"),
           "започване": ("започването", "започване")}


class GenericWordsTest(unittest.TestCase):
    def setUp(self):
        self.facts = rs.facts("period", False, "2026-12-31")

    def found(self, parts):
        return report_guard.cross_part_violations(parts, MONTHS, self.facts)

    def statements(self, sentence, month_start=None):
        return report_guard._statements(sentence, self.facts, month_start)

    def test_the_generic_words_are_exactly_these(self):
        self.assertEqual(report_guard.GENERIC_ACTIONS, frozenset({"разговор", "решение", "ангажимент", "започване"}))
        self.assertFalse({"финализиране", "подписване", "граница"} & report_guard.GENERIC_ACTIONS)

    def test_a_generic_word_gives_only_a_recommendation_or_a_prohibition_on_a_date(self):
        for sentence in ("Изчакайте до 14 ноември с решението.", "Решението вземете след 14 ноември.", "От 3 до 25 декември избягвайте решения.",
                         "Не бързайте с решения до 25 декември.", "Между 15 и 27 декември избягвайте решения.",
                         "Отложете разговорите до 14 ноември.", "След 25 декември водете разговорите за партньорства.",
                         "Изчакайте до 14 ноември с ангажиментите.", "Не започвайте до 14 ноември нови неща."):
            self.assertEqual(self.statements(sentence), [], sentence)
        self.assertEqual([(r[0], r[1]) for r in self.statements("На 31 октомври е добър момент да вземете решение.")], [("решение", "do")])
        self.assertEqual([(r[0], r[1]) for r in self.statements("На 31 октомври избягвайте решения.")], [("решение", "avoid")])

    def test_the_same_date_for_a_generic_word_is_still_a_recommendation_and_a_prohibition(self):
        for word, (noun, label) in GENERIC.items():
            do = {"разговор": "На 24 октомври е добър момент за разговор.", "решение": "На 24 октомври е добър момент да вземете решение.",
                  "ангажимент": "На 24 октомври е добър момент за нов ангажимент.", "започване": "На 24 октомври започнете новия проект."}[word]
            avoid = {"разговор": "На 24 октомври избягвайте разговорите.", "решение": "На 24 октомври избягвайте решения.",
                     "ангажимент": "На 24 октомври не поемайте ангажименти.", "започване": "На 24 октомври не започвайте нищо ново."}[word]
            found = self.found([("month:2026-10", do), ("overview", avoid)])
            self.assertEqual(list(found), ["overview"], word)
            self.assertIn("и препоръка, и забрана", found["overview"][0].detail, word)

    def test_known_loss_a_postponed_generic_word_against_an_earlier_recommendation_is_not_found(self):
        """Одобрена загуба на откриване: за общите думи „до / след дата“ и прозорец не се следят."""
        for first, second in (("Изчакайте до 14 ноември с решението.", "На 31 октомври е добър момент да вземете решение."),
                              ("Решението вземете след 14 ноември.", "На 31 октомври вземете решение."),
                              ("Отложете разговорите до 14 ноември.", "На 31 октомври е добър момент за разговор."),
                              ("От 3 до 25 ноември избягвайте решения.", "На 13 ноември е добър момент да вземете решение.")):
            self.assertEqual(self.found([("month:2026-10", second), ("overview", first)]), {}, (first, second))

    def test_a_specific_word_keeps_every_form(self):
        for first, second in (("Изчакайте до 14 ноември с финализирането на проекта.", "На 31 октомври е добър момент да финализирате проекта."),
                              ("Подписването планирайте след 14 ноември.", "На 31 октомври подписвайте договора."),
                              ("Не поставяйте границата до 14 ноември.", "На 31 октомври поставете границата."),
                              ("От 3 до 25 ноември избягвайте финализирането на проекта.", "На 13 ноември е добър момент да финализирате проекта."),
                              ("Отложете подписването до 14 ноември.", "На 31 октомври подписвайте договора.")):
            found = self.found([("month:2026-10", second), ("overview", first)] if "31 октомври" in second else [("month:2026-11", second), ("overview", first)])
            self.assertEqual(list(found), ["overview"], (first, second))

    def test_the_sentences_of_the_third_live_series_are_no_longer_refused(self):
        """Пресъздадени с реалните изречения от логовете на отказите q1, q5, q6, q7 и q8 (общи думи)."""
        december = ("Около 24 декември преценете собствените си нужди, преди да поемете ангажименти, а след 25 декември водете разговорите за отношения "
                    "и договорки конкретно и ясно.")
        november = "В края на месеца енергията е по-благоприятна за действие и за разговори, около 24 ноември."
        summary = ("Около 9 декември си поставете една конкретна цел за навиците, а след 11 и 13 декември водете разговорите си със спокойствие. "
                   "След 25 декември разговорите стават по-практични и е добре да уточните условия и срокове.")
        advice_a = "Между 15 и 27 декември има повече енергия, но избягвайте прибързани резки решения."
        advice_b = "- Между 15 и 27 декември насочете повечето енергия към малки, конкретни стъпки, а не към прибързани решения."
        whole = "Периодът от 18 октомври до 31 декември има ясен характер на подреждане, а не на стартиране – не започвайте нови начинания."
        november_start = "През ноември основната ви задача е да подредите ежедневието си – около 9 ноември е добре да започнете малка рутина."
        self.assertEqual(self.found([("month:2026-12", december), ("overview", december)]), {})
        self.assertEqual(self.found([("month:2026-11", november), ("overview", december)]), {})
        self.assertEqual(self.found([("overview", summary), ("month:2026-12", december)]), {})
        self.assertEqual(self.found([("month:2026-12", advice_a), ("overview", advice_b)]), {})
        self.assertEqual(self.found([("month:2026-11", november_start), ("overview", whole)]), {})


class SameSentenceTest(unittest.TestCase):
    """Едно изречение е едно съобщение: двойки от него не се сравняват помежду си (дата и дума от различни части на изречението)."""

    def setUp(self):
        self.facts = rs.facts("period", False, "2026-12-31")

    def found(self, parts):
        return report_guard.cross_part_violations(parts, MONTHS, self.facts)

    SAME = "Около 24 декември е добър момент да финализирате проекта, а след 25 декември финализирайте останалото."

    def test_the_sentence_alone_gives_a_recommendation_and_a_postponement_that_collide_only_if_compared(self):
        rows = report_guard._statements(self.SAME, self.facts, date(2026, 12, 1))
        self.assertEqual({(r[0], r[1], r[2]) for r in rows}, {("финализиране", "do", date(2026, 12, 24)), ("финализиране", "from", date(2026, 12, 25))})
        self.assertEqual(len({r[6] for r in rows}), 1)                                       # и двете са от едно изречение

    def test_two_statements_of_one_sentence_are_not_compared(self):
        self.assertEqual(self.found([("month:2026-12", self.SAME)]), {})
        self.assertEqual(self.found([("overview", self.SAME)]), {})
        sentence = ("Около 24 декември преценете нуждите си, преди да поемете ангажименти, а след 25 декември финализирайте договорките "
                    "и ги подписвайте.")
        self.assertEqual(self.found([("month:2026-12", sentence)]), {})

    def test_the_same_two_statements_in_two_sentences_are_a_contradiction(self):
        found = self.found([("month:2026-12", "Около 24 декември е добър момент да финализирате проекта. След 25 декември финализирайте останалото.")])
        self.assertEqual(list(found), ["month:2026-12"])
        found = self.found([("month:2026-12", "Около 24 декември е добър момент да финализирате проекта."), ("overview", "След 25 декември финализирайте останалото.")])
        self.assertEqual(list(found), ["overview"])

    def test_identity_is_the_part_and_the_sentence_not_the_text(self):
        found = self.found([("month:2026-12", self.SAME), ("overview", self.SAME)])        # еднакво изречение в две различни части
        self.assertEqual(list(found), ["overview"])

    def test_statements_carry_the_number_of_their_sentence(self):
        rows = report_guard._statements("Няма дата тук. На 24 октомври финализирайте проекта. Не финализирайте на 25 октомври.", self.facts)
        self.assertEqual([(r[1], r[2], r[6]) for r in rows], [("do", date(2026, 10, 24), 1), ("avoid", date(2026, 10, 25), 2)])


if __name__ == "__main__":
    unittest.main()
