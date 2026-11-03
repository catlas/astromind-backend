"""Дневен транзит (03.11.2026): „Обща картина“ за основния профил в Таблото и в писмото в 08:00 българско време."""
import asyncio
import os
import unittest
from datetime import datetime, timedelta
from types import SimpleNamespace
from unittest import mock

import testenv  # noqa: F401  (трябва да е преди main)
from testenv import register_and_login  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

import daily_transit as dt  # noqa: E402
import main  # noqa: E402
from database import DailyTransit, Profile, SessionLocal, User  # noqa: E402
from rate_limit import limiter  # noqa: E402

FIX = os.path.join(os.path.dirname(os.path.abspath(__file__)), "fixtures")
PROFILE = {"name": "Мария", "gender": "female", "birth_date": "1990-02-15", "birth_time": "13:00", "unknown_time": False,
           "birth_place": "София", "lat": 42.6977, "lon": 23.3219, "relation": "self", "is_primary": True}
GENERAL = ("На избраната дата има смесена картина: от една страна вътрешно напрежение, от друга активност и отворени контакти.\n\n"
           "Ресурсите са в гъвкавостта и в проверката на конкретиката.")
CONTENT = f"## Транзитите към Мария\n\n### Транзитното Слънце е в съвпад с наталната Луна\nТекст на картата.\n\n## Обща картина\n{GENERAL}\n\n## Какво да имате предвид в този ден\n- Съвет едно.\n\n## Заключение\nКрайно заключение, което не трябва да е в писмото.\n"


def fixture(name):
    with open(os.path.join(FIX, name), encoding="utf-8") as f:
        return f.read()


def run(coro):
    return asyncio.run(coro)


def fake_run(content=CONTENT):
    async def _run(request, user_id, emit):
        return SimpleNamespace(content=content, quality="full")
    return _run


class GeneralPictureTest(unittest.TestCase):
    def test_only_the_general_picture_block_is_taken(self):
        self.assertEqual(dt.general_picture(CONTENT), GENERAL.replace("\n\n", "\n\n"))
        self.assertNotIn("Заключение", dt.general_picture(CONTENT))
        self.assertNotIn("Съвет едно", dt.general_picture(CONTENT))

    def test_the_real_snapshot_fixtures_give_a_text_without_the_other_blocks(self):
        for name in ("live_2026_10_18_final_single_snapshot_A.txt", "live_2026_10_18_final_pair_snapshot_A_IVAN_love.txt", "live_2026_10_20_phase32_love_pair_A_IVAN.txt"):
            text = fixture(name)
            picture = dt.general_picture(text)
            self.assertTrue(picture, name)
            block = text[text.index("## Обща картина"):].split("\n## ")[0].split("\n", 1)[1].strip().replace("**", "")
            self.assertEqual(picture, block, name)
            self.assertNotIn("### ", picture)

    def test_html_content_and_missing_block(self):
        html = "<h2>Карта</h2><p>x</p><h2>Обща картина</h2><p>Обобщение.</p><h2>Заключение</h2><p>Край.</p>"
        self.assertEqual(dt.general_picture(html).replace("\n", ""), "Обобщение.")
        self.assertEqual(dt.general_picture("## Друго\nтекст"), "")
        self.assertEqual(dt.general_picture(""), "")

    def test_the_health_notes_never_enter_the_text(self):
        text = "## Обща картина\nТекст.\n<p class=\"astro-disclaimer\"><i>Бележка</i></p>\n"
        self.assertEqual(dt.general_picture(text), "Текст.")


class DailyTransitFlowTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.client = TestClient(main.app)

    def user(self, email, verified=True, profile=True, daily=True):
        limiter.reset()
        h = register_and_login(self.client, email)
        db = SessionLocal()
        try:
            u = db.query(User).filter(User.email == email).first()
            u.email_verified, u.daily_email = verified, daily
            db.commit()
            uid = u.id
        finally:
            db.close()
        if profile:
            self.assertEqual(self.client.post("/profiles", json=PROFILE, headers=h).status_code, 200)
        return h, uid

    def row(self, uid, day):
        db = SessionLocal()
        try:
            r = db.query(DailyTransit).filter(DailyTransit.user_id == uid, DailyTransit.day == day).first()
            return None if r is None else SimpleNamespace(status=r.status, text=r.text, error=r.error, emailed_at=r.emailed_at, attempts=r.email_attempts)
        finally:
            db.close()

    def test_request_is_for_today_at_noon_without_a_question_and_in_the_general_theme(self):
        db = SessionLocal()
        try:
            h, uid = self.user("dt-req@test.bg")
            request = dt.request_for(dt.primary_profile(db, uid), "2026-11-03")
        finally:
            db.close()
        self.assertEqual((request.report_type, request.target_date, request.target_time, request.forecast_timezone, request.question),
                         ("general", "2026-11-03", "12:00", "Europe/Sofia", None))
        self.assertEqual((request.name, request.gender, request.birth_time_known, request.is_dynamic), ("Мария", "female", True, False))

    def test_no_profile_pending_then_ready(self):
        h, uid = self.user("dt-flow@test.bg", profile=False)
        self.assertEqual(self.client.get("/daily-transit", headers=h).json()["status"], "no_profile")
        self.assertEqual(self.client.post("/profiles", json=PROFILE, headers=h).status_code, 200)
        with mock.patch.object(dt, "start") as started:
            first = self.client.get("/daily-transit", headers=h).json()
        self.assertEqual((first["status"], first["time"]), ("pending", "12:00"))
        started.assert_called_once()
        with mock.patch.object(dt.generation, "run", new=fake_run()):
            run(dt.generate(uid, dt.today()))
        ready = self.client.get("/daily-transit", headers=h).json()
        self.assertEqual((ready["status"], ready["profile"]), ("ready", "Мария"))
        self.assertEqual(ready["text"], GENERAL)

    def test_a_failure_is_recorded_and_the_retry_starts_again(self):
        h, uid = self.user("dt-fail@test.bg")
        async def boom(request, user_id, emit):
            raise RuntimeError("AI не отговаря")
        with mock.patch.object(dt.generation, "run", new=boom):
            run(dt.generate(uid, dt.today()))
        self.assertEqual(self.row(uid, dt.today()).status, "failed")
        self.assertEqual(self.client.get("/daily-transit", headers=h).json()["status"], "failed")
        with mock.patch.object(dt, "start") as started:
            self.client.post("/daily-transit/retry", headers=h)
        started.assert_called_once()

    def test_an_analysis_without_the_block_is_a_failure(self):
        h, uid = self.user("dt-noblock@test.bg")
        with mock.patch.object(dt.generation, "run", new=fake_run("## Нещо друго\nтекст")):
            run(dt.generate(uid, dt.today()))
        self.assertEqual((self.row(uid, dt.today()).status, self.row(uid, dt.today()).error), ("failed", "no_general_picture"))

    def test_the_email_goes_after_eight_only_with_the_general_picture_and_only_once(self):
        h, uid = self.user("dt-mail@test.bg")
        day = "2026-11-03"
        send = mock.AsyncMock(return_value=True)
        with mock.patch.object(dt.generation, "run", new=fake_run()), mock.patch.object(dt.mailer, "send_email", new=send):
            while run(dt.step(datetime(2026, 11, 3, 6, 0))):             # преди 08:00 само се подготвя
                pass
            self.assertEqual(self.row(uid, day).status, "ready")
            before = send.await_count
            self.assertEqual(send.await_count, before)
            while run(dt.step(datetime(2026, 11, 3, 8, 1))):
                pass
            mine = [c for c in send.await_args_list if c.args[0] == "dt-mail@test.bg"]
            self.assertEqual(len(mine), 1)
            to, subject, text, html = mine[0].args
            self.assertEqual(subject, "Вашият ден: 3 ноември 2026")
            self.assertIn("вътрешно напрежение", text)
            self.assertNotIn("Крайно заключение", text + html)
            self.assertNotIn("Съвет едно", text + html)
            self.assertIn("/unsubscribe?token=", text)
            self.assertIsNotNone(self.row(uid, day).emailed_at)
            count = send.await_count
            while run(dt.step(datetime(2026, 11, 3, 9, 0))):
                pass
            self.assertEqual(send.await_count, count)                      # втори път за деня не се праща

    def test_nothing_is_prepared_before_five_and_opt_out_or_unverified_get_no_email(self):
        _, quiet = self.user("dt-quiet@test.bg", daily=False)
        _, raw = self.user("dt-raw@test.bg", verified=False)
        _, early = self.user("dt-early@test.bg")
        day = "2026-11-04"
        send = mock.AsyncMock(return_value=True)
        with mock.patch.object(dt.generation, "run", new=fake_run()), mock.patch.object(dt.mailer, "send_email", new=send):
            self.assertFalse(run(dt.step(datetime(2026, 11, 4, 4, 30))))
            self.assertIsNone(self.row(early, day))
            while run(dt.step(datetime(2026, 11, 4, 9, 0))):
                pass
        self.assertIsNone(self.row(quiet, day))
        self.assertIsNone(self.row(raw, day))
        self.assertEqual(self.row(early, day).status, "ready")
        self.assertEqual([c.args[0] for c in send.await_args_list if c.args[0] in ("dt-quiet@test.bg", "dt-raw@test.bg")], [])

    def test_a_failed_send_is_retried_at_most_three_times(self):
        h, uid = self.user("dt-retry@test.bg")
        day = "2026-11-05"
        send = mock.AsyncMock(return_value=False)
        with mock.patch.object(dt.generation, "run", new=fake_run()), mock.patch.object(dt.mailer, "send_email", new=send):
            for _ in range(8):
                while run(dt.step(datetime(2026, 11, 5, 8, 10))):
                    pass
            mine = [c for c in send.await_args_list if c.args[0] == "dt-retry@test.bg"]
        self.assertEqual(len(mine), dt.MAX_EMAIL_ATTEMPTS)
        self.assertIsNone(self.row(uid, day).emailed_at)

    def test_unsubscribe_link_and_the_setting(self):
        h, uid = self.user("dt-unsub@test.bg")
        self.assertTrue(self.client.get("/me", headers=h).json()["daily_email"])
        db = SessionLocal()
        try:
            token = dt.unsubscribe_token(db.get(User, uid))
        finally:
            db.close()
        self.assertEqual(self.client.post("/daily-transit/unsubscribe", json={"token": "bad"}).status_code, 400)
        r = self.client.post("/daily-transit/unsubscribe", json={"token": token})
        self.assertEqual(r.status_code, 200, r.text)
        self.assertFalse(self.client.get("/me", headers=h).json()["daily_email"])
        self.assertTrue(self.client.patch("/me", json={"daily_email": True}, headers=h).json()["daily_email"])

    def test_old_rows_are_purged_and_the_test_email_needs_a_ready_analysis(self):
        h, uid = self.user("dt-old@test.bg")
        db = SessionLocal()
        try:
            db.add(DailyTransit(user_id=uid, day="2020-01-01", status="ready", text="x"))
            db.commit()
            self.assertGreaterEqual(dt.purge_old(db), 1)
        finally:
            db.close()
        with mock.patch.object(dt.mailer, "is_configured", return_value=True):
            r = self.client.post("/daily-transit/test-email", headers=h).json()
        self.assertFalse(r["sent"])

    def test_the_endpoints_need_a_login(self):
        for method, path in (("get", "/daily-transit"), ("post", "/daily-transit/retry"), ("post", "/daily-transit/test-email")):
            self.assertEqual(getattr(self.client, method)(path).status_code, 401, path)

    def test_the_hours_are_eight_and_five(self):
        self.assertEqual((dt.SEND_HOUR, dt.PREPARE_HOUR, dt.ZONE), (8, 5, "Europe/Sofia"))


class DailyScreensTextTest(unittest.TestCase):
    SRC = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), "src")

    def read(self, *parts):
        with open(os.path.join(self.SRC, *parts), encoding="utf-8") as f:
            return f.read()

    def test_dashboard_settings_unsubscribe_and_terms_mention_the_daily_transit(self):
        dash = self.read("pages", "Dashboard.jsx")
        for needle in ("/daily-transit", "Транзити за днес", "Опитай отново", "Пълен транзитен анализ"):
            self.assertIn(needle, dash)
        settings = self.read("pages", "Settings.jsx")
        for needle in ("daily_email", "Изпрати ми тестово писмо сега", "08:00"):
            self.assertIn(needle, settings)
        self.assertIn('path="/unsubscribe"', self.read("App.jsx"))
        self.assertIn("/daily-transit/unsubscribe", self.read("pages", "Unsubscribe.jsx"))
        self.assertIn("Дневен транзит по имейл", self.read("pages", "Legal.jsx"))
        self.assertIn("08:00", self.read("pages", "Start.jsx"))


if __name__ == "__main__":
    unittest.main()
