"""
Тестове за Фаза 7: приемане на условията и ценови тестове.

Пускане: python -m unittest discover -s tests
"""
import json
import os
import unittest
from unittest import mock

import testenv  # noqa: F401  (трябва да е преди main)
from fastapi.testclient import TestClient  # noqa: E402

import account_api  # noqa: E402
import billing  # noqa: E402
import main  # noqa: E402
from database import SessionLocal, User  # noqa: E402
from rate_limit import limiter  # noqa: E402
from testenv import PASSWORD, register_and_login  # noqa: E402

EXPERIMENT = json.dumps({
    "A": [{"id": "starter", "coins": 50, "amount_cents": 499}],
    "B": [{"id": "starter", "coins": 60, "amount_cents": 599}],
})


class TermsTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.client = TestClient(main.app)

    def setUp(self):
        limiter.reset()

    def test_register_requires_terms(self):
        r = self.client.post("/register", json={"email": "noterms@test.bg", "password": PASSWORD, "full_name": "X"})
        self.assertEqual(r.status_code, 400)
        self.assertIn("Общите условия", r.json()["detail"])

    def test_new_user_has_current_terms(self):
        h = register_and_login(self.client, "terms@test.bg")
        self.assertTrue(self.client.get("/me", headers=h).json()["terms_accepted"])

    def test_legacy_user_accepts_later(self):
        h = register_and_login(self.client, "legacy-terms@test.bg")
        db = SessionLocal()
        u = db.query(User).filter(User.email == "legacy-terms@test.bg").first()
        u.terms_version = None
        db.commit()
        db.close()
        self.assertFalse(self.client.get("/me", headers=h).json()["terms_accepted"])
        r = self.client.post("/accept-terms", headers=h)
        self.assertTrue(r.json()["terms_accepted"])


class PricingExperimentTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.client = TestClient(main.app)

    def setUp(self):
        limiter.reset()

    def test_no_experiment_by_default(self):
        self.assertIsNone(billing.pricing_variant(1))
        self.assertIsNone(self.client.get("/billing/config").json()["pricing_variant"])

    @mock.patch.dict(os.environ, {"PRICING_EXPERIMENT": EXPERIMENT})
    def test_variant_is_stable_and_used(self):
        self.assertEqual(billing.pricing_variant(2), "A")
        self.assertEqual(billing.pricing_variant(3), "B")
        self.assertEqual(billing.pricing_variant(3), "B")
        self.assertIsNone(self.client.get("/billing/config").json()["pricing_variant"])  # без вход

        h = register_and_login(self.client, "pricing@test.bg")
        cfg = self.client.get("/billing/config", headers=h).json()
        uid = self.client.get("/me", headers=h).json()["id"]
        expected = "A" if uid % 2 == 0 else "B"
        self.assertEqual(cfg["pricing_variant"], expected)
        self.assertEqual(cfg["packages"][0]["amount_cents"], 499 if expected == "A" else 599)
        self.assertEqual(billing.find_package("starter", uid)["amount_cents"], cfg["packages"][0]["amount_cents"])


if __name__ == "__main__":
    unittest.main()
