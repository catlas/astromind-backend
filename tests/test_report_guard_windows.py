"""
Прозорци в проверката за съгласуваност между части на отчета (report_guard) и журналът при отказ.

Живо приемане 18.10.2026: от 10 пускания на период 3 бяха отказани и 1 резервен, всички заради „отложено до дата“ срещу препоръка на по-ранна
дата, а проверката четеше „от 3 до 25 декември избягвайте решения“ като „до 25 декември“ (началото се губеше) и сравняваше съвет за декември
с препоръки от октомври. Сега:
- „до дата“ само с край в месечен блок е прозорец от първия ден на ТОЗИ месец; в общия преглед важи за всички по-ранни дати, както преди;
- „от 3 до 25 декември“, „между 3 и 25 декември“, „3–25 декември“, „от 30 ноември до 5 декември“ са прозорци с начало и край: препоръка вътре в
  прозореца е противоречие, преди него — не е (отлагане: без самия край; забрана: с двата края).
Проверката не е отслабена: противоречието вътре в прозореца и всички стари случаи остават.
Тестовете са с КОНКРЕТНО действие („финализиране“): за общите думи („разговор“, „решение“, „ангажимент“, „започване“) „след / до / прозорец“ вече не се
следят (18.10.2026, виж test_report_guard_generic.py).
"""
import contextlib
import io
import os
import re
import unittest
from datetime import date

import testenv  # noqa: F401  (трябва да е преди engine)
import engine
import regression_support as rs
import report_guard
import text_check as tc
import text_guard
from scanner import TransitScanner

MONTHS = ["2026-10", "2026-11", "2026-12"]
OCTOBER = "На 31 октомври е добър момент да финализирате проекта."
NOVEMBER_END = "На 29 ноември е добър момент да финализирате проекта."


def decide_on(day):
    """Препоръка на дата за конкретно действие („финализиране“)."""
    return f"На {day} е добър момент да финализирате проекта."


