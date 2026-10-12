"""
Анализ „за дата“ за двама през истинските задачи (POST /jobs): пълният се таксува, резервният се таксува и има бележка,
частичният НЕ се таксува (сумата се връща), отказаният не се записва. AI е подменен със сценарий по идентификаторите на картите.
"""
import os
import re
import unittest
from unittest import mock

import testenv  # noqa: F401  (трябва да е преди main)
from fastapi.testclient import TestClient  # noqa: E402

import billing  # noqa: E402
import main  # noqa: E402
from database import SessionLocal, User  # noqa: E402
from rate_limit import limiter  # noqa: E402
from testenv import register_and_login  # noqa: E402

GOOD = "Това напрежение изисква търпение и ясни граници в разговорите в този ден."
WRONG = "Транзитното Слънце е в тригон с наталната Венера на Иван, затова в този ден всичко върви леко."
SUMMARY = "Денят е напрегнат и за двамата, затова е разумно да се говори бавно и да се изчаква, ако нещо тежи."
ADVICE = "- Говорете спокойно и изчакайте, ако усетите напрежение.\n- Не бързайте с големи решения през този ден."
CONCLUSION = "Денят е подходящ за внимателен разговор и неподходящ за прибързани решения."

BODY = {"date": "1990-02-15", "time": "13:00", "lat": 42.6977, "lon": 23.3219, "name": "Мария", "partner_name": "Иван",
        "partner_date": "1985-07-12", "partner_time": "18:45", "partner_lat": 48.2082, "partner_lon": 16.3738,
        "target_date": "2026-10-14", "target_time": "18:00", "report_type": "general"}


def balances(uid):
    db = SessionLocal()
    try:
        row = db.query(User.paid_cents, User.gift_cents).filter(User.id == uid).one()
        return int(row[0]), int(row[1])
    finally:
        db.close()


def fund(email, paid):
    db = SessionLocal()
    try:
        uid = db.query(User.id).filter(User.email == email).scalar()
        now_paid, now_gift = int(db.query(User.paid_cents).filter(User.id == uid).scalar()), int(db.query(User.gift_cents).filter(User.id == uid).scalar())
        billing.apply_transaction(db, uid, "admin", paid=paid - now_paid, gift=-now_gift, allow_negative=True, description="тест")
        db.commit()
        return uid
    finally:
        db.close()


class Scenario:
    def __init__(self, wrong_cards=(), summary=SUMMARY, compose=None):
        self.wrong, self.summary, self.compose = set(wrong_cards), summary, compose or {}
        self.calls = {"main": 0, "compose": 0, "repair": 0}

    async def __call__(self, system_prompt, user_prompt, max_tokens, **kwargs):
        blocks = {"SUMMARY": self.summary, "ADVICE": ADVICE, "CONCLUSION": CONCLUSION}
        if "COMPOSITION FROM VERIFIED BLOCKS" in system_prompt:
            self.calls["compose"] += 1
            return "\n\n".join(f"[[{k}]]\n{self.compose.get(k, v)}" for k, v in blocks.items())
        if "TWO-PERSON SNAPSHOT for ONE chosen" in system_prompt:
            self.calls["main"] += 1
            ids = re.findall(r"^([ABS]\d): ", user_prompt, re.M)
            parts = [f"[[{i}]]\n{WRONG if i in self.wrong else GOOD}" for i in ids]
            parts += [f"[[{k}]]\n{v}" for k, v in blocks.items()]
            return "\n\n".join(parts)
        self.calls["repair"] += 1
        return WRONG if "наталната Венера на Иван" in user_prompt else user_prompt


class PairSnapshotJobsTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.client = TestClient(main.app)

    def setUp(self):
        limiter.reset()

    def run_job(self, email, scenario):
        headers = register_and_login(self.client, email)
        uid = fund(email, 1000)
        with mock.patch.dict(os.environ, {"BALANCE_ENFORCED": "1"}), mock.patch.object(main.ai_interpreter, "_call_api", scenario):
            response = self.client.post("/jobs?wait=60", json=BODY, headers=headers)
        self.assertEqual(response.status_code, 200, response.text)
        job = response.json()["job"]
        report = None
        if job["report_id"]:
            report = self.client.get(f"/reports/{job['report_id']}", headers=headers).json()
        return job, report, balances(uid)

    def test_a_full_analysis_is_charged_and_has_no_note(self):
        job, report, (paid, gift) = self.run_job("ps-full@test.bg", Scenario())
        self.assertEqual(job["status"], "succeeded")
        self.assertEqual(job["charged_cents"], 180)                         # анализ за двама: 1,80 €
        self.assertEqual(paid, 1000 - 180)
        self.assertNotIn("Частичен анализ", report["content"])
        self.assertNotIn("Резервен анализ", report["content"])
        self.assertNotIn("quality", (report.get("params") or {}))
        self.assertIn("## Транзитите към Иван", report["content"])

    def test_a_fallback_analysis_keeps_every_card_is_charged_and_labelled(self):
        job, report, (paid, _) = self.run_job("ps-fallback@test.bg", Scenario(summary=WRONG, compose={"SUMMARY": SUMMARY}))
        self.assertEqual(job["status"], "succeeded")
        self.assertEqual(job["charged_cents"], 180)
        self.assertEqual(paid, 1000 - 180)
        self.assertTrue(report["content"].startswith("**Резервен анализ.**"))
        self.assertEqual(report["params"]["quality"], "fallback")

    def test_a_partial_analysis_is_not_charged_and_is_labelled(self):
        scenario = Scenario(wrong_cards={"A2"})
        job, report, (paid, gift) = self.run_job("ps-partial@test.bg", scenario)
        self.assertEqual(job["status"], "succeeded")
        self.assertEqual(job["charged_cents"], 0)
        self.assertEqual((paid, gift), (1000, 0))                            # сумата е върната изцяло
        self.assertTrue(report["content"].startswith("**Частичен анализ.**"))
        self.assertIn("не се таксува", report["content"])
        self.assertEqual(report["params"]["quality"], "partial")
        self.assertEqual(scenario.calls["compose"], 1)
        db = SessionLocal()
        try:
            from database import Event
            props = [e.props for e in db.query(Event).filter(Event.name == "analysis_completed").all()]
        finally:
            db.close()
        self.assertTrue(any("partial" in str(p) for p in props))               # статистиката вижда частичните

    def test_a_refused_analysis_is_not_saved_and_not_charged(self):
        scenario = Scenario(wrong_cards={"A1", "A2", "A3", "A4", "A5", "A6"})
        job, report, (paid, gift) = self.run_job("ps-refused@test.bg", scenario)
        self.assertEqual(job["status"], "failed")
        self.assertIsNone(job["report_id"])
        self.assertEqual((paid, gift), (1000, 0))
        self.assertIn("код:", job["error"] if isinstance(job.get("error"), str) else str(job))


if __name__ == "__main__":
    unittest.main()
