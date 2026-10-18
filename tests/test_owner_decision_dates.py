"""
ТВЪРДО РЕШЕНИЕ НА СОБСТВЕНИКА (18–19.10.2026): датите извън избрания период ОСТАВАТ и в никакъв случай не се променят.

Картата на периода показва истинския интервал на действие („Интервал на действие: от 6 ноември до 13 януари“), дори краят или началото му да са
извън избрания период, и реда „Точен аспект извън периода: 31 декември“. AI може да ползва датите от картите, а износът показва пълния интервал.
Анализът на ChatGPT от 18.10.2026 водеше това за грешка и предложи изрязване до границите на периода; собственикът го отхвърли два пъти (първо с
„В — не пипаме нищо“, после с „твърдо да останат и в никакъв случай да не се променят“).

Тези тестове са за да не се „поправи“ това по невнимание (от човек, от друг агент или с промпт): ако някой от тях се счупи, промяната е в противоречие с
решението на собственика и не бива да се прави, нито тестът да се променя, без негово изрично искане. Виж и HANDOVER_2026_10_12.md, раздел 6, т. 1г.

Пускане: python -m unittest discover -s tests -p "test_owner_decision_dates.py"
"""
import types
import unittest

import testenv  # noqa: F401  (трябва да е преди engine)
import period_cards
import regression_support as rs
from card_engine import Card

# Юпитер срещу Слънцето: точен на 25 ноември (в периода 18.10–30.11), пак точен на 31 декември (след периода); интервалът е 6 ноември – 13 януари
CROSSES_THE_END = {"type": "TRANSIT", "target": "User", "planet": "Jupiter", "natal_planet": "Sun", "aspect": "Opposition",
                   "exact": [{"when": "2026-11-25 01:57", "motion": "direct"}], "exact_outside_period": ["2026-12-31 02:38"],
                   "active_from": "2026-11-06", "active_to": "2027-01-13"}
# започнал преди периода: точен на 21 юли (преди началото), интервалът започва на 21 юни
STARTS_BEFORE = {"type": "TRANSIT", "target": "User", "planet": "Uranus", "natal_planet": "Mercury", "aspect": "Trine",
                 "exact": [{"when": "2026-10-31 11:00", "motion": "retrograde"}], "exact_outside_period": ["2026-07-21 10:00"],
                 "active_from": "2026-06-21", "active_to": "2026-11-25"}


class DatesOutsideThePeriodStayTest(unittest.TestCase):
    def test_the_card_keeps_the_exact_pass_after_the_period_and_the_real_end_of_the_interval(self):
        _title, detail = period_cards.transit_parts(CROSSES_THE_END, "Иван")
        self.assertIn("Точен аспект извън периода: 31 декември", detail)
        self.assertIn("Интервал на действие: от 6 ноември до 13 януари", detail)

    def test_the_card_keeps_the_exact_pass_before_the_period_and_the_real_start_of_the_interval(self):
        _title, detail = period_cards.transit_parts(STARTS_BEFORE, "Иван")
        self.assertIn("Точен аспект извън периода: 21 юли", detail)
        self.assertIn("Интервал на действие: от 21 юни до 25 ноември", detail)

    def test_the_full_heading_the_ai_receives_has_the_same_dates(self):
        heading = period_cards.transit_heading(CROSSES_THE_END, "Иван")
        self.assertIn("31 декември", heading)
        self.assertIn("13 януари", heading)

    def test_the_closing_blocks_may_name_the_dates_of_the_cards_even_outside_the_period(self):
        card = Card("M2E3", "M2", "x", row={"kind": "event", "event": CROSSES_THE_END})
        allowed = period_cards.allowed_dates([card], types.SimpleNamespace(start="2026-10-18", end="2026-11-30"))
        for month, day in ((11, 6), (1, 13), (12, 31), (11, 25)):
            self.assertIn((month, day), allowed)

    def test_a_real_calendar_still_has_cards_with_a_window_outside_the_period(self):
        cal = rs.calendar(True, "2026-10-31")
        outside = [r for r in cal.public_events() if r["type"] == "TRANSIT" and (r["active_from"] < cal.start or r["active_to"] > cal.end)]
        self.assertTrue(outside)
        for row in outside[:3]:
            _title, detail = period_cards.transit_parts(row, "Иван")
            self.assertIn(f"{int(row['active_to'][8:10])} ", detail.split("Интервал на действие:")[1])


if __name__ == "__main__":
    unittest.main()
