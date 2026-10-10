"""
Тестове: английските имена на знаци и планети и „natal/transit“ в готовия текст стават български, а имената на хората остават.

Пускане: python -m unittest discover -s tests -p "test_bg_text.py"
"""
import unittest
from unittest import mock

import testenv  # noqa: F401  (трябва да е преди main)
from fastapi.testclient import TestClient  # noqa: E402

import bg_text  # noqa: E402
import main  # noqa: E402
from rate_limit import limiter  # noqa: E402
from testenv import CHART, register_and_login  # noqa: E402

fix = bg_text.bulgarianize


class ReplaceTest(unittest.TestCase):
    def test_signs_in_every_form_of_the_report_from_the_user(self):
        self.assertEqual(fix("Новолуние в Libra (17°22')."), "Новолуние във Везни (17°22').")
        self.assertEqual(fix("Плутон става директен в Aquarius (3°04')."), "Плутон става директен във Водолей (3°04').")
        self.assertEqual(fix("Меркурий е ретрограден в Scorpio (20°59')."), "Меркурий е ретрограден в Скорпион (20°59').")
        self.assertEqual(fix("Пълнолуние в Taurus (2°46')."), "Пълнолуние в Телец (2°46').")
        self.assertEqual(fix("Слънцето навлиза в Scorpio."), "Слънцето навлиза в Скорпион.")

    def test_all_twelve_signs_and_capitals(self):
        for english, bulgarian in bg_text.SIGNS.items():
            with self.subTest(sign=english):
                self.assertEqual(fix(f"Венера е в {english}."), f"Венера е в {bulgarian}.".replace(" в В", " във В").replace(" в ф", " във ф"))
                self.assertEqual(fix(english.upper()), bulgarian.upper())

    def test_the_preposition_agrees(self):
        self.assertEqual(fix("в Libra"), "във Везни")
        self.assertEqual(fix("В Aquarius"), "Във Водолей")
        self.assertEqual(fix("в Pisces"), "в Риби")
        self.assertEqual(fix("в Sagittarius"), "в Стрелец")

    def test_natal_follows_the_gender_of_the_next_word(self):
        self.assertEqual(fix("квадратура към natal Луна"), "квадратура към натална Луна")
        self.assertEqual(fix("аспект към natal Слънце"), "аспект към натално Слънце")
        self.assertEqual(fix("съвпад с natal Меркурий"), "съвпад с натален Меркурий")
        self.assertEqual(fix("аспект към natal Neptune"), "аспект към натален Нептун")
        self.assertEqual(fix("спрямо natal картата"), "спрямо наталната карта")
        self.assertEqual(fix("Natal Марс"), "натален Марс")
        self.assertEqual(fix("transit Венера"), "транзитна Венера")

    def test_english_aspect_and_planet_words(self):
        self.assertEqual(fix("Trine Марс – Neptune"), "Тригон Марс – Нептун")
        self.assertEqual(fix("Сатурн square Луна"), "Сатурн квадратура Луна")
        self.assertEqual(fix("Sun в Leo"), "Слънце в Лъв")

    def test_nothing_else_is_touched(self):
        for text in ("Libraries, Leonid и Cancerous остават.", "Градуси: 17°22' и 3°04'.", "", "Само български текст в 7-ми дом."):
            self.assertEqual(fix(text), text)
        self.assertEqual(fix("**Тема:** кариера"), "**Тема:** кариера")

    def test_names_of_people_are_kept(self):
        token = bg_text.bind(["Leo", "Libra"])
        try:
            self.assertEqual(fix("Leo и Libra са приятели. Луната е в Taurus."), "Leo и Libra са приятели. Луната е в Телец.")
            self.assertEqual(bg_text.protected.get(), ("Leo", "Libra"))
        finally:
            bg_text.unbind(token)
        self.assertEqual(bg_text.protected.get(), ())

    def test_idempotent(self):
        once = fix("Новолуние в Libra, natal Луна, Trine Марс – Neptune.")
        self.assertEqual(fix(once), once)


class PipelineTest(unittest.TestCase):
    """Всеки отговор на AI за анализ се превежда, а името на човека остава."""

    @classmethod
    def setUpClass(cls):
        cls.client = TestClient(main.app)

    def setUp(self):
        limiter.reset()

    def test_the_saved_report_has_no_english_zodiac_words(self):
        h = register_and_login(self.client, "bg-text@test.bg")

        # през истинския _call_api: подменен е само HTTP клиентът
        class FakeResponse:
            status_code = 200

            def json(self):
                return {"choices": [{"message": {"content": "Новолуние в Libra. Сатурн квадратура natal Луна. Leo е в Aquarius."},
                                     "finish_reason": "stop"}], "usage": {}}

        class FakeClient:
            def __init__(self, *a, **k):
                pass

            async def __aenter__(self):
                return self

            async def __aexit__(self, *a):
                return False

            async def post(self, *a, **k):
                return FakeResponse()
        ai = main.ai_interpreter
        with mock.patch.object(ai, "ollama_key", "k"), mock.patch.object(ai, "ollama_url", "http://x"), \
                mock.patch("ai_interpreter.httpx.AsyncClient", FakeClient), \
                mock.patch.dict("os.environ", {"TEXT_CHECK_MODE": "off"}):
            job = self.client.post("/jobs?wait=60", json={**CHART, "name": "Leo", "report_type": "general"}, headers=h).json()["job"]
        self.assertEqual(job["status"], "succeeded", job)
        content = self.client.get(f"/reports/{job['report_id']}", headers=h).json()["content"]
        self.assertIn("Новолуние във Везни", content)
        self.assertIn("натална Луна", content)
        self.assertIn("Leo е във Водолей", content)
        for word in ("Libra", "Aquarius", "natal"):
            self.assertNotIn(word, content)

    def test_an_old_saved_report_is_read_in_bulgarian_without_changing_the_record(self):
        from database import SessionLocal, Report
        import data_api
        h = register_and_login(self.client, "bg-old@test.bg")
        db = SessionLocal()
        try:
            from database import User
            user = db.query(User).filter(User.email == "bg-old@test.bg").one()
            report = data_api.save_report(db, user, content="Новолуние в Libra, natal Луна. Leo е тук.", report_type="general",
                                          profile_name="Leo", label="Стар", cost_cents=0, params={"name": "Leo"})
            db.commit()
            rid = report.id
        finally:
            db.close()
        shown = self.client.get(f"/reports/{rid}", headers=h).json()["content"]
        self.assertEqual(shown, "Новолуние във Везни, натална Луна. Leo е тук.")
        for fmt in ("md", "docx"):
            self.assertEqual(self.client.get(f"/reports/{rid}/export?format={fmt}", headers=h).status_code, 200)
        md = self.client.get(f"/reports/{rid}/export?format=md", headers=h).text
        self.assertIn("Новолуние във Везни", md)
        db = SessionLocal()
        try:
            self.assertIn("Libra", db.get(Report, rid).content)                 # в базата е непроменен
        finally:
            db.close()

    def test_technical_answers_are_not_touched(self):
        self.assertEqual(main.ai_interpreter._finish_text('{"city": "Libra"}', False), '{"city": "Libra"}')

    def test_the_language_rules_name_the_signs(self):
        rules = main.ai_interpreter._get_bulgarian_language_rules()
        for pair in ("Libra=Везни", "Aquarius=Водолей", "Scorpio=Скорпион", "Taurus=Телец", "natal"):
            self.assertIn(pair, rules)


if __name__ == "__main__":
    unittest.main()
