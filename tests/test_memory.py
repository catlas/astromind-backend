"""
Тестове за Фаза 6: контролирана AI памет и времева линия.

Пускане: python -m unittest discover -s tests
"""
import unittest
from unittest import mock

import testenv  # noqa: F401  (трябва да е преди main)
from fastapi.testclient import TestClient  # noqa: E402

import main  # noqa: E402
import memory  # noqa: E402
from rate_limit import limiter  # noqa: E402
from testenv import CHART, register_and_login  # noqa: E402


class MemoryTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.client = TestClient(main.app)

    def setUp(self):
        limiter.reset()

    def make_owner(self, headers, name="Аз"):
        """Основният (личен) профил на собственика: само за него важат бележките „за всички анализи“."""
        r = self.client.post("/profiles", json={"name": name, "relation": "self", "birth_date": "1990-05-15",
                                                "birth_time": "14:30", "lat": 42.6977, "lon": 23.3219,
                                                "is_primary": True}, headers=headers)
        self.assertEqual(r.status_code, 200, r.text)

    def test_notes_crud_and_preview(self):
        h = register_and_login(self.client, "mem@test.bg")
        self.make_owner(h)
        self.assertEqual(self.client.get("/memory", headers=h).json(), {"enabled": True, "notes": [], "preview": ""})
        a = self.client.post("/memory", json={"text": "Работя като  учител."}, headers=h).json()
        self.assertEqual(a["text"], "Работя като учител.")
        self.client.post("/memory", json={"text": "Тя е моя сестра.", "profile_name": "Мария"}, headers=h)
        self.assertEqual(self.client.post("/memory", json={"text": "   "}, headers=h).status_code, 400)

        # Без избран профил прегледът е за основния профил: личните бележки на собственика
        general = self.client.get("/memory", headers=h).json()["preview"]
        self.assertIn("учител", general)
        self.assertNotIn("сестра", general)
        # Анализ за друг човек не получава личните бележки на собственика, а само своите
        maria = self.client.get("/memory", params={"profile_name": "Мария"}, headers=h).json()["preview"]
        self.assertIn("сестра", maria)
        self.assertNotIn("учител", maria)
        self.assertIn("За Мария:", maria)

        self.client.put(f"/memory/{a['id']}", json={"text": "Работя като лекар."}, headers=h)
        self.assertIn("лекар", self.client.get("/memory", headers=h).json()["preview"])
        self.client.delete(f"/memory/{a['id']}", headers=h)
        self.assertEqual(self.client.get("/memory", headers=h).json()["preview"], "")

    def test_notes_are_private(self):
        h = register_and_login(self.client, "mem-a@test.bg")
        other = register_and_login(self.client, "mem-b@test.bg")
        note = self.client.post("/memory", json={"text": "таен"}, headers=h).json()
        self.assertEqual(self.client.delete(f"/memory/{note['id']}", headers=other).status_code, 404)
        self.assertEqual(self.client.get("/memory", headers=other).json()["notes"], [])

    def test_context_reaches_ai_only_when_enabled(self):
        h = register_and_login(self.client, "mem-ai@test.bg")
        self.make_owner(h)
        self.client.post("/memory", json={"text": "Мечтая да отворя пекарна."}, headers=h)
        seen = []

        async def fake_interpret(**kwargs):
            seen.append(memory.current_context.get())
            return "<p>ok</p>"

        with mock.patch.object(main.ai_interpreter, "interpret_chart", side_effect=fake_interpret):
            r = self.client.post("/interpret", json={**CHART, "name": "Аз"}, headers=h)
            self.assertIn("пекарна", seen[-1])
            report = self.client.get(f"/reports/{r.json()['report_id']}", headers=h).json()
            self.assertTrue(report["params"]["memory_used"])

            self.client.put("/memory/settings", json={"enabled": False}, headers=h)
            limiter.reset()
            self.client.post("/interpret", json={**CHART, "name": "Аз"}, headers=h)
            self.assertEqual(seen[-1], "")
        self.assertFalse(self.client.get("/me", headers=h).json()["memory_enabled"])

    def test_owner_notes_do_not_leak_into_other_people(self):
        h = register_and_login(self.client, "mem-owner@test.bg")
        self.make_owner(h)
        self.client.post("/memory", json={"text": "Работя като учител."}, headers=h)                    # на собственика
        self.client.post("/memory", json={"text": "Иван е мой колега.", "profile_name": "Иван"}, headers=h)
        seen = []

        async def fake_interpret(**kwargs):
            seen.append(memory.current_context.get())
            return "<p>ok</p>"

        with mock.patch.object(main.ai_interpreter, "interpret_chart", side_effect=fake_interpret):
            def ask(**extra):
                limiter.reset()
                self.client.post("/interpret", json={**CHART, **extra}, headers=h)
                return seen[-1]

            owner_only = ask(name="Аз")
            self.assertIn("учител", owner_only)
            self.assertNotIn("колега", owner_only)
            friend = ask(name="Иван")
            self.assertIn("колега", friend)
            self.assertNotIn("учител", friend)                                                    # без личните факти на собственика
            unnamed = ask()
            self.assertEqual(unnamed, "")                                                         # ръчно въведен човек без име
            pair = ask(name="Аз", partner_name="Иван", partner_date="1985-07-12", partner_time="18:45",
                       partner_lat=48.2082, partner_lon=16.3738)
            self.assertIn("За Аз:", pair)
            self.assertIn("За Иван:", pair)
            self.assertLess(pair.index("учител"), pair.index("колега"))

    def test_previous_analysis_titles_are_not_sent_to_ai(self):
        h = register_and_login(self.client, "mem-titles@test.bg")
        self.make_owner(h)
        self.client.post("/memory", json={"text": "Обичам да пътувам."}, headers=h)
        self.client.post("/reports/import", json={"reports": [
            {"type": "career", "label": "Кариера октомври", "profile": "Аз", "content": "<p>1</p>", "date": "2026-01-10"},
        ]}, headers=h)
        preview = self.client.get("/memory", headers=h).json()["preview"]
        self.assertIn("пътувам", preview)
        self.assertNotIn("Предишни анализи", preview)
        self.assertNotIn("Кариера октомври", preview)

    def test_call_api_appends_context(self):
        captured = {}

        class FakeResponse:
            status_code = 200

            def json(self):
                return {"choices": [{"message": {"content": "отговор"}, "finish_reason": "stop"}], "usage": {}}

            def raise_for_status(self):
                return None

        class FakeClient:
            def __init__(self, *a, **k):
                pass

            async def __aenter__(self):
                return self

            async def __aexit__(self, *a):
                return False

            async def post(self, url, headers=None, json=None, **k):
                captured["messages"] = json["messages"]
                return FakeResponse()

        ai = main.ai_interpreter
        token = memory.current_context.set("КОНТЕКСТ: тест")
        try:
            with mock.patch.object(ai, "ollama_key", "k"), mock.patch.object(ai, "ollama_url", "http://x"), \
                    mock.patch("ai_interpreter.httpx.AsyncClient", FakeClient):
                import asyncio
                asyncio.run(ai._call_api("система", "потребител", 100))
        finally:
            memory.current_context.reset(token)
        self.assertIn("ПРАВИЛА ЗА БЕЗОПАСНОСТ", captured["messages"][0]["content"])
        self.assertIn("КОНТЕКСТ: тест", captured["messages"][1]["content"])

    def test_timeline_groups_by_month(self):
        h = register_and_login(self.client, "timeline@test.bg")
        self.client.post("/reports/import", json={"reports": [
            {"type": "career", "label": "Кариера", "profile": "Аз", "content": "<p>1</p>", "date": "2026-01-10"},
            {"type": "love", "label": "Любов", "profile": "Аз", "content": "<p>2</p>", "date": "2026-01-20"},
            {"type": "general", "label": "Общ", "profile": "Мария", "content": "<p>3</p>", "date": "2026-03-05"},
        ]}, headers=h)
        data = self.client.get("/timeline", headers=h).json()
        self.assertEqual([g["label"] for g in data["groups"]], ["Март 2026", "Януари 2026"])
        self.assertEqual([r["label"] for r in data["groups"][1]["reports"]], ["Любов", "Кариера"])
        self.assertEqual(data["profiles"], ["Аз", "Мария"])
        only = self.client.get("/timeline", params={"profile": "Мария"}, headers=h).json()
        self.assertEqual(len(only["groups"]), 1)


if __name__ == "__main__":
    unittest.main()
