"""Табло на собственика (26.10.2026): само за администратор, само четене, без чувствителни колони."""
import os
import unittest
from unittest import mock

import testenv  # noqa: F401  (трябва да е преди main)
from testenv import register_and_login  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

import admin_api  # noqa: E402
import main  # noqa: E402
from database import SessionLocal, User  # noqa: E402


class AdminApiTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.client = TestClient(main.app)
        cls.boss = register_and_login(cls.client, "adm-boss@test.bg")
        cls.user = register_and_login(cls.client, "adm-user@test.bg")
        db = SessionLocal()
        try:
            db.query(User).filter(User.email == "adm-boss@test.bg").update({"email_verified": True})
            db.commit()
        finally:
            db.close()

    def setUp(self):
        patcher = mock.patch.dict(os.environ, {"ADMIN_EMAILS": "adm-boss@test.bg"})
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_it_is_closed_to_everyone_but_the_admin(self):
        for path in ("/admin/overview", "/admin/data", "/admin/data/users"):
            self.assertEqual(self.client.get(path).status_code, 401, path)
            self.assertEqual(self.client.get(path, headers=self.user).status_code, 403, path)
            self.assertEqual(self.client.get(path, headers=self.boss).status_code, 200, path)

    def test_the_overview_has_a_day_for_every_label_and_the_summary(self):
        data = self.client.get("/admin/overview?days=7", headers=self.boss).json()
        self.assertEqual(len(data["labels"]), 7)
        for key in ("registered", "verified", "logins", "ok", "failed", "cancelled", "active"):
            self.assertEqual(len(data["series"][key]), 7, key)
        self.assertEqual(len(data["ai_cost_eur"]), 7)
        self.assertGreaterEqual(data["summary"]["registered"], 2)
        self.assertIn("state", data["ai_budget"])

    def test_the_data_viewer_hides_passwords_texts_and_birth_data(self):
        users = self.client.get("/admin/data/users", headers=self.boss).json()
        self.assertNotIn("hashed_password", users["columns"])
        self.assertIn("email", users["columns"])
        self.assertGreaterEqual(users["total"], 2)
        reports = self.client.get("/admin/data/reports", headers=self.boss).json()
        self.assertNotIn("content", reports["columns"])
        self.assertNotIn("params", reports["columns"])
        jobs = self.client.get("/admin/data/jobs", headers=self.boss).json()
        self.assertNotIn("request", jobs["columns"])
        self.assertNotIn("events", jobs["columns"])
        self.assertEqual(self.client.get("/admin/data/profiles", headers=self.boss).status_code, 404)
        self.assertEqual(self.client.get("/admin/data/nothing", headers=self.boss).status_code, 404)

    def test_paging_and_the_user_filter(self):
        page = self.client.get("/admin/data/events?limit=1&offset=0", headers=self.boss).json()
        self.assertLessEqual(len(page["rows"]), 1)
        mine = self.client.get("/admin/data/users?user_id=999999", headers=self.boss).json()
        self.assertEqual(mine["total"], 0)

    def test_every_listed_table_can_be_read(self):
        for table in self.client.get("/admin/data", headers=self.boss).json():
            self.assertEqual(self.client.get(f"/admin/data/{table['id']}", headers=self.boss).status_code, 200, table["id"])
        self.assertEqual(set(admin_api.TABLES), {"users", "jobs", "reports", "events", "purchases", "balance", "ai_usage"})

    def test_the_separate_admin_login_gives_access_without_a_user_account(self):
        with mock.patch.dict(os.environ, {"ADMIN_PASSWORD": "dalga-parola-123", "ADMIN_USERNAME": "owner"}):
            bad = self.client.post("/admin/login", json={"username": "owner", "password": "greshna-parola"})
            self.assertEqual(bad.status_code, 401)
            ok = self.client.post("/admin/login", json={"username": "owner", "password": "dalga-parola-123"})
            self.assertEqual(ok.status_code, 200, ok.text)
            header = {"Authorization": f"Bearer {ok.json()['token']}"}
            self.assertEqual(self.client.get("/admin/overview", headers=header).status_code, 200)
            self.assertEqual(self.client.get("/admin/metrics", headers=header).status_code, 200)
            self.assertEqual(self.client.get("/admin/data/users", headers=header).status_code, 200)
            self.assertEqual(self.client.get("/me", headers=header).status_code, 401)           # токенът не е потребителски вход

    def test_without_a_configured_password_the_login_is_closed_and_a_user_token_is_not_an_admin(self):
        with mock.patch.dict(os.environ, {"ADMIN_PASSWORD": ""}):
            self.assertEqual(self.client.post("/admin/login", json={"username": "admin", "password": "whatever-long-1"}).status_code, 503)
        with mock.patch.dict(os.environ, {"ADMIN_EMAILS": "", "ADMIN_PASSWORD": "dalga-parola-123"}):
            self.assertEqual(self.client.get("/admin/overview", headers=self.boss).status_code, 403)


if __name__ == "__main__":
    unittest.main()
