"""
Тестове за Фаза 11: задачи за генериране на анализ (jobs.py).

Покриват: идемпотентност, резервиране на сумата (и защита от паралелно харчене), връщане при неуспех, отказ и край като
състезание, прекъсване и възстановяване, срок, лимити, собственост, поверителност и почистване.

Паралелните случаи вървят в един event loop с httpx.ASGITransport (TestClient пуска нов loop за всяка заявка и не може
да държи задача между две заявки). Останалите ползват TestClient с ?wait=.

Пускане: python -m unittest discover -s tests -p "test_jobs.py"
"""
import asyncio
import hashlib
import hmac
import json
import os
import time
import unittest
from datetime import datetime, timedelta
from unittest import mock

import testenv  # noqa: F401  (трябва да е преди main)
import httpx  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

import billing  # noqa: E402
import jobs  # noqa: E402
import main  # noqa: E402
import scanner  # noqa: E402
from database import CoinTransaction, Event, Job, Report, SessionLocal, User  # noqa: E402
from rate_limit import limiter  # noqa: E402
from testenv import CHART, give_deposit, register_and_login  # noqa: E402

ENFORCED = {"BALANCE_ENFORCED": "1"}
WEBHOOK_SECRET = "whsec_jobs_test"
STRIPE_ENV = {"STRIPE_SECRET_KEY": "sk_test_jobs", "STRIPE_WEBHOOK_SECRET": WEBHOOK_SECRET}
FORECAST = {"is_dynamic": True, "target_date": "2026-01-01", "end_date": "2026-01-31"}
PAIR = {"partner_date": "1992-07-20", "partner_time": "09:30", "partner_lat": 43.2141, "partner_lon": 27.9147}


def uid_of(email):
    db = SessionLocal()
    try:
        return db.query(User.id).filter(User.email == email).scalar()
    finally:
        db.close()


def balances(uid):
    db = SessionLocal()
    try:
        row = db.query(User.paid_cents, User.gift_cents).filter(User.id == uid).one()
        return int(row[0]), int(row[1])
    finally:
        db.close()


def set_balances(uid, paid, gift):
    db = SessionLocal()
    try:
        now_paid, now_gift = balances(uid)
        billing.apply_transaction(db, uid, "admin", paid=paid - now_paid, gift=gift - now_gift, allow_negative=True,
                                  description="тест")
        db.commit()
    finally:
        db.close()


def ledger(uid):
    db = SessionLocal()
    try:
        rows = db.query(CoinTransaction).filter(CoinTransaction.user_id == uid).order_by(CoinTransaction.id).all()
        return [(r.reason, r.delta, r.delta_gift) for r in rows]
    finally:
        db.close()


def assert_ledger_matches(test, uid):
    db = SessionLocal()
    try:
        rows = db.query(CoinTransaction).filter(CoinTransaction.user_id == uid).all()
        paid, gift = balances(uid)
        test.assertEqual(sum(r.delta for r in rows), paid + gift)
        test.assertEqual(sum(r.delta_gift for r in rows), gift)
    finally:
        db.close()


def job_row(job_id):
    db = SessionLocal()
    try:
        job = db.get(Job, job_id)
        return {c.name: getattr(job, c.name) for c in Job.__table__.columns} if job else None
    finally:
        db.close()


def report_count(uid):
    db = SessionLocal()
    try:
        return db.query(Report).filter(Report.user_id == uid).count()
    finally:
        db.close()


def ok_ai(text="<p>текст</p>"):
    return mock.patch.object(main.ai_interpreter, "interpret_chart", mock.AsyncMock(return_value=text))


def month_ai():
    return (mock.patch.object(main.ai_interpreter, "_process_monthly_chunk", mock.AsyncMock(return_value="<p>м</p>")),
            mock.patch.object(main.ai_interpreter, "compose_period_overview", mock.AsyncMock(return_value="<p>о</p>")))


class Gate:
    """AI подмяна, която чака разрешение: така задачата остава "работеща", докато тестът действа."""

    def __init__(self, text="<p>текст</p>"):
        self.started = asyncio.Event()
        self.release = asyncio.Event()
        self.text = text
        self.calls = 0

    async def __call__(self, *args, **kwargs):
        self.calls += 1
        self.started.set()
        await self.release.wait()
        return self.text


