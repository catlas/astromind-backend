"""
Тестове за Фаза 1Б: задължителен вход за AI/DOCX, лимити на заявките,
валидация на имейл и парола при регистрация.

Пускане: python -m unittest discover -s tests
"""
import unittest
from unittest import mock

import testenv  # noqa: F401  (трябва да е преди main)
from fastapi.testclient import TestClient  # noqa: E402

import main  # noqa: E402
from auth import validate_password  # noqa: E402
from database import SessionLocal, User  # noqa: E402
from rate_limit import limiter  # noqa: E402

from testenv import CHART, PASSWORD, analyze  # noqa: E402


class SecurityTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.client = TestClient(main.app)

    def setUp(self):
        limiter.reset()

    def register(self, email, password=PASSWORD, name="Тест"):
        return self.client.post("/register", json={"email": email, "password": password, "full_name": name, "accept_terms": True})

    def token_for(self, email):
        self.assertEqual(self.register(email).status_code, 200)
        r = self.client.post("/login", json={"email": email, "password": PASSWORD})
        self.assertEqual(r.status_code, 200)
        return r.json()["access_token"]

    def test_ai_endpoints_require_login(self):
        self.assertEqual(analyze(self.client, None, CHART).status_code, 401)
        self.assertEqual(analyze(self.client, None, {**CHART, "is_dynamic": True}, stream=True).status_code, 401)
        self.assertEqual(self.client.get("/reports/1/export?format=docx").status_code, 401)
        self.assertEqual(self.client.post("/generate-docx", json={}).status_code, 404)        # старата крайна точка е махната

    def test_ai_rate_limit_per_user(self):
        headers = {"Authorization": f"Bearer {self.token_for('limit@test.bg')}"}
        # Лимитът е 2 анализа на час (testenv). Всяка създадена задача се брои; невалидните заявки не се броят
        with mock.patch.object(main.ai_interpreter, "interpret_chart", mock.AsyncMock(return_value="<p>текст</p>")):
            for _ in range(2):
                self.assertEqual(analyze(self.client, headers, CHART).status_code, 200)
            self.assertEqual(self.client.post("/jobs", json={**CHART, "is_dynamic": True}, headers=headers).status_code, 400)
            r = self.client.post("/jobs", json=CHART, headers=headers)
        self.assertEqual(r.status_code, 429, r.text)
        self.assertIn("Retry-After", r.headers)
        limits = self.client.get("/jobs/limits", headers=headers).json()
        self.assertEqual((limits["hour_remaining"], limits["can_start"]), (0, False))
        self.assertGreater(limits["retry_after_seconds"], 0)

    def test_login_rate_limit(self):
        self.register("brute@test.bg")
        for _ in range(3):
            r = self.client.post("/login", json={"email": "brute@test.bg", "password": "wrong-pass-1"})
            self.assertEqual(r.status_code, 401)
        r = self.client.post("/login", json={"email": "brute@test.bg", "password": PASSWORD})
        self.assertEqual(r.status_code, 429)

    def test_register_rejects_weak_password_and_bad_email(self):
        self.assertEqual(self.register("weak@test.bg", password="123456").status_code, 400)
        self.assertEqual(self.register("weak@test.bg", password="onlyletters").status_code, 400)
        self.assertEqual(self.register("not-an-email", password=PASSWORD).status_code, 400)
        self.assertEqual(self.register("noname@test.bg", name="  ").status_code, 400)

    def test_email_is_case_insensitive(self):
        self.assertEqual(self.register("Mixed@Test.bg").status_code, 200)
        self.assertEqual(self.register("mixed@test.bg").status_code, 400)
        r = self.client.post("/login", json={"email": "MIXED@test.bg", "password": PASSWORD})
        self.assertEqual(r.status_code, 200)

    def test_legacy_mixed_case_account_can_still_log_in(self):
        db = SessionLocal()
        db.add(User(email="Legacy@Test.bg", full_name="Стар", hashed_password=main.hash_password("old")))
        db.commit()
        db.close()
        r = self.client.post("/login", json={"email": "Legacy@Test.bg", "password": "old"})
        self.assertEqual(r.status_code, 200)

    def test_password_rules(self):
        self.assertIsNone(validate_password(PASSWORD, "ivan@test.bg"))
        self.assertIsNotNone(validate_password("aaaaaaaaa1", ""))
        self.assertIsNotNone(validate_password("ivanpetrov1", "ivanpetrov@test.bg"))
        self.assertIsNotNone(validate_password("Абв1" * 20, ""))


if __name__ == "__main__":
    unittest.main()
