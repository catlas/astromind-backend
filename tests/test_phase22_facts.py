"""
Фаза 22, R01 (регресионен одит 11.10.2026): анализ без пълни факти за проверка не се доставя.

- text_check.validate_facts: пълнотата на фактите според режима; неизвестният час е явен договор (no_houses/alt_signs), не липса.
- AIInterpreter.build_text_facts: при непълни факти или грешка връща MissingFacts (без лични данни в дневника).
- Отказ ПРЕДИ разход на AI заявка (единичен анализ и периодичен отчет), причина facts_unavailable; warn/off пропускат.
"""
import asyncio
import copy
import io
import os
import unittest
from contextlib import redirect_stdout
from unittest import mock

import testenv  # noqa: F401  (трябва да е преди engine)
import ai_interpreter
import jobs
import main
import period_cards
import period_report
import regression_support as rs
import text_check as tc
import text_guard
from database import AIReservation, Event, Job, Report, SessionLocal, User
from fastapi.testclient import TestClient
from generation import GenerationFailure, text_check_failure
from rate_limit import limiter
from testenv import CHART, give_deposit, register_and_login


class ValidateFactsTest(unittest.TestCase):
    def test_complete_facts_pass_in_every_mode(self):
        for mode, pair in (("natal", False), ("natal", True), ("snapshot", False), ("snapshot", True),
                           ("period", False), ("period", True)):
            tc.validate_facts(rs.facts(mode, pair))

    def test_unknown_birth_time_is_a_contract_not_a_gap(self):
        for row_id in (47, 48):
            tc.validate_facts(rs.facts_for(row_id))
        tc.validate_facts(tc.build_facts(mode="natal", user_name="Иван", natal_chart=rs.unknown_ivan()))

    def test_sun_and_moon_with_a_sign_range_count_as_present(self):
        facts = rs.facts_for(47)
        self.assertNotIn("Moon", facts.natal["user"])            # при неизвестен час Луната е само в alt_signs
        self.assertIn(("user", "Moon"), facts.alt_signs)
        tc.validate_facts(facts)

    def test_each_missing_part_is_refused_with_a_short_reason(self):
        base = rs.facts("snapshot", True)
        cases = {
            "transit_sky_missing": lambda f: f.sky.clear(),
            "cusps_missing:partner": lambda f: f.cusps.pop("partner"),
            "natal_aspects_missing:user": lambda f: f.natal_aspects.pop("user"),
            "natal_bodies_missing:user": lambda f: f.natal["user"].pop("Mars"),
            "transit_houses_missing:user": lambda f: f.transit_houses.pop("user"),
            "transit_aspects_missing:partner": lambda f: f.transit_aspects.pop("partner"),
            "natal_missing": lambda f: f.natal.pop("user"),
        }
        for reason, damage in cases.items():
            facts = copy.deepcopy(base)
            damage(facts)
            with self.assertRaises(tc.FactsUnavailable, msg=reason) as caught:
                tc.validate_facts(facts)
            self.assertEqual(caught.exception.reason, reason)

    def test_a_period_without_a_calendar_is_refused(self):
        for mode in ("period", "overview"):
            facts = copy.deepcopy(rs.facts("period", False))
            facts.mode, facts.calendar = mode, None
            with self.assertRaises(tc.FactsUnavailable):
                tc.validate_facts(facts)

    def test_the_reason_never_contains_personal_data(self):
        facts = copy.deepcopy(rs.facts("natal", True))
        facts.natal["partner"].pop("Venus")
        with self.assertRaises(tc.FactsUnavailable) as caught:
            tc.validate_facts(facts)
        for private in ("Иван", "1985", "Виена", rs.A_NAME):
            self.assertNotIn(private, caught.exception.reason)


class BuildTextFactsTest(unittest.TestCase):
    def build(self, **overrides):
        kwargs = dict(mode="natal", user_name="Иван", natal_chart=rs.chart("Ivan"))
        kwargs.update(overrides)
        return ai_interpreter.AIInterpreter.build_text_facts(**kwargs)

    def test_complete_facts_come_back_as_facts(self):
        self.assertIsInstance(self.build(), tc.Facts)

    def test_an_incomplete_chart_gives_missing_facts_not_none(self):
        broken = copy.deepcopy(rs.chart("Ivan"))
        broken["planets"].pop("Mars")
        result = self.build(natal_chart=broken)
        self.assertIsInstance(result, text_guard.MissingFacts)
        self.assertTrue(result.reason.startswith("natal_bodies_missing"))

    def test_an_unexpected_error_gives_missing_facts_and_logs_only_the_type(self):
        buffer = io.StringIO()
        with mock.patch.object(tc, "build_facts", side_effect=KeyError("Иван 1985 Виена")), redirect_stdout(buffer):
            result = self.build()
        self.assertEqual(result.reason, "build_error:KeyError")
        self.assertNotIn("Иван", buffer.getvalue())
        self.assertNotIn("1985", buffer.getvalue())


