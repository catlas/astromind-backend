"""
Тестове за Фаза 4: проверка за криза, филтър на забранени твърдения,
събития на фунията и админ статистика.

Пускане: python -m unittest discover -s tests
"""
import os
import unittest
from unittest import mock

import testenv  # noqa: F401  (трябва да е преди main)
from fastapi.testclient import TestClient  # noqa: E402

import main  # noqa: E402
import safety  # noqa: E402
from auth import create_purpose_token  # noqa: E402
from database import Event, SessionLocal, User  # noqa: E402
from rate_limit import limiter  # noqa: E402
from testenv import CHART, register_and_login  # noqa: E402


def events_for(email):
    db = SessionLocal()
    try:
        uid = db.query(User.id).filter(User.email == email).scalar()
        return [(e.name, e.props) for e in db.query(Event).filter(Event.user_id == uid).order_by(Event.id).all()]
    finally:
        db.close()


class SafetyUnitTest(unittest.TestCase):
    def test_detect_crisis(self):
        self.assertTrue(safety.detect_crisis("Понякога не искам да живея повече"))
        self.assertTrue(safety.detect_crisis(None, "мисля за САМОУБИЙСТВО"))
        self.assertFalse(safety.detect_crisis("Кога е добре да сменя работата си?"))
        self.assertFalse(safety.detect_crisis(None, ""))

    def test_severe_sentences_removed(self):
        html = "<p>Сатурн носи уроци. Баща ви ще почине през 2027 година. Юпитер помага.</p>"
        clean, flags = safety.check_output(html)
        self.assertIn("death_prediction", flags)
        self.assertNotIn("почине", clean)
        self.assertIn("Сатурн носи уроци.", clean)
        self.assertIn("Юпитер помага.", clean)
        self.assertIn("astro-disclaimer", clean)

    def test_soft_flags_keep_text_and_add_disclaimer(self):
        clean, flags = safety.check_output("<p>Купете акции през март.</p>", "general")
        self.assertEqual(flags, ["investment_advice"])
        self.assertIn("Купете акции", clean)
        self.assertIn("astro-disclaimer", clean)

    def test_clean_text_unchanged(self):
        html = "<p>Венера в Телец подкрепя стабилни връзки.</p>"
        self.assertEqual(safety.check_output(html), (html, []))

    def test_health_always_gets_note(self):
        clean, flags = safety.check_output("<p>Марс дава енергия.</p>", "health")
        self.assertEqual(flags, [])
        self.assertIn("не диагнози", clean)


class SafetyApiTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.client = TestClient(main.app)

    def setUp(self):
        limiter.reset()

    @mock.patch.dict(os.environ, {"COINS_ENFORCED": "1", "SIGNUP_BONUS_COINS": "0"})
    def test_crisis_question_skips_ai_and_charge(self):
        h = register_and_login(self.client, "crisis@test.bg")
        never = mock.AsyncMock(return_value="<p>no</p>")
        with mock.patch.object(main.ai_interpreter, "interpret_chart", never):
            r = self.client.post("/interpret", json={**CHART, "question": "Не искам да живея вече"}, headers=h)
        self.assertEqual(r.status_code, 200, r.text)  # дори с 0 монети
        self.assertIn("112", r.json()["interpretation"])
        self.assertEqual(r.json()["coins_charged"], 0)
        self.assertIsNone(r.json()["report_id"])
        never.assert_not_called()
        self.assertIn("crisis_detected", [n for n, _ in events_for("crisis@test.bg")])

    def test_crisis_in_stream(self):
        h = register_and_login(self.client, "crisis-stream@test.bg")
        body = {**CHART, "is_dynamic": True, "end_date": "2026-03-01", "question": "мисля за самоубийство"}
        r = self.client.post("/interpret-stream", json=body, headers=h)
        self.assertIn('"type": "crisis"', r.text)

    def test_ai_output_is_filtered(self):
        h = register_and_login(self.client, "filter@test.bg")
        bad = mock.AsyncMock(return_value="<p>Добър период. Ще умре ваш близък през май.</p>")
        with mock.patch.object(main.ai_interpreter, "interpret_chart", bad):
            r = self.client.post("/interpret", json=CHART, headers=h)
        self.assertNotIn("умре", r.json()["interpretation"])
        names = [n for n, _ in events_for("filter@test.bg")]
        self.assertIn("ai_output_flagged", names)

    def test_funnel_events(self):
        h = register_and_login(self.client, "funnel@test.bg")
        with mock.patch.object(main.ai_interpreter, "interpret_chart", mock.AsyncMock(return_value="<p>ok</p>")):
            self.client.post("/interpret", json=CHART, headers=h)
            self.client.post("/interpret", json=CHART, headers=h)
        ev = events_for("funnel@test.bg")
        names = [n for n, _ in ev]
        self.assertEqual(names[:2], ["register", "login"])
        completed = [p for n, p in ev if n == "analysis_completed"]
        self.assertEqual([p["first"] for p in completed], [True, False])

    def test_client_events_allowlist(self):
        h = register_and_login(self.client, "client-ev@test.bg")
        self.assertEqual(self.client.post("/events", json={"name": "pricing_viewed"}, headers=h).status_code, 200)
        self.assertEqual(self.client.post("/events", json={"name": "hack"}, headers=h).status_code, 400)
        self.assertEqual(self.client.post("/events", json={"name": "pricing_viewed"}).status_code, 401)

    def test_admin_metrics(self):
        h = register_and_login(self.client, "boss@test.bg")
        self.assertEqual(self.client.get("/admin/metrics", headers=h).status_code, 403)
        with mock.patch.dict(os.environ, {"ADMIN_EMAILS": "boss@test.bg"}):
            # администраторът трябва да има потвърден имейл
            self.assertEqual(self.client.get("/admin/metrics", headers=h).status_code, 403)
            db = SessionLocal()
            user = db.query(User).filter(User.email == "boss@test.bg").first()
            token = create_purpose_token("verify", user)
            db.close()
            self.client.post("/verify-email", json={"token": token})
            r = self.client.get("/admin/metrics", headers=h)
            self.assertEqual(r.status_code, 200)
            self.assertEqual(r.json()["funnel"][0]["step"], "Регистрирани")
            self.assertTrue(self.client.get("/me", headers=h).json()["is_admin"])


if __name__ == "__main__":
    unittest.main()