class WindowTest(unittest.TestCase):
    def setUp(self):
        self.facts = rs.facts("period", False, "2026-12-31")

    def found(self, parts):
        return report_guard.cross_part_violations(parts, MONTHS, self.facts)

    def december(self, sentence, other, other_stage="month:2026-12", stage="month:2026-12"):
        """Съвет в частта на декември срещу препоръка в друга (или същата) част."""
        if other_stage == stage:
            return self.found([(stage, other + " " + sentence)])
        return self.found([(other_stage, other), (stage, sentence)])

    # --- „до дата“ само с край ---
    def test_until_only_in_a_month_block_counts_from_the_first_day_of_that_month(self):
        sentence = "Не бързайте с финализирането на проекта до 25 декември."
        self.assertEqual(self.december(sentence, OCTOBER, "month:2026-10"), {})                  # октомври е преди декември: не е противоречие
        self.assertEqual(self.december(sentence, NOVEMBER_END, "month:2026-11"), {})
        found = self.december(sentence, decide_on("13 декември"))                                  # вътре в декември е
        self.assertEqual(list(found), ["month:2026-12"])
        self.assertIn("отложено до 25 декември, а на 13 декември е препоръчано", found["month:2026-12"][0].detail)

    def test_until_only_in_the_overview_still_covers_every_earlier_date(self):
        found = self.found([("month:2026-10", OCTOBER), ("overview", "Не бързайте с финализирането на проекта до 25 декември.")])
        self.assertEqual(list(found), ["overview"])
        self.assertIn("отложено до 25 декември, а на 31 октомври е препоръчано", found["overview"][0].detail)

    def test_until_in_a_month_block_reaches_back_to_its_own_month_only_when_the_end_is_later(self):
        # „до 5 ноември“ в октомврийския блок: прозорецът е от 1 октомври до 5 ноември и покрива 20 октомври
        found = self.found([("month:2026-10", "Изчакайте до 5 ноември с финализирането на проекта. " + decide_on("20 октомври"))])
        self.assertEqual(list(found), ["month:2026-10"])
        # край преди началото на месеца (декември: „до 25 ноември“) няма начало: както преди, важи за всички по-ранни дати
        found = self.found([("month:2026-11", decide_on("10 ноември")), ("month:2026-12", "Изчакайте до 25 ноември с финализирането на проекта.")])
        self.assertEqual(list(found), ["month:2026-12"])

    # --- прозорци с начало и край ---
    def test_a_window_with_avoid_covers_both_ends_and_nothing_before_the_start(self):
        sentence = "От 3 до 25 декември избягвайте финализирането на проекта."
        for outside in ("1 декември", "2 декември", "26 декември"):
            self.assertEqual(self.december(sentence, decide_on(outside)), {}, outside)
        self.assertEqual(self.december(sentence, OCTOBER, "month:2026-10"), {})
        for inside in ("3 декември", "13 декември", "25 декември"):
            found = self.december(sentence, decide_on(inside))
            self.assertEqual(list(found), ["month:2026-12"], inside)
            self.assertIn("не се препоръчва от 3 декември до 25 декември", found["month:2026-12"][0].detail)

    def test_a_window_with_postponement_does_not_cover_its_end_day(self):
        sentence = "От 3 до 25 декември отложете финализирането на проекта."
        for outside in ("2 декември", "25 декември", "30 декември"):
            self.assertEqual(self.december(sentence, decide_on(outside)), {}, outside)
        self.assertEqual(self.december(sentence, OCTOBER, "month:2026-10"), {})
        found = self.december(sentence, decide_on("13 декември"))
        self.assertIn("отложено до 25 декември, а на 13 декември е препоръчано", found["month:2026-12"][0].detail)

    def test_every_way_of_writing_a_window_is_read_as_a_window(self):
        for sentence in ("Между 3 и 25 декември избягвайте финализирането на проекта.", "3–25 декември избягвайте финализирането на проекта.",
                         "От 3 декември до 25 декември избягвайте финализирането на проекта.", "Избягвайте финализирането на проекта между 3 и 25 декември.",
                         "Избягвайте финализирането на проекта от 3 до 25 декември."):
            self.assertEqual(self.december(sentence, OCTOBER, "month:2026-10"), {}, sentence)
            self.assertEqual(self.december(sentence, decide_on("2 декември")), {}, sentence)
            self.assertEqual(list(self.december(sentence, decide_on("13 декември"))), ["month:2026-12"], sentence)

    def test_a_window_across_two_months(self):
        for sentence in ("От 30 ноември до 5 декември избягвайте финализирането на проекта.", "Между 30 ноември и 5 декември избягвайте финализирането на проекта.",
                         "Избягвайте финализирането на проекта между 30 ноември и 5 декември."):
            self.assertEqual(self.found([("month:2026-11", NOVEMBER_END), ("month:2026-12", sentence)]), {}, sentence)      # 29 ноември е преди прозореца
            found = self.found([("month:2026-11", decide_on("30 ноември")), ("month:2026-12", sentence)])
            self.assertEqual(list(found), ["month:2026-12"], sentence)
            self.assertEqual(list(self.december(sentence, decide_on("2 декември"))), ["month:2026-12"], sentence)
            self.assertEqual(self.december(sentence, decide_on("6 декември")), {}, sentence)                                # след края

    def test_a_window_in_the_overview_conflicts_with_a_recommendation_inside_it_in_any_month(self):
        found = self.found([("month:2026-11", decide_on("9 ноември")), ("overview", "Между 3 и 17 ноември избягвайте финализирането на проекта.")])
        self.assertEqual(list(found), ["overview"])
        self.assertEqual(self.found([("month:2026-10", OCTOBER), ("overview", "Между 3 и 17 ноември избягвайте финализирането на проекта.")]), {})

    def test_a_window_is_not_mixed_up_with_different_people_or_other_actions(self):
        pair = rs.facts("period", True, "2026-12-31")
        sentence = f"{rs.IVAN}: от 3 до 25 декември избягвайте финализирането на проекта."
        other = f"{rs.A_NAME}: на 13 декември финализирайте проекта."
        self.assertEqual(report_guard.cross_part_violations([("month:2026-12", other + " " + sentence)], MONTHS, pair), {})
        self.assertEqual(self.december("От 3 до 25 декември избягвайте финализирането на проекта.", "На 13 декември подписвайте договора."), {})

    # --- вътрешни помощници ---
    def test_statements_carry_the_start_of_the_window(self):
        rows = report_guard._statements("От 3 до 25 декември избягвайте финализирането на проекта.", self.facts)
        self.assertEqual([(r[0], r[1], r[2], r[5]) for r in rows], [("финализиране", "avoid_window", date(2026, 12, 25), date(2026, 12, 3))])
        rows = report_guard._statements("Изчакайте до 25 декември с финализирането на проекта.", self.facts, date(2026, 12, 1))
        self.assertEqual([(r[0], r[1], r[2], r[5]) for r in rows], [("финализиране", "from", date(2026, 12, 25), date(2026, 12, 1))])
        rows = report_guard._statements("Изчакайте до 25 декември с финализирането на проекта.", self.facts, None)
        self.assertEqual([(r[1], r[5]) for r in rows], [("from", None)])
        rows = report_guard._statements("Финализирането планирайте след 14 ноември.", self.facts, date(2026, 11, 1))          # „след“ в месечен блок: от първия ден на месеца
        self.assertEqual([(r[1], r[5]) for r in rows], [("from", date(2026, 11, 1))])
        rows = report_guard._statements("Финализирането планирайте след 14 ноември.", self.facts, None)                        # в общия преглед няма начало
        self.assertEqual([(r[1], r[5]) for r in rows], [("from", None)])

    def test_a_deadline_and_a_recommended_window_are_not_followed_as_windows(self):
        for sentence in ("Финализирайте проекта до 25 декември.", "Финализирайте от 3 до 25 декември подготовката."):
            self.assertEqual([r for r in report_guard._statements(sentence, self.facts, date(2026, 12, 1)) if r[1] in ("from", "avoid_window")], [], sentence)

    def test_invalid_or_reversed_windows_are_not_windows(self):
        items = tc.parse_dates("От 28 до 5 декември избягвайте решения.", self.facts)
        self.assertEqual(report_guard._windows("От 28 до 5 декември избягвайте решения.", items), ([], set()))
        self.assertEqual(report_guard._windows("От 31 до 31 ноември избягвайте решения.", tc.parse_dates("От 31 до 31 ноември избягвайте решения.", self.facts)), ([], set()))

    def test_the_first_day_of_the_month_of_a_part(self):
        self.assertEqual(report_guard._month_start("month:2026-12"), date(2026, 12, 1))
        for stage in ("overview", "", "month:2026-13", "month:x", None):
            self.assertIsNone(report_guard._month_start(stage), stage)


