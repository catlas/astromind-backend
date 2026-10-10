"""
Тестове за Фаза 14: най-големи дължини на входа във всички заявки с текст. Прекалено дълъг низ е 422, без да стигне до
хеширане на парола, база или AI.

Пускане: python -m unittest discover -s tests -p "test_input_limits.py"
"""
import unittest

import testenv  # noqa: F401  (трябва да е преди main)
from fastapi.testclient import TestClient  # noqa: E402

import main  # noqa: E402
from rate_limit import limiter  # noqa: E402
from testenv import CHART, register_and_login  # noqa: E402


class LimitsTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.client = TestClient(main.app)

    def setUp(self):
        limiter.reset()

    def refused(self, response, field=None):
        self.assertEqual(response.status_code, 422, response.text[:200])
        if field:
            self.assertIn(field, response.text)

    def test_register_and_login(self):
        ok = {"email": "lim@test.bg", "password": "Zvezdi2026x", "full_name": "Име", "accept_terms": True}
        for field, value in (("email", "a" * 250 + "@x.bg"), ("password", "Zz1" + "x" * 130), ("full_name", "и" * 101)):
            self.refused(self.client.post("/register", json={**ok, field: value}), field)
        self.refused(self.client.post("/login", json={"email": "e" * 300, "password": "x"}), "email")
        self.refused(self.client.post("/login", json={"email": "e@x.bg", "password": "p" * 129}), "password")

    def test_account_endpoints(self):
        h = register_and_login(self.client, "lim2@test.bg")
        self.refused(self.client.patch("/me", json={"full_name": "и" * 101}, headers=h), "full_name")
        self.refused(self.client.patch("/me", json={"email": "a" * 260 + "@x.bg"}, headers=h), "email")
        self.refused(self.client.post("/change-password", json={"current_password": "x" * 129, "new_password": "Zvezdi2026y"}, headers=h))
        self.refused(self.client.post("/forgot-password", json={"email": "a" * 300}))
        self.refused(self.client.post("/verify-email", json={"token": "t" * 2001}))
        self.refused(self.client.post("/reset-password", json={"token": "t" * 2001, "new_password": "Zvezdi2026y"}))
        self.refused(self.client.post("/events", json={"name": "n" * 61}, headers=h))
        self.refused(self.client.post("/billing/checkout", json={"package_id": "p" * 41}, headers=h))

    def test_profiles_notes_and_onboarding(self):
        h = register_and_login(self.client, "lim3@test.bg")
        base = {"name": "Профил", "relation": "self", "birth_date": "1990-05-15", "birth_time": "14:30"}
        for field, value in (("name", "и" * 101), ("relation", "r" * 21), ("birth_date", "1990-05-15" + "0"),
                             ("birth_time", "14:30:00" + "0"), ("birth_place", "м" * 201), ("gender", "g" * 21)):
            self.refused(self.client.post("/profiles", json={**base, field: value}, headers=h), field)
        self.refused(self.client.post("/memory", json={"text": "б" * 501}, headers=h), "text")
        self.refused(self.client.post("/onboarding", json={"name": "И", "birth_date": "1990-05-15", "lat": 1, "lon": 1,
                                                           "gender": "g" * 21}, headers=h), "gender")

    def test_analysis_requests(self):
        h = register_and_login(self.client, "lim4@test.bg")
        for field, value in (("name", "и" * 101), ("question", "в" * 1501), ("partner_name", "и" * 101),
                             ("relationship", "r" * 21), ("gender", "g" * 21)):
            self.refused(self.client.post("/jobs", json={**CHART, field: value}, headers=h), field)
        self.assertEqual(self.client.get("/jobs", headers=h).json()["jobs"], [])


if __name__ == "__main__":
    unittest.main()
