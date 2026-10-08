"""
Тестове за изпращането на имейли през SMTP: избор на защитена връзка по порт,
проверка на сертификата, вход, доставка, диагностика и сигурност на данните в лога.

Част от тестовете говорят с истински локален SMTP сървър през TLS (tests/smtp_testserver.py),
за да се провери реалното поведение на smtplib, а не само извикванията към него.

Пускане: python -m unittest discover -s tests
"""
import asyncio
import contextlib
import email
import email.generator
import io
import os
import smtplib
import ssl
import tempfile
import unittest
from datetime import timedelta
from email import policy
from email.utils import parsedate_to_datetime
from unittest import mock

import testenv  # noqa: F401  (трябва да е преди модулите на приложението)

import mailer  # noqa: E402
from smtp_testserver import FakeSmtpServer, make_self_signed_cert  # noqa: E402

MAIL_VARS = (
    "RESEND_API_KEY", "SMTP_HOST", "SMTP_PORT", "SMTP_USER", "SMTP_PASSWORD", "SMTP_SECURITY",
    "SMTP_VERIFY_TLS", "SMTP_TIMEOUT", "MAIL_FROM", "SSL_CERT_FILE", "SSL_CERT_DIR",
)
PASSWORD = "s3cret-pass"


class MailTestCase(unittest.TestCase):
    """Всеки тест започва без никакви настройки за имейл и ги възстановява след края си."""

    def setUp(self):
        patcher = mock.patch.dict(os.environ, {}, clear=False)
        patcher.start()
        self.addCleanup(patcher.stop)
        for name in MAIL_VARS:
            os.environ.pop(name, None)

    @staticmethod
    def set_env(**values):
        os.environ.update({key: str(value) for key, value in values.items()})

    @staticmethod
    def send(to="to@example.test", subject="Тема", text="текст", html="<p>html</p>"):
        """Изпраща писмо и връща (резултат, записаното в лога)."""
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            result = asyncio.run(mailer.send_email(to, subject, text, html))
        return result, output.getvalue()


class ConfigTest(MailTestCase):
    def test_security_follows_port(self):
        self.assertEqual(mailer._smtp_security(465), "ssl")
        for port in (26, 587, 25, 2525):
            self.assertEqual(mailer._smtp_security(port), "starttls")

    def test_security_can_be_forced(self):
        self.set_env(SMTP_SECURITY="SSL")
        self.assertEqual(mailer._smtp_security(26), "ssl")
        self.set_env(SMTP_SECURITY="none")
        self.assertEqual(mailer._smtp_security(465), "none")
        self.set_env(SMTP_SECURITY="bogus")
        self.assertEqual(mailer._smtp_security(465), "ssl")  # непозната стойност: избор по порта

    def test_port_and_timeout_fall_back_to_defaults(self):
        self.assertEqual(mailer._smtp_port(), 587)
        self.set_env(SMTP_PORT="")
        self.assertEqual(mailer._smtp_port(), 587)
        self.set_env(SMTP_PORT="abc")
        self.assertEqual(mailer._smtp_port(), 587)
        self.set_env(SMTP_PORT=" 26 ")
        self.assertEqual(mailer._smtp_port(), 26)

        self.assertEqual(mailer._smtp_timeout(), 15.0)
        for bad in ("abc", "0", "-3"):
            self.set_env(SMTP_TIMEOUT=bad)
            self.assertEqual(mailer._smtp_timeout(), 15.0)
        self.set_env(SMTP_TIMEOUT="7.5")
        self.assertEqual(mailer._smtp_timeout(), 7.5)

    def test_is_configured(self):
        self.assertFalse(mailer.is_configured())
        self.set_env(SMTP_HOST="mail.example.test", SMTP_USER="u@example.test")
        self.assertFalse(mailer.is_configured())          # има потребител, но няма парола
        self.assertIn("SMTP_PASSWORD", mailer._config_problem())
        self.set_env(SMTP_PASSWORD=PASSWORD)
        self.assertTrue(mailer.is_configured())
        os.environ.pop("SMTP_USER")
        os.environ.pop("SMTP_PASSWORD")
        self.assertTrue(mailer.is_configured())           # сървър без удостоверяване
        os.environ.pop("SMTP_HOST")
        self.assertFalse(mailer.is_configured())
        self.set_env(RESEND_API_KEY="re_test")
        self.assertTrue(mailer.is_configured())

    def test_sender_defaults_to_the_mailbox(self):
        self.set_env(SMTP_USER="astromind@example.test")
        self.assertEqual(mailer._smtp_from(), "AstroMind <astromind@example.test>")
        self.assertEqual(mailer._from_domain(), "example.test")
        self.set_env(MAIL_FROM="Други <other@mail.example.test>")
        self.assertEqual(mailer._smtp_from(), "Други <other@mail.example.test>")
        self.assertEqual(mailer._from_domain(), "mail.example.test")