def parts_of_report(text):
    """(етап, текст) по раздели на записан отчет: „Общ преглед на периода“ и месечните раздели (и в Markdown, и в записания HTML вид)."""
    names = {"Януари": "01", "Февруари": "02", "Март": "03", "Април": "04", "Май": "05", "Юни": "06", "Юли": "07", "Август": "08",
             "Септември": "09", "Октомври": "10", "Ноември": "11", "Декември": "12"}
    parts = []
    for chunk in re.split(r"(?m)^(?:## |<h2>)", text)[1:]:
        head = chunk.split("\n", 1)[0].replace("</h2>", "")
        month = re.search(r"(%s) (\d{4})" % "|".join(names), head)
        if "Общ преглед" in head:
            parts.append(("overview", chunk))
        elif month:
            parts.append((f"month:{month.group(2)}-{names[month.group(1)]}", chunk))
    return parts


class RealReportsTest(unittest.TestCase):
    """Всички реални отчети, доставени от продукцията, остават без противоречия и с новите прозорци (никакъв нов отказ на приет текст)."""
    REPORTS = (("live_2026_10_18_period_single_first.txt", "2026-10-01", "2026-12-31", False), ("live_2026_10_18_period_single_next.txt", "2027-01-01", "2027-03-31", False),
               ("live_2026_10_18_period_single_past_start_after_changes.txt", "2026-10-01", "2026-12-31", False),
               ("live_2026_10_18_period_single_today_after_changes.txt", "2026-10-18", "2026-12-31", False),
               ("live_2026_10_18_period_single_two_months.txt", "2026-10-18", "2026-11-30", False),
               ("live_2026_10_18_period_single_windows_a.txt", "2026-10-18", "2026-12-31", False),
               ("live_2026_10_18_period_single_windows_b.txt", "2026-10-18", "2026-12-31", False),
               ("live_2026_10_18_period_single_windows_c.txt", "2026-10-18", "2026-12-31", False),
               ("live_2026_10_18_final_period_single_A.txt", "2026-10-18", "2026-12-31", False),
               ("live_2026_10_18_final_period_pair_A_IVAN.txt", "2026-10-18", "2026-11-30", True),
               ("live_2026_10_18_phase30_period_pair_A_IVAN_love.txt", "2026-10-18", "2026-11-30", True),
               ("live_2026_10_12_period_pair_work.txt", "2026-10-20", "2026-10-31", True), ("live_2026_10_12_period_pair_dst_second.txt", "2026-10-20", "2026-10-31", True),
               ("live_2026_10_12_period_pair_two_months.txt", "2026-10-20", "2026-11-30", True), ("live_2026_10_12_period_pair_dst.txt", "2026-10-20", "2026-10-31", True))

    def test_the_real_period_reports_have_no_contradictions(self):
        base = os.path.join(os.path.dirname(os.path.abspath(__file__)), "fixtures")
        calendars = {}
        for name, start, end, pair in self.REPORTS:
            with open(os.path.join(base, name), encoding="utf-8") as f:
                text = f.read()
            if (start, end, pair) not in calendars:
                calendars[(start, end, pair)] = TransitScanner(engine_instance=engine.get_engine()).build_calendar(
                    rs.chart("A"), start, end, rs.A_BIRTH["lat"], rs.A_BIRTH["lon"], partner_chart=rs.chart("Ivan") if pair else None, tz_name="Europe/Sofia")
            cal = calendars[(start, end, pair)]
            facts = tc.build_facts(mode="period", user_name=rs.A_NAME, natal_chart=rs.chart("A"), partner_name=rs.IVAN if pair else None,
                                   partner_chart=rs.chart("Ivan") if pair else None, calendar=cal, report_date="2026-10-18", question="")
            parts = parts_of_report(text)
            self.assertGreaterEqual(len(parts), 2, name)
            self.assertEqual(report_guard.cross_part_violations(parts, sorted({s[6:] for s, _ in parts if s.startswith("month:")}), facts), {}, name)


