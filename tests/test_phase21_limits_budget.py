"""
Фаза 21: точна квота при отказ (AM-N10) и атомична резервация на AI бюджета (AM-N08).

- Всяка приета заявка държи свое място в лимита (token): отказът освобождава точно него, идемпотентно.
- Преди всяка заявка към доставчика се резервира най-високият възможен разход; паралелни заявки не минават над тавана.
"""
import asyncio
import os
import unittest
from datetime import datetime, timedelta
from unittest import mock

import testenv  # noqa: F401  (трябва да е преди main)
import ai_budget  # noqa: E402
import generation  # noqa: E402
import jobs  # noqa: E402
import main  # noqa: E402
import rate_limit  # noqa: E402
from database import AIReservation, AIUsage, Job, SessionLocal, User  # noqa: E402
from rate_limit import RateLimiter, limiter  # noqa: E402
from schemas import ChartRequest  # noqa: E402
from testenv import CHART, register_and_login  # noqa: E402


class Clock:
    def __init__(self):
        self.now = 0.0

    def __call__(self):
        return self.now


class LimiterTokenTest(unittest.TestCase):
    def test_a_release_frees_exactly_the_failed_request_not_the_last_one(self):
        clock, l = Clock(), RateLimiter()
        with mock.patch("rate_limit.time.monotonic", clock):
            clock.now = 0.0
            self.assertEqual(l.hit("k", 2, 3600, "a"), 0)               # по-стара задача
            clock.now = 120.0
            self.assertEqual(l.hit("k", 2, 3600, "b"), 0)               # по-нова успешна задача
            self.assertEqual(l.peek("k", 2, 3600)[0], 0)
            self.assertTrue(l.release("k", "a"))                        # отказът е на старата
            self.assertEqual(l.peek("k", 2, 3600)[0], 1)
            clock.now = 3601.0                                           # старата така или иначе е излязла от прозореца
            self.assertEqual(l.peek("k", 2, 3600), (1, 0))               # новата още се брои: остава едно място, не две

    def test_release_is_idempotent_and_ignores_unknown_or_missing_tokens(self):
        clock, l = Clock(), RateLimiter()
        with mock.patch("rate_limit.time.monotonic", clock):
            l.hit("k", 3, 3600, "a")
            l.hit("k", 3, 3600)                                          # обикновена заявка без token
            self.assertTrue(l.release("k", "a"))
            self.assertFalse(l.release("k", "a"))
            self.assertFalse(l.release("k", "other"))
            self.assertFalse(l.release("k", None))
            self.assertFalse(l.release("missing", "a"))
            self.assertEqual(l.peek("k", 3, 3600)[0], 2)

    def test_a_blocked_request_does_not_take_a_place(self):
        clock, l = Clock(), RateLimiter()
        with mock.patch("rate_limit.time.monotonic", clock):
            l.hit("k", 1, 3600, "a")
            self.assertGreater(l.hit("k", 1, 3600, "b"), 0)
            self.assertFalse(l.release("k", "b"))
            self.assertEqual(l.peek("k", 1, 3600)[0], 0)