class MessageTest(MailTestCase):
    def test_headers_and_parts(self):
        self.set_env(SMTP_USER="astromind@example.test")
        msg = mailer._build_message("to@example.test", "Потвърдете имейла", "обикновен текст", "<p>html</p>")
        self.assertEqual(msg["From"], "AstroMind <astromind@example.test>")
        self.assertEqual(msg["To"], "to@example.test")
        self.assertEqual(msg["Subject"], "Потвърдете имейла")
        self.assertEqual(parsedate_to_datetime(msg["Date"]).utcoffset(), timedelta(0))
        self.assertTrue(msg["Message-ID"].endswith("@example.test>"))
        self.assertEqual(msg.get_body(preferencelist=("plain",)).get_content().strip(), "обикновен текст")
        self.assertIn("<p>html</p>", msg.get_body(preferencelist=("html",)).get_content())

    def test_message_is_seven_bit_clean_on_the_wire(self):
        msg = mailer._build_message("to@example.test", "Тема на кирилица", "Текст на кирилица", "<p>Здравей</p>")
        buffer = io.BytesIO()
        email.generator.BytesGenerator(buffer).flatten(msg, linesep="\r\n")
        wire = buffer.getvalue()
        self.assertTrue(all(byte < 128 for byte in wire))
        for part in msg.iter_parts():
            self.assertIn(part["Content-Transfer-Encoding"], ("base64", "quoted-printable"))
        # и пак се чете правилно
        self.assertIn("Текст на кирилица", msg.get_body(preferencelist=("plain",)).get_content())


