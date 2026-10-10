"""
Баланс в евро: подарък при регистрация, внесени средства, премиум услуги, дебит при успешен анализ,
Stripe webhook (подпис, идемпотентност, възстановяване на сума).

Правилата, които се проверяват:
- при регистрация: 5,00 € подарък, само за основните анализи (анализ за един човек);
- премиум (анализ за двама, прогноза за период) се плащат САМО с внесени средства, затова са заключени до първото зареждане;
- основните първо ползват подаръка, после внесените средства;
- сумата на регистъра е равна на баланса, поотделно за подаръка и за внесените средства.

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

import billing  # noqa: E402
import main  # noqa: E402
from billing_api import verify_signature  # noqa: E402
from database import CoinTransaction, Purchase, SessionLocal, User  # noqa: E402
from rate_limit import limiter  # noqa: E402
from testenv import CHART, register_and_login  # noqa: E402

WEBHOOK_SECRET = "whsec_test"
STRIPE_ENV = {"STRIPE_SECRET_KEY": "sk_test_x", "STRIPE_WEBHOOK_SECRET": WEBHOOK_SECRET}
ENFORCED = {"BALANCE_ENFORCED": "1"}
PAIR = {"partner_date": "1985-07-12", "partner_time": "18:45", "partner_lat": 48.2082, "partner_lon": 16.3738,
        "partner_name": "Иван"}
FORECAST = {"is_dynamic": True, "target_date": "2026-01-01", "end_date": "2026-01-31"}


def sign(payload: bytes, secret=WEBHOOK_SECRET, ts=None):
    ts = int(ts or time.time())
    sig = hmac.new(secret.encode(), f"{ts}.".encode() + payload, hashlib.sha256).hexdigest()
    return f"t={ts},v1={sig}"


def user_id(email):
    db = SessionLocal()
    try:
        return db.query(User.id).filter(User.email == email).scalar()
    finally:
        db.close()


def balances(uid):
    """(внесени, подарък) от таблицата users."""
    db = SessionLocal()
    try:
        row = db.query(User.paid_cents, User.gift_cents).filter(User.id == uid).one()
        return int(row[0]), int(row[1])
    finally:
        db.close()


def set_balances(uid, paid, gift):
    """Задава баланса през регистъра, за да останат сумите равни."""
    db = SessionLocal()
    try:
        now_paid, now_gift = balances(uid)
        billing.apply_transaction(db, uid, "admin", paid=paid - now_paid, gift=gift - now_gift, allow_negative=True,
                                  description="тест")
        db.commit()
    finally:
        db.close()


def assert_ledger_matches(test, uid):
    """Сумата на регистъра е равна на баланса, и общо, и за подаръчния дял."""
    db = SessionLocal()
    try:
        rows = db.query(CoinTransaction).filter(CoinTransaction.user_id == uid).order_by(CoinTransaction.id).all()
        paid, gift = balances(uid)
        test.assertEqual(sum(r.delta for r in rows), paid + gift)
        test.assertEqual(sum(r.delta_gift for r in rows), gift)
        if rows:
            test.assertEqual(rows[-1].balance_after, paid + gift)
            test.assertEqual(rows[-1].gift_after, gift)
    finally:
        db.close()


def me(client, headers):
    return client.get("/me", headers=headers).json()


class QuoteTest(unittest.TestCase):
    """Цените и нивата: чисти функции, без заявки."""

    def test_default_prices_are_the_agreed_ones(self):
        # основните +100% върху 0,80 €; премиум +50% върху 1,20 € (двама), 0,50 € (месец) и 0,40 € (надбавка)
        self.assertEqual(billing.prices(), {"basic_analysis": 160, "pair_analysis": 180, "forecast_month": 75,
                                            "forecast_partner_extra": 60})
        self.assertEqual(billing.signup_gift_cents(), 500)

    def test_tiers(self):
        self.assertEqual(billing.analysis_quote(False), billing.Quote(160, billing.BASIC))
        self.assertEqual(billing.analysis_quote(True), billing.Quote(180, billing.PREMIUM))
        self.assertEqual(billing.forecast_quote(3, False), billing.Quote(225, billing.PREMIUM))
        self.assertEqual(billing.forecast_quote(2, True), billing.Quote(210, billing.PREMIUM))
        self.assertEqual(billing.forecast_quote(0, False).cents, 75)         # най-малко един месец

    def test_split_charge_basic_uses_the_gift_first(self):
        self.assertEqual(billing.split_charge(0, 500, 160, billing.BASIC), (0, 160))
        self.assertEqual(billing.split_charge(1000, 100, 160, billing.BASIC), (60, 100))     # (от внесени, от подарък)
        self.assertEqual(billing.split_charge(200, 0, 160, billing.BASIC), (160, 0))
        self.assertIsNone(billing.split_charge(100, 50, 160, billing.BASIC))

    def test_split_charge_premium_never_touches_the_gift(self):
        self.assertIsNone(billing.split_charge(0, 5000, 75, billing.PREMIUM))
        self.assertIsNone(billing.split_charge(70, 5000, 75, billing.PREMIUM))
        self.assertEqual(billing.split_charge(500, 5000, 180, billing.PREMIUM), (180, 0))

    def test_every_split_is_exact_and_never_negative(self):
        for paid in (0, 1, 59, 160, 1000):
            for gift in (0, 1, 100, 160, 500):
                for cents in (1, 75, 160, 225):
                    for tier in (billing.BASIC, billing.PREMIUM):
                        split = billing.split_charge(paid, gift, cents, tier)
                        if split is None:
                            self.assertGreater(cents, billing.available_cents(paid, gift, tier))
                            continue
                        from_paid, from_gift = split
                        self.assertEqual(from_paid + from_gift, cents)
                        self.assertTrue(0 <= from_paid <= paid and 0 <= from_gift <= gift)
                        if tier == billing.PREMIUM:
                            self.assertEqual(from_gift, 0)

    def test_prices_come_from_the_environment_and_old_coin_settings_are_ignored(self):
        with mock.patch.dict(os.environ, {"PRICE_BASIC_ANALYSIS_CENTS": "199", "COST_ANALYSIS": "8", "SIGNUP_BONUS_COINS": "10",
                                          "SIGNUP_GIFT_CENTS": "300"}):
            self.assertEqual(billing.prices()["basic_analysis"], 199)
            self.assertEqual(billing.signup_gift_cents(), 300)
        with mock.patch.dict(os.environ, {"COST_ANALYSIS": "8", "SIGNUP_BONUS_COINS": "10", "COIN_PACKAGES": "[]"}):
            self.assertEqual(billing.prices()["basic_analysis"], 160)         # старите имена (монети) не се четат
            self.assertEqual(billing.signup_gift_cents(), 500)
            self.assertEqual([t["id"] for t in billing.topups()], ["topup5", "topup10", "topup20"])
        with mock.patch.dict(os.environ, {"PRICE_BASIC_ANALYSIS_CENTS": "нещо", "SIGNUP_GIFT_CENTS": "-5"}):
            self.assertEqual(billing.prices()["basic_analysis"], 160)
            self.assertEqual(billing.signup_gift_cents(), 0)                   # отрицателно значи без подарък

    def test_format_eur(self):
        self.assertEqual(billing.format_eur(160), "1,60 €")
        self.assertEqual(billing.format_eur(5), "0,05 €")
        self.assertEqual(billing.format_eur(0), "0,00 €")
        self.assertEqual(billing.format_eur(1050), "10,50 €")

    def test_topups_from_the_environment_must_be_valid(self):
        good = json.dumps([{"id": "x", "amount_cents": 300, "credit_cents": 330}])
        with mock.patch.dict(os.environ, {"TOPUP_PACKAGES": good}):
            self.assertEqual(billing.topups()[0]["id"], "x")
        for bad in ('[{"id": "x", "amount_cents": 0, "credit_cents": 5}]', '[{"id": "x"}]', "не JSON", "[]"):
            with mock.patch.dict(os.environ, {"TOPUP_PACKAGES": bad}):
                self.assertEqual(billing.topups(), billing.DEFAULT_TOPUPS, bad)

    def test_enforcement_follows_payments_unless_set(self):
        with mock.patch.dict(os.environ, {}, clear=False):
            for name in ("BALANCE_ENFORCED", "COINS_ENFORCED", "STRIPE_SECRET_KEY", "STRIPE_WEBHOOK_SECRET"):
                os.environ.pop(name, None)
            self.assertFalse(billing.balance_enforced())
            os.environ.update(STRIPE_ENV)
            self.assertTrue(billing.balance_enforced())
            os.environ["BALANCE_ENFORCED"] = "0"
            self.assertFalse(billing.balance_enforced())
            os.environ.pop("BALANCE_ENFORCED")
            os.environ["COINS_ENFORCED"] = "0"                                  # старото име още важи
            self.assertFalse(billing.balance_enforced())


class BillingTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.client = TestClient(main.app)

    def setUp(self):
        limiter.reset()

    def ok_ai(self, text="<p>ok</p>"):
        return mock.patch.object(main.ai_interpreter, "interpret_chart", mock.AsyncMock(return_value=text))

    def stream_ai(self):
        return (mock.patch.object(main.ai_interpreter, "_process_monthly_chunk", mock.AsyncMock(return_value="<p>m</p>")),
                mock.patch.object(main.ai_interpreter, "compose_period_overview", mock.AsyncMock(return_value="<p>o</p>")))

    # --- подарък и регистър -------------------------------------------------------------------------------------
    def test_signup_gift_is_in_the_ledger_as_gift(self):
        h = register_and_login(self.client, "gift@test.bg")
        uid = user_id("gift@test.bg")
        payload = me(self.client, h)
        self.assertEqual((payload["balance_cents"], payload["gift_cents"], payload["paid_cents"]), (500, 500, 0))
        self.assertNotIn("coins", payload)
        assert_ledger_matches(self, uid)
        tx = self.client.get("/billing/transactions", headers=h).json()
        self.assertEqual([(t["reason"], t["delta"], t["delta_gift"]) for t in tx["transactions"]], [("signup_gift", 500, 500)])
        self.assertEqual((tx["balance_cents"], tx["gift_cents"], tx["paid_cents"]), (500, 500, 0))

    @mock.patch.dict(os.environ, {"SIGNUP_GIFT_CENTS": "0"})
    def test_no_gift_means_no_ledger_row(self):
        h = register_and_login(self.client, "nogift@test.bg")
        self.assertEqual(me(self.client, h)["balance_cents"], 0)
        self.assertEqual(self.client.get("/billing/transactions", headers=h).json()["transactions"], [])

    # --- докато плащанията са изключени нищо не се взема и нищо не е заключено ---------------------------------------
    def test_everything_is_free_and_unlocked_while_payments_are_off(self):
        h = register_and_login(self.client, "free@test.bg")
        with self.ok_ai():
            basic = self.client.post("/interpret", json=CHART, headers=h)
            limiter.reset()
            pair = self.client.post("/interpret", json={**CHART, **PAIR}, headers=h)
        for r in (basic, pair):
            self.assertEqual(r.status_code, 200, r.text)
            self.assertEqual(r.json()["charged_cents"], 0)
        self.assertEqual(me(self.client, h)["balance_cents"], 500)
        cfg = self.client.get("/billing/config").json()
        self.assertFalse(cfg["balance_enforced"])

    # --- основни анализи -----------------------------------------------------------------------------------------
    @mock.patch.dict(os.environ, ENFORCED)
    def test_basic_analysis_is_paid_from_the_gift_and_failure_costs_nothing(self):
        h = register_and_login(self.client, "basic@test.bg")
        uid = user_id("basic@test.bg")
        failing = mock.AsyncMock(side_effect=RuntimeError("AI down"))
        with mock.patch.object(main.ai_interpreter, "interpret_chart", failing):
            r = self.client.post("/interpret", json=CHART, headers=h)
        self.assertEqual(r.status_code, 500)
        self.assertEqual(me(self.client, h)["balance_cents"], 500)                 # без дебит при грешка

        with self.ok_ai():
            r = self.client.post("/interpret", json=CHART, headers=h)
        self.assertEqual(r.status_code, 200)
        body = r.json()
        self.assertEqual((body["charged_cents"], body["balance_cents"], body["gift_cents"], body["paid_cents"]), (160, 340, 340, 0))
        report = self.client.get(f"/reports/{body['report_id']}", headers=h).json()
        self.assertEqual(report["cost_cents"], 160)
        assert_ledger_matches(self, uid)

    @mock.patch.dict(os.environ, ENFORCED)
    def test_the_gift_covers_three_basic_analyses_then_balance_is_needed(self):
        h = register_and_login(self.client, "three@test.bg")
        uid = user_id("three@test.bg")
        for _ in range(3):
            limiter.reset()
            with self.ok_ai():
                self.assertEqual(self.client.post("/interpret", json=CHART, headers=h).status_code, 200)
        self.assertEqual(balances(uid), (0, 20))
        limiter.reset()
        never = mock.AsyncMock(return_value="<p>no</p>")
        with mock.patch.object(main.ai_interpreter, "interpret_chart", never):
            r = self.client.post("/interpret", json=CHART, headers=h)
        self.assertEqual(r.status_code, 402)
        self.assertIn("Заредете баланса", r.json()["detail"])
        self.assertIn("1,60 €", r.json()["detail"])
        never.assert_not_called()                                                  # AI моделът не се вика
        assert_ledger_matches(self, uid)

    @mock.patch.dict(os.environ, ENFORCED)
    def test_basic_spends_the_gift_first_then_deposited_money(self):
        h = register_and_login(self.client, "order@test.bg")
        uid = user_id("order@test.bg")
        set_balances(uid, paid=1000, gift=100)
        with self.ok_ai():
            r = self.client.post("/interpret", json=CHART, headers=h)
        self.assertEqual(r.status_code, 200)
        self.assertEqual(balances(uid), (940, 0))                                   # подаръкът 100 + внесени 60
        db = SessionLocal()
        try:
            last = db.query(CoinTransaction).filter(CoinTransaction.user_id == uid).order_by(CoinTransaction.id.desc()).first()
            self.assertEqual((last.reason, last.delta, last.delta_gift), ("analysis", -160, -100))
        finally:
            db.close()
        assert_ledger_matches(self, uid)

    # --- премиум -------------------------------------------------------------------------------------------------
    @mock.patch.dict(os.environ, ENFORCED)
    def test_premium_is_locked_with_only_the_gift(self):
        h = register_and_login(self.client, "locked@test.bg")
        uid = user_id("locked@test.bg")
        never = mock.AsyncMock(return_value="<p>no</p>")
        with mock.patch.object(main.ai_interpreter, "interpret_chart", never):
            pair = self.client.post("/interpret", json={**CHART, **PAIR}, headers=h)
            limiter.reset()
            plain_forecast = self.client.post("/interpret", json={**CHART, **FORECAST}, headers=h)
        for r in (pair, plain_forecast):
            self.assertEqual(r.status_code, 402, r.text)
            self.assertIn("Премиум услугите се плащат с внесени средства", r.json()["detail"])
            self.assertIn("Подаръчният кредит важи само за основните анализи", r.json()["detail"])
        never.assert_not_called()
        limiter.reset()
        monthly = mock.AsyncMock(return_value="<p>m</p>")
        with mock.patch.object(main.ai_interpreter, "_process_monthly_chunk", monthly):
            stream = self.client.post("/interpret-stream", json={**CHART, **FORECAST}, headers=h)
        self.assertIn('"code": 402', stream.text)
        self.assertIn("Премиум услугите", stream.text)
        monthly.assert_not_called()
        self.assertEqual(balances(uid), (0, 500))                                   # подаръкът е недокоснат
        assert_ledger_matches(self, uid)

    @mock.patch.dict(os.environ, ENFORCED)
    def test_a_deposit_unlocks_premium_and_premium_is_paid_from_deposited_money_only(self):
        h = register_and_login(self.client, "unlock@test.bg")
        uid = user_id("unlock@test.bg")
        set_balances(uid, paid=500, gift=500)
        with self.ok_ai():
            r = self.client.post("/interpret", json={**CHART, **PAIR}, headers=h)
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual(r.json()["charged_cents"], 180)
        self.assertEqual(balances(uid), (320, 500))                                 # подаръкът не е пипнат
        assert_ledger_matches(self, uid)

    @mock.patch.dict(os.environ, ENFORCED)
    def test_a_big_gift_does_not_make_up_for_missing_deposited_money(self):
        h = register_and_login(self.client, "short@test.bg")
        uid = user_id("short@test.bg")
        set_balances(uid, paid=100, gift=5000)
        never = mock.AsyncMock(return_value="<p>no</p>")
        with mock.patch.object(main.ai_interpreter, "interpret_chart", never):
            r = self.client.post("/interpret", json={**CHART, **PAIR}, headers=h)
        self.assertEqual(r.status_code, 402)
        self.assertIn("1,80 €", r.json()["detail"])
        self.assertIn("1,00 €", r.json()["detail"])                                  # колко са внесените
        never.assert_not_called()

    @mock.patch.dict(os.environ, ENFORCED)
    def test_forecast_costs_per_month_and_extra_for_two_people(self):
        h = register_and_login(self.client, "forecast@test.bg")
        uid = user_id("forecast@test.bg")
        set_balances(uid, paid=1000, gift=0)
        month, overview = self.stream_ai()
        with month, overview:
            r = self.client.post("/interpret-stream", json={**CHART, **FORECAST}, headers=h)
        self.assertIn('"charged_cents": 75', r.text)
        self.assertEqual(balances(uid), (925, 0))
        limiter.reset()
        month, overview = self.stream_ai()
        with month, overview:
            r = self.client.post("/interpret-stream", json={**CHART, **FORECAST, **PAIR, "end_date": "2026-02-28"}, headers=h)
        self.assertIn('"charged_cents": 210', r.text)                                # 2 месеца x 0,75 + 0,60
        self.assertEqual(balances(uid), (715, 0))
        assert_ledger_matches(self, uid)

    @mock.patch.dict(os.environ, ENFORCED)
    def test_the_plain_endpoint_prices_a_forecast_by_months_and_not_as_one_analysis(self):
        h = register_and_login(self.client, "plain@test.bg")
        uid = user_id("plain@test.bg")
        set_balances(uid, paid=1000, gift=0)
        month, overview = self.stream_ai()
        with month, overview:
            r = self.client.post("/interpret", json={**CHART, **FORECAST, "end_date": "2026-03-31"}, headers=h)
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual(r.json()["charged_cents"], 225)                             # 3 месеца x 0,75
        self.assertEqual(balances(uid), (775, 0))

    @mock.patch.dict(os.environ, ENFORCED)
    def test_a_failed_forecast_costs_nothing(self):
        h = register_and_login(self.client, "failed@test.bg")
        uid = user_id("failed@test.bg")
        set_balances(uid, paid=1000, gift=0)
        broken = mock.AsyncMock(side_effect=RuntimeError("провайдърът не отговори"))
        with mock.patch("period_report.RETRY_PAUSE_SECONDS", 0.0), \
                mock.patch.object(main.ai_interpreter, "_process_monthly_chunk", broken):
            r = self.client.post("/interpret-stream", json={**CHART, **FORECAST}, headers=h)
        self.assertIn('"code": 502', r.text)
        self.assertEqual(balances(uid), (1000, 0))

    # --- конфигурация и Stripe ----------------------------------------------------------------------------------------
    def test_config_describes_prices_gift_and_topups(self):
        cfg = self.client.get("/billing/config").json()
        self.assertFalse(cfg["payments_enabled"])
        self.assertEqual(cfg["prices"], {"basic_analysis": 160, "pair_analysis": 180, "forecast_month": 75,
                                         "forecast_partner_extra": 60})
        self.assertEqual(cfg["signup_gift_cents"], 500)
        self.assertEqual(cfg["premium_services"], ["pair_analysis", "forecast"])
        self.assertEqual([(t["amount_cents"], t["credit_cents"], t["bonus_cents"]) for t in cfg["topups"]],
                         [(500, 500, 0), (1000, 1050, 50), (2000, 2200, 200)])
        self.assertNotIn("packages", cfg)
        self.assertNotIn("costs", cfg)

    def test_checkout_disabled_without_stripe(self):
        h = register_and_login(self.client, "nostripe@test.bg")
        r = self.client.post("/billing/checkout", json={"package_id": "topup5", "accept_immediate_delivery": True}, headers=h)
        self.assertEqual(r.status_code, 503)

    @mock.patch.dict(os.environ, STRIPE_ENV)
    def test_checkout_requires_consent_and_creates_session(self):
        h = register_and_login(self.client, "checkout@test.bg")
        r = self.client.post("/billing/checkout", json={"package_id": "topup10"}, headers=h)
        self.assertEqual(r.status_code, 400)
        self.assertEqual(self.client.post("/billing/checkout", json={"package_id": "starter", "accept_immediate_delivery": True},
                                          headers=h).status_code, 400)               # непознат (стар) пакет
        fake = mock.AsyncMock(return_value={"id": "cs_test_1", "url": "https://checkout.stripe.com/c/cs_test_1"})
        with mock.patch("billing_api._stripe_post", fake):
            r = self.client.post("/billing/checkout", json={"package_id": "topup10", "accept_immediate_delivery": True}, headers=h)
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual(r.json()["url"], "https://checkout.stripe.com/c/cs_test_1")
        form = fake.call_args.args[1]
        self.assertEqual(form["line_items[0][price_data][currency]"], "eur")
        self.assertEqual(form["line_items[0][price_data][unit_amount]"], "1000")           # платената сума
        self.assertIn("10,50 €", form["line_items[0][price_data][product_data][name]"])    # кредитът в баланса
        self.assertIn("/#/balance?status=success", form["success_url"])
        self.assertIn("/#/balance?status=cancel", form["cancel_url"])
        db = SessionLocal()
        try:
            purchase = db.query(Purchase).filter(Purchase.stripe_session_id == "cs_test_1").one()
            self.assertEqual((purchase.amount_cents, purchase.credit_cents), (1000, 1050))
        finally:
            db.close()

    def _paid_event(self, session_id, purchase_id, amount):
        event = {"type": "checkout.session.completed", "data": {"object": {
            "id": session_id, "payment_status": "paid", "amount_total": amount, "currency": "eur",
            "payment_intent": "pi_" + session_id, "metadata": {"purchase_id": str(purchase_id)}}}}
        return json.dumps(event).encode()

    def _purchase(self, uid, session_id, amount=1000, credit=1050):
        db = SessionLocal()
        try:
            purchase = Purchase(user_id=uid, package_id="topup10", credit_cents=credit, amount_cents=amount,
                                currency="eur", status="pending", stripe_session_id=session_id)
            db.add(purchase)
            db.commit()
            return purchase.id
        finally:
            db.close()

    def _refund_event(self, session_id, amount):
        return json.dumps({"type": "charge.refunded", "data": {"object": {
            "payment_intent": "pi_" + session_id, "amount_refunded": amount}}}).encode()

    @mock.patch.dict(os.environ, STRIPE_ENV)
    def test_webhook_credits_deposited_money_once_and_a_full_refund_takes_it_back(self):
        h = register_and_login(self.client, "webhook@test.bg")
        uid = user_id("webhook@test.bg")
        pid = self._purchase(uid, "cs_test_2")
        payload = self._paid_event("cs_test_2", pid, 1000)

        bad = self.client.post("/billing/webhook", content=payload, headers={"stripe-signature": sign(payload, "wrong")})
        self.assertEqual(bad.status_code, 400)
        old = self.client.post("/billing/webhook", content=payload, headers={"stripe-signature": sign(payload, ts=time.time() - 3600)})
        self.assertEqual(old.status_code, 400)

        with mock.patch("billing_api._stripe_get", mock.AsyncMock(return_value=None)):
            for _ in range(2):  # Stripe може да изпрати събитието повече от веднъж
                r = self.client.post("/billing/webhook", content=payload, headers={"stripe-signature": sign(payload)})
                self.assertEqual(r.status_code, 200)
        payload_me = me(self.client, h)
        self.assertEqual((payload_me["paid_cents"], payload_me["gift_cents"], payload_me["balance_cents"]), (1050, 500, 1550))

        rp = self._refund_event("cs_test_2", 1000)
        for _ in range(2):
            self.client.post("/billing/webhook", content=rp, headers={"stripe-signature": sign(rp)})
        self.assertEqual(balances(uid), (0, 500))                                       # подаръкът не се връща и не се пипа

        tx = self.client.get("/billing/transactions", headers=h).json()
        self.assertEqual([t["reason"] for t in tx["transactions"]], ["refund", "purchase", "signup_gift"])
        self.assertEqual(tx["purchases"][0]["status"], "refunded")
        self.assertEqual(tx["purchases"][0]["credit_cents"], 1050)
        assert_ledger_matches(self, uid)

    @mock.patch.dict(os.environ, STRIPE_ENV)
    def test_a_partial_refund_takes_back_the_credit_in_proportion(self):
        register_and_login(self.client, "partial@test.bg")
        uid = user_id("partial@test.bg")
        pid = self._purchase(uid, "cs_test_5")
        payload = self._paid_event("cs_test_5", pid, 1000)
        with mock.patch("billing_api._stripe_get", mock.AsyncMock(return_value=None)):
            self.client.post("/billing/webhook", content=payload, headers={"stripe-signature": sign(payload)})
        rp = self._refund_event("cs_test_5", 500)                                       # половината от платеното
        self.client.post("/billing/webhook", content=rp, headers={"stripe-signature": sign(rp)})
        self.assertEqual(balances(uid), (525, 500))
        assert_ledger_matches(self, uid)

    @mock.patch.dict(os.environ, STRIPE_ENV)
    def test_a_refund_takes_no_more_than_what_is_left_of_the_deposited_money(self):
        h = register_and_login(self.client, "spent@test.bg")
        uid = user_id("spent@test.bg")
        pid = self._purchase(uid, "cs_test_6")
        payload = self._paid_event("cs_test_6", pid, 1000)
        with mock.patch("billing_api._stripe_get", mock.AsyncMock(return_value=None)):
            self.client.post("/billing/webhook", content=payload, headers={"stripe-signature": sign(payload)})
        with mock.patch.dict(os.environ, ENFORCED), self.ok_ai():
            self.assertEqual(self.client.post("/interpret", json={**CHART, **PAIR}, headers=h).status_code, 200)   # 1,80 € премиум
        self.assertEqual(balances(uid), (870, 500))
        rp = self._refund_event("cs_test_6", 1000)
        self.client.post("/billing/webhook", content=rp, headers={"stripe-signature": sign(rp)})
        self.assertEqual(balances(uid), (0, 500))                                       # вътре е само каквото е останало
        assert_ledger_matches(self, uid)

    @mock.patch.dict(os.environ, STRIPE_ENV)
    def test_webhook_rejects_amount_mismatch(self):
        register_and_login(self.client, "mismatch@test.bg")
        uid = user_id("mismatch@test.bg")
        pid = self._purchase(uid, "cs_test_3", amount=500, credit=500)
        payload = self._paid_event("cs_test_3", pid, 1)
        self.client.post("/billing/webhook", content=payload, headers={"stripe-signature": sign(payload)})
        self.assertEqual(balances(uid), (0, 500))

    def test_verify_signature_multiple_v1(self):
        payload = b'{"a":1}'
        header = sign(payload)
        self.assertTrue(verify_signature(payload, "v1=deadbeef," + header, WEBHOOK_SECRET))
        self.assertFalse(verify_signature(payload, "garbage", WEBHOOK_SECRET))

    # --- регистърът ---------------------------------------------------------------------------------------------------
    def test_a_debit_can_never_make_a_part_of_the_balance_negative_and_a_ref_is_used_once(self):
        register_and_login(self.client, "ledger@test.bg")
        uid = user_id("ledger@test.bg")
        db = SessionLocal()
        try:
            self.assertIsNone(billing.apply_transaction(db, uid, "analysis", gift=-501))           # подаръкът е 500
            self.assertIsNone(billing.apply_transaction(db, uid, "analysis", paid=-1))             # внесените са 0
            self.assertIsNotNone(billing.apply_transaction(db, uid, "analysis", gift=-100, ref="r:1"))
            self.assertIsNone(billing.apply_transaction(db, uid, "analysis", gift=-100, ref="r:1"))   # същият ref втори път
            db.commit()
        finally:
            db.close()
        self.assertEqual(balances(uid), (0, 400))
        assert_ledger_matches(self, uid)

    def test_charge_for_the_same_report_is_taken_once(self):
        register_and_login(self.client, "once@test.bg")
        uid = user_id("once@test.bg")
        db = SessionLocal()
        try:
            quote = billing.analysis_quote(False)
            self.assertIsNotNone(billing.charge(db, uid, quote, "analysis", "report:900", "тест"))
            self.assertIsNone(billing.charge(db, uid, quote, "analysis", "report:900", "тест"))
            db.commit()
        finally:
            db.close()
        self.assertEqual(balances(uid), (0, 340))


if __name__ == "__main__":
    unittest.main()
