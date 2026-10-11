"""
Фаза 25, R10 (регресионен одит 11.10.2026): регистър на AI опитите.

- settle е една идемпотентна транзакция по attempt_id: повторно извикване не отчита втори път;
- грешка при записа не освобождава резервацията като нулев разход: тя остава несигурна и се брои към тавана;
- платен 200 без текст (празен content, само reasoning, finish_reason=length) се отчита ПРЕДИ резервния доставчик;
- таймаут след изпращане е несигурна резервация, неотворена връзка е освободена; резервният опит и поправката са отделни опити;
- резервацията ползва консервативна горна граница на входа и тарифата на доставчика;
- рестартът на деня и месеца не смесва резервациите;
- реален тест с два процеса на PostgreSQL (бариера): таванът не се надвишава (пропуска се на SQLite).
"""
import multiprocessing
import os
import unittest
from datetime import datetime
from unittest import mock

import testenv  # noqa: F401  (трябва да е преди main)
import httpx  # noqa: E402
import sqlalchemy as sa  # noqa: E402
from alembic import command  # noqa: E402

import ai_budget  # noqa: E402
from database import AIReservation, AIUsage, SessionLocal  # noqa: E402
from test_euro_balance_migration import alembic_config, make_engine  # noqa: E402
from test_phase21_limits_budget import PRICES, ProviderCallTest, clean, env, reservations, usage_cost  # noqa: E402


def ledger():
    db = SessionLocal()
    try:
        return [(r.state, r.provider, r.prompt_tokens, r.completion_tokens, r.attempt_id)
                for r in db.query(AIReservation).order_by(AIReservation.id).all()]
    finally:
        db.close()


class Base(unittest.TestCase):
    def setUp(self):
        clean()
        self.addCleanup(clean)


class SettleTest(Base):
    def test_a_duplicate_settle_counts_once(self):
        with env(AI_DAILY_BUDGET_CENTS=10, AI_MONTHLY_BUDGET_CENTS=100, **PRICES):
            reservation = ai_budget.reserve(0, 400, provider="ollama")
            self.assertTrue(reservation.attempt_id)
            self.assertTrue(ai_budget.settle(reservation, 0, 100, provider="ollama"))
            self.assertFalse(ai_budget.settle(reservation, 0, 100, provider="ollama"))
            self.assertFalse(ai_budget.release(reservation))                          # вече отчетена: не се освобождава
            self.assertEqual(usage_cost(), (ai_budget.cost_millicents(0, 100), 1))
            self.assertEqual([row[0] for row in ledger()], ["settled"])
            self.assertEqual(ledger()[0][1:4], ("ollama", 0, 100))

    def test_release_is_idempotent_and_does_not_touch_a_paid_attempt(self):
        with env(AI_DAILY_BUDGET_CENTS=10, AI_MONTHLY_BUDGET_CENTS=100, **PRICES):
            reservation = ai_budget.reserve(0, 400)
            self.assertTrue(ai_budget.release(reservation))
            self.assertFalse(ai_budget.release(reservation))
            self.assertFalse(ai_budget.settle(reservation, 0, 50))
            self.assertEqual(usage_cost(), (0, 0))

    def test_a_failed_write_keeps_the_money_reserved_not_free(self):
        with env(AI_DAILY_BUDGET_CENTS=1, AI_MONTHLY_BUDGET_CENTS=100000, **PRICES):
            reservation = ai_budget.reserve(0, 600)
            with mock.patch.object(ai_budget, "_add_usage", side_effect=RuntimeError("база")):
                self.assertFalse(ai_budget.settle(reservation, 0, 100))
            self.assertEqual([row[0] for row in ledger()], ["uncertain"])
            self.assertEqual(usage_cost(), (0, 0))
            with self.assertRaises(ai_budget.BudgetExceeded):                         # 600 още се броят към тавана
                ai_budget.reserve(0, 600)
            self.assertFalse(ai_budget.release(reservation))                          # несигурна не се освобождава „на сляпо“