class GuardWithMissingFactsTest(unittest.TestCase):
    def guard(self, facts, mode):
        async def repair(system_prompt, user_prompt):
            raise AssertionError("поправка не трябва да се вика")
        return asyncio.run(text_guard.guard_text("Слънцето е в Овен.", facts, stage="natal", repair_call=repair, mode=mode))

    def test_enforce_refuses(self):
        with self.assertRaises(text_guard.TextCheckError) as caught:
            self.guard(text_guard.MissingFacts("cusps_missing:user"), "enforce")
        outcome = caught.exception.outcome
        self.assertTrue(outcome.rejected)
        self.assertEqual(outcome.reason, "facts_unavailable")
        self.assertEqual(outcome.codes(), "facts")
        self.assertEqual(outcome.summary()["result"], "rejected")
        self.assertEqual(outcome.summary()["reason"], "facts_unavailable")

    def test_warn_lets_the_text_through_and_keeps_the_reason(self):
        outcome = self.guard(text_guard.MissingFacts("cusps_missing:user"), "warn")
        self.assertFalse(outcome.rejected)
        self.assertEqual(outcome.text, "Слънцето е в Овен.")
        self.assertEqual(outcome.error, "facts:cusps_missing:user")
        self.assertEqual(outcome.result, "error")

    def test_off_and_not_applicable_pass(self):
        self.assertFalse(self.guard(text_guard.MissingFacts("x"), "off").rejected)
        self.assertEqual(self.guard(None, "enforce").text, "Слънцето е в Овен.")        # None = проверката не се прилага (друг език)


class RefusalBeforeSpendTest(unittest.TestCase):
    def setUp(self):
        self.interp = ai_interpreter.AIInterpreter(api_key="test-dummy")
        self.missing = text_guard.MissingFacts("cusps_missing:user")

    def test_a_single_analysis_makes_no_ai_call(self):
        fake = mock.AsyncMock(return_value="Слънцето е в Рак.")
        checks = []
        with mock.patch.object(self.interp, "_call_api", fake), \
                mock.patch.object(self.interp, "build_text_facts", return_value=self.missing):
            with self.assertRaises(text_guard.TextCheckError) as caught:
                asyncio.run(self.interp.interpret_chart(natal_chart=rs.chart("Ivan"), user_name="Иван", checks=checks))
        self.assertEqual(fake.await_count, 0)
        self.assertEqual(caught.exception.outcome.reason, "facts_unavailable")
        self.assertEqual(checks[0]["reason"], "facts_unavailable")
        self.assertEqual(checks[0]["codes"], "facts")

    def test_a_snapshot_makes_no_ai_call(self):
        fake = mock.AsyncMock(return_value="x")
        with mock.patch.object(self.interp, "_call_api", fake), \
                mock.patch.object(self.interp, "build_text_facts", return_value=self.missing):
            with self.assertRaises(text_guard.TextCheckError) as caught:
                asyncio.run(self.interp.interpret_chart(natal_chart=rs.chart("Ivan"), transit_chart=rs.sky(False),
                                                        target_date=rs.SNAPSHOT, user_name="Иван"))
        self.assertEqual(fake.await_count, 0)
        self.assertEqual(caught.exception.stage, "snapshot")

    def test_warn_mode_still_calls_the_ai_and_returns_the_text(self):
        calls = []

        async def fake(system_prompt=None, user_prompt=None, **kwargs):
            calls.append(system_prompt)
            return testenv.pair_reply(user_prompt)                       # натален анализ за един човек е по карти
        with mock.patch.dict(os.environ, {"TEXT_CHECK_MODE": "warn"}), mock.patch.object(self.interp, "_call_api", fake), \
                mock.patch.object(self.interp, "build_text_facts", return_value=self.missing):
            text = asyncio.run(self.interp.interpret_chart(natal_chart=rs.chart("Ivan"), user_name="Иван"))
        self.assertIn("## Натална карта на Иван", text)
        self.assertEqual(len(calls), 1)

    def test_a_period_report_sends_no_month_to_the_ai(self):
        calls = []

        async def fake(system_prompt=None, user_prompt=None, **kwargs):
            calls.append(system_prompt)
            return testenv.period_reply(system_prompt, user_prompt)

        async def go():
            return [step async for step in period_cards.run_period_report(
                self.interp, calendar=rs.calendar(False), natal_chart=rs.chart("Ivan"), partner_chart=None,
                report_type="general", user_name="Иван", partner_name=None, question="", report_date="2026-10-11", retry_pause=0)]

        with mock.patch.object(self.interp, "_call_api", fake), mock.patch.object(self.interp, "build_text_facts", return_value=self.missing):
            with self.assertRaises(period_report.ForecastGenerationError) as caught:
                asyncio.run(go())
        self.assertEqual(calls, [])                                             # нито една заявка към AI
        self.assertEqual(caught.exception.stage, "facts")
        self.assertIsInstance(caught.exception.cause, text_guard.TextCheckError)
        self.assertEqual(caught.exception.checks[0]["reason"], "facts_unavailable")


