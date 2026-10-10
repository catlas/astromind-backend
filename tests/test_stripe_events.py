"""
Тестове за Фаза 16: безопасно включване на Stripe и устойчивост на webhook при странни последователности.

- ключ за живо работи само с изрично STRIPE_ALLOW_LIVE=1; тестов ключ показва режим „test“;
- събитие от другия режим се отхвърля;
- повторени, закъснели, неподредени (връщане преди плащане), оспорени и неуспешни плащания никога не дават двоен или
  липсващ кредит, а регистърът винаги е равен на баланса.
Всичко е с подписани събития и подменен Stripe (без мрежа и без истински ключове).

Пускане: python -m unittest discover -s tests -p "test_stripe_events.py"
"""
import asyncio
import json
import os
import unittest
from unittest import mock

import testenv  # noqa: F401  (трябва да е преди main)
import httpx  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

import billing  # noqa: E402
import main  # noqa: E402
from database import CoinTransaction, Purchase, SessionLocal, User  # noqa: E402
from rate_limit import limiter  # noqa: E402
from test_billing import STRIPE_ENV, balances, sign, user_id  # noqa: E402
from testenv import register_and_login  # noqa: E402


def event(kind, obj, livemode=None):
    body = {"type": kind, "data": {"object": obj}}
    if livemode is not None:
        body["livemode"] = livemode
    return json.dumps(body).encode()


def session(sid, pid, amount=1000, status="paid", intent=None):
    return {"id": sid, "payment_status": status, "amount_total": amount, "currency": "eur",
            "payment_intent": intent or f"pi_{sid}", "metadata": {"purchase_id": str(pid)}}


class ModeTest(unittest.TestCase):
    def test_modes_from_the_key(self):
        for key, mode in (("sk_test_x", "test"), ("rk_test_x", "test"), ("sk_live_x", "live"), ("rk_live_x", "live"),
                          ("nonsense", None), ("", None)):
            with self.subTest(key=key), mock.patch.dict(os.environ, {"STRIPE_SECRET_KEY": key}):
                self.assertEqual(billing.stripe_mode(), mode)

    def test_a_live_key_is_inert_without_the_explicit_approval(self):
        env = {"STRIPE_SECRET_KEY": "sk_live_x", "STRIPE_WEBHOOK_SECRET": "whsec_x"}
        with mock.patch.dict(os.environ, env):
            os.environ.pop("STRIPE_ALLOW_LIVE", None)
            self.assertFalse(billing.payments_enabled())
        with mock.patch.dict(os.environ, {**env, "STRIPE_ALLOW_LIVE": "0"}):
            self.assertFalse(billing.payments_enabled())
        with mock.patch.dict(os.environ, {**env, "STRIPE_ALLOW_LIVE": "1"}):
            self.assertTrue(billing.payments_enabled())

    def test_test_mode_is_enabled_and_reported(self):
        client = TestClient(main.app)
        with mock.patch.dict(os.environ, STRIPE_ENV):
            config = client.get("/billing/config").json()
        self.assertEqual((config["payments_enabled"], config["payments_mode"]), (True, "test"))
        self.assertIsNone(client.get("/billing/config").json()["payments_mode"])        # без ключове: изключено

    def test_checkout_is_refused_for_an_unapproved_live_key(self):
        client = TestClient(main.app)
        limiter.reset()
        h = register_and_login(client, "live-guard@test.bg")
        env = {"STRIPE_SECRET_KEY": "sk_live_x", "STRIPE_WEBHOOK_SECRET": "whsec_x"}
        with mock.patch.dict(os.environ, env):
            os.environ.pop("STRIPE_ALLOW_LIVE", None)
            r = client.post("/billing/checkout", json={"package_id": "topup10", "accept_immediate_delivery": True}, headers=h)
        self.assertEqual(r.status_code, 503)


class WebhookSequencesTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.client = TestClient(main.app)

    def setUp(self):
        limiter.reset()
        self.env = mock.patch.dict(os.environ, STRIPE_ENV)
        self.env.start()
        self.get = mock.patch("billing_api._stripe_get", mock.AsyncMock(return_value=None))
        self.get.start()

    def tearDown(self):
        self.get.stop()
        self.env.stop()

    def account(self, email, sid, amount=1000, credit=1050, intent=False):
        register_and_login(self.client, email)
        uid = user_id(email)
        db = SessionLocal()
        try:
            purchase = Purchase(user_id=uid, package_id="topup10", credit_cents=credit, amount_cents=amount, currency="eur",
                                status="pending", stripe_session_id=sid, stripe_payment_intent=f"pi_{sid}" if intent else None)
            db.add(purchase)
            db.commit()
            return uid, purchase.id
        finally:
            db.close()

    def send(self, payload, secret=None):
        kwargs = {"secret": secret} if secret else {}
        return self.client.post("/billing/webhook", content=payload, headers={"stripe-signature": sign(payload, **kwargs)})

    def status(self, pid):
        db = SessionLocal()
        try:
            return db.get(Purchase, pid).status
        finally:
            db.close()

    def assert_ledger(self, uid):
        db = SessionLocal()
        try:
            user = db.get(User, uid)
            rows = db.query(CoinTransaction).filter(CoinTransaction.user_id == uid).all()
            self.assertEqual(sum(t.delta for t in rows), user.paid_cents + user.gift_cents)
            self.assertEqual(sum(t.delta_gift or 0 for t in rows), user.gift_cents)
        finally:
            db.close()

    def test_an_event_from_the_other_mode_is_refused(self):
        uid, pid = self.account("sx-mode@test.bg", "cs_mode")
        wrong = event("checkout.session.completed", session("cs_mode", pid), livemode=True)          # ключът е тестов
        self.assertEqual(self.send(wrong).status_code, 400)
        self.assertEqual(balances(uid), (0, 500))
        right = event("checkout.session.completed", session("cs_mode", pid), livemode=False)
        self.assertEqual(self.send(right).status_code, 200)
        self.assertEqual(balances(uid), (1050, 500))

    def test_a_delayed_payment_is_credited_once_when_it_finally_succeeds(self):
        uid, pid = self.account("sx-delay@test.bg", "cs_delay")
        self.send(event("checkout.session.completed", session("cs_delay", pid, status="unpaid")))
        self.assertEqual((balances(uid), self.status(pid)), ((0, 500), "pending"))
        for _ in range(2):
            self.send(event("checkout.session.async_payment_succeeded", session("cs_delay", pid)))
        self.assertEqual((balances(uid), self.status(pid)), ((1050, 500), "paid"))
        self.assert_ledger(uid)

    def test_a_failed_delayed_payment_credits_nothing(self):
        uid, pid = self.account("sx-fail@test.bg", "cs_fail")
        self.send(event("checkout.session.completed", session("cs_fail", pid, status="unpaid")))
        self.send(event("checkout.session.async_payment_failed", session("cs_fail", pid, status="unpaid")))
        self.assertEqual((balances(uid), self.status(pid)), ((0, 500), "failed"))

    def test_a_cancelled_checkout_expires_and_a_paid_one_is_not_expired(self):
        uid, pid = self.account("sx-expire@test.bg", "cs_exp")
        self.send(event("checkout.session.expired", {"id": "cs_exp"}))
        self.assertEqual(self.status(pid), "expired")
        uid2, pid2 = self.account("sx-expire2@test.bg", "cs_exp2")
        self.send(event("checkout.session.completed", session("cs_exp2", pid2)))
        self.send(event("checkout.session.expired", {"id": "cs_exp2"}))
        self.assertEqual(self.status(pid2), "paid")

    def test_a_refund_that_arrives_before_the_payment_is_netted_when_it_comes(self):
        uid, pid = self.account("sx-order@test.bg", "cs_order")
        charge = {"payment_intent": "pi_cs_order", "amount_refunded": 1000, "metadata": {"purchase_id": str(pid)}}
        self.assertEqual(self.send(event("charge.refunded", charge)).status_code, 200)
        self.assertEqual(balances(uid), (0, 500))                                   # още няма нищо за връщане
        self.send(event("checkout.session.completed", session("cs_order", pid)))
        self.assertEqual((balances(uid), self.status(pid)), ((0, 500), "refunded"))   # кредитът е нетиран веднага
        self.assert_ledger(uid)

    def test_a_partial_refund_before_the_payment_is_netted_in_proportion(self):
        uid, pid = self.account("sx-order2@test.bg", "cs_order2")
        charge = {"payment_intent": "pi_cs_order2", "amount_refunded": 500, "metadata": {"purchase_id": str(pid)}}
        self.send(event("charge.refunded", charge))
        self.send(event("checkout.session.completed", session("cs_order2", pid)))
        self.assertEqual((balances(uid), self.status(pid)), ((525, 500), "paid"))
        self.assert_ledger(uid)

    def test_a_dispute_takes_the_credit_back_once(self):
        uid, pid = self.account("sx-dispute@test.bg", "cs_disp")
        self.send(event("checkout.session.completed", session("cs_disp", pid)))
        self.assertEqual(balances(uid), (1050, 500))
        dispute = {"payment_intent": "pi_cs_disp", "amount": 1000, "metadata": {"purchase_id": str(pid)}}
        for _ in range(3):
            self.send(event("charge.dispute.created", dispute))
        self.assertEqual((balances(uid), self.status(pid)), ((0, 500), "disputed"))
        # по-късно и връщане на същата сума: нищо повече не се отнема
        self.send(event("charge.refunded", {"payment_intent": "pi_cs_disp", "amount_refunded": 1000}))
        self.assertEqual(balances(uid), (0, 500))
        self.assert_ledger(uid)

    def test_duplicates_delivered_at_the_same_time_credit_once(self):
        uid, pid = self.account("sx-concurrent@test.bg", "cs_conc")
        payload = event("checkout.session.completed", session("cs_conc", pid))
        header = sign(payload)

        async def run():
            transport = httpx.ASGITransport(app=main.app)
            async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
                return await asyncio.gather(*[client.post("/billing/webhook", content=payload, headers={"stripe-signature": header})
                                              for _ in range(8)], return_exceptions=True)
        results = asyncio.run(run())
        self.assertTrue(all(not isinstance(r, Exception) and r.status_code == 200 for r in results), results)
        self.assertEqual((balances(uid), self.status(pid)), ((1050, 500), "paid"))
        self.assert_ledger(uid)

    def test_unknown_purchases_and_mismatches_change_nothing(self):
        uid, pid = self.account("sx-odd@test.bg", "cs_odd")
        self.assertEqual(self.send(event("checkout.session.completed", session("cs_unknown", 999999))).status_code, 200)
        self.assertEqual(self.send(event("charge.refunded", {"payment_intent": "pi_nothing", "amount_refunded": 5})).status_code, 200)
        self.assertEqual(self.send(event("charge.dispute.created", {"payment_intent": "pi_nothing", "amount": 5})).status_code, 200)
        self.send(event("checkout.session.completed", session("cs_odd", pid, amount=1)))              # друга сума
        self.send(event("checkout.session.completed", {**session("cs_odd", pid), "currency": "usd"}))  # друга валута
        self.assertEqual((balances(uid), self.status(pid)), ((0, 500), "pending"))

    def test_a_forged_event_is_refused(self):
        uid, pid = self.account("sx-forged@test.bg", "cs_forged")
        payload = event("checkout.session.completed", session("cs_forged", pid))
        self.assertEqual(self.send(payload, secret="whsec_attacker").status_code, 400)
        self.assertEqual(self.client.post("/billing/webhook", content=payload).status_code, 400)
        self.assertEqual(balances(uid), (0, 500))


if __name__ == "__main__":
    unittest.main()
