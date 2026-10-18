"""
Фаза 28 (живо приемане 12.10.2026): при отказ в журнала остава кодът на остатъчното нарушение и верните стойности,
без текст на анализа и без имената на хората. Така се вижда дали грешката е в текста на AI или в самата проверка.
Изключение (18.10.2026, по искане на потребителя): при consistency двете изречения, а при date къс откъс около датата, с маскирани имена.
"""
import asyncio
import contextlib
import io
import unittest

import testenv  # noqa: F401  (трябва да е преди модулите)
import regression_support as rs
import text_guard

BAD = "През 24 октомври Сатурн ще подкрепи кариерата ви."          # дата в миналото спрямо report_date: тежко нарушение „date“


class RemainingViolationsLogTest(unittest.TestCase):
    def run_guard(self, facts):
        async def repair(system_prompt, user_prompt):
            return BAD                                                     # поправката не променя нищо

        buffer = io.StringIO()
        with contextlib.redirect_stdout(buffer):
            with self.assertRaises(text_guard.TextCheckError):
                asyncio.run(text_guard.guard_text(BAD, facts, stage="snapshot", repair_call=repair, mode="enforce"))
        return buffer.getvalue()

    def test_a_refusal_logs_the_code_and_the_true_values_and_only_a_short_span_of_the_text_for_a_date(self):
        """Логът при отказ носи кода и верните стойности. Текстът на анализа не се логва, освен къс откъс при код date (мястото около датата, с
        маскирани имена; по искане на потребителя на 18.10.2026, за да се види изречението на отказ за дата) и при consistency."""
        facts = rs.facts("natal", False)
        facts.report_date = "2026-10-11"
        out = self.run_guard(facts)
        self.assertIn("🔎 Остава (snapshot): date:", out)
        self.assertIn("|| През 24 октомври Сатурн ще подкрепи", out)             # откъсът около датата
        self.assertNotIn("кариерата ви", out)                               # а останалият текст на анализа не се логва
        remaining = [line for line in out.splitlines() if line.startswith("🔎 Остава")]
        self.assertTrue(all(len(line.split("||", 1)[1].strip()) <= 200 for line in remaining if "||" in line))

    def test_names_are_masked_in_the_logged_detail(self):
        class Facts:
            names = {"user": "Тест А — одит", "partner": "Иван"}
        detail = "Слънцето на Тест А — одит е в квадратура с Марс на Иван (орб 2,1°), а не в тригон"
        safe = text_guard._safe_detail(detail, Facts())
        self.assertNotIn("Тест", safe)
        self.assertNotIn("Иван", safe)
        self.assertNotIn("одит", safe)
        self.assertIn("квадратура с Марс", safe)
        self.assertIn("2,1°", safe)
        self.assertLessEqual(len(safe), 150)


if __name__ == "__main__":
    unittest.main()