class JobsLimitTest(unittest.TestCase):
    """Лимитът в тестовете е два анализа на час; задачите се създават без да се пускат (остават в опашката)."""

    @classmethod
    def setUpClass(cls):
        from fastapi.testclient import TestClient
        cls.client = TestClient(main.app)

    def setUp(self):
        limiter.reset()
        self.clock = Clock()
        patcher = mock.patch("rate_limit.time.monotonic", self.clock)
        patcher.start()
        self.addCleanup(patcher.stop)

    def user(self, email):
        register_and_login(self.client, email)
        db = SessionLocal()
        self.addCleanup(db.close)
        return db, db.query(User).filter(User.email == email).one()

    def create(self, db, user, name, key):
        job, state = jobs.create_job(db, user, ChartRequest(**{**CHART, "name": name}), key)
        self.assertEqual(state, "created")
        return job.id

    def remaining(self, db, user):
        return jobs.limits_payload(db, user)["hour_remaining"]

    def fail(self, job_id):
        return jobs._fail(job_id, generation.GenerationFailure("internal", "Не успяхме"))

    def test_a_rejected_old_job_gives_back_its_own_place_and_a_repeat_changes_nothing(self):
        db, user = self.user("p21-a@test.bg")
        self.clock.now = 0.0
        first = self.create(db, user, "А", "k1")
        self.clock.now = 120.0
        second = self.create(db, user, "Б", "k2")
        self.assertEqual(self.remaining(db, user), 0)
        self.assertTrue(self.fail(first))
        self.assertEqual(self.remaining(db, user), 1)
        self.assertFalse(self.fail(first))                               # повторен _fail: нищо не се освобождава втори път
        self.assertEqual(self.remaining(db, user), 1)
        self.clock.now = 3601.0                                           # първата така или иначе е излязла от прозореца
        self.assertEqual(self.remaining(db, user), 1)                     # втората още се брои
        self.assertTrue(self.fail(second))
        self.assertEqual(self.remaining(db, user), 2)

    def test_the_failures_in_reverse_order_free_the_right_places(self):
        db, user = self.user("p21-b@test.bg")
        self.clock.now = 0.0
        first = self.create(db, user, "А", "k1")
        self.clock.now = 60.0
        second = self.create(db, user, "Б", "k2")
        self.assertTrue(self.fail(second))                               # отказва по-новата
        self.clock.now = 3601.0                                           # първата е излязла от прозореца
        self.assertEqual(self.remaining(db, user), 2)
        self.assertFalse(self.fail(second))
        self.assertEqual(self.remaining(db, user), 2)
        self.assertIsNotNone(first)

    def test_a_job_cancelled_by_the_client_still_counts(self):
        db, user = self.user("p21-c@test.bg")
        job_id = self.create(db, user, "А", "k1")
        self.assertEqual(self.remaining(db, user), 1)
        jobs.cancel_job(db, user, job_id)
        self.assertEqual(self.remaining(db, user), 1)

    def test_a_job_that_cannot_be_recovered_gives_its_place_back(self):
        db, user = self.user("p21-d@test.bg")
        job_id = self.create(db, user, "А", "k1")
        self.assertEqual(self.remaining(db, user), 1)
        job = db.get(Job, job_id)
        job.attempts = jobs.JOB_MAX_ATTEMPTS
        job.lease_until = datetime.utcnow() - timedelta(minutes=5)
        db.commit()
        jobs.recover_stale()
        db.expire_all()
        self.assertEqual(db.get(Job, job_id).status, "failed")
        self.assertEqual(self.remaining(db, user), 2)

    def test_a_replay_and_an_invalid_request_take_no_place(self):
        db, user = self.user("p21-e@test.bg")
        first = self.create(db, user, "А", "same")
        job, state = jobs.create_job(db, user, ChartRequest(**{**CHART, "name": "А"}), "same")
        self.assertEqual((state, job.id), ("replay", first))
        self.assertEqual(self.remaining(db, user), 1)

    def test_the_token_is_kept_in_the_job_but_not_shown_to_the_client(self):
        db, user = self.user("p21-f@test.bg")
        job_id = self.create(db, user, "А", "k1")
        job = db.get(Job, job_id)
        self.assertTrue(job.request["_limit_token"])
        self.assertNotIn("_limit_token", str(jobs.serialize(job, after=0)))


def env(**values):
    return mock.patch.dict(os.environ, {k: str(v) for k, v in values.items()})


def clean():
    db = SessionLocal()
    try:
        db.query(AIReservation).delete()
        db.query(AIUsage).delete()
        db.commit()
    finally:
        db.close()


def reservations():
    db = SessionLocal()
    try:
        return db.query(AIReservation).count()
    finally:
        db.close()


def usage_cost():
    db = SessionLocal()
    try:
        row = db.query(AIUsage).first()
        return (row.cost_millicents, row.calls) if row else (0, 0)
    finally:
        db.close()


# 1000 евроцента за милион изходни токена: 400 изходни токена струват 400 милицента (0,4 евроцента)
PRICES = dict(AI_PRICE_INPUT_CENTS_PER_MTOK=0, AI_PRICE_OUTPUT_CENTS_PER_MTOK=1000)