class TransportSelectionTest(MailTestCase):
    """Кой клас от smtplib се ползва и в какъв ред: проверка със заместители (без мрежа)."""

    def setUp(self):
        super().setUp()
        self.set_env(SMTP_HOST="mail.example.test", SMTP_USER="u@example.test", SMTP_PASSWORD=PASSWORD)

    def run_send(self):
        with mock.patch("mailer.smtplib.SMTP_SSL") as ssl_cls, mock.patch("mailer.smtplib.SMTP") as plain_cls:
            for cls in (ssl_cls, plain_cls):
                cls.return_value.esmtp_features = {"auth": "PLAIN LOGIN"}
            mailer._send_smtp("to@example.test", "Тема", "текст", "<p>html</p>")
        return ssl_cls, plain_cls

    def assert_single_plain_login(self, server):
        server.auth.assert_called_once()
        self.assertEqual(server.auth.call_args.args[0], "PLAIN")
        self.assertEqual((server.user, server.password), ("u@example.test", PASSWORD))
        server.login.assert_not_called()  # login() би опитал и втори начин при грешка

    def test_port_465_uses_implicit_tls_with_verification(self):
        self.set_env(SMTP_PORT=465)
        ssl_cls, plain_cls = self.run_send()
        plain_cls.assert_not_called()
        args, kwargs = ssl_cls.call_args
        self.assertEqual(args, ("mail.example.test", 465))
        self.assertEqual(kwargs["timeout"], 15.0)
        self.assertEqual(kwargs["local_hostname"], "example.test")
        self.assertEqual(kwargs["context"].verify_mode, ssl.CERT_REQUIRED)
        self.assertTrue(kwargs["context"].check_hostname)
        server = ssl_cls.return_value
        server.starttls.assert_not_called()
        self.assert_single_plain_login(server)
        server.send_message.assert_called_once()

    def test_port_26_uses_starttls_before_login(self):
        self.set_env(SMTP_PORT=26)
        ssl_cls, plain_cls = self.run_send()
        ssl_cls.assert_not_called()
        args, kwargs = plain_cls.call_args
        self.assertEqual(args, ("mail.example.test", 26))
        server = plain_cls.return_value
        order = [call[0] for call in server.mock_calls if call[0] in ("starttls", "auth", "send_message")]
        self.assertEqual(order, ["starttls", "auth", "send_message"])
        self.assert_single_plain_login(server)
        self.assertEqual(server.starttls.call_args.kwargs["context"].verify_mode, ssl.CERT_REQUIRED)

    def test_certificate_check_can_be_disabled(self):
        self.set_env(SMTP_PORT=465, SMTP_VERIFY_TLS=0)
        ssl_cls, _ = self.run_send()
        context = ssl_cls.call_args.kwargs["context"]
        self.assertEqual(context.verify_mode, ssl.CERT_NONE)
        self.assertFalse(context.check_hostname)

    def test_no_login_without_user(self):
        os.environ.pop("SMTP_USER")
        os.environ.pop("SMTP_PASSWORD")
        self.set_env(SMTP_PORT=26)
        _, plain_cls = self.run_send()
        plain_cls.return_value.login.assert_not_called()
        plain_cls.return_value.auth.assert_not_called()
        plain_cls.return_value.send_message.assert_called_once()

    def test_credentials_are_never_sent_without_starttls(self):
        self.set_env(SMTP_PORT=26)
        with mock.patch("mailer.smtplib.SMTP") as plain_cls:
            plain_cls.return_value.has_extn.return_value = False
            with self.assertRaises(smtplib.SMTPNotSupportedError):
                mailer._send_smtp("to@example.test", "Тема", "текст", "<p>html</p>")
        plain_cls.return_value.auth.assert_not_called()
        plain_cls.return_value.login.assert_not_called()
        plain_cls.return_value.send_message.assert_not_called()

    def test_login_only_servers_use_login_method(self):
        self.set_env(SMTP_PORT=26)
        with mock.patch("mailer.smtplib.SMTP") as plain_cls:
            plain_cls.return_value.esmtp_features = {"auth": "LOGIN"}
            mailer._send_smtp("to@example.test", "Тема", "текст", "<p>html</p>")
        server = plain_cls.return_value
        self.assertEqual(server.auth.call_args.args[0], "LOGIN")

    def test_server_without_auth_extension_is_an_error(self):
        self.set_env(SMTP_PORT=26)
        with mock.patch("mailer.smtplib.SMTP") as plain_cls:
            plain_cls.return_value.has_extn.side_effect = lambda name: name == "starttls"
            with self.assertRaises(smtplib.SMTPNotSupportedError):
                mailer._send_smtp("to@example.test", "Тема", "текст", "<p>html</p>")
        plain_cls.return_value.send_message.assert_not_called()


