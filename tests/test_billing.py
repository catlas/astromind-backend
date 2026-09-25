"""
Тестове за Фаза 3: регистър на монетите, дебит при успешен анализ,
Stripe webhook (подпис, идемпотентност, възстановяване на сума).

Пускане: python -m unittest discover -s tests
"""
import hashlib
import hmac
import json
import os
import time
import unittest
from unittest import mock

import testenv  # noqa: F401  (трябва да е преди main)
from fastapi.testclient import TestClient  # noqa: E402

import main  # noqa: E402
from billing_api import verify_signature  # noqa: E402
from database import CoinTransaction, Purchase, SessionLocal, User  # noqa: E402
from rate_limit import limiter  # noqa: E402
from testenv import CHART, register_and_login  # noqa: E402

WEBHOOK_SECRET = "whsec_test"
STRIPE_ENV = {"STRIPE_SECRET_KEY": "sk_test_x", "STRIPE_WEBHOOK_SECRET": WEBHOOK_SECRET}


def sign(payload: bytes, secret=WEBHOOK_SECRET, ts=None):
    ts = int(ts or time.time())
    sig = hmac.new(secret.encode(), f"{ts}.".encode() + payload, hashlib.sha256).hexdigest()
    return f"t={ts},v1={sig}"


def ledger_sum(user_id):
    db = SessionLocal()
    try:
        rows = db.query(CoinTransaction).filter(CoinTransaction.user_id == user_id).all()
        return sum(r.delta for r in rows), db.get(User, user_id).coins
    finally:
        db.close()


def user_id(email):
    db = SessionLocal()
    try:
        return db.query(User.id).filter(User.email == email).scalar()
    finally:
        db.close()


class BillingTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.client = TestClient(main.app)

    def setUp(self):
        limiter.reset()

    def test_signup_bonus_is_in_ledger(self):
        register_and_login(self.client, "bonus@test.bg")
        total, balance = ledger_sum(user_id("bonus@test.bg"))
        self.assertEqual(balance, 10)
        self.assertEqual(total, balance)

    def test_analysis_free_when_not_enforced(self):
        h = register_and_login(self.client, "free@test.bg")
        with mock.patch.object(main.ai_interpreter, "interpret_chart", mock.AsyncMock(return_value="<p>x</p>")):
            r = self.client.post("/interpret", json=CHART, headers=h)
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.json()["coins_charged"], 0)
        self.assertEqual(self.client.get("/me", headers=h).json()["coins"], 10)

    @mock.patch.dict(os.environ, {"COINS_ENFORCED": "1"})
    def test_debit_on_success_only(self):
        h = register_and_login(self.client, "debit@test.bg")
        failing = mock.AsyncMock(side_effect=RuntimeError("AI down"))
        with mock.patch.object(main.ai_interpreter, "interpret_chart", failing):
            r = self.client.post("/interpret", json=CHART, headers=h)
        self.assertEqual(r.status_code, 500)
        self.assertEqual(self.client.get("/me", headers=h).json()["coins"], 10)  # без дебит при грешка

        with mock.patch.object(main.ai_interpreter, "interpret_chart", mock.AsyncMock(return_value="<p>ok</p>")):
            r = self.client.post("/interpret", json=CHART, headers=h)
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.json()["coins_charged"], 8)
        self.assertEqual(r.json()["balance"], 2)
        report = self.client.get(f"/reports/{r.json()['report_id']}", headers=h).json()
        self.assertEqual(report["coins"], 8)

        # Недостатъчен баланс: 402 и AI моделът не се вика
        limiter.reset()  # тестовият лимит е 2 AI заявки на час
        never = mock.AsyncMock(return_value="<p>no</p>")
        with mock.patch.object(main.ai_interpreter, "interpret_chart", never):
            r = self.client.post("/interpret", json=CHART, headers=h)
        self.assertEqual(r.status_code, 402)
        never.assert_not_called()

        total, balance = ledger_sum(user_id("debit@test.bg"))
        self.assertEqual((total, balance), (2, 2))

    @mock.patch.dict(os.environ, {"COINS_ENFORCED": "1"})
    def test_forecast_debits_per_month(self):
        h = register_and_login(self.client, "forecast@test.bg")
        body = {**CHART, "is_dynamic": True, "target_date": "2026-01-01", "end_date": "2026-01-31"}
        with mock.patch.object(main.ai_interpreter, "_process_monthly_chunk", mock.AsyncMock(return_value="<p>m</p>")):
            r = self.client.post("/interpret-stream", json=body, headers=h)
        self.assertIn('"coins_charged": 5', r.text)
        self.assertEqual(self.client.get("/me", headers=h).json()["coins"], 5)

    def test_checkout_disabled_without_stripe(self):
        h = register_and_login(self.client, "nostripe@test.bg")
        r = self.client.post("/billing/checkout", json={"package_id": "starter", "accept_immediate_delivery": True}, headers=h)
        self.assertEqual(r.status_code, 503)
        cfg = self.client.get("/billing/config").json()
        self.assertFalse(cfg["payments_enabled"])
        self.assertEqual([p["amount_cents"] for p in cfg["packages"]], [499, 1299, 3599])

    @mock.patch.dict(os.environ, STRIPE_ENV)
    def test_checkout_requires_consent_and_creates_session(self):
        h = register_and_login(self.client, "checkout@test.bg")
        r = self.client.post("/billing/checkout", json={"package_id": "starter"}, headers=h)
        self.assertEqual(r.status_code, 400)
        fake = mock.AsyncMock(return_value={"id": "cs_test_1", "url": "https://checkout.stripe.com/c/cs_test_1"})
        with mock.patch("billing_api._stripe_post", fake):
            r = self.client.post("/billing/checkout", json={"package_id": "starter", "accept_immediate_delivery": True}, headers=h)
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual(r.json()["url"], "https://checkout.stripe.com/c/cs_test_1")
        form = fake.call_args.args[1]
        self.assertEqual(form["line_items[0][price_data][currency]"], "eur")
        self.assertEqual(form["line_items[0][price_data][unit_amount]"], "499")

    @mock.patch.dict(os.environ, STRIPE_ENV)
    def test_webhook_credits_once_and_handles_refund(self):
        h = register_and_login(self.client, "webhook@test.bg")
        uid = user_id("webhook@test.bg")
        db = SessionLocal()
        purchase = Purchase(user_id=uid, package_id="popular", coins=150, amount_cents=1299,
                            currency="eur", status="pending", stripe_session_id="cs_test_2")
        db.add(purchase)
        db.commit()
        pid = purchase.id
        db.close()

        event = {"type": "checkout.session.completed", "data": {"object": {
            "id": "cs_test_2", "payment_status": "paid", "amount_total": 1299, "currency": "eur",
            "payment_intent": "pi_test_2", "metadata": {"purchase_id": str(pid)}}}}
        payload = json.dumps(event).encode()

        bad = self.client.post("/billing/webhook", content=payload, headers={"stripe-signature": sign(payload, "wrong")})
        self.assertEqual(bad.status_code, 400)
        old = self.client.post("/billing/webhook", content=payload,
                               headers={"stripe-signature": sign(payload, ts=time.time() - 3600)})
        self.assertEqual(old.status_code, 400)

        with mock.patch("billing_api._stripe_get", mock.AsyncMock(return_value=None)):
            for _ in range(2):  # Stripe може да изпрати събитието повече от веднъж
                r = self.client.post("/billing/webhook", content=payload, headers={"stripe-signature": sign(payload)})
                self.assertEqual(r.status_code, 200)
        self.assertEqual(self.client.get("/me", headers=h).json()["coins"], 160)

        refund = {"type": "charge.refunded", "data": {"object": {"payment_intent": "pi_test_2", "amount_refunded": 1299}}}
        rp = json.dumps(refund).encode()
        self.client.post("/billing/webhook", content=rp, headers={"stripe-signature": sign(rp)})
        self.client.post("/billing/webhook", content=rp, headers={"stripe-signature": sign(rp)})
        self.assertEqual(self.client.get("/me", headers=h).json()["coins"], 10)

        tx = self.client.get("/billing/transactions", headers=h).json()
        self.assertEqual([t["reason"] for t in tx["transactions"]], ["refund", "purchase", "signup_bonus"])
        self.assertEqual(tx["purchases"][0]["status"], "refunded")
        total, balance = ledger_sum(uid)
        self.assertEqual(total, balance)

    @mock.patch.dict(os.environ, STRIPE_ENV)
    def test_webhook_rejects_amount_mismatch(self):
        register_and_login(self.client, "mismatch@test.bg")
        uid = user_id("mismatch@test.bg")
        db = SessionLocal()
        purchase = Purchase(user_id=uid, package_id="starter", coins=50, amount_cents=499, currency="eur",
                            status="pending", stripe_session_id="cs_test_3")
        db.add(purchase)
        db.commit()
        pid = purchase.id
        db.close()
        event = {"type": "checkout.session.completed", "data": {"object": {
            "id": "cs_test_3", "payment_status": "paid", "amount_total": 1, "currency": "eur",
            "metadata": {"purchase_id": str(pid)}}}}
        payload = json.dumps(event).encode()
        self.client.post("/billing/webhook", content=payload, headers={"stripe-signature": sign(payload)})
        self.assertEqual(ledger_sum(uid)[1], 10)

    def test_verify_signature_multiple_v1(self):
        payload = b'{"a":1}'
        header = sign(payload)
        self.assertTrue(verify_signature(payload, "v1=deadbeef," + header, WEBHOOK_SECRET))
        self.assertFalse(verify_signature(payload, "garbage", WEBHOOK_SECRET))


if __name__ == "__main__":
    unittest.main()
