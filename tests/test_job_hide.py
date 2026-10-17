"""
Скриване на неуспешни и отказани задачи от История (jobs.hidden, миграция 0016).

- потребителят скрива само СВОИ неуспешни или отказани задачи; успешните и активните не се скриват (409), чужди и несъществуващи са 404;
- скритата задача не е в списъка, но се чете по номер; повторното скриване е безобидно; парите и отчетите не се променят;
- „скрий всички неуспешни“ скрива само неуспешните и отказаните на този потребител;
- миграцията е обратима и старите задачи получават hidden=false.
"""
import unittest
from unittest import mock

import testenv  # noqa: F401  (трябва да е преди main)
import sqlalchemy as sa  # noqa: E402
from alembic import command  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

import main  # noqa: E402
from database import Job, SessionLocal, User  # noqa: E402
from rate_limit import limiter  # noqa: E402
from test_euro_balance_migration import alembic_config, make_engine  # noqa: E402
from testenv import CHART, register_and_login  # noqa: E402


def uid_of(email):
    db = SessionLocal()
    try:
        return db.query(User.id).filter(User.email == email).scalar()
    finally:
        db.close()


def set_status(job_id, status):
    db = SessionLocal()
    try:
        db.query(Job).filter(Job.id == job_id).update({"status": status})
        db.commit()
    finally:
        db.close()


class HideJobsTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.client = TestClient(main.app)

    def setUp(self):
        limiter.reset()

    def failed_job(self, headers, name="Иван"):
        with mock.patch.object(main.ai_interpreter, "interpret_chart", mock.AsyncMock(side_effect=RuntimeError("AI down"))):
            response = self.client.post("/jobs?wait=60", json={**CHART, "name": name}, headers=headers)
        self.assertEqual(response.status_code, 200, response.text)
        job = response.json()["job"]
        self.assertEqual(job["status"], "failed")
        limiter.reset()
        return job["id"]

    def ok_job(self, headers):
        with mock.patch.object(main.ai_interpreter, "interpret_chart", mock.AsyncMock(return_value="<p>Анализ.</p>")):
            response = self.client.post("/jobs?wait=60", json={**CHART, "name": "Мария"}, headers=headers)
        job = response.json()["job"]
        self.assertEqual(job["status"], "succeeded")
        limiter.reset()
        return job["id"]

    def listed(self, headers):
        return {j["id"]: j["status"] for j in self.client.get("/jobs", headers=headers).json()["jobs"]}

    def test_a_failed_job_can_be_hidden_and_disappears_from_the_list_but_can_still_be_read(self):
        h = register_and_login(self.client, "hide-one@test.bg")
        failed = self.failed_job(h)
        self.assertEqual(self.listed(h), {failed: "failed"})
        response = self.client.post(f"/jobs/{failed}/hide", headers=h)
        self.assertEqual((response.status_code, response.json()), (200, {"hidden": True, "id": failed}))
        self.assertEqual(self.listed(h), {})
        self.assertEqual(self.client.get(f"/jobs/{failed}", headers=h).json()["job"]["status"], "failed")           # по номер се чете
        self.assertEqual(self.client.post(f"/jobs/{failed}/hide", headers=h).status_code, 200)                       # безобидно повторение

    def test_a_cancelled_job_can_be_hidden_too(self):
        h = register_and_login(self.client, "hide-cancelled@test.bg")
        failed = self.failed_job(h)
        set_status(failed, "cancelled")
        self.assertEqual(self.client.post(f"/jobs/{failed}/hide", headers=h).status_code, 200)
        self.assertEqual(self.listed(h), {})

    def test_a_succeeded_or_an_active_job_cannot_be_hidden(self):
        h = register_and_login(self.client, "hide-refused@test.bg")
        done = self.ok_job(h)
        response = self.client.post(f"/jobs/{done}/hide", headers=h)
        self.assertEqual(response.status_code, 409)
        self.assertIn("неуспешни или отказани", response.json()["detail"])
        running = self.failed_job(h)
        set_status(running, "running")
        self.assertEqual(self.client.post(f"/jobs/{running}/hide", headers=h).status_code, 409)
        set_status(running, "queued")
        self.assertEqual(self.client.post(f"/jobs/{running}/hide", headers=h).status_code, 409)
        self.assertEqual(set(self.listed(h)), {done, running})                                  # нищо не е скрито
        set_status(running, "failed")                                                            # да не остане активна задача

    def test_another_users_job_and_an_unknown_job_are_not_found_and_stay_visible(self):
        owner = register_and_login(self.client, "hide-owner@test.bg")
        other = register_and_login(self.client, "hide-other@test.bg")
        failed = self.failed_job(owner)
        self.assertEqual(self.client.post(f"/jobs/{failed}/hide", headers=other).status_code, 404)
        self.assertEqual(self.client.post("/jobs/99999999/hide", headers=other).status_code, 404)
        self.assertEqual(self.listed(owner), {failed: "failed"})
        self.assertEqual(self.client.post(f"/jobs/{failed}/hide").status_code, 401)                # без вход

    def test_hide_all_failed_hides_only_the_failed_and_cancelled_jobs_of_this_user(self):
        h = register_and_login(self.client, "hide-all@test.bg")
        other = register_and_login(self.client, "hide-all-other@test.bg")
        first, second = self.failed_job(h), self.failed_job(h, "Втори")
        cancelled = self.failed_job(h, "Трети")
        set_status(cancelled, "cancelled")
        done = self.ok_job(h)
        foreign = self.failed_job(other)
        response = self.client.post("/jobs/hide-failed", headers=h)
        self.assertEqual((response.status_code, response.json()), (200, {"hidden": 3}))
        self.assertEqual(self.listed(h), {done: "succeeded"})
        self.assertEqual(self.listed(other), {foreign: "failed"})                                # чуждата остава
        self.assertEqual(self.client.post("/jobs/hide-failed", headers=h).json(), {"hidden": 0})
        self.assertTrue(first and second)

    def test_hiding_changes_neither_the_balance_nor_the_reports(self):
        h = register_and_login(self.client, "hide-money@test.bg")
        failed = self.failed_job(h)
        before = self.client.get("/me", headers=h).json()
        reports = self.client.get("/reports", headers=h).json()
        self.client.post(f"/jobs/{failed}/hide", headers=h)
        after = self.client.get("/me", headers=h).json()
        self.assertEqual({k: before[k] for k in ("balance_cents", "gift_cents", "paid_cents")},
                         {k: after[k] for k in ("balance_cents", "gift_cents", "paid_cents")})
        self.assertEqual(self.client.get("/reports", headers=h).json(), reports)