class TelemetryTest(unittest.TestCase):
    def test_the_user_message_and_the_detail_are_safe(self):
        outcome = text_guard.GuardOutcome(stage="natal", mode="enforce", text="", rejected=True, reason="facts_unavailable",
                                          error="facts:cusps_missing:user")
        failure = text_check_failure(text_guard.TextCheckError("natal", outcome), [outcome.summary()])
        self.assertIsInstance(failure, GenerationFailure)
        self.assertEqual(failure.code, "text_check")
        self.assertIn(text_guard.USER_MESSAGE, str(failure))
        props = jobs._failure_props(mock.Mock(request={"report_type": "general"}, kind="analysis", sku="x"), failure)
        self.assertEqual(props["reason"], "text_check")
        self.assertEqual(props["detail"], "facts_unavailable")
        self.assertEqual(props["codes"], "facts")


class JobEndToEndTest(unittest.TestCase):
    """R01 на ниво задача: фактите не се построяват -> задачата пропада без отчет, без дебит, без AI заявка и без изразходена квота."""
    ENFORCED = {"BALANCE_ENFORCED": "1"}

    @classmethod
    def setUpClass(cls):
        cls.client = TestClient(main.app)

    def setUp(self):
        limiter.reset()

    @staticmethod
    def state(email):
        db = SessionLocal()
        try:
            user = db.query(User).filter(User.email == email).one()
            return (int(user.paid_cents), int(user.gift_cents), db.query(Report).filter(Report.user_id == user.id).count(),
                    db.query(AIReservation).count(), jobs.limits_payload(db, user)["hour_remaining"], user.id)
        finally:
            db.close()

    def run_failing_job(self, email, body, ai_patches):
        headers = register_and_login(self.client, email)
        give_deposit(email, 500)
        before = self.state(email)
        with mock.patch.dict(os.environ, self.ENFORCED), \
                mock.patch.object(tc, "build_facts", side_effect=KeyError("карта Иван 1985")):
            for patch in ai_patches:
                patch.start()
            try:
                response = self.client.post("/jobs?wait=60", json=body, headers=headers)
            finally:
                for patch in ai_patches:
                    patch.stop()
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()["job"], before, self.state(email)

    def test_a_single_analysis_fails_before_any_ai_call_and_costs_nothing(self):
        call = mock.AsyncMock(return_value="<p>текст</p>")
        job, before, after = self.run_failing_job("r01-single@test.bg", {**CHART, "name": "Иван"},
                                                  [mock.patch.object(main.ai_interpreter, "_call_api", call)])
        self.assertEqual((job["status"], job["error"]["code"]), ("failed", "text_check"))
        self.assertIn(text_guard.USER_MESSAGE, job["error"]["message"])
        self.assertEqual(call.await_count, 0)
        self.assertEqual(job["charged_cents"], 0)
        self.assertEqual(after[:4], before[:4])                              # баланс, отчети, резервации на AI бюджета
        self.assertEqual(after[4], before[4])                                # мястото в лимита е върнато
        self.assertNotIn("Иван", str(job["error"]))                          # без лични данни в отказа

    def test_a_forecast_fails_before_any_month_is_generated_and_costs_nothing(self):
        call = mock.AsyncMock(return_value="<p>м</p>")
        body = {**CHART, "is_dynamic": True, "target_date": "2026-01-01", "end_date": "2026-01-31"}
        job, before, after = self.run_failing_job("r01-forecast@test.bg", body, [mock.patch.object(main.ai_interpreter, "_call_api", call)])
        self.assertEqual(job["status"], "failed")
        self.assertEqual(call.await_count, 0)
        self.assertEqual(job["charged_cents"], 0)
        self.assertEqual(after[:4], before[:4])
        self.assertEqual(after[4], before[4])

    def test_the_telemetry_has_the_reason_and_no_personal_data(self):
        self.run_failing_job("r01-events@test.bg", {**CHART, "name": "Иван"},
                             [mock.patch.object(main.ai_interpreter, "_call_api", mock.AsyncMock(return_value="x"))])
        db = SessionLocal()
        try:
            uid = db.query(User.id).filter(User.email == "r01-events@test.bg").scalar()
            failed = db.query(Event).filter(Event.user_id == uid, Event.name == "analysis_failed").all()
        finally:
            db.close()
        self.assertEqual(len(failed), 1)
        props = failed[0].props
        self.assertEqual((props["reason"], props["detail"], props["codes"]), ("text_check", "facts_unavailable", "facts"))
        self.assertNotIn("Иван", str(props))


if __name__ == "__main__":
    unittest.main()