class FakePayer:
    """
    Фалшив платец за теста от край до край: връща адрес за плащане (като Stripe Checkout) и подписва събитията, които
    Stripe би изпратил по-късно. Към Stripe не се праща нищо.
    """

    def __init__(self):
        self.sessions = {}

    async def create_session(self, path, data, idempotency_key=None):
        session_id = f"cs_fake_{len(self.sessions) + 1}"
        self.sessions[session_id] = {"purchase_id": data["metadata[purchase_id]"],
                                     "amount": int(data["line_items[0][price_data][unit_amount]"])}
        return {"id": session_id, "url": f"https://fake-pay.test/{session_id}"}

    @staticmethod
    def _signed(event):
        payload = json.dumps(event).encode()
        ts = int(time.time())
        signature = hmac.new(WEBHOOK_SECRET.encode(), f"{ts}.".encode() + payload, hashlib.sha256).hexdigest()
        return payload, {"stripe-signature": f"t={ts},v1={signature}"}

    def paid_event(self, session_id):
        info = self.sessions[session_id]
        return self._signed({"type": "checkout.session.completed", "data": {"object": {
            "id": session_id, "payment_status": "paid", "amount_total": info["amount"], "currency": "eur",
            "payment_intent": "pi_" + session_id, "metadata": {"purchase_id": info["purchase_id"]}}}})

    def refund_event(self, session_id, amount):
        return self._signed({"type": "charge.refunded", "data": {"object": {
            "payment_intent": "pi_" + session_id, "amount_refunded": amount}}})


class SyncJobsTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.client = TestClient(main.app)

    def setUp(self):
        limiter.reset()

    def post(self, headers, body=CHART, **params):
        query = "&".join(f"{k}={v}" for k, v in {"wait": 60, **params}.items())
        return self.client.post(f"/jobs?{query}", json=body, headers=headers)

    # --- основен ход ----------------------------------------------------------------------------------------------
    def test_a_job_runs_to_the_end_and_saves_the_report(self):
        h = register_and_login(self.client, "j-basic@test.bg")
        with ok_ai("<p>Анализ</p>"):
            r = self.post(h, {**CHART, "name": "Аз", "question": "Тайният въпрос?"})
        self.assertEqual(r.status_code, 200, r.text)
        job = r.json()["job"]
        self.assertEqual((job["status"], job["stage"], job["kind"], job["sku"], job["tier"]),
                         ("succeeded", "done", "analysis", "analysis.basic", "basic"))
        types = [e["type"] for e in job["events"]]
        self.assertEqual(types[0], "stage")
        self.assertIn("start", types)
        self.assertEqual(types[-1], "complete")
        self.assertEqual(next(e for e in job["events"] if e["type"] == "text")["interpretation"], "<p>Анализ</p>")
        report = self.client.get(f"/reports/{job['report_id']}", headers=h).json()
        self.assertEqual(report["content"], "<p>Анализ</p>")
        self.assertEqual(report["params"]["report_type"], "general")
        self.assertNotIn("question", report["params"])
        # свободният текст на въпроса не остава в задачата
        self.assertNotIn("question", job_row(job["id"])["request"])

    def test_forecast_events_follow_the_months(self):
        h = register_and_login(self.client, "j-forecast@test.bg")
        uid = uid_of("j-forecast@test.bg")
        give_deposit("j-forecast@test.bg", 500)
        with mock.patch.dict(os.environ, ENFORCED):
            a, b = month_ai()
            with a, b:
                r = self.post(h, {**CHART, **FORECAST})
        job = r.json()["job"]
        self.assertEqual((job["status"], job["kind"], job["sku"], job["tier"]), ("succeeded", "forecast", "forecast.single", "premium"))
        types = [e["type"] for e in job["events"] if e["type"] != "stage"]
        self.assertEqual(types, ["start", "month_start", "month_complete", "overview_start", "overview_complete", "complete"])
        self.assertEqual(job["charged_cents"], 75)
        self.assertEqual(balances(uid), (425, 500))
        assert_ledger_matches(self, uid)

    def test_events_can_be_read_from_an_index(self):
        h = register_and_login(self.client, "j-after@test.bg")
        with ok_ai():
            job = self.post(h).json()["job"]
        count = job["event_count"]
        self.assertEqual(len(job["events"]), count)
        later = self.client.get(f"/jobs/{job['id']}?after={count - 1}", headers=h).json()["job"]
        self.assertEqual([e["type"] for e in later["events"]], ["complete"])
        none_left = self.client.get(f"/jobs/{job['id']}?after={count}", headers=h).json()["job"]
        self.assertEqual(none_left["events"], [])
        self.assertIn("balance_cents", self.client.get(f"/jobs/{job['id']}", headers=h).json()["job"]["balance"])

    # --- проверки при създаване ------------------------------------------------------------------------------------
    def test_invalid_input_is_a_400_and_creates_nothing(self):
        h = register_and_login(self.client, "j-invalid@test.bg")
        uid = uid_of("j-invalid@test.bg")
        for body, text in [({**CHART, "date": "1990-13-45"}, "Невалидни входни данни"),
                           ({**CHART, "is_dynamic": True}, "end_date"),
                           ({**CHART, **FORECAST, "end_date": "2027-12-31"}, "най-много 3 месеца")]:
            with self.subTest(text=text), mock.patch.dict(os.environ, ENFORCED):
                r = self.post(h, body)
                self.assertEqual(r.status_code, 400, r.text)
                self.assertIn(text, r.json()["detail"])
        self.assertEqual(self.client.get("/jobs", headers=h).json()["jobs"], [])
        self.assertEqual(ledger(uid), [("signup_gift", 500, 500)])

    def test_crisis_creates_no_job_and_takes_nothing(self):
        h = register_and_login(self.client, "j-crisis@test.bg")
        uid = uid_of("j-crisis@test.bg")
        with mock.patch.dict(os.environ, ENFORCED), ok_ai():
            r = self.post(h, {**CHART, "question": "Не искам да живея вече"})
        self.assertEqual(r.status_code, 200)
        self.assertTrue(r.json()["crisis"])
        self.assertIn("112", r.json()["html"])
        self.assertEqual(self.client.get("/jobs", headers=h).json()["jobs"], [])
        self.assertEqual(balances(uid), (0, 500))

    def test_jobs_need_a_login(self):
        self.assertEqual(self.client.post("/jobs", json=CHART).status_code, 401)
        self.assertEqual(self.client.get("/jobs/1").status_code, 401)
        self.assertEqual(self.client.get("/jobs/limits").status_code, 401)

    # --- сума -------------------------------------------------------------------------------------------------------
    def test_not_enforced_reserves_nothing_and_costs_zero(self):
        h = register_and_login(self.client, "j-free@test.bg")
        uid = uid_of("j-free@test.bg")
        with ok_ai():
            job = self.post(h, {**CHART, **PAIR}).json()["job"]
        self.assertEqual((job["status"], job["tier"], job["charged_cents"]), ("succeeded", "premium", 0))
        self.assertEqual(ledger(uid), [("signup_gift", 500, 500)])
        self.assertEqual(self.client.get(f"/reports/{job['report_id']}", headers=h).json()["cost_cents"], 0)

    @mock.patch.dict(os.environ, ENFORCED)
    def test_the_amount_is_taken_when_the_job_is_created_and_failure_returns_it(self):
        h = register_and_login(self.client, "j-fail@test.bg")
        uid = uid_of("j-fail@test.bg")
        broken = mock.AsyncMock(side_effect=RuntimeError("AI не отговаря"))
        with mock.patch.object(main.ai_interpreter, "interpret_chart", broken):
            job = self.post(h).json()["job"]
        self.assertEqual((job["status"], job["error"]["code"]), ("failed", "internal"))
        self.assertIn("Не успяхме", job["error"]["message"])
        self.assertEqual(job["charged_cents"], 0)
        self.assertEqual(balances(uid), (0, 500))
        self.assertEqual(ledger(uid), [("signup_gift", 500, 500), ("analysis", -160, -160), ("job_refund", 160, 160)])
        self.assertEqual(report_count(uid), 0)
        assert_ledger_matches(self, uid)
        self.assertEqual(job["events"][-1]["type"], "error")

    @mock.patch.dict(os.environ, ENFORCED)
    def test_refund_goes_back_to_the_same_buckets(self):
        h = register_and_login(self.client, "j-split@test.bg")
        uid = uid_of("j-split@test.bg")
        set_balances(uid, paid=500, gift=100)                       # основен анализ 1,60 €: 1,00 € подарък + 0,60 € внесени
        broken = mock.AsyncMock(side_effect=RuntimeError("AI не отговаря"))
        with mock.patch.object(main.ai_interpreter, "interpret_chart", broken):
            self.post(h)
        self.assertEqual(balances(uid), (500, 100))
        self.assertEqual([r for r in ledger(uid) if r[0] in ("analysis", "job_refund")],
                         [("analysis", -160, -100), ("job_refund", 160, 100)])

    @mock.patch.dict(os.environ, ENFORCED)
    def test_not_enough_money_is_a_402_without_a_job(self):
        h = register_and_login(self.client, "j-poor@test.bg")
        uid = uid_of("j-poor@test.bg")
        set_balances(uid, paid=0, gift=100)
        never = mock.AsyncMock(return_value="<p>не</p>")
        with mock.patch.object(main.ai_interpreter, "interpret_chart", never):
            r = self.post(h)
        self.assertEqual(r.status_code, 402)
        self.assertIn("1,60 €", r.json()["detail"])
        never.assert_not_called()
        self.assertEqual(self.client.get("/jobs", headers=h).json()["jobs"], [])
        self.assertEqual(balances(uid), (0, 100))

    @mock.patch.dict(os.environ, ENFORCED)
    def test_forecast_months_without_events_are_refunded(self):
        h = register_and_login(self.client, "j-months@test.bg")
        uid = uid_of("j-months@test.bg")
        give_deposit("j-months@test.bg", 300)
        a, b = month_ai()
        two_months = mock.patch.object(scanner.PeriodCalendar, "months_with_events", lambda self: self.months[:2])
        with a, b, two_months:
            job = self.post(h, {**CHART, "is_dynamic": True, "target_date": "2026-01-01", "end_date": "2026-03-31"}).json()["job"]
        self.assertEqual((job["status"], job["quote_cents"], job["charged_cents"]), ("succeeded", 225, 150))
        self.assertEqual(balances(uid), (150, 500))
        self.assertEqual([r for r in ledger(uid) if r[0] in ("analysis", "job_refund")], [("analysis", -225, 0), ("job_refund", 75, 0)])
        self.assertEqual(self.client.get(f"/reports/{job['report_id']}", headers=h).json()["cost_cents"], 150)
        assert_ledger_matches(self, uid)

    # --- идемпотентност и лимити -------------------------------------------------------------------------------------
    def test_same_key_returns_the_same_job_without_a_second_report(self):
        h = register_and_login(self.client, "j-key@test.bg")
        uid = uid_of("j-key@test.bg")
        with mock.patch.dict(os.environ, ENFORCED), ok_ai():
            first = self.client.post("/jobs?wait=60", json=CHART, headers={**h, "Idempotency-Key": "k-1"})
            again = self.client.post("/jobs?wait=60", json=CHART, headers={**h, "Idempotency-Key": "k-1"})
        self.assertEqual(first.status_code, 200)
        self.assertEqual((first.json()["created"], again.json()["created"]), (True, False))
        self.assertEqual(first.json()["job"]["id"], again.json()["job"]["id"])
        self.assertEqual(report_count(uid), 1)
        self.assertEqual(balances(uid), (0, 340))                      # само един анализ е платен

    def test_same_key_with_another_request_is_a_conflict(self):
        h = register_and_login(self.client, "j-key2@test.bg")
        with ok_ai():
            self.client.post("/jobs?wait=60", json=CHART, headers={**h, "Idempotency-Key": "k-2"})
            r = self.client.post("/jobs?wait=60", json={**CHART, "name": "Друг"}, headers={**h, "Idempotency-Key": "k-2"})
        self.assertEqual(r.status_code, 409)

    def test_a_replay_is_not_counted_against_the_ai_limit(self):
        h = register_and_login(self.client, "j-replay@test.bg")        # лимитът в тестовете е 2 анализа на час
        with ok_ai():
            for _ in range(5):
                r = self.client.post("/jobs?wait=60", json=CHART, headers={**h, "Idempotency-Key": "same"})
                self.assertEqual(r.status_code, 200, r.text)
            self.assertEqual(self.client.post("/jobs?wait=60", json=CHART, headers=h).status_code, 200)
            self.assertEqual(self.client.post("/jobs?wait=60", json=CHART, headers=h).status_code, 429)

    def test_limits_show_what_is_left_before_the_start(self):
        h = register_and_login(self.client, "j-limits@test.bg")
        start = self.client.get("/jobs/limits", headers=h).json()
        self.assertEqual((start["hour_limit"], start["hour_remaining"], start["can_start"], start["retry_after_seconds"]), (2, 2, True, 0))
        with ok_ai():
            self.post(h)
        after = self.client.get("/jobs/limits", headers=h).json()
        self.assertEqual((after["hour_remaining"], after["can_start"]), (1, True))

    # --- собственост и почистване -------------------------------------------------------------------------------------
    def test_other_accounts_cannot_read_or_cancel_a_job(self):
        mine = register_and_login(self.client, "j-owner@test.bg")
        other = register_and_login(self.client, "j-other@test.bg")
        with ok_ai():
            job_id = self.post(mine).json()["job"]["id"]
        self.assertEqual(self.client.get(f"/jobs/{job_id}", headers=other).status_code, 404)
        self.assertEqual(self.client.post(f"/jobs/{job_id}/cancel", headers=other).status_code, 404)
        self.assertEqual(self.client.get("/jobs", headers=other).json()["jobs"], [])
        self.assertEqual(len(self.client.get("/jobs", headers=mine).json()["jobs"]), 1)

    def test_finished_jobs_cannot_be_cancelled(self):
        h = register_and_login(self.client, "j-late@test.bg")
        uid = uid_of("j-late@test.bg")
        with mock.patch.dict(os.environ, ENFORCED), ok_ai():
            job_id = self.post(h).json()["job"]["id"]
        r = self.client.post(f"/jobs/{job_id}/cancel", headers=h)
        self.assertEqual(r.status_code, 409)
        self.assertEqual(job_row(job_id)["status"], "succeeded")
        self.assertEqual(balances(uid), (0, 340))                      # сумата остава платена, отчетът е доставен
        self.assertEqual(report_count(uid), 1)

    def test_old_finished_jobs_are_purged_and_active_ones_are_kept(self):
        register_and_login(self.client, "j-purge@test.bg")
        uid = uid_of("j-purge@test.bg")
        old = datetime.utcnow() - timedelta(days=jobs.JOB_RETENTION_DAYS + 1)
        db = SessionLocal()
        try:
            for status in ("succeeded", "failed", "cancelled", "running", "queued"):
                db.add(Job(user_id=uid, kind="analysis", sku="analysis.basic", status=status, request={}, events=[],
                           created_at=old))
            db.add(Job(user_id=uid, kind="analysis", sku="analysis.basic", status="succeeded", request={}, events=[],
                       created_at=datetime.utcnow()))
            db.commit()
        finally:
            db.close()
        self.assertEqual(jobs.purge_old(), 3)
        db = SessionLocal()
        try:
            self.assertEqual(sorted(j.status for j in db.query(Job).filter(Job.user_id == uid)),
                             ["queued", "running", "succeeded"])
        finally:
            db.close()

    def test_deleting_the_account_deletes_jobs_and_export_skips_them(self):
        h = register_and_login(self.client, "j-delete@test.bg")
        uid = uid_of("j-delete@test.bg")
        with ok_ai():
            self.post(h)
        export = self.client.get("/me/export", headers=h).json()
        self.assertNotIn("jobs", export)
        self.assertEqual(len(export["reports"]), 1)
        self.assertEqual(self.client.delete("/me", headers=h).status_code, 200)
        db = SessionLocal()
        try:
            self.assertEqual(db.query(Job).filter(Job.user_id == uid).count(), 0)
        finally:
            db.close()

    def test_telemetry_has_no_text(self):
        h = register_and_login(self.client, "j-events@test.bg")
        uid = uid_of("j-events@test.bg")
        with ok_ai():
            self.post(h, {**CHART, "question": "Много личен въпрос"})
        db = SessionLocal()
        try:
            rows = [(e.name, e.props) for e in db.query(Event).filter(Event.user_id == uid).order_by(Event.id)]
        finally:
            db.close()
        completed = [p for n, p in rows if n == "analysis_completed"]
        self.assertEqual(len(completed), 1)
        self.assertEqual(completed[0]["sku"], "analysis.basic")
        self.assertNotIn("личен", str(rows))


