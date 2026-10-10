"""
Тестове за Фаза 12: контекст на отношенията между двамата души и един и същ профил за двете роли.

- вторият човек не е автоматично романтичен партньор: потребителят избира отношенията, а изборът важи за целия анализ;
- контекстът влиза в системния промпт на всяка AI заявка, но само при анализ за двама;
- един и същ профил не може да е и първи, и втори човек (по номер на профил, не по име).

Пускане: python -m unittest discover -s tests -p "test_relationship.py"
"""
import asyncio
import unittest
from unittest import mock

import testenv  # noqa: F401  (трябва да е преди main)
from fastapi.testclient import TestClient  # noqa: E402

import main  # noqa: E402
import relationship  # noqa: E402
from rate_limit import limiter  # noqa: E402
from testenv import CHART, register_and_login  # noqa: E402

PAIR = {"partner_name": "Втори", "partner_date": "1992-07-20", "partner_time": "09:30", "partner_lat": 43.2141,
        "partner_lon": 27.9147}
FORECAST = {"is_dynamic": True, "target_date": "2026-01-01", "end_date": "2026-02-28"}


class ContextTest(unittest.TestCase):
    def test_every_context_has_a_label_and_rules(self):
        self.assertEqual(set(relationship.CONTEXTS), {"general", "romantic", "friend", "family", "parent_child", "work"})
        for code, (label, rules) in relationship.CONTEXTS.items():
            with self.subTest(code=code):
                self.assertTrue(label)
                self.assertIn(label, relationship.block(code))
                self.assertIn(rules, relationship.block(code))
                self.assertEqual(relationship.LABELS[code], label)

    def test_only_the_romantic_context_allows_romance(self):
        for code in ("friend", "family", "parent_child", "work"):
            with self.subTest(code=code):
                text = relationship.block(code)
                self.assertTrue("NOT describe romance" in text or "never romantic" in text, text)
                self.assertIn("do not use words that name another kind of relationship", text)
        for code in ("romantic", "general"):                                   # романтичното го позволява, а общото не предполага нищо
            text = relationship.block(code)
            self.assertNotIn("NOT describe romance", text)
            self.assertNotIn("never romantic", text)

    def test_an_unknown_or_missing_choice_is_the_general_interaction(self):
        for value in (None, "", "lover", "ROMANTIC"):
            with self.subTest(value=value):
                self.assertEqual(relationship.normalize(value), "general")
                self.assertEqual(relationship.block(value), relationship.block("general"))
        self.assertTrue(relationship.is_valid(None) and relationship.is_valid("") and relationship.is_valid("work"))
        self.assertFalse(relationship.is_valid("lover"))

    def test_the_parent_is_never_guessed(self):
        self.assertIn("do not guess who is the parent", relationship.block("parent_child"))


class PromptTest(unittest.TestCase):
    """Контекстът влиза в системния промпт на всяка заявка към доставчика на AI."""

    def system_prompt(self, bind=None, add_context=True):
        captured = {}

        class FakeResponse:
            status_code = 200

            def json(self):
                return {"choices": [{"message": {"content": "отговор"}, "finish_reason": "stop"}], "usage": {}}

        class FakeClient:
            def __init__(self, *a, **k):
                pass

            async def __aenter__(self):
                return self

            async def __aexit__(self, *a):
                return False

            async def post(self, url, headers=None, json=None, **k):
                captured["system"] = json["messages"][0]["content"]
                return FakeResponse()

        async def run():
            token = relationship.bind(bind) if bind is not None else None
            try:
                await main.ai_interpreter._call_api("система", "потребител", 100, add_context=add_context)
            finally:
                if token is not None:
                    relationship.unbind(token)

        ai = main.ai_interpreter
        with mock.patch.object(ai, "ollama_key", "k"), mock.patch.object(ai, "ollama_url", "http://x"), \
                mock.patch("ai_interpreter.httpx.AsyncClient", FakeClient):
            asyncio.run(run())
        return captured["system"]

    def test_the_bound_context_is_in_the_system_prompt(self):
        prompt = self.system_prompt("friend")
        self.assertIn("RELATIONSHIP CONTEXT (chosen by the user): Приятелство.", prompt)
        self.assertIn("ПРАВИЛА ЗА БЕЗОПАСНОСТ", prompt)                        # правилата за безопасност остават

    def test_no_context_without_a_binding_or_for_technical_requests(self):
        self.assertNotIn("RELATIONSHIP CONTEXT", self.system_prompt())
        self.assertNotIn("RELATIONSHIP CONTEXT", self.system_prompt("friend", add_context=False))

    def test_the_binding_does_not_leak_after_the_analysis(self):
        self.system_prompt("work")
        self.assertEqual(relationship.current.get(), "")


class JobTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.client = TestClient(main.app)

    def setUp(self):
        limiter.reset()

    def post(self, headers, body, **params):
        query = "&".join(f"{k}={v}" for k, v in {"wait": 60, **params}.items())
        return self.client.post(f"/jobs?{query}", json=body, headers=headers)

    def seen_contexts(self):
        """AI подмяна, която записва какъв контекст на отношенията е видяла."""
        seen = []

        async def fake(*args, **kwargs):
            seen.append(relationship.current.get())
            return "<p>текст</p>"
        return seen, mock.patch.object(main.ai_interpreter, "interpret_chart", fake)

    def test_a_pair_analysis_sees_the_chosen_context(self):
        h = register_and_login(self.client, "rel-pair@test.bg")
        seen, patch = self.seen_contexts()
        with patch:
            job = self.post(h, {**CHART, **PAIR, "relationship": "friend"}).json()["job"]
        self.assertEqual(job["status"], "succeeded")
        self.assertEqual(len(seen), 1)
        self.assertIn("Приятелство", seen[0])
        self.assertEqual(relationship.current.get(), "")

    def test_a_pair_analysis_without_a_choice_is_the_general_interaction(self):
        h = register_and_login(self.client, "rel-general@test.bg")
        seen, patch = self.seen_contexts()
        with patch:
            self.post(h, {**CHART, **PAIR})
            self.post(h, {**CHART, **PAIR, "relationship": ""})
        self.assertEqual(len(seen), 2)
        for block in seen:
            self.assertIn("Общо взаимодействие", block)

    def test_an_analysis_for_one_person_has_no_relationship_context(self):
        h = register_and_login(self.client, "rel-single@test.bg")
        seen, patch = self.seen_contexts()
        with patch:
            job = self.post(h, {**CHART, "relationship": "work"}).json()["job"]
        self.assertEqual(job["status"], "succeeded")
        self.assertEqual(seen, [""])

    def test_every_month_of_a_pair_forecast_sees_the_context(self):
        h = register_and_login(self.client, "rel-forecast@test.bg")
        seen = []

        async def month(*args, **kwargs):
            seen.append(relationship.current.get())
            return "<p>м</p>"
        with mock.patch.object(main.ai_interpreter, "_process_monthly_chunk", month), \
                mock.patch.object(main.ai_interpreter, "compose_period_overview", mock.AsyncMock(return_value="<p>о</p>")):
            job = self.post(h, {**CHART, **PAIR, **FORECAST, "relationship": "parent_child"}).json()["job"]
        self.assertEqual(job["status"], "succeeded")
        self.assertEqual(len(seen), 2)                                        # януари и февруари
        for block in seen:
            self.assertIn("Родител и дете", block)

    def test_the_choice_is_saved_with_a_pair_report_only(self):
        h = register_and_login(self.client, "rel-params@test.bg")
        seen, patch = self.seen_contexts()
        with patch:
            pair = self.post(h, {**CHART, **PAIR, "relationship": "family"}).json()["job"]
            single = self.post(h, {**CHART, "relationship": "family"}).json()["job"]
        self.assertEqual(self.client.get(f"/reports/{pair['report_id']}", headers=h).json()["params"]["relationship"], "family")
        self.assertNotIn("relationship", self.client.get(f"/reports/{single['report_id']}", headers=h).json()["params"])

    def test_an_unknown_relationship_and_a_huge_question_are_rejected(self):
        h = register_and_login(self.client, "rel-bad@test.bg")
        self.assertEqual(self.post(h, {**CHART, **PAIR, "relationship": "lover"}).status_code, 422)
        self.assertEqual(self.post(h, {**CHART, "question": "в" * 1501}).status_code, 422)
        self.assertEqual(self.post(h, {**CHART, "name": "и" * 101}).status_code, 422)
        self.assertEqual(self.client.get("/jobs", headers=h).json()["jobs"], [])

    def test_the_same_profile_cannot_be_both_people(self):
        h = register_and_login(self.client, "rel-same@test.bg")
        seen, patch = self.seen_contexts()
        with patch:
            same = self.post(h, {**CHART, **PAIR, "profile_id": 7, "partner_profile_id": 7})
            self.assertEqual(same.status_code, 400, same.text)
            self.assertIn("един и същ профил", same.json()["detail"])
            self.assertEqual(self.client.get("/jobs", headers=h).json()["jobs"], [])
            # различни профили и липсващи номера (ръчно въведени хора, близнаци с еднакви данни) са позволени
            self.assertEqual(self.post(h, {**CHART, **PAIR, "profile_id": 7, "partner_profile_id": 8}).status_code, 200)
            self.assertEqual(self.post(h, {**CHART, **PAIR}).status_code, 200)


if __name__ == "__main__":
    unittest.main()
