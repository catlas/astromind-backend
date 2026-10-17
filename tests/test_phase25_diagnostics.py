"""
Фаза 25, R16 (регресионен одит 11.10.2026): диагностика на остатъчните AI откази.

Всеки анализ (успешен или не) оставя събитие analysis_diagnostics: етап (категория), референтен код, време, опити, доставчици и
токени. Потребителят вижда съобщение с кода; операторът намира причината. Без текст на анализа, рождени данни, ключове и токени.
"""
import os
import re
import unittest
from types import SimpleNamespace
from unittest import mock

import testenv  # noqa: F401  (трябва да е преди main)
from fastapi.testclient import TestClient  # noqa: E402

import ai_budget  # noqa: E402
import data_api  # noqa: E402
import generation  # noqa: E402
import main  # noqa: E402
import text_check  # noqa: E402
import text_guard  # noqa: E402
from database import Event, Job, Report, SessionLocal, User  # noqa: E402
from rate_limit import limiter  # noqa: E402
from test_phase21_limits_budget import ProviderCallTest, clean  # noqa: E402
from testenv import CHART, register_and_login  # noqa: E402


def diagnostics(email):
    db = SessionLocal()
    try:
        uid = db.query(User.id).filter(User.email == email).scalar()
        return [e.props for e in db.query(Event).filter(Event.user_id == uid, Event.name == "analysis_diagnostics").order_by(Event.id).all()]
    finally:
        db.close()


class CategoryTest(unittest.TestCase):
    def failure(self, code, stage="", cause=None):
        return generation.GenerationFailure(code, "съобщение", stage=stage, cause=cause)

    def test_every_stage_has_its_category(self):
        outcome = text_guard.GuardOutcome(stage="month:2026-10", mode="enforce", text="", rejected=True, reason="violations_remain")
        check_error = text_guard.TextCheckError("month:2026-10", outcome)
        cases = [
            (self.failure("invalid_input"), "validation"), (self.failure("no_events"), "validation"),
            (self.failure("ephemeris_incomplete"), "ephemeris"), (self.failure("timeout"), "timeout"),
            (self.failure("stopped", cause=ai_budget.BudgetExceeded("daily")), "budget"),
            (self.failure("text_check", "natal", check_error), "fact_check"),
            (self.failure("forecast_failed", "month:2026-10", check_error), "fact_check"),
            (self.failure("forecast_failed", "final:overview", check_error), "assembly"),
            (self.failure("internal", "persistence"), "persistence"),
            (self.failure("forecast_failed", "month:2026-10", RuntimeError("Грешка при комуникация с AI провайдърите")), "provider"),
            (self.failure("internal"), "provider"),
        ]
        for failure, expected in cases:
            self.assertEqual(generation.failure_category(failure), expected, msg=(failure.code, failure.stage))
        self.assertEqual({c for _, c in cases}, set(generation.FAILURE_CATEGORIES))


class AttemptLogTest(unittest.TestCase):
    def setUp(self):
        clean()
        self.addCleanup(clean)

    def test_the_provider_attempts_are_summarised_without_texts(self):
        import httpx
        harness = ProviderCallTest("test_a_successful_call_holds_one_reservation_and_settles_it")
        log: list = []
        token = ai_budget.attempt_log.set(log)
        try:
            harness.run_call([httpx.ReadTimeout("бавно"), 503, dict(ProviderCallTest.GOOD)])
        finally:
            ai_budget.attempt_log.reset(token)
        self.assertEqual([a["outcome"] for a in log], ["timeout", "http_error", "ok"])
        self.assertEqual([a["provider"] for a in log], ["ollama", "ollama", "together"])
        summary = ai_budget.summarize_attempts(log)
        self.assertEqual((summary["attempts"], summary["providers"], summary["last"]), (3, "ollama,together", "ok"))
        self.assertEqual((summary["prompt_tokens"], summary["completion_tokens"]), (10, 50))
        self.assertNotIn("отговор", str(log))                                          # няма текст на отговора
        self.assertEqual(ai_budget.summarize_attempts(None)["attempts"], 0)


class JobDiagnosticsTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.client = TestClient(main.app)

    def setUp(self):
        limiter.reset()

    def post(self, email, body=None, patches=()):
        headers = register_and_login(self.client, email)
        for patch in patches:
            patch.start()
        try:
            response = self.client.post("/jobs?wait=60", json=body or {**CHART, "name": "Иван"}, headers=headers)
        finally:
            for patch in patches:
                patch.stop()
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()["job"]

    def reference(self, job):
        match = re.search(r"\(код: ([0-9a-f]{8})\)", job["error"]["message"])
        self.assertTrue(match, job["error"]["message"])
        return match.group(1)

    def test_a_provider_failure_is_categorised_and_the_code_matches(self):
        broken = mock.AsyncMock(side_effect=RuntimeError("Грешка при комуникация с AI провайдърите: Иван 1985"))
        job = self.post("r16-provider@test.bg", patches=[mock.patch.object(main.ai_interpreter, "interpret_chart", broken)])
        self.assertEqual(job["status"], "failed")
        events = diagnostics("r16-provider@test.bg")
        self.assertEqual(len(events), 1)
        self.assertEqual((events[0]["outcome"], events[0]["category"]), ("failed", "provider"))
        self.assertEqual(events[0]["ref"], self.reference(job))
        self.assertIsNotNone(events[0]["latency_ms"])
        self.assertNotIn("Иван", str(events[0]))                                        # без лични данни от текста на грешката

    def test_a_fact_check_refusal_is_categorised(self):
        with mock.patch.object(text_check, "build_facts", side_effect=KeyError("карта")):
            job = self.post("r16-facts@test.bg", patches=[mock.patch.object(main.ai_interpreter, "_call_api", mock.AsyncMock(return_value="x"))])
        events = diagnostics("r16-facts@test.bg")
        self.assertEqual((events[0]["category"], events[0]["stage"]), ("fact_check", "natal"))
        self.assertEqual(events[0]["ref"], self.reference(job))
        self.assertEqual(events[0]["provider_calls"], None if events[0]["provider_calls"] is None else 0)

    def test_a_failure_while_saving_is_persistence_and_nothing_is_kept_or_charged(self):
        ok = mock.AsyncMock(return_value="<p>Анализ.</p>")
        with mock.patch.object(data_api, "save_report", side_effect=RuntimeError("базата не отговаря")):
            job = self.post("r16-persist@test.bg", patches=[mock.patch.object(main.ai_interpreter, "interpret_chart", ok)])
        self.assertEqual((job["status"], job["error"]["code"]), ("failed", "internal"))
        events = diagnostics("r16-persist@test.bg")
        self.assertEqual((events[0]["category"], events[0]["stage"]), ("persistence", "persistence"))
        self.assertEqual(events[0]["ref"], self.reference(job))
        db = SessionLocal()
        try:
            uid = db.query(User.id).filter(User.email == "r16-persist@test.bg").scalar()
            self.assertEqual(db.query(Report).filter(Report.user_id == uid).count(), 0)
        finally:
            db.close()

    def test_a_success_leaves_a_diagnostic_with_the_provider_usage(self):
        async def call(system_prompt, user_prompt, max_tokens, **kwargs):
            ai_budget.note_attempt("ollama", "ok", 0.0, 100, 50, model="тестов-модел")
            return testenv.pair_reply(user_prompt)                       # натален анализ за един човек е по карти: една заявка
        patches = [mock.patch.object(main.ai_interpreter, "_call_api", call),
                   mock.patch.object(main.ai_interpreter, "build_text_facts", return_value=None)]
        job = self.post("r16-ok@test.bg", patches=patches)
        self.assertEqual(job["status"], "succeeded")
        event = diagnostics("r16-ok@test.bg")[0]
        self.assertEqual((event["outcome"], event["stage"], event["provider_calls"], event["tokens"], event["providers"]),
                         ("succeeded", "done", 1, 150, "ollama"))
        self.assertEqual(event["model"], "тестов-модел")

    def test_a_validation_refusal_is_not_an_analysis_failure_event(self):
        headers = register_and_login(self.client, "r16-validation@test.bg")
        response = self.client.post("/jobs?wait=60", json={**CHART, "date": "1700-01-01"}, headers=headers)
        self.assertEqual(response.status_code, 400)
        self.assertEqual(diagnostics("r16-validation@test.bg"), [])


if __name__ == "__main__":
    unittest.main()
