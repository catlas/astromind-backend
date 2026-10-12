"""
Фаза 26 (регресионен одит 11.10.2026): R07 град и профили, R08 отношения.

- профилът се записва само с истинска дата и час (не само по регулярен израз);
- „Родител и дете“: ролята е избор на потребителя и стига до промпта, непълнолетното дете има отделно правило;
- заглавията и тонът в шаблоните на темите съответстват на отношенията (не „Романтична атмосфера“ за приятели);
- паметта е по идентичност (номер на профил), показаното име е етикет: две лица с едно име не губят бележки;
- геокодирането връща идентификатор на GeoNames, зона, версия и източник като една стойност; AI резултатът иска потвърждение.
"""
import unittest
from unittest import mock

import testenv  # noqa: F401  (трябва да е преди main)
from fastapi.testclient import TestClient  # noqa: E402

import ai_interpreter  # noqa: E402
import generation  # noqa: E402
import geocode_api  # noqa: E402
import main  # noqa: E402
import memory  # noqa: E402
import places  # noqa: E402
import relationship  # noqa: E402
from database import MemoryNote, Profile, SessionLocal, User  # noqa: E402
from rate_limit import limiter  # noqa: E402
from schemas import ChartRequest  # noqa: E402
from testenv import CHART, register_and_login  # noqa: E402


class ProfileValidationTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.client = TestClient(main.app)

    def setUp(self):
        limiter.reset()
        self.headers = register_and_login(self.client, f"p26-{id(self)}@test.bg")

    def save(self, **fields):
        body = {"name": f"Профил{abs(hash(str(fields))) % 10**6}", "relation": "friend", "birth_date": "1985-07-12", "birth_time": "18:45",
                "lat": 48.2082, "lon": 16.3738, **fields}
        return self.client.post("/profiles", json=body, headers=self.headers)

    def test_a_real_date_and_time_are_saved(self):
        self.assertEqual(self.save().status_code, 200)
        self.assertEqual(self.save(birth_date="2024-02-29", birth_time="00:00").status_code, 200)       # високосна година
        self.assertEqual(self.save(birth_time="23:59:59").status_code, 200)

    def test_impossible_dates_are_refused_even_if_the_pattern_matches(self):
        for date in ("2023-02-30", "1985-13-01", "1985-00-10", "2023-04-31", "1900-02-29"):
            response = self.save(birth_date=date)
            self.assertEqual(response.status_code, 400, msg=date)
            self.assertIn("Невалидна дата", response.json()["detail"])

    def test_the_future_and_dates_outside_the_ephemeris_are_refused(self):
        future = self.save(birth_date="2099-01-01")
        self.assertEqual(future.status_code, 400)
        self.assertIn("бъдещето", future.json()["detail"])
        outside = self.save(birth_date="1700-01-01")
        self.assertEqual(outside.status_code, 400)
        self.assertIn("диапазон", outside.json()["detail"])
        self.assertIn("диапазон", self.save(birth_date="2999-01-01").json()["detail"])

    def test_impossible_times_are_refused(self):
        for time in ("25:00", "12:60", "24:00", "12:30:61"):
            self.assertEqual(self.save(birth_time=time).status_code, 400, msg=time)
        self.assertEqual(self.save(birth_time="ab:cd").status_code, 400)


class ParentRoleTest(unittest.TestCase):
    def request(self, **extra):
        base = {**CHART, "name": "Мария", "partner_name": "Иван", "partner_date": "2015-03-10", "partner_time": "10:00",
                "partner_lat": 42.6977, "partner_lon": 23.3219, "relationship": "parent_child"}
        return ChartRequest(**{**base, **extra})

    def test_the_role_is_only_kept_for_parent_and_child(self):
        self.assertEqual(self.request(parent_role="user").parent_role, "user")
        self.assertIsNone(self.request(relationship="friend", parent_role="user").parent_role)
        self.assertIsNone(self.request().parent_role)
        with self.assertRaises(ValueError):
            self.request(parent_role="mother")

    def test_the_prompt_names_the_parent_and_never_guesses_without_a_choice(self):
        names = ("Мария", "Иван")
        self.assertIn("Мария is the parent and Иван is the child", relationship.block("parent_child", "user", names))
        self.assertIn("Иван is the parent and Мария is the child", relationship.block("parent_child", "partner", names))
        self.assertNotIn("ROLES", relationship.block("parent_child", None, names))
        self.assertIn("do not guess who is the parent", relationship.block("parent_child"))
        self.assertNotIn("ROLES", relationship.block("friend", "user", names))

    def test_a_minor_child_gets_the_protective_rule_and_an_adult_does_not(self):
        child = self.request(parent_role="user")                                   # детето е родено 2015
        self.assertTrue(generation.child_is_minor(child, today="2026-10-12"))
        self.assertFalse(generation.child_is_minor(child, today="2033-03-10"))      # навършва 18 на 10.03.2033
        self.assertTrue(generation.child_is_minor(child, today="2033-03-09"))
        self.assertFalse(generation.child_is_minor(self.request(), today="2026-10-12"))              # без избрана роля не се гадае
        self.assertFalse(generation.child_is_minor(self.request(relationship="friend", parent_role="user"), today="2026-10-12"))
        text = relationship.block("parent_child", "user", ("Мария", "Иван"), minor=True)
        self.assertIn("MINOR (under 18)", text)
        self.assertIn("Never use a romantic or sexual frame", text)
        self.assertNotIn("MINOR", relationship.block("parent_child", "user", ("Мария", "Иван"), minor=False))

    def test_the_other_way_round_the_first_person_is_the_child(self):
        request = self.request(date="2015-03-10", partner_date="1985-07-12", parent_role="partner")
        self.assertTrue(generation.child_is_minor(request, today="2026-10-12"))

    def test_the_role_is_saved_with_the_report_params(self):
        params = generation.report_params(self.request(parent_role="partner"))
        self.assertEqual((params["relationship"], params["parent_role"]), ("parent_child", "partner"))
        self.assertNotIn("parent_role", generation.report_params(self.request()))


