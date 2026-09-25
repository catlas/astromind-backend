"""
Тестове за Фаза 5: onboarding и безплатното първо прозрение.

Пускане: python -m unittest discover -s tests
"""
import unittest

import testenv  # noqa: F401  (трябва да е преди main)
from fastapi.testclient import TestClient  # noqa: E402

import engine  # noqa: E402
import insights  # noqa: E402
import main  # noqa: E402
from rate_limit import limiter  # noqa: E402
from testenv import register_and_login  # noqa: E402

BIRTH = {"name": "Иван", "birth_date": "1990-05-15", "birth_time": "14:30", "lat": 42.6977, "lon": 23.3219,
         "birth_place": "София"}


class InsightsUnitTest(unittest.TestCase):
    def test_big_three_known_time(self):
        chart = engine.calculate_chart(date="1990-05-15", time="14:30", lat=42.6977, lon=23.3219)
        result = insights.big_three(chart)
        self.assertEqual(result["sun"]["sign_bg"], "Телец")
        self.assertEqual(result["ascendant"]["sign_bg"], "Дева")
        self.assertTrue(result["moon"]["text"])
        self.assertEqual(sum(e["count"] for e in result["elements"]), 7)

    def test_unknown_time_has_no_ascendant(self):
        chart = engine.calculate_chart(date="1990-05-15", time="12:00", lat=42.6977, lon=23.3219)
        result = insights.big_three(chart, time_known=False)
        self.assertIsNone(result["ascendant"])
        self.assertIn("Асцендента", result["note"])


class OnboardingApiTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.client = TestClient(main.app)

    def setUp(self):
        limiter.reset()

    def test_new_user_onboarding_flow(self):
        h = register_and_login(self.client, "onboard@test.bg")
        self.assertFalse(self.client.get("/me", headers=h).json()["onboarding_completed"])
        self.assertEqual(self.client.get("/insights/big-three", headers=h).status_code, 404)

        r = self.client.post("/onboarding", json=BIRTH, headers=h)
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual(r.json()["sun"]["sign_bg"], "Телец")
        self.assertTrue(r.json()["profile"]["is_primary"])
        self.assertTrue(self.client.get("/me", headers=h).json()["onboarding_completed"])

        again = self.client.get("/insights/big-three", headers=h).json()
        self.assertEqual(again["ascendant"]["sign_bg"], "Дева")
        self.assertEqual(len(self.client.get("/profiles", headers=h).json()), 1)

    def test_invalid_birth_data(self):
        h = register_and_login(self.client, "onboard-bad@test.bg")
        r = self.client.post("/onboarding", json={**BIRTH, "birth_date": "1990-13-45"}, headers=h)
        self.assertEqual(r.status_code, 400)
        self.assertFalse(self.client.get("/me", headers=h).json()["onboarding_completed"])

    def test_skip(self):
        h = register_and_login(self.client, "onboard-skip@test.bg")
        self.assertEqual(self.client.post("/onboarding/skip", headers=h).status_code, 200)
        self.assertTrue(self.client.get("/me", headers=h).json()["onboarding_completed"])


if __name__ == "__main__":
    unittest.main()
