"""
Тестове за потоците, които изпращат писма: регистрация, забравена парола, смяна на имейл
и „Изпрати писмото отново“. Самото изпращане е подменено (виж test_mailer.py за него).

Пускане: python -m unittest discover -s tests
"""
import os
import unittest
from unittest import mock

import testenv  # noqa: F401  (трябва да е преди main)
from fastapi.testclient import TestClient  # noqa: E402

import mailer  # noqa: E402
import main  # noqa: E402
from auth import create_purpose_token, decode_purpose_token  # noqa: E402
from database import SessionLocal, User  # noqa: E402
from rate_limit import limiter  # noqa: E402
from testenv import PASSWORD, register_and_login  # noqa: E402

SMTP_ENV = {"SMTP_HOST": "mail.example.test", "SMTP_USER": "u@example.test", "SMTP_PASSWORD": "pw-123"}


def user_by_email(email):
    db = SessionLocal()
    try:
        user = db.query(User).filter(User.email == email).first()
        db.expunge(user)
        return user
    finally:
        db.close()


class EmailFlowsTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.client = TestClient(main.app)

    def setUp(self):
        limiter.reset()

    def register(self, email):
        return self.client.post("/register", json={"email": email, "password": PASSWORD,
                                                   "full_name": "Тест", "accept_terms": True})

    def test_register_sends_a_valid_verification_link(self):
        sent = mock.AsyncMock(return_value=True)
        with mock.patch.object(mailer, "send_verification_email", sent):
            r = self.register("mail-reg@test.bg")
        self.assertEqual(r.status_code, 200)
        sent.assert_awaited_once()
        to, token = sent.await_args.args
        self.assertEqual(to, "mail-reg@test.bg")
        payload = decode_purpose_token(token, "verify")
        self.assertEqual(payload["email"], "mail-reg@test.bg")
        self.assertEqual(self.client.post("/verify-email", json={"token": token}).status_code, 200)

    def test_register_succeeds_even_if_the_mail_server_is_broken(self):
        broken = mock.AsyncMock(return_value=False)
        with mock.patch.object(mailer, "send_verification_email", broken):
            self.assertEqual(self.register("mail-broken@test.bg").status_code, 200)
        # акаунтът е създаден и може да се влезе
        r = self.client.post("/login", json={"email": "mail-broken@test.bg", "password": PASSWORD})
        self.assertEqual(r.status_code, 200)

    def test_forgot_password_mails_only_registered_users_but_answers_the_same(self):
        register_and_login(self.client, "mail-forgot@test.bg")
        sent = mock.AsyncMock(return_value=True)
        with mock.patch.object(mailer, "send_password_reset_email", sent):
            known = self.client.post("/forgot-password", json={"email": "Mail-Forgot@test.bg"})
            unknown = self.client.post("/forgot-password", json={"email": "nobody-here@test.bg"})
        self.assertEqual(known.json(), unknown.json())
        sent.assert_awaited_once()
        to, token = sent.await_args.args
        self.assertEqual(to, "mail-forgot@test.bg")
        self.assertEqual(decode_purpose_token(token, "reset")["email"], "mail-forgot@test.bg")

    def test_changing_email_sends_verification_to_the_new_address(self):
        headers = register_and_login(self.client, "mail-change@test.bg")
        sent = mock.AsyncMock(return_value=True)
        with mock.patch.object(mailer, "send_verification_email", sent):
            r = self.client.patch("/me", json={"email": "mail-changed@test.bg"}, headers=headers)
        self.assertEqual(r.status_code, 200)
        sent.assert_awaited_once()
        self.assertEqual(sent.await_args.args[0], "mail-changed@test.bg")

    def test_name_only_change_sends_nothing(self):
        headers = register_and_login(self.client, "mail-name@test.bg")
        sent = mock.AsyncMock(return_value=True)
        with mock.patch.object(mailer, "send_verification_email", sent):
            self.client.patch("/me", json={"full_name": "Ново Име"}, headers=headers)
        sent.assert_not_awaited()

    def test_resend_says_so_when_email_is_not_activated(self):
        headers = register_and_login(self.client, "mail-off@test.bg")
        r = self.client.post("/resend-verification", headers=headers)
        self.assertEqual(r.status_code, 503)
        self.assertIn("не е активирано", r.json()["detail"])

    @mock.patch.dict(os.environ, SMTP_ENV)
    def test_resend_reports_success_only_when_the_mail_was_sent(self):
        headers = register_and_login(self.client, "mail-resend@test.bg")
        with mock.patch.object(mailer, "send_verification_email", mock.AsyncMock(return_value=True)) as ok:
            r = self.client.post("/resend-verification", headers=headers)
        self.assertEqual((r.status_code, r.json()["sent"]), (200, True))
        to, token = ok.await_args.args
        self.assertEqual(to, "mail-resend@test.bg")
        self.assertEqual(decode_purpose_token(token, "verify")["email"], to)

        with mock.patch.object(mailer, "send_verification_email", mock.AsyncMock(return_value=False)):
            r = self.client.post("/resend-verification", headers=headers)
        self.assertEqual(r.status_code, 502)
        self.assertIn("Не успяхме", r.json()["detail"])

    @mock.patch.dict(os.environ, SMTP_ENV)
    def test_resend_does_nothing_for_verified_email(self):
        headers = register_and_login(self.client, "mail-done@test.bg")
        token = create_purpose_token("verify", user_by_email("mail-done@test.bg"))
        self.client.post("/verify-email", json={"token": token})
        sent = mock.AsyncMock(return_value=True)
        with mock.patch.object(mailer, "send_verification_email", sent):
            r = self.client.post("/resend-verification", headers=headers)
        self.assertEqual((r.status_code, r.json()["sent"]), (200, False))
        sent.assert_not_awaited()

    @mock.patch.dict(os.environ, SMTP_ENV)
    def test_resend_is_rate_limited(self):
        headers = register_and_login(self.client, "mail-limit@test.bg")
        with mock.patch.object(mailer, "send_verification_email", mock.AsyncMock(return_value=True)):
            codes = [self.client.post("/resend-verification", headers=headers).status_code for _ in range(4)]
        self.assertEqual(codes, [200, 200, 200, 429])


if __name__ == "__main__":
    unittest.main()