class RelationshipTitlesTest(unittest.TestCase):
    TEMPLATE = ("FOCUS: Romantic timing, relationship dynamics, and partnership opportunities.\n1. Romantic Atmosphere for this Month\n"
                "2. Key Dates (best times for romance, talks, or intimacy)\nTONE: Romantic, insightful, sensitive.")

    def adapted(self, code):
        token = relationship.bind(code)
        try:
            return relationship.adapt(self.TEMPLATE)
        finally:
            relationship.unbind(token)

    def test_non_romantic_contexts_do_not_get_romantic_headings(self):
        for code in ("friend", "family", "parent_child", "work", "general"):
            text = self.adapted(code)
            for word in ("Romantic", "romance", "intimacy"):
                self.assertNotIn(word, text, msg=(code, word))
            self.assertIn("Atmosphere of", text)
        self.assertIn("friendship", self.adapted("friend"))
        self.assertIn("collaboration", self.adapted("work"))

    def test_a_romantic_relationship_and_no_context_keep_the_template(self):
        self.assertEqual(self.adapted("romantic"), self.TEMPLATE)
        self.assertEqual(relationship.adapt(self.TEMPLATE), self.TEMPLATE)               # без контекст нищо не се променя

    def test_the_real_templates_are_adapted_in_every_request(self):
        templates = [v for name in dir(ai_interpreter) for v in [getattr(ai_interpreter, name)]
                     if isinstance(v, dict) for v in v.values() if isinstance(v, str) and "Romantic Atmosphere" in v]
        self.assertTrue(templates)
        token = relationship.bind("friend")
        try:
            for template in templates:
                self.assertNotIn("Romantic Atmosphere", relationship.adapt(template))
        finally:
            relationship.unbind(token)
        self.assertEqual(relationship.current_code.get(), "")                                # връзката е свалена след анализа


class MemoryIdentityTest(unittest.TestCase):
    def test_two_people_with_one_displayed_name_keep_their_own_notes(self):
        db = SessionLocal()
        try:
            user = User(email="p26-memory@test.bg", hashed_password="x", full_name="Т")
            db.add(user)
            db.flush()
            first = Profile(user_id=user.id, name="Иван А", relation="friend", birth_date="1985-07-12")
            second = Profile(user_id=user.id, name="Иван Б", relation="friend", birth_date="1990-01-01")
            owner = Profile(user_id=user.id, name="Аз", relation="self", birth_date="1980-01-01", is_primary=True)
            db.add_all([first, second, owner])
            db.flush()
            db.add_all([MemoryNote(user_id=user.id, profile_id=first.id, profile_name="Иван А", text="Първият е учител."),
                        MemoryNote(user_id=user.id, profile_id=second.id, profile_name="Иван Б", text="Вторият е шофьор.")])
            db.flush()
            groups = memory.notes_by_person(db, user, [("Иван", first.id), ("Иван", second.id)])        # еднакво показано име
            self.assertEqual(len(groups), 2)
            texts = sorted(n.text for notes in groups.values() for n in notes)
            self.assertEqual(texts, ["Вторият е шофьор.", "Първият е учител."])
            self.assertEqual(len(set(groups)), 2)                                                        # етикетите се различават
            self.assertIn("Иван", list(groups)[0])
            db.rollback()
        finally:
            db.close()


class GeocodeIdentityTest(unittest.TestCase):
    def test_a_verified_place_is_one_value_with_id_zone_version_and_source(self):
        place = places.search("Wien", "AT")[0]
        answer = geocode_api.verified_answer(place, "Виена", "Австрия", None)
        self.assertEqual((answer["verified"], answer["source"], answer["geoname_id"]), (True, "geonames", place.id))
        self.assertEqual(answer["timezone"], "Europe/Vienna")
        self.assertEqual(answer["canonical_city"], place.name)
        self.assertEqual(len(answer["data_version"]), 12)

    def test_an_unknown_place_comes_from_the_ai_and_needs_an_explicit_confirmation(self):
        import asyncio
        import json
        from types import SimpleNamespace
        reply = json.dumps({"found": True, "city": "Нямамград", "country": "България", "city_latin": "Nyamamgrad", "country_code": "BG",
                            "lat": 42.5, "lon": 25.5})
        limiter.reset()
        with mock.patch.object(geocode_api, "get_interpreter", return_value=SimpleNamespace(_call_api=mock.AsyncMock(return_value=reply))):
            answer = asyncio.run(geocode_api.geocode(geocode_api.GeocodeIn(city="Нямамград", country="България"),
                                                      current_user=SimpleNamespace(id=999001)))
        self.assertEqual((answer["verified"], answer["source"], answer["needs_confirmation"]), (False, "ai", True))
        self.assertNotIn("geoname_id", answer)

    def test_the_source_and_the_coordinate_criterion_are_documented_in_the_data(self):
        info = places.source_info()
        self.assertEqual((info["source"], info["license"]), ("GeoNames cities15000", "CC BY 4.0"))
        self.assertIn("център", info["criterion"])
        self.assertTrue(info["version"])


if __name__ == "__main__":
    unittest.main()