class ReservationTest(unittest.TestCase):
    def setUp(self):
        clean()
        self.addCleanup(clean)

    def test_parallel_requests_cannot_pass_the_daily_cap_together(self):
        with env(AI_DAILY_BUDGET_CENTS=1, AI_MONTHLY_BUDGET_CENTS=100000, **PRICES):        # таван 1000 милицента
            first = ai_budget.reserve(0, 400)
            second = ai_budget.reserve(0, 400)                                            # 800 от 1000
            with self.assertRaises(ai_budget.BudgetExceeded) as stop:
                ai_budget.reserve(0, 400)                                                 # би станало 1200
            self.assertEqual(stop.exception.reason, "daily")
            self.assertEqual(reservations(), 2)
            ai_budget.release(first)                                                      # свободно място: пак може
            self.assertIsNotNone(ai_budget.reserve(0, 400))
            ai_budget.release(second)

    def test_the_monthly_cap_counts_the_reservations_too(self):
        with env(AI_DAILY_BUDGET_CENTS=0, AI_MONTHLY_BUDGET_CENTS=1, **PRICES):
            ai_budget.reserve(0, 600)
            with self.assertRaises(ai_budget.BudgetExceeded) as stop:
                ai_budget.reserve(0, 600)
            self.assertEqual(stop.exception.reason, "monthly")

    def test_settle_replaces_the_reservation_with_the_real_cost_and_release_is_idempotent(self):
        with env(AI_DAILY_BUDGET_CENTS=10, AI_MONTHLY_BUDGET_CENTS=100, **PRICES):
            reservation = ai_budget.reserve(0, 400)
            self.assertEqual(reservations(), 1)
            ai_budget.settle(reservation, 0, 100)
            self.assertEqual((reservations(), usage_cost()), (0, (ai_budget.cost_millicents(0, 100), 1)))
            ai_budget.release(reservation)                                               # втори път: нищо
            ai_budget.release(None)
            self.assertEqual(reservations(), 0)

    def test_an_abandoned_reservation_expires(self):
        with env(AI_DAILY_BUDGET_CENTS=1, AI_MONTHLY_BUDGET_CENTS=100000, **PRICES):
            old = datetime.utcnow() - timedelta(minutes=ai_budget.RESERVATION_TTL_MINUTES + 5)
            db = SessionLocal()
            try:
                db.add(AIReservation(day=old.strftime("%Y-%m-%d"), millicents=900, created_at=old))
                db.commit()
            finally:
                db.close()
            self.assertIsNotNone(ai_budget.reserve(0, 400))                              # старата не пречи
            self.assertEqual(reservations(), 1)

    def test_the_real_spend_and_the_reservations_both_count(self):
        with env(AI_DAILY_BUDGET_CENTS=1, AI_MONTHLY_BUDGET_CENTS=100000, **PRICES):
            ai_budget.record(0, 500)                                                      # 500 вече изразходени
            ai_budget.reserve(0, 300)                                                     # 800
            with self.assertRaises(ai_budget.BudgetExceeded):
                ai_budget.reserve(0, 300)                                                 # 1100

    def test_emergency_stop_and_no_caps(self):
        with env(AI_EMERGENCY_STOP=1, **PRICES):
            with self.assertRaises(ai_budget.BudgetExceeded) as stop:
                ai_budget.reserve(0, 10)
            self.assertEqual(stop.exception.reason, "emergency")
        with env(AI_DAILY_BUDGET_CENTS=0, AI_MONTHLY_BUDGET_CENTS=0, **PRICES):
            self.assertIsNone(ai_budget.reserve(0, 10 ** 6))
            self.assertEqual(reservations(), 0)