class FakePayerTest(unittest.TestCase):
    """Целият път на парите с фалшив платец: от заключен премиум до връщане на сума, със задачите по средата."""

    @classmethod
    def setUpClass(cls):
        cls.client = TestClient(main.app)

    def setUp(self):
        limiter.reset()

    def webhook(self, payload_and_headers):
        payload, headers = payload_and_headers
        with mock.patch("billing_api._stripe_get", mock.AsyncMock(return_value=None)):
            return self.client.post("/billing/webhook", content=payload, headers=headers)

    @mock.patch.dict(os.environ, {**STRIPE_ENV, **ENFORCED})
    def test_from_a_locked_premium_to_a_refund(self):
        h = register_and_login(self.client, "fp-flow@test.bg")
        uid = uid_of("fp-flow@test.bg")
        pair_forecast = {**CHART, **PAIR, "is_dynamic": True, "target_date": "2026-10-01", "end_date": "2026-11-30"}   # 2 месеца за двама

        # 1. Само подарък: премиумът е заключен, а основният анализ работи
        a, b = month_ai()
        with a, b:
            locked = self.client.post("/jobs?wait=60", json=pair_forecast, headers=h)
        self.assertEqual(locked.status_code, 402)
        self.assertIn("внесени средства", locked.json()["detail"])
        self.assertEqual(balances(uid), (0, 500))

        # 2. Зареждане на 10 € през фалшивия платец; връщането към сайта още не кредитира нищо
        payer = FakePayer()
        with mock.patch("billing_api._stripe_post", payer.create_session):
            checkout = self.client.post("/billing/checkout", json={"package_id": "topup10", "accept_immediate_delivery": True}, headers=h)
        self.assertEqual(checkout.status_code, 200, checkout.text)
        session_id = "cs_fake_1"
        self.assertEqual(payer.sessions[session_id]["amount"], 1000)
        self.assertEqual(balances(uid), (0, 500))

        # 3. Подписаното събитие кредитира внесените средства веднъж (дублираното събитие не добавя нищо)
        for _ in range(2):
            self.assertEqual(self.webhook(payer.paid_event(session_id)).status_code, 200)
        self.assertEqual(balances(uid), (1050, 500))                              # 10 € + 0,50 € бонус
        assert_ledger_matches(self, uid)

        # 4. Премиум прогнозата вече минава: 2 × 0,75 € + 0,60 € за двама = 2,10 € от внесените средства
        a, b = month_ai()
        with a, b:
            done = self.client.post("/jobs?wait=60", json=pair_forecast, headers=h).json()["job"]
        self.assertEqual((done["status"], done["charged_cents"], done["tier"]), ("succeeded", 210, "premium"))
        self.assertEqual(balances(uid), (840, 500))                               # подаръкът не е пипнат от премиум
        assert_ledger_matches(self, uid)

        # 5. Неуспешна премиум задача връща сумата
        broken = mock.AsyncMock(side_effect=RuntimeError("AI не отговаря"))
        with mock.patch.object(main.ai_interpreter, "_process_monthly_chunk", broken), \
                mock.patch.object(main.ai_interpreter, "compose_period_overview", mock.AsyncMock(return_value="<p>о</p>")), \
                mock.patch("period_report.RETRY_PAUSE_SECONDS", 0.0):
            failed = self.client.post("/jobs?wait=60", json={**pair_forecast, "name": "Втори"}, headers=h).json()["job"]
        self.assertEqual((failed["status"], failed["error"]["code"]), ("failed", "forecast_failed"))
        self.assertEqual(balances(uid), (840, 500))
        assert_ledger_matches(self, uid)

        # 6. Връщане на цялата сума: отнема се само каквото е останало от внесените средства; подаръкът остава
        self.assertEqual(self.webhook(payer.refund_event(session_id, 1000)).status_code, 200)
        self.assertEqual(balances(uid), (0, 500))
        assert_ledger_matches(self, uid)
        reasons = [r[0] for r in ledger(uid)]
        self.assertEqual(reasons, ["signup_gift", "purchase", "analysis", "analysis", "job_refund", "refund"])