class SendEmailLoggingTest(MailTestCase):
    """Какво се връща и какво се записва в лога при различни проблеми."""

    def setUp(self):
        super().setUp()
        self.set_env(SMTP_HOST="mail.example.test", SMTP_PORT=26, SMTP_USER="u@example.test", SMTP_PASSWORD=PASSWORD)

    def failing_send(self, exc):
        with mock.patch("mailer._send_smtp", side_effect=exc):
            return self.send(to="victim@example.test")

    def assert_log_is_clean(self, log):
        self.assertNotIn(PASSWORD, log)
        self.assertNotIn("victim@example.test", log)

    def test_success_is_logged_without_recipient(self):
        with mock.patch("mailer._send_smtp"):
            result, log = self.send(to="victim@example.test")
        self.assertTrue(result)
        self.assertIn("Изпратен имейл", log)
        self.assert_log_is_clean(log)

    def test_certificate_problem(self):
        result, log = self.failing_send(ssl.SSLCertVerificationError(1, "certificate verify failed"))
        self.assertFalse(result)
        self.assertIn("SMTP_VERIFY_TLS", log)
        self.assert_log_is_clean(log)

    def test_wrong_credentials(self):
        result, log = self.failing_send(smtplib.SMTPAuthenticationError(535, b"Incorrect authentication data"))
        self.assertFalse(result)
        self.assertIn("SMTP_PASSWORD", log)
        self.assert_log_is_clean(log)

    def test_blocked_port_explains_render_free(self):
        result, log = self.failing_send(TimeoutError("timed out"))
        self.assertFalse(result)
        self.assertIn("Render Free", log)
        self.assertIn("напр. 26", log)       # подсказката предлага порт 26, не само името на порта в реда

    def test_refused_recipient_is_not_logged(self):
        exc = smtplib.SMTPRecipientsRefused({"victim@example.test": (550, b"no such user")})
        result, log = self.failing_send(exc)
        self.assertFalse(result)
        self.assertIn("550", log)
        self.assert_log_is_clean(log)

    def test_sender_refused_points_to_mail_from(self):
        result, log = self.failing_send(smtplib.SMTPSenderRefused(553, b"sender not allowed", "x@example.test"))
        self.assertFalse(result)
        self.assertIn("MAIL_FROM", log)

    def test_non_ascii_password_error_does_not_leak_characters(self):
        exc = UnicodeEncodeError("ascii", "☃парола", 0, 1, "ordinal not in range(128)")
        self.assertIn("u2603", str(exc))             # без защитата текстът на грешката съдържа знака
        result, log = self.failing_send(exc)
        self.assertFalse(result)
        self.assertNotIn("u2603", log)
        self.assertNotIn("☃", log)
        self.assertIn("извън ASCII", log)

    def test_not_configured(self):
        for name in ("SMTP_HOST", "SMTP_PORT", "SMTP_USER", "SMTP_PASSWORD"):
            os.environ.pop(name)
        result, log = self.send()
        self.assertFalse(result)
        self.assertIn("не е настроен", log)

    def test_half_configured_does_not_try_to_connect(self):
        os.environ.pop("SMTP_PASSWORD")
        with mock.patch("mailer._send_smtp") as send_smtp:
            result, log = self.send()
        self.assertFalse(result)
        send_smtp.assert_not_called()
        self.assertIn("непълно", log)
        self.assertIn("SMTP_PASSWORD", log)