class ProviderCallTest(unittest.TestCase):
    """Всяка заявка към доставчика (първичен, резервен, поправка) има своя резервация и я затваря."""

    def setUp(self):
        clean()
        self.addCleanup(clean)

    def run_call(self, behaviours, **budget):
        """behaviours: за всяка заявка по ред - отговор (dict), HTTP статус (int) или грешка (Exception)."""
        calls = []
        seen_reservations = []

        class FakeResponse:
            def __init__(self, item):
                self.item = item
                self.status_code = item if isinstance(item, int) else 200
                self.text = "грешка"

            def json(self):
                return self.item

        class FakeClient:
            def __init__(self, *a, **k):
                pass

            async def __aenter__(self):
                return self

            async def __aexit__(self, *a):
                return False

            async def post(self, *a, **k):
                seen_reservations.append(reservations())
                calls.append(1)
                item = behaviours.pop(0)
                if isinstance(item, Exception):
                    raise item
                return FakeResponse(item)

        ai = main.ai_interpreter
        with mock.patch.object(ai, "ollama_key", "k"), mock.patch.object(ai, "ollama_url", "http://x"), \
                mock.patch.object(ai, "together_key", "t"), mock.patch.object(ai, "together_url", "http://t"), \
                mock.patch("ai_interpreter.httpx.AsyncClient", FakeClient), mock.patch("ai_interpreter.asyncio.sleep", mock.AsyncMock()), \
                env(**{**dict(AI_DAILY_BUDGET_CENTS=100, AI_MONTHLY_BUDGET_CENTS=1000, **PRICES), **budget}):
            try:
                result = asyncio.run(ai._call_api("система", "потребител", 400, max_retries=2, add_context=False))
            except Exception as exc:
                result = exc
        return result, calls, seen_reservations

    GOOD = {"choices": [{"message": {"content": "отговор"}, "finish_reason": "stop"}], "usage": {"prompt_tokens": 10, "completion_tokens": 50}}

    def test_a_successful_call_holds_one_reservation_and_settles_it(self):
        result, calls, seen = self.run_call([dict(self.GOOD)])
        self.assertEqual(result, "отговор")
        self.assertEqual((len(calls), seen), (1, [1]))                                   # по време на заявката има резервация
        self.assertEqual((reservations(), usage_cost()[1]), (0, 1))                       # след нея няма, реалният разход е записан
        with env(**PRICES):
            self.assertEqual(usage_cost()[0], ai_budget.cost_millicents(10, 50))

    def test_a_failed_attempt_is_released_and_the_fallback_gets_its_own_reservation(self):
        result, calls, seen = self.run_call([503, 503, dict(self.GOOD)])                  # два опита към Ollama, после Together
        self.assertEqual(result, "отговор")
        self.assertEqual(len(calls), 3)
        self.assertEqual(seen, [1, 1, 1])                                                # никога повече от една наведнъж
        self.assertEqual((reservations(), usage_cost()[1]), (0, 1))                       # само платеният отговор е записан

    def test_when_every_provider_fails_nothing_stays_reserved_or_spent(self):
        result, calls, seen = self.run_call([RuntimeError("мрежа"), RuntimeError("мрежа"), RuntimeError("мрежа")])
        self.assertIsInstance(result, RuntimeError)
        self.assertEqual((reservations(), usage_cost()), (0, (0, 0)))

    def test_a_request_that_does_not_fit_the_budget_never_reaches_the_provider(self):
        result, calls, seen = self.run_call([dict(self.GOOD)], AI_DAILY_BUDGET_CENTS=1, AI_MONTHLY_BUDGET_CENTS=1000,
                                            AI_PRICE_OUTPUT_CENTS_PER_MTOK=3000)               # 400 токена струват 1200 > таван 1000
        self.assertIsInstance(result, ai_budget.BudgetExceeded)
        self.assertEqual((calls, reservations()), ([], 0))


if __name__ == "__main__":
    unittest.main()


class LogoutAllTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from fastapi.testclient import TestClient
        cls.client = TestClient(main.app)

    def setUp(self):
        limiter.reset()

    def test_logout_everywhere_revokes_every_issued_token_and_a_new_login_works(self):
        h1 = register_and_login(self.client, "p21-logout@test.bg")
        login = self.client.post("/login", json={"email": "p21-logout@test.bg", "password": testenv.PASSWORD})
        h2 = {"Authorization": f"Bearer {login.json()['access_token']}"}
        self.assertEqual(self.client.get("/me", headers=h1).status_code, 200)
        self.assertEqual(self.client.get("/me", headers=h2).status_code, 200)
        self.assertEqual(self.client.post("/logout-all", headers=h1).status_code, 200)
        self.assertEqual(self.client.get("/me", headers=h1).status_code, 401)
        self.assertEqual(self.client.get("/me", headers=h2).status_code, 401)
        again = self.client.post("/login", json={"email": "p21-logout@test.bg", "password": testenv.PASSWORD})
        self.assertEqual(self.client.get("/me", headers={"Authorization": f"Bearer {again.json()['access_token']}"}).status_code, 200)

    def test_logout_everywhere_needs_a_login(self):
        self.assertEqual(self.client.post("/logout-all").status_code, 401)