class ProviderOutcomesTest(Base):
    def setUp(self):
        super().setUp()
        self.harness = ProviderCallTest("test_a_successful_call_holds_one_reservation_and_settles_it")

    def call(self, behaviours, **budget):
        return self.harness.run_call(behaviours, **budget)

    GOOD = ProviderCallTest.GOOD

    def test_a_paid_empty_200_with_usage_is_counted_before_the_fallback(self):
        empty = {"choices": [{"message": {"content": "", "reasoning": "мисли"}, "finish_reason": "stop"}],
                 "usage": {"prompt_tokens": 20, "completion_tokens": 300}}
        result, calls, _ = self.call([empty, dict(self.GOOD)])
        self.assertEqual(result, "отговор")
        self.assertEqual(len(calls), 2)
        states = ledger()
        self.assertEqual([(s[0], s[1], s[2], s[3]) for s in states], [("settled", "ollama", 20, 300), ("settled", "together", 10, 50)])
        self.assertEqual(usage_cost()[1], 2)                                          # и двата платени опита са в разхода

    def test_a_reasoning_only_answer_without_content_is_counted_too(self):
        reasoning = {"choices": [{"message": {"content": None, "reasoning": "само мисли"}, "finish_reason": "length"}],
                     "usage": {"prompt_tokens": 30, "completion_tokens": 400}}
        result, calls, _ = self.call([reasoning, dict(self.GOOD)])
        self.assertEqual(result, "отговор")
        self.assertEqual(ledger()[0][:4], ("settled", "ollama", 30, 400))

    def test_a_truncated_answer_is_paid_and_settled_once(self):
        cut = {"choices": [{"message": {"content": "започнато изречение и"}, "finish_reason": "length"}],
               "usage": {"prompt_tokens": 12, "completion_tokens": 400}}
        result, calls, _ = self.call([cut])
        self.assertEqual(result, "започнато изречение и")
        self.assertEqual(([s[0] for s in ledger()], usage_cost()[1]), (["settled"], 1))

    def test_an_empty_200_without_usage_is_estimated_not_free(self):
        empty = {"choices": [{"message": {"content": ""}, "finish_reason": "stop"}]}
        self.call([empty, dict(self.GOOD)])
        first = ledger()[0]
        self.assertEqual(first[0], "settled")
        self.assertGreater(first[2], 0)                                                # входът е оценен по дължината

    def test_a_timeout_after_sending_is_uncertain_and_still_counts(self):
        result, calls, _ = self.call([httpx.ReadTimeout("бавно"), httpx.ReadTimeout("бавно"), dict(self.GOOD)])
        self.assertEqual(result, "отговор")
        self.assertEqual([s[0] for s in ledger()], ["uncertain", "uncertain", "settled"])
        self.assertEqual(ai_budget.uncertain_summary()["uncertain_attempts"], 2)
        self.assertEqual(reservations(), 2)                                            # двата несигурни още заемат таван

    def test_an_unopened_connection_is_released_not_uncertain(self):
        self.call([httpx.ConnectError("няма връзка"), httpx.ConnectTimeout("няма връзка"), dict(self.GOOD)])
        self.assertEqual([s[0] for s in ledger()], ["released", "released", "settled"])

    def test_a_http_error_is_released(self):
        self.call([500, 502, dict(self.GOOD)])
        self.assertEqual([s[0] for s in ledger()], ["released", "released", "settled"])

    def test_every_attempt_is_a_separate_row_with_its_own_id(self):
        self.call([503, 503, dict(self.GOOD)])
        ids = [row[4] for row in ledger()]
        self.assertEqual(len(ids), 3)
        self.assertEqual(len(set(ids)), 3)
        self.assertEqual([row[1] for row in ledger()], ["ollama", "ollama", "together"])