class RealSmtpTest(MailTestCase):
    """Истински SMTP разговор с локален сървър през TLS."""

    @classmethod
    def setUpClass(cls):
        cls._tmp = tempfile.TemporaryDirectory()
        cls.certfile, cls.keyfile = make_self_signed_cert(cls._tmp.name)

    @classmethod
    def tearDownClass(cls):
        cls._tmp.cleanup()

    def start_server(self, **kwargs):
        server = FakeSmtpServer(self.certfile, self.keyfile, **kwargs).start()
        self.addCleanup(server.stop)
        return server

    def configure(self, server, *, trust=True, password=PASSWORD, **extra):
        self.set_env(
            SMTP_HOST="localhost", SMTP_PORT=server.port, SMTP_USER=server.username, SMTP_PASSWORD=password,
            MAIL_FROM=f"AstroMind <{server.username}>", SMTP_TIMEOUT=5, **extra,
        )
        if trust:
            # Тестовият сертификат е самоподписан: добавяме го към доверените, като в реална среда
            # би бил доверен чрез удостоверителен орган
            os.environ["SSL_CERT_FILE"] = self.certfile
        else:
            os.environ.pop("SSL_CERT_FILE", None)

    def parsed(self, server):
        self.assertEqual(len(server.messages), 1)
        return server.messages[0], email.message_from_bytes(server.messages[0]["data"], policy=policy.default)

    def assert_delivered(self, server):
        record, msg = self.parsed(server)
        self.assertEqual(record["mail_from"], server.username)
        self.assertEqual(record["rcpt_to"], ["to@example.test"])
        self.assertEqual(msg["Subject"], "Тема на кирилица")
        self.assertEqual(msg["From"], f"AstroMind <{server.username}>")
        self.assertTrue(msg["Message-ID"].endswith("@example.test>"))
        self.assertIn("текст на писмото", msg.get_body(preferencelist=("plain",)).get_content())
        self.assertIn("<p>html</p>", msg.get_body(preferencelist=("html",)).get_content())

    def deliver(self):
        return self.send(subject="Тема на кирилица", text="текст на писмото", html="<p>html</p>")

    def test_delivery_over_implicit_tls(self):
        server = self.start_server(implicit_tls=True, starttls=False)
        self.configure(server, SMTP_SECURITY="ssl")
        result, log = self.deliver()
        self.assertTrue(result, log)
        self.assert_delivered(server)
        self.assertEqual(server.auth_results, [True])

    def test_delivery_over_starttls(self):
        server = self.start_server()
        self.configure(server)
        result, log = self.deliver()
        self.assertTrue(result, log)
        self.assert_delivered(server)
        self.assertEqual(server.events, ["starttls"])
        self.assertEqual(server.auth_results, [True])
        self.assertEqual(server.auth_methods, ["PLAIN"])

    def test_wrong_password_is_reported_and_not_logged(self):
        server = self.start_server()
        self.configure(server, password="wrong-password-123")
        result, log = self.deliver()
        self.assertFalse(result)
        self.assertEqual(server.auth_results, [False])   # един неуспешен опит, не два
        self.assertEqual(server.messages, [])
        self.assertIn("SMTP_PASSWORD", log)
        self.assertNotIn("wrong-password-123", log)

    def test_login_only_server(self):
        server = self.start_server(auth_mechanisms="LOGIN")
        self.configure(server)
        result, log = self.deliver()
        self.assertTrue(result, log)
        self.assertEqual(server.auth_methods, ["LOGIN"])
        self.assert_delivered(server)

    def test_server_without_authentication_is_refused(self):
        server = self.start_server(auth_mechanisms="")
        self.configure(server)
        result, log = self.deliver()
        self.assertFalse(result)
        self.assertEqual(server.auth_results, [])
        self.assertIn("AUTH", log)

    def test_untrusted_certificate_is_rejected_before_credentials_are_sent(self):
        server = self.start_server()
        self.configure(server, trust=False)
        result, log = self.deliver()
        self.assertFalse(result)
        self.assertEqual(server.auth_results, [])   # паролата не е напуснала приложението
        self.assertEqual(server.messages, [])
        self.assertIn("SMTP_VERIFY_TLS", log)

    def test_untrusted_implicit_tls_is_rejected_too(self):
        server = self.start_server(implicit_tls=True, starttls=False)
        self.configure(server, trust=False, SMTP_SECURITY="ssl")
        result, log = self.deliver()
        self.assertFalse(result)
        self.assertEqual(server.auth_results, [])
        self.assertIn("SMTP_VERIFY_TLS", log)

    def test_certificate_check_can_be_switched_off_explicitly(self):
        server = self.start_server()
        self.configure(server, trust=False, SMTP_VERIFY_TLS=0)
        result, log = self.deliver()
        self.assertTrue(result, log)
        self.assert_delivered(server)

    def test_server_without_starttls_never_receives_credentials(self):
        server = self.start_server(starttls=False)
        self.configure(server)
        result, log = self.deliver()
        self.assertFalse(result)
        self.assertEqual(server.auth_results, [])
        self.assertEqual(server.messages, [])
        self.assertIn("STARTTLS", log)

    def test_unreachable_server_fails_fast_with_a_hint(self):
        server = self.start_server()
        self.configure(server)
        server.stop()                                # нищо не слуша на този порт
        result, log = self.deliver()
        self.assertFalse(result)
        self.assertIn("SMTP_PORT", log)

    def test_probe_reports_authentication_without_logging_in(self):
        server = self.start_server()
        self.configure(server)
        self.assertEqual(mailer.probe_smtp(), "PLAIN LOGIN")
        self.assertEqual(server.auth_results, [])
        self.assertEqual(server.messages, [])

    def test_startup_check_logs_status_without_secrets(self):
        server = self.start_server()
        self.configure(server)
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            mailer._startup_check()
        log = output.getvalue()
        self.assertIn("✅", log)
        self.assertIn("PLAIN LOGIN", log)
        self.assertNotIn(PASSWORD, log)

        output = io.StringIO()
        self.configure(server, trust=False)
        with contextlib.redirect_stdout(output):
            mailer._startup_check()
        self.assertIn("❌", output.getvalue())


if __name__ == "__main__":
    unittest.main()
