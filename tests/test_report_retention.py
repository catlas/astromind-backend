"""Най-много 50 отчета на потребител (20.10.2026): при нов отчет най-старите над границата се трият автоматично."""
import os
import unittest

import testenv  # noqa: F401  (трябва да е преди main)
from testenv import register_and_login  # noqa: E402

import data_api  # noqa: E402
from database import Job, Report, SessionLocal, User  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402
import main  # noqa: E402


class ReportRetentionTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.client = TestClient(main.app)

    def make(self, email, count, keep=None):
        register_and_login(self.client, email)
        db = SessionLocal()
        try:
            user = db.query(User).filter(User.email == email).first()
            ids = []
            for i in range(count):
                r = data_api.save_report(db, user, content=f"текст {i}", report_type="general", profile_name="А", label=f"отчет {i}")
                ids.append(r.id)
            db.commit()
            return user.id, ids
        finally:
            db.close()

    def test_the_default_is_fifty(self):
        self.assertEqual(data_api.MAX_REPORTS_PER_USER, 50)

    def test_the_oldest_are_deleted_and_the_newest_fifty_stay(self):
        user_id, ids = self.make("ret-a@test.bg", 53)
        db = SessionLocal()
        try:
            left = [r.id for r in db.query(Report).filter(Report.user_id == user_id).order_by(Report.id)]
            self.assertEqual(len(left), 50)
            self.assertEqual(left, ids[3:])
        finally:
            db.close()

    def test_another_users_reports_are_not_touched(self):
        _, other = self.make("ret-b@test.bg", 3)
        self.make("ret-c@test.bg", 52)
        db = SessionLocal()
        try:
            self.assertEqual(db.query(Report).filter(Report.id.in_(other)).count(), 3)
        finally:
            db.close()

    def test_a_job_that_pointed_to_a_deleted_report_loses_the_pointer(self):
        user_id, ids = self.make("ret-d@test.bg", 2)
        db = SessionLocal()
        try:
            job = Job(user_id=user_id, kind="analysis", status="succeeded", request={}, report_id=ids[0])
            db.add(job)
            db.commit()
            self.assertEqual(data_api.prune_reports(db, user_id, keep=1), 1)
            db.commit()
            db.refresh(job)
            self.assertIsNone(job.report_id)
            self.assertEqual(db.query(Report).filter(Report.user_id == user_id).count(), 1)
        finally:
            db.close()


class PricesAndNoticesTextTest(unittest.TestCase):
    """Цените още не са обявени („(предстои)“), лимитите по план и бележката за 50-те анализа са в интерфейса и в условията."""
    SRC = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), "src")

    def read(self, *parts):
        with open(os.path.join(self.SRC, *parts), encoding="utf-8") as f:
            return f.read()

    def test_no_page_shows_a_service_price_and_the_plan_limits_are_written(self):
        for page in ("Home.jsx", "Balance.jsx", "Settings.jsx"):
            text = self.read("pages", page)
            self.assertNotIn("formatEur(prices.basic_analysis)", text, page)
            self.assertNotIn("formatEur(prices.pair_analysis)", text, page)
            self.assertNotIn("formatEur(prices.forecast_month)", text, page)
            self.assertNotIn("eur(prices.basic_analysis)", text, page)
            self.assertIn("PRICE_SOON", text, page)
        self.assertIn("(предстои)", self.read("utils", "money.js"))
        plans = self.read("components", "PlanLimits.jsx")
        for needle in ("1 хороскоп на ден", "Платен 10 €", "до 5 хороскопа на час и до 10 на ден", "Платен 20 €", "до 20 хороскопа на час и до 60 на ден"):
            self.assertIn(needle, plans)

    def test_the_fifty_report_rule_is_written_in_history_and_in_the_terms(self):
        self.assertIn("последните 50 анализа", self.read("pages", "History.jsx"))
        legal = self.read("pages", "Legal.jsx")
        self.assertEqual(legal.count("последните 50 анализа"), 2)

    def test_the_date_and_time_icons_are_white(self):
        css = self.read("index.css")
        self.assertIn('input[type="date"]', css)
        self.assertIn("color-scheme: dark", css)


if __name__ == "__main__":
    unittest.main()
