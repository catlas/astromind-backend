"""
Тестове за Фаза 2: Настройки (PATCH /me, смяна на парола, изтриване),
забравена парола, потвърждение на имейл, профили и отчети на сървъра.

Пускане: python -m unittest discover -s tests
"""
import unittest
from unittest import mock

import testenv  # noqa: F401  (трябва да е преди main)
from fastapi.testclient import TestClient  # noqa: E402

import main  # noqa: E402
from auth import create_purpose_token  # noqa: E402
from database import Profile, Report, SessionLocal, User  # noqa: E402
from rate_limit import limiter  # noqa: E402
from testenv import CHART, PASSWORD, register_and_login  # noqa: E402


def _user(email):
    db = SessionLocal()
    try:
        u = db.query(User).filter(User.email == email).first()
        db.expunge(u)
        return u
    finally:
        db.close()


class AccountTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.client = TestClient(main.app)

    def setUp(self):
        limiter.reset()

    def test_me_and_update_name_and_email(self):
        h = register_and_login(self.client, "acc1@test.bg")
        me = self.client.get("/me", headers=h).json()
        self.assertEqual(me["email"], "acc1@test.bg")
        self.assertFalse(me["email_verified"])

        r = self.client.patch("/me", json={"full_name": "Ново Име", "email": "ACC1-new@test.bg"}, headers=h)
        self.assertEqual(r.status_code, 200, r.text)
        body = r.json()
        self.assertEqual(body["email"], "acc1-new@test.bg")
        new_h = {"Authorization": f"Bearer {body['access_token']}"}
        self.assertEqual(self.client.get("/me", headers=new_h).json()["full_name"], "Ново Име")

    def test_update_email_rejects_taken(self):
        register_and_login(self.client, "taken@test.bg")
        h = register_and_login(self.client, "other@test.bg")
        r = self.client.patch("/me", json={"email": "Taken@test.bg"}, headers=h)
        self.assertEqual(r.status_code, 400)

    def test_change_password_invalidates_old_sessions(self):
        h = register_and_login(self.client, "pw@test.bg")
        bad = self.client.post("/change-password", json={"current_password": "wrong", "new_password": "Novaparola123"}, headers=h)
        self.assertEqual(bad.status_code, 400)
        weak = self.client.post("/change-password", json={"current_password": PASSWORD, "new_password": "123"}, headers=h)
        self.assertEqual(weak.status_code, 400)
        r = self.client.post("/change-password", json={"current_password": PASSWORD, "new_password": "Novaparola123"}, headers=h)
        self.assertEqual(r.status_code, 200)
        self.assertEqual(self.client.get("/me", headers=h).status_code, 401)  # старата сесия
        new_h = {"Authorization": f"Bearer {r.json()['access_token']}"}
        self.assertEqual(self.client.get("/me", headers=new_h).status_code, 200)
        login = self.client.post("/login", json={"email": "pw@test.bg", "password": "Novaparola123"})
        self.assertEqual(login.status_code, 200)

    def test_delete_account_removes_data(self):
        h = register_and_login(self.client, "del@test.bg")
        self.client.post("/profiles", json={"name": "Аз", "birth_date": "1990-05-15"}, headers=h)
        uid = _user("del@test.bg").id
        r = self.client.delete("/me", headers=h)
        self.assertEqual(r.status_code, 200)
        db = SessionLocal()
        try:
            self.assertIsNone(db.get(User, uid))
            self.assertEqual(db.query(Profile).filter(Profile.user_id == uid).count(), 0)
        finally:
            db.close()
        self.assertEqual(self.client.get("/me", headers=h).status_code, 401)

    def test_export(self):
        h = register_and_login(self.client, "exp@test.bg")
        self.client.post("/profiles", json={"name": "Аз", "birth_date": "1990-05-15"}, headers=h)
        data = self.client.get("/me/export", headers=h).json()
        self.assertEqual(data["user"]["email"], "exp@test.bg")
        self.assertNotIn("hashed_password", data["user"])
        self.assertEqual(len(data["profiles"]), 1)

    def test_forgot_and_reset_password(self):
        register_and_login(self.client, "reset@test.bg")
        r = self.client.post("/forgot-password", json={"email": "reset@test.bg"})
        self.assertEqual(r.status_code, 200)
        unknown = self.client.post("/forgot-password", json={"email": "nobody@test.bg"})
        self.assertEqual(unknown.json(), r.json())  # не издава дали имейлът съществува

        token = create_purpose_token("reset", _user("reset@test.bg"))
        ok = self.client.post("/reset-password", json={"token": token, "new_password": "Novaparola456"})
        self.assertEqual(ok.status_code, 200, ok.text)
        again = self.client.post("/reset-password", json={"token": token, "new_password": "Drugaparola123"})
        self.assertEqual(again.status_code, 400)  # линкът е еднократен
        self.assertEqual(self.client.post("/login", json={"email": "reset@test.bg", "password": "Novaparola456"}).status_code, 200)

    def test_purpose_token_is_not_a_login_token(self):
        register_and_login(self.client, "purpose@test.bg")
        token = create_purpose_token("reset", _user("purpose@test.bg"))
        self.assertEqual(self.client.get("/me", headers={"Authorization": f"Bearer {token}"}).status_code, 401)

    def test_verify_email(self):
        h = register_and_login(self.client, "verify@test.bg")
        token = create_purpose_token("verify", _user("verify@test.bg"))
        self.assertEqual(self.client.post("/verify-email", json={"token": token}).status_code, 200)
        self.assertTrue(self.client.get("/me", headers=h).json()["email_verified"])
        self.assertEqual(self.client.post("/verify-email", json={"token": "garbage"}).status_code, 400)


class ProfilesReportsTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.client = TestClient(main.app)

    def setUp(self):
        limiter.reset()

    def test_profiles_crud_and_isolation(self):
        h = register_and_login(self.client, "prof@test.bg")
        other = register_and_login(self.client, "prof-other@test.bg")
        a = self.client.post("/profiles", json={"name": "Аз", "birth_date": "1990-05-15", "birth_time": "14:30",
                                                "lat": 42.7, "lon": 23.3, "birth_place": "София"}, headers=h).json()
        self.assertTrue(a["is_primary"])
        b = self.client.post("/profiles", json={"name": "Мария", "relation": "partner", "birth_date": "1992-01-02",
                                                "unknown_time": True, "birth_time": "10:00"}, headers=h).json()
        self.assertFalse(b["is_primary"])
        self.assertEqual(b["birth_time"], "")
        dup = self.client.post("/profiles", json={"name": "Аз", "birth_date": "1990-05-15"}, headers=h)
        self.assertEqual(dup.status_code, 400)
        bad = self.client.post("/profiles", json={"name": "X", "birth_date": "15.05.1990"}, headers=h)
        self.assertEqual(bad.status_code, 400)

        upd = self.client.put(f"/profiles/{b['id']}", json={"name": "Мария", "relation": "partner",
                                                            "birth_date": "1992-01-02", "is_primary": True}, headers=h)
        self.assertTrue(upd.json()["is_primary"])
        names = [(p["name"], p["is_primary"]) for p in self.client.get("/profiles", headers=h).json()]
        self.assertEqual(names, [("Мария", True), ("Аз", False)])

        self.assertEqual(self.client.get("/profiles", headers=other).json(), [])
        self.assertEqual(self.client.delete(f"/profiles/{a['id']}", headers=other).status_code, 404)
        self.assertEqual(self.client.delete(f"/profiles/{b['id']}", headers=h).status_code, 200)
        remaining = self.client.get("/profiles", headers=h).json()
        self.assertEqual([(p["name"], p["is_primary"]) for p in remaining], [("Аз", True)])

    def test_upsert_keeps_settings(self):
        h = register_and_login(self.client, "upsert@test.bg")
        body = {"name": "Аз", "birth_date": "1990-05-15", "settings": {"enableTransit": True}}
        first = self.client.put("/profiles/upsert", json=body, headers=h).json()
        second = self.client.put("/profiles/upsert", json={**body, "birth_time": "08:15"}, headers=h).json()
        self.assertEqual(first["id"], second["id"])
        self.assertEqual(second["birth_time"], "08:15")
        self.assertEqual(second["settings"], {"enableTransit": True})

    def test_import_from_browser(self):
        h = register_and_login(self.client, "import@test.bg")
        r = self.client.post("/profiles/import", json={"profiles": [
            {"name": "Аз", "birth_date": "1990-05-15", "birth_time": "14:30"},
            {"name": "Счупен", "birth_date": "не е дата"},
        ]}, headers=h)
        self.assertEqual(r.json()["imported"], 1)
        r = self.client.post("/reports/import", json={"reports": [
            {"type": "career", "label": "Кариера", "profile": "Аз", "content": "<p>текст</p>", "date": "2026-01-10"},
            {"type": "general", "label": "Празен", "content": ""},
        ]}, headers=h)
        self.assertEqual(r.json()["imported"], 1)
        reports = self.client.get("/reports", headers=h).json()
        self.assertEqual(reports[0]["created_at"][:10], "2026-01-10")

    def test_interpret_saves_report(self):
        h = register_and_login(self.client, "report@test.bg")
        fake = mock.AsyncMock(return_value="<p>Тестов анализ</p>")
        with mock.patch.object(main.ai_interpreter, "interpret_chart", fake):
            r = self.client.post("/interpret", json={**CHART, "name": "Аз", "report_type": "career"}, headers=h)
        self.assertEqual(r.status_code, 200, r.text)
        report_id = r.json()["report_id"]
        listed = self.client.get("/reports", headers=h).json()
        self.assertEqual(listed[0]["id"], report_id)
        self.assertEqual(listed[0]["type"], "career")
        self.assertNotIn("content", listed[0])
        full = self.client.get(f"/reports/{report_id}", headers=h).json()
        self.assertEqual(full["content"], "<p>Тестов анализ</p>")

        other = register_and_login(self.client, "report-other@test.bg")
        self.assertEqual(self.client.get(f"/reports/{report_id}", headers=other).status_code, 404)
        self.assertEqual(self.client.delete(f"/reports/{report_id}", headers=h).status_code, 200)
        self.assertEqual(self.client.get("/reports", headers=h).json(), [])

    def test_stream_saves_report(self):
        h = register_and_login(self.client, "stream@test.bg")
        fake = mock.AsyncMock(return_value="<p>Месец</p>")
        body = {**CHART, "name": "Аз", "is_dynamic": True, "target_date": "2026-01-01", "end_date": "2026-02-28"}
        with mock.patch.object(main.ai_interpreter, "_process_monthly_chunk", fake):
            r = self.client.post("/interpret-stream", json=body, headers=h)
        self.assertEqual(r.status_code, 200)
        self.assertIn('"type": "complete"', r.text)
        reports = self.client.get("/reports", headers=h).json()
        self.assertEqual(len(reports), 1)
        self.assertTrue(reports[0]["label"].startswith("Прогноза"))
        db = SessionLocal()
        try:
            self.assertIn("<p>Месец</p>", db.get(Report, reports[0]["id"]).content)
        finally:
            db.close()


if __name__ == "__main__":
    unittest.main()
