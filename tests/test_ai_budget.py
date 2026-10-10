"""
Тестове за Фаза 14: бюджет за AI с аварийно спиране (дневен и месечен), записване на разхода и спрени задачи без такса.

Пускане: python -m unittest discover -s tests -p "test_ai_budget.py"
"""
import os
import unittest
from datetime import datetime
from unittest import mock

import testenv  # noqa: F401  (трябва да е преди main)
from fastapi.testclient import TestClient  # noqa: E402

import ai_budget  # noqa: E402
import main  # noqa: E402
from database import AIUsage, SessionLocal, User  # noqa: E402
from rate_limit import limiter  # noqa: E402
from testenv import CHART, give_deposit, register_and_login  # noqa: E402


def clear_usage():
    db = SessionLocal()
    try:
        db.query(AIUsage).delete()
        db.commit()
    finally:
        db.close()


def env(**values):
    return mock.patch.dict(os.environ, {k: str(v) for k, v in values.items()})


class MathTest(unittest.TestCase):
    def test_cost_from_tokens(self):
        with env(AI_PRICE_INPUT_CENTS_PER_MTOK=30, AI_PRICE_OUTPUT_CENTS_PER_MTOK=120):
            self.assertEqual(ai_budget.cost_millicents(1_000_000, 0), 30_000)
            self.assertEqual(ai_budget.cost_millicents(0, 1_000_000), 120_000)
            self.assertEqual(ai_budget.cost_millicents(7000, 4500), int(round((7000 * 30 + 4500 * 120) / 1_000_000 * 1000)))

    def test_estimate_without_usage(self):
        self.assertEqual(ai_budget.estimate_tokens("а" * 300), 100)
        self.assertEqual(ai_budget.estimate_tokens(""), 1)


class RecordAndCheckTest(unittest.TestCase):
    def setUp(self):
        clear_usage()

    def test_records_accumulate_per_day_and_month(self):
        with env(AI_PRICE_INPUT_CENTS_PER_MTOK=100, AI_PRICE_OUTPUT_CENTS_PER_MTOK=100):
            ai_budget.record(500_000, 500_000, datetime(2026, 10, 3, 10))        # 100 ц.
            ai_budget.record(500_000, 500_000, datetime(2026, 10, 3, 22))        # още 100 ц. същия ден
            ai_budget.record(1_000_000, 0, datetime(2026, 10, 9, 8))             # 100 ц. друг ден
            ai_budget.record(1_000_000, 0, datetime(2026, 11, 1, 8))             # друг месец
            used = ai_budget.spent(datetime(2026, 10, 9, 12))
        self.assertEqual((used["today_cents"], used["month_cents"], used["today_calls"]), (100, 300, 1))
        self.assertEqual(ai_budget.spent(datetime(2026, 10, 3, 23))["today_cents"], 200)
        self.assertEqual(ai_budget.spent(datetime(2026, 11, 20))["month_cents"], 100)

    def test_emergency_stop(self):
        ai_budget.check()
        with env(AI_EMERGENCY_STOP=1):
            with self.assertRaises(ai_budget.BudgetExceeded) as stop:
                ai_budget.check()
        self.assertEqual(stop.exception.reason, "emergency")

    def test_the_daily_budget_stops_at_the_limit_and_resets_the_next_day(self):
        with env(AI_PRICE_INPUT_CENTS_PER_MTOK=100, AI_PRICE_OUTPUT_CENTS_PER_MTOK=100, AI_DAILY_BUDGET_CENTS=150,
                 AI_MONTHLY_BUDGET_CENTS=0):
            ai_budget.record(1_000_000, 0, datetime(2026, 10, 5, 9))
            ai_budget.check(datetime(2026, 10, 5, 10))                            # 100 < 150
            ai_budget.record(500_000, 0, datetime(2026, 10, 5, 11))               # 150 = таванът
            with self.assertRaises(ai_budget.BudgetExceeded) as stop:
                ai_budget.check(datetime(2026, 10, 5, 12))
            self.assertEqual(stop.exception.reason, "daily")
            ai_budget.check(datetime(2026, 10, 6, 0, 1))                          # нов ден

    def test_the_monthly_budget(self):
        with env(AI_PRICE_INPUT_CENTS_PER_MTOK=100, AI_PRICE_OUTPUT_CENTS_PER_MTOK=100, AI_DAILY_BUDGET_CENTS=0,
                 AI_MONTHLY_BUDGET_CENTS=250):
            for day in (1, 2, 3):
                ai_budget.record(1_000_000, 0, datetime(2026, 10, day, 9))
            with self.assertRaises(ai_budget.BudgetExceeded) as stop:
                ai_budget.check(datetime(2026, 10, 4))
            self.assertEqual(stop.exception.reason, "monthly")
            ai_budget.check(datetime(2026, 11, 1))

    def test_zero_means_no_ceiling(self):
        with env(AI_PRICE_INPUT_CENTS_PER_MTOK=100, AI_DAILY_BUDGET_CENTS=0, AI_MONTHLY_BUDGET_CENTS=0):
            ai_budget.record(50_000_000, 0, datetime(2026, 10, 5, 9))
            ai_budget.check(datetime(2026, 10, 5, 10))

    def test_garbage_settings_fall_back_to_defaults(self):
        with env(AI_DAILY_BUDGET_CENTS="много", AI_MONTHLY_BUDGET_CENTS=""):
            self.assertEqual(ai_budget.settings()["daily_cents"], 1000)

    def test_status_for_the_admin(self):
        with env(AI_EMERGENCY_STOP=1):
            self.assertEqual(ai_budget.status()["state"], "stopped:emergency")
        self.assertEqual(ai_budget.status()["state"], "ok")