class AsyncJobsTest(unittest.IsolatedAsyncioTestCase):
    """Случаите, в които задачата трябва да остане работеща между две заявки."""

    @classmethod
    def setUpClass(cls):
        cls.sync = TestClient(main.app)

    async def asyncSetUp(self):
        limiter.reset()
        self.client = httpx.AsyncClient(transport=httpx.ASGITransport(app=main.app), base_url="http://test", timeout=30)

    async def asyncTearDown(self):
        for task in list(jobs._tasks.values()):
            task.cancel()
        await asyncio.sleep(0)
        await self.client.aclose()

    def user(self, email):
        return register_and_login(self.sync, email), uid_of(email)

    async def wait_for(self, headers, job_id, states=("succeeded", "failed", "cancelled"), seconds=20):
        for _ in range(int(seconds / 0.05)):
            job = (await self.client.get(f"/jobs/{job_id}", headers=headers)).json()["job"]
            if job["status"] in states:
                return job
            await asyncio.sleep(0.05)
        self.fail(f"задачата {job_id} не стигна до {states}")

    async def start(self, headers, body=CHART, **extra):
        return await self.client.post("/jobs", json=body, headers={**headers, **extra})

    async def test_a_running_job_shows_its_progress_and_survives_the_client(self):
        h, uid = self.user("a-progress@test.bg")
        gate = Gate("<p>Готово</p>")
        with mock.patch.object(main.ai_interpreter, "interpret_chart", gate):
            r = await self.start(h)
            self.assertEqual(r.status_code, 202, r.text)
            job_id = r.json()["job"]["id"]
            await asyncio.wait_for(gate.started.wait(), 10)
            job = (await self.client.get(f"/jobs/{job_id}?after=0", headers=h)).json()["job"]
            self.assertEqual((job["status"], job["stage"]), ("running", "analyzing"))
            self.assertIn("start", [e["type"] for e in job["events"]])        # картите вече са на екрана
            seen = job["event_count"]
            gate.release.set()                                                   # клиентът не е държал заявка отворена
            done = await self.wait_for(h, job_id)
        self.assertEqual(done["status"], "succeeded")
        rest = (await self.client.get(f"/jobs/{job_id}?after={seen}", headers=h)).json()["job"]["events"]
        self.assertEqual([e["type"] for e in rest if e["type"] != "stage"], ["text", "complete"])
        self.assertEqual(report_count(uid), 1)

    async def test_double_submit_with_one_key_makes_one_job(self):
        h, uid = self.user("a-double@test.bg")
        gate = Gate()
        with mock.patch.dict(os.environ, ENFORCED), mock.patch.object(main.ai_interpreter, "interpret_chart", gate):
            first, second = await asyncio.gather(
                self.client.post("/jobs", json=CHART, headers={**h, "Idempotency-Key": "dbl"}),
                self.client.post("/jobs", json=CHART, headers={**h, "Idempotency-Key": "dbl"}))
            self.assertEqual(first.json()["job"]["id"], second.json()["job"]["id"])
            self.assertEqual(sorted([first.json()["created"], second.json()["created"]]), [False, True])
            await asyncio.wait_for(gate.started.wait(), 10)
            gate.release.set()
            await self.wait_for(h, first.json()["job"]["id"])
        self.assertEqual(gate.calls, 1)
        self.assertEqual(report_count(uid), 1)
        self.assertEqual(balances(uid), (0, 340))
        assert_ledger_matches(self, uid)

    async def test_parallel_jobs_cannot_spend_one_balance_twice(self):
        h, uid = self.user("a-overspend@test.bg")
        set_balances(uid, paid=0, gift=160)                                      # точно един основен анализ
        gate = Gate()
        with mock.patch.dict(os.environ, ENFORCED), mock.patch.object(main.ai_interpreter, "interpret_chart", gate):
            a, b = await asyncio.gather(self.client.post("/jobs", json=CHART, headers={**h, "Idempotency-Key": "p1"}),
                                        self.client.post("/jobs", json={**CHART, "name": "Друг"}, headers={**h, "Idempotency-Key": "p2"}))
            self.assertEqual(sorted([a.status_code, b.status_code]), [202, 402])
            winner = a if a.status_code == 202 else b
            await asyncio.wait_for(gate.started.wait(), 10)
            self.assertEqual(balances(uid), (0, 0))                              # сумата е резервирана още сега
            gate.release.set()
            await self.wait_for(h, winner.json()["job"]["id"])
        self.assertEqual(report_count(uid), 1)
        self.assertEqual(balances(uid), (0, 0))
        assert_ledger_matches(self, uid)

    async def test_cancel_returns_the_amount_and_nothing_is_delivered_afterwards(self):
        h, uid = self.user("a-cancel@test.bg")
        gate = Gate()
        with mock.patch.dict(os.environ, ENFORCED), mock.patch.object(main.ai_interpreter, "interpret_chart", gate):
            job_id = (await self.start(h)).json()["job"]["id"]
            await asyncio.wait_for(gate.started.wait(), 10)
            self.assertEqual(balances(uid), (0, 340))
            r = await self.client.post(f"/jobs/{job_id}/cancel", headers=h)
            self.assertEqual(r.status_code, 200, r.text)
            self.assertEqual(r.json()["job"]["status"], "cancelled")
            self.assertEqual(balances(uid), (0, 500))                            # сумата е върната
            gate.release.set()                                                   # дори AI да завърши, нищо не се доставя
            await asyncio.sleep(0.3)
        job = job_row(job_id)
        self.assertEqual((job["status"], job["report_id"], job["charged_cents"]), ("cancelled", None, 0))
        self.assertEqual(report_count(uid), 0)
        self.assertEqual(ledger(uid), [("signup_gift", 500, 500), ("analysis", -160, -160), ("job_refund", 160, 160)])
        assert_ledger_matches(self, uid)
        again = await self.client.post(f"/jobs/{job_id}/cancel", headers=h)
        self.assertEqual(again.status_code, 409)
        self.assertEqual(balances(uid), (0, 500))                                # повторен отказ не връща втори път

    async def test_the_active_jobs_cap_and_a_free_slot_after_the_end(self):
        h, uid = self.user("a-cap@test.bg")
        gate = Gate()
        with mock.patch.object(jobs, "JOB_MAX_ACTIVE_PER_USER", 2), mock.patch.object(main.ai_interpreter, "interpret_chart", gate):
            with mock.patch.object(jobs, "AI_LIMIT_PER_HOUR", 10):
                first = await self.client.post("/jobs", json=CHART, headers=h)
                second = await self.client.post("/jobs", json={**CHART, "name": "Б"}, headers=h)
                third = await self.client.post("/jobs", json={**CHART, "name": "В"}, headers=h)
                self.assertEqual((first.status_code, second.status_code, third.status_code), (202, 202, 429))
                self.assertIn("анализ в процес", third.json()["detail"])
                gate.release.set()
                await self.wait_for(h, first.json()["job"]["id"])
                await self.wait_for(h, second.json()["job"]["id"])
                fourth = await self.client.post("/jobs", json={**CHART, "name": "Г"}, headers=h)
                self.assertEqual(fourth.status_code, 202)
                await self.wait_for(h, fourth.json()["job"]["id"])

    async def test_the_deadline_fails_the_job_and_returns_the_amount(self):
        h, uid = self.user("a-deadline@test.bg")
        gate = Gate()                                                            # никога не се освобождава
        with mock.patch.dict(os.environ, ENFORCED), mock.patch.object(jobs, "JOB_DEADLINE_SECONDS", 0.3), \
                mock.patch.object(main.ai_interpreter, "interpret_chart", gate):
            job_id = (await self.start(h)).json()["job"]["id"]
            job = await self.wait_for(h, job_id)
        self.assertEqual((job["status"], job["error"]["code"]), ("failed", "timeout"))
        self.assertIn("Сумата е върната", job["error"]["message"])
        self.assertEqual(balances(uid), (0, 500))
        assert_ledger_matches(self, uid)

    async def test_a_crashed_run_is_requeued_once_and_then_failed_with_a_refund(self):
        h, uid = self.user("a-recover@test.bg")
        gate = Gate("<p>Втори опит</p>")
        with mock.patch.dict(os.environ, ENFORCED), mock.patch.object(main.ai_interpreter, "interpret_chart", gate):
            job_id = (await self.start(h)).json()["job"]["id"]
            await asyncio.wait_for(gate.started.wait(), 10)
            jobs._tasks[job_id].cancel()                                         # процесът е прекъснат: статусът остава "running"
            await asyncio.sleep(0.2)
            self.assertEqual(job_row(job_id)["status"], "running")
            self.assertEqual(jobs.recover_stale(), [])                           # лизингът още не е изтекъл
            self._expire_lease(job_id)
            self.assertEqual(jobs.recover_stale(), [job_id])
            row = job_row(job_id)
            self.assertEqual((row["status"], row["attempts"], row["events"]), ("queued", 1, []))
            gate.started.clear()
            gate.release.set()
            jobs.launch(job_id)
            done = await self.wait_for(h, job_id)
        self.assertEqual((done["status"], done["charged_cents"]), ("succeeded", 160))
        self.assertEqual(balances(uid), (0, 340))                                # резервирана е само веднъж
        assert_ledger_matches(self, uid)

    async def test_after_the_last_attempt_the_job_fails_and_the_amount_is_returned(self):
        h, uid = self.user("a-giveup@test.bg")
        gate = Gate()
        with mock.patch.dict(os.environ, ENFORCED), mock.patch.object(main.ai_interpreter, "interpret_chart", gate):
            job_id = (await self.start(h)).json()["job"]["id"]
            await asyncio.wait_for(gate.started.wait(), 10)
            jobs._tasks[job_id].cancel()
            await asyncio.sleep(0.2)
            db = SessionLocal()
            try:
                db.query(Job).filter(Job.id == job_id).update({"attempts": jobs.JOB_MAX_ATTEMPTS})
                db.commit()
            finally:
                db.close()
            self._expire_lease(job_id)
            self.assertEqual(jobs.recover_stale(), [])
        job = (await self.client.get(f"/jobs/{job_id}", headers=h)).json()["job"]
        self.assertEqual((job["status"], job["error"]["code"]), ("failed", "interrupted"))
        self.assertEqual(balances(uid), (0, 500))
        assert_ledger_matches(self, uid)

    def _expire_lease(self, job_id):
        db = SessionLocal()
        try:
            db.query(Job).filter(Job.id == job_id).update({"lease_until": datetime.utcnow() - timedelta(seconds=5)})
            db.commit()
        finally:
            db.close()

    async def test_the_same_job_cannot_be_started_twice(self):
        h, uid = self.user("a-claim@test.bg")
        gate = Gate()
        with mock.patch.object(main.ai_interpreter, "interpret_chart", gate):
            job_id = (await self.start(h)).json()["job"]["id"]
            await asyncio.wait_for(gate.started.wait(), 10)
            self.assertIs(jobs.launch(job_id), jobs._tasks[job_id])             # втори изпълнител не се пуска
            self.assertIsNone(jobs._claim(job_id))                              # а и да се пусне, не може да поеме работеща задача
            await asyncio.sleep(0.2)
            gate.release.set()
            await self.wait_for(h, job_id)
        self.assertEqual(gate.calls, 1)
        self.assertEqual(report_count(uid), 1)


if __name__ == "__main__":
    unittest.main()
