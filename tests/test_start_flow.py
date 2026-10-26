"""Нов ред при регистрация (26.10.2026): данни и безплатен преглед без акаунт, акаунт с основен профил, вход само след потвърден имейл."""
import os
import unittest
from datetime import datetime, timedelta
from unittest import mock

import testenv  # noqa: F401  (трябва да е преди main)
from fastapi.testclient import TestClient  # noqa: E402

import account_api  # noqa: E402
import main  # noqa: E402
from auth import create_purpose_token  # noqa: E402
from database import Profile, SessionLocal, User  # noqa: E402
from rate_limit import limiter  # noqa: E402

PROFILE = {"name": "Мария", "gender": "female", "birth_date": "1990-02-15", "birth_time": "13:00", "unknown_time": False,
           "birth_place": "София", "lat": 42.6977, "lon": 23.3219}
PASSWORD = "Silna-Parola-2026!"


class StartFlowTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.client = TestClient(main.app)

    def setUp(self):
        limiter.reset() if hasattr(limiter, "reset") else None
        patcher = mock.patch.object(account_api, "REQUIRE_EMAIL_VERIFICATION", True)
        patcher.start()
        self.addCleanup(patcher.stop)

    def register(self, email, profile=PROFILE):
        body = {"email": email, "password": PASSWORD, "full_name": "Мария", "accept_terms": True}
        if profile is not None:
            body["profile"] = profile
        return self.client.post("/register", json=body)

    def test_the_preview_needs_no_account_and_returns_the_big_three_and_the_elements(self):
        r = self.client.post("/public/preview", json=PROFILE)
        self.assertEqual(r.status_code, 200, r.text)
        data = r.json()
        self.assertTrue(data["sun"]["sign_bg"] and len(data["elements"]) == 4)

    def test_the_gender_is_required_and_only_male_or_female(self):
        for gender in (None, "", "other", "unknown"):
            body = {**PROFILE, "gender": gender}
            self.assertEqual(self.client.post("/public/preview", json=body).status_code, 422, gender)
        body = {**PROFILE}
        del body["gender"]
        self.assertEqual(self.client.post("/public/preview", json=body).status_code, 422)

    def test_an_impossible_date_is_refused_and_registration_with_bad_data_creates_nothing(self):
        bad = {**PROFILE, "birth_date": "2023-02-30"}
        self.assertEqual(self.client.post("/public/preview", json=bad).status_code, 400)
        self.assertEqual(self.register("start-bad@test.bg", bad).status_code, 400)
        db = SessionLocal()
        try:
            self.assertIsNone(db.query(User).filter(User.email == "start-bad@test.bg").first())
        finally:
            db.close()

    def test_registration_creates_the_primary_profile_and_completes_onboarding(self):
        r = self.register("start-ok@test.bg")
        self.assertEqual(r.status_code, 200, r.text)
        self.assertTrue(r.json()["verification_required"])
        db = SessionLocal()
        try:
            user = db.query(User).filter(User.email == "start-ok@test.bg").first()
            self.assertTrue(user.onboarding_completed)
            profiles = db.query(Profile).filter(Profile.user_id == user.id).all()
            self.assertEqual([(p.name, p.gender, p.is_primary) for p in profiles], [("Мария", "female", True)])
        finally:
            db.close()

    def test_login_is_refused_until_the_email_is_confirmed_and_the_link_logs_in(self):
        self.register("start-login@test.bg")
        refused = self.client.post("/login", json={"email": "start-login@test.bg", "password": PASSWORD})
        self.assertEqual(refused.status_code, 403)
        self.assertIn("Потвърдете имейла", refused.json()["detail"])
        db = SessionLocal()
        try:
            user = db.query(User).filter(User.email == "start-login@test.bg").first()
            token = create_purpose_token("verify", user)
        finally:
            db.close()
        r = self.client.post("/verify-email", json={"token": token})
        self.assertEqual(r.status_code, 200, r.text)
        self.assertTrue(r.json()["access_token"])
        self.assertEqual(r.json()["user"]["onboarding_completed"], True)
        self.assertEqual(self.client.post("/login", json={"email": "start-login@test.bg", "password": PASSWORD}).status_code, 200)

    def test_an_account_created_before_the_cutover_is_not_locked_out(self):
        self.register("start-old@test.bg")
        db = SessionLocal()
        try:
            user = db.query(User).filter(User.email == "start-old@test.bg").first()
            user.created_at = datetime(2026, 10, 1)
            db.commit()
        finally:
            db.close()
        self.assertEqual(self.client.post("/login", json={"email": "start-old@test.bg", "password": PASSWORD}).status_code, 200)

    def test_the_public_resend_gives_the_same_answer_for_known_and_unknown_emails(self):
        self.register("start-resend@test.bg")
        with mock.patch("mailer.is_configured", return_value=True), mock.patch("mailer.send_verification_email", new=mock.AsyncMock(return_value=True)) as sender:
            known = self.client.post("/resend-verification-public", json={"email": "start-resend@test.bg"})
            unknown = self.client.post("/resend-verification-public", json={"email": "nobody-here@test.bg"})
        self.assertEqual(known.json(), unknown.json())
        self.assertEqual(sender.await_count, 1)

    def test_old_unverified_accounts_are_purged_after_seven_days_but_never_paid_or_recent_ones(self):
        for email in ("purge-old@test.bg", "purge-new@test.bg", "purge-paid@test.bg"):
            self.register(email)
        db = SessionLocal()
        try:
            old = datetime.utcnow() - timedelta(days=8)
            db.query(User).filter(User.email.in_(["purge-old@test.bg", "purge-paid@test.bg"])).update({User.created_at: old}, synchronize_session=False)
            db.query(User).filter(User.email == "purge-paid@test.bg").update({User.paid_cents: 500}, synchronize_session=False)
            db.commit()
            with mock.patch.object(account_api, "VERIFICATION_REQUIRED_FROM", datetime(2026, 1, 1)):       # „стари“ акаунти в теста са отпреди седем дни
                self.assertGreaterEqual(account_api.purge_unverified(db), 1)       # и старите акаунти от другите тестове
            left = {u.email for u in db.query(User).filter(User.email.like("purge-%@test.bg"))}
            self.assertEqual(left, {"purge-new@test.bg", "purge-paid@test.bg"})
        finally:
            db.close()