class ProviderTest(unittest.TestCase):
    """_call_api записва разхода и не вика доставчика при спиране."""

    def setUp(self):
        clear_usage()

    def call(self, response_json):
        class FakeResponse:
            status_code = 200

            def json(self_inner):
                return response_json

        class FakeClient:
            def __init__(self, *a, **k):
                pass

            async def __aenter__(self):
                return self

            async def __aexit__(self, *a):
                return False

            async def post(self, *a, **k):
                self.called = True
                return FakeResponse()

        import asyncio
        ai = main.ai_interpreter
        with mock.patch.object(ai, "ollama_key", "k"), mock.patch.object(ai, "ollama_url", "http://x"), \
                mock.patch("ai_interpreter.httpx.AsyncClient", FakeClient):
            return asyncio.run(ai._call_api("система", "потребител", 100, add_context=False))

    def test_usage_from_the_provider_is_recorded(self):
        with env(AI_PRICE_INPUT_CENTS_PER_MTOK=100, AI_PRICE_OUTPUT_CENTS_PER_MTOK=200):
            self.call({"choices": [{"message": {"content": "отговор"}, "finish_reason": "stop"}],
                       "usage": {"prompt_tokens": 1000, "completion_tokens": 500}})
            db = SessionLocal()
            try:
                row = db.query(AIUsage).one()
                self.assertEqual((row.calls, row.prompt_tokens, row.completion_tokens), (1, 1000, 500))
                self.assertEqual(row.cost_millicents, ai_budget.cost_millicents(1000, 500))
            finally:
                db.close()

    def test_without_usage_the_text_length_is_used(self):
        self.call({"choices": [{"message": {"content": "а" * 90}, "finish_reason": "stop"}], "usage": {}})
        db = SessionLocal()
        try:
            row = db.query(AIUsage).one()
            self.assertEqual((row.prompt_tokens, row.completion_tokens), (ai_budget.estimate_tokens("системапотребител"), 30))
        finally:
            db.close()

    def test_a_stopped_service_never_reaches_the_provider(self):
        with env(AI_EMERGENCY_STOP=1):
            with self.assertRaises(ai_budget.BudgetExceeded):
                self.call({"choices": [{"message": {"content": "x"}}]})
        db = SessionLocal()
        try:
            self.assertEqual(db.query(AIUsage).count(), 0)
        finally:
            db.close()


class JobsTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.client = TestClient(main.app)

    def setUp(self):
        limiter.reset()
        clear_usage()

    def balances(self, email):
        db = SessionLocal()
        try:
            row = db.query(User.paid_cents, User.gift_cents).filter(User.email == email).one()
            return int(row[0]), int(row[1])
        finally:
            db.close()

    def test_a_stopped_service_creates_no_job_and_takes_nothing(self):
        h = register_and_login(self.client, "bud-stop@test.bg")
        fake = mock.AsyncMock(return_value="<p>т</p>")
        with mock.patch.object(main.ai_interpreter, "interpret_chart", fake), env(AI_EMERGENCY_STOP=1, BALANCE_ENFORCED=1):
            r = self.client.post("/jobs?wait=30", json=CHART, headers=h)
        self.assertEqual(r.status_code, 503, r.text)
        self.assertIn("Нищо не е таксувано", r.json()["detail"])
        self.assertEqual(self.client.get("/jobs", headers=h).json()["jobs"], [])
        self.assertEqual(self.balances("bud-stop@test.bg"), (0, 500))
        fake.assert_not_called()

    def test_the_budget_stops_new_jobs_and_a_new_day_resumes_them(self):
        h = register_and_login(self.client, "bud-day@test.bg")
        with env(AI_PRICE_INPUT_CENTS_PER_MTOK=100, AI_PRICE_OUTPUT_CENTS_PER_MTOK=100, AI_DAILY_BUDGET_CENTS=100,
                 AI_MONTHLY_BUDGET_CENTS=0):
            ai_budget.record(1_000_000, 0)                                         # днешният таван е достигнат
            r = self.client.post("/jobs?wait=30", json=CHART, headers=h)
            self.assertEqual(r.status_code, 503)
            clear_usage()
            with mock.patch.object(main.ai_interpreter, "interpret_chart", mock.AsyncMock(return_value="<p>т</p>")):
                self.assertEqual(self.client.post("/jobs?wait=30", json=CHART, headers=h).status_code, 200)

    def test_a_job_stopped_midway_fails_and_the_money_returns(self):
        h = register_and_login(self.client, "bud-mid@test.bg")
        calls = {"n": 0}

        async def stop_midway(*args, **kwargs):
            calls["n"] += 1
            raise ai_budget.BudgetExceeded("emergency")
        import billing
        with mock.patch.object(main.ai_interpreter, "interpret_chart", stop_midway), env(BALANCE_ENFORCED=1):
            self.assertTrue(billing.balance_enforced())                         # сумата наистина се резервира
            job = self.client.post("/jobs?wait=30", json=CHART, headers=h).json()["job"]
        reasons = [t["reason"] for t in self.client.get("/billing/transactions", headers=h).json()["transactions"]]
        self.assertIn("job_refund", reasons)                                    # и се връща
        self.assertEqual(job["status"], "failed", job)
        self.assertEqual(job["error"]["code"], "stopped")
        self.assertIn("Нищо не е таксувано", job["error"]["message"])
        self.assertEqual(self.balances("bud-mid@test.bg"), (0, 500))

    def test_the_admin_sees_the_budget(self):
        from testenv import PASSWORD
        admin = "bud-admin@test.bg"
        with env(ADMIN_EMAILS=admin):
            h = register_and_login(self.client, admin)
            db = SessionLocal()
            try:
                db.query(User).filter(User.email == admin).update({"email_verified": True})
                db.commit()
            finally:
                db.close()
            with env(AI_EMERGENCY_STOP=1):
                r = self.client.get("/admin/metrics", headers=h)
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual(r.json()["ai_budget"]["state"], "stopped:emergency")


if __name__ == "__main__":
    unittest.main()
