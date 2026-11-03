"""Контакт с нас (03.11.2026): съобщението отива при собственика с Reply-To към подателя."""
import os
import unittest
from unittest import mock

import testenv  # noqa: F401  (трябва да е преди main)
from fastapi.testclient import TestClient  # noqa: E402

import contact_api  # noqa: E402
import main  # noqa: E402
from rate_limit import limiter  # noqa: E402

SRC = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), "src")
BODY = {"name": "Мария", "email": "maria@example.com", "message": "Здравейте, имам въпрос за анализа ми.", "website": ""}


class ContactTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.client = TestClient(main.app)

    def setUp(self):
        limiter.reset()
        p = mock.patch.object(contact_api.mailer, "is_configured", return_value=True)
        p.start()
        self.addCleanup(p.stop)

    def post(self, body=None):
        send = mock.AsyncMock(return_value=True)
        with mock.patch.object(contact_api.mailer, "send_email", new=send):
            r = self.client.post("/contact", json=body or BODY)
        return r, send

    def test_the_message_goes_to_the_owner_with_reply_to_the_sender(self):
        r, send = self.post()
        self.assertEqual(r.status_code, 200, r.text)
        to, subject, text, html = send.await_args.args
        self.assertEqual(to, "evgeniatanasov@gmail.com")
        self.assertEqual(send.await_args.kwargs["reply_to"], "maria@example.com")
        self.assertIn("Мария", subject)
        self.assertIn("имам въпрос", text)
        self.assertIn("maria@example.com", html)

    def test_html_in_the_message_is_escaped(self):
        r, send = self.post({**BODY, "message": "<script>alert(1)</script> достатъчно дълго съобщение"})
        self.assertEqual(r.status_code, 200)
        self.assertNotIn("<script>", send.await_args.args[3])

    def test_bad_input_is_refused_and_a_bot_gets_a_silent_ok(self):
        for bad in ({**BODY, "email": "not-an-email"}, {**BODY, "name": "  "}, {**BODY, "message": "къс"}):
            r, send = self.post(bad)
            self.assertEqual(r.status_code, 400, bad)
            send.assert_not_awaited()
        r, send = self.post({**BODY, "website": "http://spam"})
        self.assertEqual(r.status_code, 200)
        send.assert_not_awaited()

    def test_the_rate_limit_and_a_failed_send(self):
        for _ in range(3):
            self.assertEqual(self.post()[0].status_code, 200)
        self.assertEqual(self.post()[0].status_code, 429)
        limiter.reset()
        with mock.patch.object(contact_api.mailer, "send_email", new=mock.AsyncMock(return_value=False)):
            self.assertEqual(self.client.post("/contact", json=BODY).status_code, 502)

    def test_the_link_is_on_every_page_bottom_right(self):
        app = open(os.path.join(SRC, "App.jsx"), encoding="utf-8").read()
        self.assertIn("<ContactUs />", app)
        comp = open(os.path.join(SRC, "components", "ContactUs.jsx"), encoding="utf-8").read()
        for needle in ("Контакт с нас", "bottom-4 right-4", "/contact", "Вашият имейл", "Вашето съобщение"):
            self.assertIn(needle, comp)


if __name__ == "__main__":
    unittest.main()