class RefusalLogTest(unittest.TestCase):
    def logged(self, violations, facts):
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            text_guard._log_remaining("overview", violations, facts)
        return out.getvalue()

    def test_a_contradiction_is_logged_with_both_sentences_and_without_names(self):
        facts = rs.facts("period", True)
        excerpt = f"{rs.IVAN}: не финализирайте проекта на 24 октомври ⟷ Финализирайте проекта на 24 октомври"
        line = self.logged([tc.Violation("consistency", "hard", excerpt, "Противоречие между части на отчета: на 24 октомври за „финализиране“ има и препоръка, и забрана.")], facts)
        self.assertIn("🔎 Остава (overview): consistency: Противоречие между части на отчета", line)
        self.assertIn("||", line)
        self.assertIn("не финализирайте проекта на 24 октомври", line)
        self.assertIn("Финализирайте проекта на 24 октомври", line)
        self.assertIn("[име]", line)
        self.assertNotIn(rs.IVAN, line)
        self.assertEqual(len(line.strip().splitlines()), 1)

    def test_the_text_of_any_other_violation_is_not_logged(self):
        facts = rs.facts("period", True)
        line = self.logged([tc.Violation("aspect", "hard", f"Сатурн е в опозиция с Луната на {rs.IVAN}", "Няма такъв аспект: има тригон.")], facts)
        self.assertIn("aspect: Няма такъв аспект", line)
        self.assertNotIn("||", line)
        self.assertNotIn("опозиция с Луната", line)

    def test_at_most_four_violations_and_a_long_excerpt_is_cut(self):
        facts = rs.facts("period", False)
        many = [tc.Violation("consistency", "hard", "а" * 400, f"Противоречие {i}") for i in range(7)]
        lines = self.logged(many, facts).strip().splitlines()
        self.assertEqual(len(lines), 4)
        self.assertTrue(all(len(line) < 500 for line in lines))


if __name__ == "__main__":
    unittest.main()
