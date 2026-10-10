"""
Тестове за Фаза 14: достъп между два акаунта. Акаунт A не вижда и не променя нищо на акаунт B, като познава или налучква
номера: профили, отчети, износ, задачи, бележки в паметта, регистър на баланса, износ на данните и администраторските точки.
Липсващият и чуждият обект са неразличими (404), за да не се разкрива какво съществува.

Пускане: python -m unittest discover -s tests -p "test_isolation.py"
"""
import json
import unittest
from unittest import mock

import testenv  # noqa: F401  (трябва да е преди main)
from fastapi.testclient import TestClient  # noqa: E402

import main  # noqa: E402
from rate_limit import limiter  # noqa: E402
from testenv import CHART, give_deposit, register_and_login  # noqa: E402

SECRET_NAME = "ТАЙНОБ"          # низ, който трябва да остане само при B


class TwoAccountsTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.client = TestClient(main.app)
        limiter.reset()
        cls.a = register_and_login(cls.client, "iso-a@test.bg", name="Акаунт А")
        cls.b = register_and_login(cls.client, "iso-b@test.bg", name=f"Акаунт {SECRET_NAME}")
        give_deposit("iso-b@test.bg", 500)
        call = cls.client.post
        cls.b_profile = call("/profiles", headers=cls.b, json={
            "name": f"{SECRET_NAME} профил", "relation": "friend", "birth_date": "1990-05-15", "birth_time": "14:30",
            "birth_place": "София", "lat": 42.6977, "lon": 23.3219}).json()["id"]
        cls.b_note = call("/memory", headers=cls.b, json={"text": f"{SECRET_NAME} бележка"}).json()["id"]
        fake = mock.AsyncMock(return_value="<p>секретен текст</p>")
        with mock.patch.object(main.ai_interpreter, "interpret_chart", fake):
            job = call("/jobs?wait=60", headers=cls.b, json={**CHART, "name": SECRET_NAME,
                                                             "question": "лична тема"}).json()["job"]
        cls.b_job, cls.b_report = job["id"], job["report_id"]
        cls.a_profile = call("/profiles", headers=cls.a, json={
            "name": "Мой профил", "relation": "self", "birth_date": "1985-07-12", "birth_time": "18:45",
            "birth_place": "Варна", "lat": 43.2141, "lon": 27.9147}).json()["id"]

    def setUp(self):
        limiter.reset()

    def get(self, path, who=None, **kw):
        return self.client.get(path, headers=who or self.a, **kw)

    # ------------------------------------------------------------------ профили
    def test_profiles(self):
        self.assertEqual(self.get(f"/profiles/{self.b_profile}").status_code, 405)          # няма GET по номер
        r = self.client.put(f"/profiles/{self.b_profile}", headers=self.a, json={
            "name": "Присвоен", "relation": "self", "birth_date": "2000-01-01"})
        self.assertEqual(r.status_code, 404)
        self.assertEqual(self.client.delete(f"/profiles/{self.b_profile}", headers=self.a).status_code, 404)
        mine = self.get("/profiles").json()
        self.assertEqual([p["name"] for p in mine], ["Мой профил"])
        theirs = self.get("/profiles", self.b).json()
        self.assertEqual([p["name"] for p in theirs], [f"{SECRET_NAME} профил"])            # непокътнат

    def test_upsert_and_import_never_touch_another_account(self):
        same_name = {"name": f"{SECRET_NAME} профил", "relation": "self", "birth_date": "2001-02-03", "birth_time": "01:00"}
        r = self.client.put("/profiles/upsert", headers=self.a, json=same_name)
        self.assertEqual(r.status_code, 200)
        self.assertNotEqual(r.json()["id"], self.b_profile)
        self.client.post("/profiles/import", headers=self.a, json={"profiles": [dict(same_name, birth_date="2002-02-02")]})
        b_now = self.get("/profiles", self.b).json()
        self.assertEqual((len(b_now), b_now[0]["birth_date"]), (1, "1990-05-15"))
        self.client.delete(f"/profiles/{r.json()['id']}", headers=self.a)

    # ------------------------------------------------------------------ отчети и износ
    def test_reports_and_export(self):
        rid = self.b_report
        for fmt in ("docx", "md"):
            self.assertEqual(self.get(f"/reports/{rid}/export?format={fmt}").status_code, 404)
        self.assertEqual(self.get(f"/reports/{rid}").status_code, 404)
        self.assertEqual(self.client.delete(f"/reports/{rid}", headers=self.a).status_code, 404)
        self.assertNotIn(rid, [r["id"] for r in self.get("/reports").json()])
        self.assertNotIn(SECRET_NAME, self.get("/reports").text + self.get("/timeline").text)
        self.assertEqual(self.get(f"/reports/{rid}", self.b).status_code, 200)             # при B си е там

    def test_enumerating_ids_finds_nothing_of_the_other_account(self):
        for number in range(1, 40):
            for path in (f"/reports/{number}", f"/jobs/{number}", f"/reports/{number}/export?format=md"):
                r = self.get(path)
                if r.status_code == 200:
                    body = r.text
                    self.assertNotIn(SECRET_NAME, body, path)
                    self.assertNotIn("секретен текст", body, path)

    # ------------------------------------------------------------------ задачи
    def test_jobs(self):
        self.assertEqual(self.get(f"/jobs/{self.b_job}").status_code, 404)
        self.assertEqual(self.client.post(f"/jobs/{self.b_job}/cancel", headers=self.a).status_code, 404)
        self.assertNotIn(self.b_job, [j["id"] for j in self.get("/jobs").json()["jobs"]])
        self.assertEqual(self.get(f"/jobs/{self.b_job}", self.b).json()["job"]["status"], "succeeded")

    def test_the_same_idempotency_key_in_two_accounts_is_two_different_jobs(self):
        fake = mock.AsyncMock(return_value="<p>текст</p>")
        with mock.patch.object(main.ai_interpreter, "interpret_chart", fake):
            first = self.client.post("/jobs?wait=60", headers={**self.a, "Idempotency-Key": "shared-key-1234"}, json=CHART).json()["job"]
            second = self.client.post("/jobs?wait=60", headers={**self.b, "Idempotency-Key": "shared-key-1234"}, json=CHART).json()["job"]
        self.assertNotEqual(first["id"], second["id"])
        self.assertEqual(first["status"], "succeeded")

    def test_profile_numbers_in_a_request_are_not_a_way_to_reach_data(self):
        fake = mock.AsyncMock(return_value="<p>текст</p>")
        with mock.patch.object(main.ai_interpreter, "interpret_chart", fake):
            r = self.client.post("/jobs?wait=60", headers=self.a, json={**CHART, "profile_id": self.b_profile})
        self.assertEqual(r.status_code, 200)
        self.assertNotIn(SECRET_NAME, r.text)

    # ------------------------------------------------------------------ памет
    def test_memory(self):
        r = self.client.put(f"/memory/{self.b_note}", headers=self.a, json={"text": "променено"})
        self.assertEqual(r.status_code, 404)
        self.assertEqual(self.client.delete(f"/memory/{self.b_note}", headers=self.a).status_code, 404)
        self.assertNotIn(SECRET_NAME, self.get("/memory").text)
        self.client.delete("/memory", headers=self.a)
        self.assertIn(SECRET_NAME, self.get("/memory", self.b).text)                         # изчистването на A не пипа B

    # ------------------------------------------------------------------ баланс, износ на данните, администрация
    def test_ledger_and_data_export_contain_only_own_data(self):
        mine = self.get("/billing/transactions").json()["transactions"]
        theirs = self.get("/billing/transactions", self.b).json()["transactions"]
        self.assertTrue(any(t["description"] == "тест: внесени средства" for t in theirs))     # внасянето е при B
        self.assertFalse(any(t["description"] == "тест: внесени средства" for t in mine))
        self.assertEqual({t["id"] for t in mine} & {t["id"] for t in theirs}, set())
        export = self.get("/me/export").text
        for secret in (SECRET_NAME, "iso-b@test.bg", "секретен текст"):
            self.assertNotIn(secret, export)
        self.assertIn(SECRET_NAME, self.get("/me/export", self.b).text)

    def test_admin_and_webhook_are_closed(self):
        self.assertIn(self.get("/admin/metrics").status_code, (401, 403))
        self.assertEqual(self.client.get("/admin/metrics").status_code, 401)
        self.assertIn(self.client.post("/billing/webhook", content=b"{}").status_code, (400, 401, 403, 503))

    def test_deleting_one_account_leaves_the_other(self):
        c = register_and_login(self.client, "iso-c@test.bg")
        self.client.post("/profiles", headers=c, json={"name": "К", "relation": "self", "birth_date": "1999-09-09"})
        self.assertEqual(self.client.request("DELETE", "/me", headers=c, json={"password": "Zvezdi2026x"}).status_code, 200)
        self.assertEqual(len(self.get("/profiles", self.b).json()), 1)
        self.assertEqual(self.get(f"/reports/{self.b_report}", self.b).status_code, 200)


if __name__ == "__main__":
    unittest.main()
