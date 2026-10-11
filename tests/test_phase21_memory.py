"""
Фаза 21: AI паметта се свързва с профил по номер (AM-N07).

Двама различни „Иван“ не споделят бележки, преименуването пази връзката, изключената памет не се подава, а записите, които не
могат да се свържат еднозначно, остават без профил и не се ползват, докато собственикът не избере.
"""
import unittest
from pathlib import Path
from unittest import mock

import sqlalchemy as sa
from alembic import command
from alembic.config import Config

import testenv  # noqa: F401  (трябва да е преди main)
from fastapi.testclient import TestClient  # noqa: E402

import main  # noqa: E402
import memory  # noqa: E402
from database import MemoryNote, Profile, SessionLocal, User  # noqa: E402
from rate_limit import limiter  # noqa: E402
from testenv import CHART, analyze, register_and_login  # noqa: E402

MIGRATIONS = Path(__file__).resolve().parent.parent / "migrations"


class MemoryByProfileTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.client = TestClient(main.app)

    def setUp(self):
        limiter.reset()

    def profile(self, h, name, relation="friend", primary=False):
        r = self.client.post("/profiles", json={"name": name, "relation": relation, "birth_date": "1985-07-12", "birth_time": "18:45",
                                                "lat": 48.2082, "lon": 16.3738, "is_primary": primary}, headers=h)
        self.assertEqual(r.status_code, 200, r.text)
        return r.json()["id"]

    def owner(self, h):
        return self.profile(h, "Аз", "self", primary=True)

    def seen_context(self, h, **extra):
        seen = []

        async def fake(**kwargs):
            seen.append(memory.current_context.get())
            return "<p>ok</p>"
        limiter.reset()
        with mock.patch.object(main.ai_interpreter, "interpret_chart", side_effect=fake):
            analyze(self.client, h, {**CHART, **extra})
        return seen[-1]

    def test_the_note_belongs_to_the_profile_and_a_namesake_without_a_profile_gets_nothing(self):
        h = register_and_login(self.client, "p21m-a@test.bg")
        self.owner(h)
        ivan = self.profile(h, "Иван")
        note = self.client.post("/memory", json={"text": "Иван е мой колега.", "profile_id": ivan}, headers=h).json()
        self.assertEqual((note["profile_id"], note["profile_name"], note["needs_profile"]), (ivan, "Иван", False))
        self.assertIn("колега", self.seen_context(h, name="Иван", profile_id=ivan))
        self.assertEqual(self.seen_context(h, name="Иван"), "")                           # друг човек със същото име, ръчно въведен
        self.assertEqual(self.seen_context(h, name="иван"), "")

    def test_renaming_a_profile_keeps_the_notes_and_shows_the_new_name(self):
        h = register_and_login(self.client, "p21m-b@test.bg")
        self.owner(h)
        ivan = self.profile(h, "Иван")
        self.client.post("/memory", json={"text": "Работи в банка.", "profile_id": ivan}, headers=h)
        r = self.client.put(f"/profiles/{ivan}", json={"name": "Иванчо", "relation": "friend", "birth_date": "1985-07-12",
                                                       "birth_time": "18:45", "lat": 48.2082, "lon": 16.3738}, headers=h)
        self.assertEqual(r.status_code, 200, r.text)
        notes = self.client.get("/memory", headers=h).json()["notes"]
        self.assertEqual([(n["profile_id"], n["profile_name"]) for n in notes], [(ivan, "Иванчо")])
        context = self.seen_context(h, name="Иванчо", profile_id=ivan)
        self.assertIn("банка", context)
        self.assertIn("За Иванчо:", context)
        self.assertEqual(self.seen_context(h, name="Иван"), "")                           # старото име вече не води към нищо

    def test_a_profile_chosen_in_the_form_gets_its_notes_even_if_the_typed_name_differs(self):
        h = register_and_login(self.client, "p21m-c@test.bg")
        self.owner(h)
        ivan = self.profile(h, "Иван")
        self.client.post("/memory", json={"text": "Обича планината.", "profile_id": ivan}, headers=h)
        self.assertIn("планината", self.seen_context(h, name="Ванката", profile_id=ivan))

    def test_notes_for_the_owner_follow_the_primary_profile_by_id(self):
        h = register_and_login(self.client, "p21m-d@test.bg")
        me = self.owner(h)
        self.client.post("/memory", json={"text": "Работя като учител."}, headers=h)
        self.assertIn("учител", self.seen_context(h, name="Аз", profile_id=me))
        self.assertIn("учител", self.seen_context(h, name="Аз"))                          # същият човек, въведен ръчно с името си
        self.assertEqual(self.seen_context(h, name="Друг"), "")

    def test_a_name_only_note_is_not_used_until_the_owner_links_it(self):
        h = register_and_login(self.client, "p21m-e@test.bg")
        self.owner(h)
        ivan = self.profile(h, "Иван")
        # име, което не съвпада с профил: бележката остава без връзка и не се подава
        note = self.client.post("/memory", json={"text": "Някой друг Иван.", "profile_name": "Пешо"}, headers=h).json()
        self.assertEqual((note["profile_id"], note["needs_profile"], note["profile_name"]), (None, True, "Пешо"))
        self.assertEqual(self.seen_context(h, name="Пешо"), "")
        self.assertEqual(self.seen_context(h, name="Иван", profile_id=ivan), "")
        self.assertEqual(self.client.get("/memory", headers=h).json()["preview"], "")
        # собственикът я свързва: от този момент се ползва за този профил
        linked = self.client.put(f"/memory/{note['id']}", json={"text": "Някой друг Иван.", "profile_id": ivan}, headers=h).json()
        self.assertEqual((linked["profile_id"], linked["needs_profile"], linked["profile_name"]), (ivan, False, "Иван"))
        self.assertIn("друг Иван", self.seen_context(h, name="Иван", profile_id=ivan))

    def test_a_legacy_client_naming_a_profile_gets_it_linked_when_the_name_is_unique(self):
        h = register_and_login(self.client, "p21m-f@test.bg")
        self.owner(h)
        ivan = self.profile(h, "Иван")
        note = self.client.post("/memory", json={"text": "Колега.", "profile_name": "иван"}, headers=h).json()
        self.assertEqual((note["profile_id"], note["profile_name"]), (ivan, "Иван"))

    def test_two_profiles_with_the_same_name_in_another_case_are_not_guessed(self):
        h = register_and_login(self.client, "p21m-g@test.bg")
        self.owner(h)
        first = self.profile(h, "Иван")
        second = self.profile(h, "иван")
        note = self.client.post("/memory", json={"text": "Кой от двамата?", "profile_name": "Иван"}, headers=h).json()
        self.assertEqual((note["profile_id"], note["needs_profile"]), (None, True))
        self.assertEqual(self.seen_context(h, name="Иван", profile_id=first), "")
        self.assertEqual(self.seen_context(h, name="иван", profile_id=second), "")

    def test_a_profile_of_another_account_cannot_be_used(self):
        h = register_and_login(self.client, "p21m-h@test.bg")
        other = register_and_login(self.client, "p21m-i@test.bg")
        self.owner(h)
        foreign = self.profile(other, "Чужд")
        self.assertEqual(self.client.post("/memory", json={"text": "х", "profile_id": foreign}, headers=h).status_code, 404)
        self.client.post("/memory", json={"text": "Моя бележка.", "profile_id": self.profile(h, "Мой")}, headers=h)
        # чужд номер на профил в анализ не дава бележки
        self.assertEqual(self.seen_context(h, name="Чужд", profile_id=foreign), "")

    def test_deleting_a_profile_deletes_its_notes_and_keeps_the_owners(self):
        h = register_and_login(self.client, "p21m-j@test.bg")
        self.owner(h)
        ivan = self.profile(h, "Иван")
        self.client.post("/memory", json={"text": "За мен."}, headers=h)
        self.client.post("/memory", json={"text": "За Иван.", "profile_id": ivan}, headers=h)
        self.assertEqual(self.client.delete(f"/profiles/{ivan}", headers=h).status_code, 200)
        self.assertEqual([n["text"] for n in self.client.get("/memory", headers=h).json()["notes"]], ["За мен."])

    def test_disabled_memory_gives_nothing_and_preview_can_be_asked_by_profile_id(self):
        h = register_and_login(self.client, "p21m-k@test.bg")
        self.owner(h)
        ivan = self.profile(h, "Иван")
        self.client.post("/memory", json={"text": "Колега.", "profile_id": ivan}, headers=h)
        preview = self.client.get("/memory", params={"profile_id": ivan}, headers=h).json()["preview"]
        self.assertIn("За Иван:", preview)
        by_name = self.client.get("/memory", params={"profile_name": "иван"}, headers=h).json()["preview"]
        self.assertIn("За Иван:", by_name)
        self.client.put("/memory/settings", json={"enabled": False}, headers=h)
        self.assertEqual(self.seen_context(h, name="Иван", profile_id=ivan), "")
        self.assertEqual(self.client.get("/memory", params={"profile_id": ivan}, headers=h).json()["preview"], "")