class PricingTest(Base):
    def test_the_input_reservation_is_an_upper_bound_not_an_estimate(self):
        text = "Български текст за проверка " * 50
        self.assertEqual(ai_budget.upper_bound_tokens(text), len(text))
        self.assertGreaterEqual(ai_budget.upper_bound_tokens(text), ai_budget.estimate_tokens(text))
        self.assertGreater(ai_budget.estimate_tokens(text), len(text) // 3)             # оценката без usage вече не е по-ниска от 2 знака/токен

    def test_each_provider_can_have_its_own_tariff(self):
        with env(AI_PRICE_INPUT_CENTS_PER_MTOK=0, AI_PRICE_OUTPUT_CENTS_PER_MTOK=1000, AI_PRICE_OUTPUT_CENTS_PER_MTOK_TOGETHER=3000):
            self.assertEqual(ai_budget.cost_millicents(0, 100, provider="ollama"), 100)
            self.assertEqual(ai_budget.cost_millicents(0, 100, provider="together"), 300)
            self.assertEqual(ai_budget.cost_millicents(0, 100), 100)


class RolloverTest(Base):
    def test_midnight_starts_a_new_day_and_the_month_a_new_month(self):
        with env(AI_DAILY_BUDGET_CENTS=1, AI_MONTHLY_BUDGET_CENTS=1, **PRICES):                    # дневен и месечен таван 1000
            end_of_october = datetime(2026, 10, 31, 23, 59, 59)
            first = ai_budget.reserve(0, 600, now=end_of_october)
            with self.assertRaises(ai_budget.BudgetExceeded) as same_day:
                ai_budget.reserve(0, 600, now=end_of_october)
            self.assertEqual(same_day.exception.reason, "daily")
            ai_budget.settle(first, 0, 600, now=end_of_october)
            new_month = datetime(2026, 11, 1, 0, 0, 1)
            second = ai_budget.reserve(0, 600, now=new_month)                                   # нов ден и нов месец: октомври не пречи
            self.assertIsNotNone(second)
            with self.assertRaises(ai_budget.BudgetExceeded) as monthly:
                ai_budget.reserve(0, 600, now=new_month)
            self.assertIn(monthly.exception.reason, ("daily", "monthly"))
            db = SessionLocal()
            try:
                self.assertEqual({r.day for r in db.query(AIUsage).all()} >= {"2026-10-31"}, True)
            finally:
                db.close()


class MigrationTest(unittest.TestCase):
    def test_old_reservations_become_reserved_and_the_migration_reverts(self):
        db_engine = make_engine("p25")
        with db_engine.begin() as conn:
            command.upgrade(alembic_config(conn), "0014_report_snapshot")
            conn.execute(sa.text("INSERT INTO ai_reservations (day, millicents, created_at) VALUES ('2026-10-11', 500, '2026-10-11 10:00:00')"))
            command.upgrade(alembic_config(conn), "0015_ai_attempt_ledger")
            row = conn.execute(sa.text("SELECT state, attempt_id, provider FROM ai_reservations")).one()
            self.assertEqual(tuple(row), ("reserved", None, None))
            command.downgrade(alembic_config(conn), "0014_report_snapshot")
            self.assertNotIn("state", {c["name"] for c in sa.inspect(conn).get_columns("ai_reservations")})
            self.assertEqual(conn.execute(sa.text("SELECT millicents FROM ai_reservations")).scalar(), 500)


@unittest.skipUnless(os.getenv("DATABASE_URL", "").startswith("postgresql"), "двупроцесният тест иска PostgreSQL (DATABASE_URL)")
class TwoProcessTest(Base):
    def test_two_real_processes_cannot_pass_the_cap_together(self):
        import ledger_worker
        context = multiprocessing.get_context("spawn")
        barrier, out = context.Barrier(2), context.Queue()
        workers = [context.Process(target=ledger_worker.run, args=(barrier, 8, out)) for _ in range(2)]
        for worker in workers:
            worker.start()
        granted = [out.get(timeout=120) for _ in workers]
        for worker in workers:
            worker.join(timeout=60)
            self.assertEqual(worker.exitcode, 0)
        # таван 1000 милицента, 400 на резервация: най-много 2 в общо 16 опита от двата процеса
        self.assertEqual(sum(granted), 2, granted)
        self.assertEqual(reservations(), 2)


if __name__ == "__main__":
    unittest.main()