class MigrationTest(unittest.TestCase):
    def test_the_hidden_column_is_added_false_for_old_jobs_and_can_be_removed(self):
        db_engine = make_engine("p30")
        with db_engine.begin() as conn:
            command.upgrade(alembic_config(conn), "0015_ai_attempt_ledger")
            conn.execute(sa.text("INSERT INTO users (id, email, hashed_password, full_name) VALUES (1, 'a@mig.bg', 'x', 'Тест')"))
            conn.execute(sa.text("INSERT INTO jobs (id, user_id, kind, sku, status, stage, request, tier, quote_cents, reserved_paid, reserved_gift, "
                                 "charged_cents, events, event_count, attempts) VALUES (1, 1, 'analysis', 'analysis.basic', 'failed', 'done', '{}', "
                                 "'basic', 160, 0, 0, 0, '[]', 0, 1)"))
            command.upgrade(alembic_config(conn), "0016_job_hidden")
            self.assertIn("hidden", {c["name"] for c in sa.inspect(conn).get_columns("jobs")})
            self.assertFalse(bool(conn.execute(sa.text("SELECT hidden FROM jobs WHERE id = 1")).scalar()))          # старата задача е видима
            command.downgrade(alembic_config(conn), "0015_ai_attempt_ledger")
            self.assertNotIn("hidden", {c["name"] for c in sa.inspect(conn).get_columns("jobs")})
            self.assertEqual(conn.execute(sa.text("SELECT status FROM jobs WHERE id = 1")).scalar(), "failed")


if __name__ == "__main__":
    unittest.main()