def make_engine():
    """Нова празна база: SQLite или, с MIGRATION_TEST_DATABASE_URL, отделна база в PostgreSQL (както в test_euro_balance_migration)."""
    from test_euro_balance_migration import make_engine as make
    return make("memory")


def alembic_config(connection):
    cfg = Config()
    cfg.set_main_option("script_location", str(MIGRATIONS))
    cfg.attributes["connection"] = connection
    return cfg


class MemoryMigrationTest(unittest.TestCase):
    """Еднозначните записи се свързват; нееднозначните остават без profile_id."""

    @classmethod
    def setUpClass(cls):
        cls.engine = make_engine()
        with cls.engine.begin() as conn:
            command.upgrade(alembic_config(conn), "0012_ai_reservations")
            for uid, email in ((1, "a@mig.bg"), (2, "b@mig.bg")):
                conn.execute(sa.text("INSERT INTO users (id, email, hashed_password, full_name) VALUES (:i, :e, 'x', 'Тест')"),
                             {"i": uid, "e": email})
            profiles = [(10, 1, "Иван"), (11, 1, "Мария"), (12, 1, "иван"), (20, 2, "Иван")]
            for pid, uid, name in profiles:
                conn.execute(sa.text("INSERT INTO profiles (id, user_id, name, relation, birth_date, unknown_time, is_primary) "
                                     "VALUES (:p, :u, :n, 'friend', '1985-07-12', :no, :no)"), {"p": pid, "u": uid, "n": name, "no": False})
            notes = [(1, 1, None, "Общо за собственика."),            # без име: за собственика
                     (2, 1, "Мария", "Една Мария."),                  # точно един профил
                     (3, 1, "Иван", "Кой Иван?"),                    # два профила (Иван и иван): двусмислено
                     (4, 1, "Пешо", "Няма такъв профил."),            # няма профил
                     (5, 2, "Иван", "Иван на другия акаунт.")]        # един профил при другия собственик
            for nid, uid, name, text in notes:
                conn.execute(sa.text("INSERT INTO memory_notes (id, user_id, profile_name, text) VALUES (:i, :u, :n, :t)"),
                             {"i": nid, "u": uid, "n": name, "t": text})
            command.upgrade(alembic_config(conn), "0013_memory_profile_id")

    def links(self):
        with self.engine.connect() as conn:
            return dict(conn.execute(sa.text("SELECT id, profile_id FROM memory_notes ORDER BY id")).fetchall())

    def test_only_the_unambiguous_records_are_linked(self):
        self.assertEqual(self.links(), {1: None, 2: 11, 3: None, 4: None, 5: 20})

    def test_the_names_are_kept_as_display_copies(self):
        with self.engine.connect() as conn:
            names = dict(conn.execute(sa.text("SELECT id, profile_name FROM memory_notes")).fetchall())
        self.assertEqual(names, {1: None, 2: "Мария", 3: "Иван", 4: "Пешо", 5: "Иван"})

    def test_the_migration_can_be_reverted(self):
        with self.engine.begin() as conn:
            command.downgrade(alembic_config(conn), "0012_ai_reservations")
            columns = {c["name"] for c in sa.inspect(conn).get_columns("memory_notes")}
            self.assertNotIn("profile_id", columns)
            command.upgrade(alembic_config(conn), "0013_memory_profile_id")
        self.assertEqual(self.links(), {1: None, 2: 11, 3: None, 4: None, 5: 20})


if __name__ == "__main__":
    unittest.main()