class StartScreensTextTest(unittest.TestCase):
    """Екраните: „Безплатен анализ“ води към /start, а там данните и прегледът са преди имейла и паролата."""
    SRC = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), "src")

    def read(self, *parts):
        with open(os.path.join(self.SRC, *parts), encoding="utf-8") as f:
            return f.read()

    def test_every_free_analysis_button_goes_to_the_start_page(self):
        home = self.read("pages", "Home.jsx")
        self.assertNotIn("setShowAuth(true); setIsLogin(false);", home)
        self.assertGreaterEqual(home.count("navigate('/start')"), 6)
        self.assertIn('path="/start"', self.read("App.jsx"))

    def test_the_start_page_orders_the_steps_and_requires_gender_and_terms(self):
        start = self.read("pages", "Start.jsx")
        self.assertLess(start.index("/public/preview"), start.index("/register"))
        for needle in ("Изберете пол", "Моля, приемете Общите условия", "/resend-verification-public", "profile: birthPayload(form)", "Потвърдете имейла си"):
            self.assertIn(needle, start)
        form = self.read("components", "BirthDataForm.jsx")
        self.assertIn("female", form)
        self.assertIn("male", form)

    def test_the_main_profile_form_asks_for_gender_and_the_verify_link_logs_in(self):
        self.assertIn("BirthDataForm", self.read("pages", "Welcome.jsx"))
        self.assertIn("Изберете пол", self.read("pages", "Welcome.jsx"))
        verify = self.read("pages", "AuthLinkPages.jsx")
        self.assertIn("data.access_token", verify)
        self.assertIn("navigate('/dashboard')", verify)
        self.assertIn("resendVerification", self.read("pages", "Home.jsx"))


if __name__ == "__main__":
    unittest.main()
