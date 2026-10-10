"""
Тестове за местата от проверим източник (Фаза 12): GeoNames cities15000 в data/places.sqlite.gz и POST /geocode.

Пускане: python -m unittest discover -s tests -p "test_places.py"
"""
import json
import os
import re
import unittest
from unittest import mock

import testenv  # noqa: F401  (трябва да е преди main)
from fastapi.testclient import TestClient  # noqa: E402

import geocode_api  # noqa: E402
import main  # noqa: E402
import places  # noqa: E402
from rate_limit import limiter  # noqa: E402
from testenv import register_and_login  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CITIES_JS = os.path.join(os.path.dirname(ROOT), "src", "utils", "bulgarianCities.js")


def ai(**fields):
    return mock.AsyncMock(return_value=json.dumps({"found": True, **fields}, ensure_ascii=False))


class DatabaseTest(unittest.TestCase):
    def test_the_packed_file_is_there_and_described(self):
        self.assertTrue(places.available())
        meta = places.info()
        self.assertIn("CC BY 4.0", meta["license"])
        self.assertEqual(meta["sha256"], "9339ac426943c265eb7fdfd5350ae351a5584b4d4271ed58f071ddc432982aeb")
        self.assertEqual(meta["places"], "34156")

    def test_normalize(self):
        self.assertEqual(places.normalize("  Wïen "), "wien")
        self.assertEqual(places.normalize("Köln"), "koln")
        self.assertEqual(places.normalize("Ню-Йорк"), "ню йорк")          # „й“ не става „и“
        self.assertNotEqual(places.normalize("Йордания"), places.normalize("Иордания"))
        self.assertEqual(places.normalize("Straße"), "strasse")
        self.assertEqual(places.normalize(""), "")

    def test_names_in_several_languages_find_the_same_place(self):
        for query in ("Виена", "Vienna", "Wien", "Wïen"):
            with self.subTest(query=query):
                found = places.search(query, "AT")
                self.assertEqual([p.name for p in found], ["Vienna"])
        for query in ("Köln", "Cologne", "Кьолн"):
            with self.subTest(query=query):
                self.assertEqual(places.search(query, "DE")[0].name, "Köln")

    def test_the_same_name_in_two_countries_is_not_merged(self):
        everywhere = places.search("Paris")
        self.assertEqual({p.country for p in everywhere} >= {"FR", "US"}, True)
        self.assertEqual(places.search("Paris", "FR")[0].population > 2_000_000, True)
        self.assertEqual({p.country for p in places.search("Paris", "FR")}, {"FR"})
        self.assertEqual(places.search("Париж", "FR")[0].name, "Paris")

    def test_unknown_and_empty_names(self):
        self.assertEqual(places.search("Нищоград"), [])
        self.assertEqual(places.search(""), [])
        self.assertEqual(places.search("   "), [])

    def test_a_quote_is_not_a_query_hazard(self):
        self.assertEqual(places.search("'; DROP TABLE place; --"), [])
        self.assertTrue(places.search("Vienna"))                 # таблицата си е на място

    def test_coordinates_are_sane_and_match_known_places(self):
        sofia = places.search("София", "BG")[0]
        self.assertLess(places.distance_km(sofia.lat, sofia.lon, 42.6977, 23.3219), 10)
        self.assertEqual(sofia.timezone, "Europe/Sofia")
        vienna = places.search("Wien", "AT")[0]
        self.assertLess(places.distance_km(vienna.lat, vienna.lon, 48.2082, 16.3738), 10)
        self.assertEqual(vienna.timezone, "Europe/Vienna")

    def test_the_cities_of_the_form_agree_with_geonames(self):
        """Градовете от падащото меню (проверени в одита) отстоят най-много на 5 км от GeoNames, където ги има."""
        with open(CITIES_JS, encoding="utf-8") as handle:
            rows = re.findall(r'name:\s*"([^"]+)",\s*lat:\s*([\d.]+),\s*lon:\s*([\d.]+)', handle.read())
        self.assertGreater(len(rows), 40)
        compared = 0
        for name, lat, lon in rows:
            found = places.search(name, "BG")
            if not found:
                continue                                          # под 15 000 жители: няма в базата
            compared += 1
            gap = places.distance_km(float(lat), float(lon), found[0].lat, found[0].lon)
            self.assertLess(gap, 5, f"{name}: {gap:.1f} km")
        self.assertGreater(compared, 25)

    def test_distance(self):
        self.assertAlmostEqual(places.distance_km(0, 0, 0, 0), 0)
        self.assertAlmostEqual(places.distance_km(42.6977, 23.3219, 48.2082, 16.3738), 825, delta=15)


class GeocodeTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.client = TestClient(main.app)

    def setUp(self):
        limiter.reset()
        self.headers = register_and_login(self.client, f"pl-{id(self)}@test.bg")

    def call(self, typed_city, typed_country, reply=None, **extra):
        fake = reply if reply is not None else ai(**extra)
        with mock.patch.object(main.ai_interpreter, "_call_api", fake):
            return self.client.post("/geocode", json={"city": typed_city, "country": typed_country}, headers=self.headers)

    def test_a_place_in_the_database_is_verified_and_gets_its_coordinates_from_it(self):
        r = self.call("Виена", "Австрия", city="Виена", country="Австрия", city_latin="Wien", country_code="AT", lat=48.21, lon=16.37)
        body = r.json()
        self.assertEqual((r.status_code, body["status"], body["verified"], body["source"]), (200, "resolved", True, "geonames"))
        self.assertLess(places.distance_km(body["lat"], body["lon"], 48.2082, 16.3738), 5)

    def test_the_ai_coordinates_are_not_used_when_the_database_knows_the_place(self):
        r = self.call("Виена", "Австрия", city="Виена", city_latin="Wien", country_code="AT", lat=48.30, lon=16.50)   # AI грешка ~12 km
        self.assertEqual(r.json()["source"], "geonames")
        self.assertNotEqual(r.json()["lat"], 48.30)

    def test_paris_in_france_is_not_paris_in_texas(self):
        r = self.call("Париж", "Франция", city="Париж", country="Франция", city_latin="Paris", country_code="FR", lat=48.85, lon=2.35)
        body = r.json()
        self.assertEqual((body["status"], body["country_code"]), ("resolved", "FR"))
        self.assertLess(places.distance_km(body["lat"], body["lon"], 48.8566, 2.3522), 10)
        tx = self.call("Paris", "USA", city="Paris", city_latin="Paris", country_code="US", lat=33.66, lon=-95.55).json()
        self.assertEqual(tx["country_code"], "US")
        self.assertLess(tx["lon"], -90)

    def test_a_wrong_place_from_the_ai_is_not_accepted_for_the_typed_city(self):
        # Въведено „Париж, Франция“, а AI върнало Виена: Виена не е близо до нищо с името Париж, но AI-името се търси
        # само в посочената от AI държава (AT) и пак е „Виена“ - затова проверката на България в полето е последна защита
        r = self.call("Банско", "България", city="Виена", country="Австрия", city_latin="Wien", country_code="AT", lat=48.2, lon=16.4)
        self.assertEqual(r.status_code, 404)

    def test_a_match_far_from_the_ai_point_is_not_trusted(self):
        # AI посочва точка в Африка за „Нанси, Франция“: съвпадението в базата е на хиляди километри и не се приема;
        # остава резултатът на AI, но непроверен (екранът го показва за проверка)
        r = self.call("Нанси", "Франция", city="Нанси", city_latin="Nancy", country_code="FR", lat=10.0, lon=10.0)
        body = r.json()
        self.assertEqual((r.status_code, body["verified"], body["source"]), (200, False, "ai"))

    def test_ambiguity_is_resolved_by_close_ai_coordinates_or_left_to_the_user(self):
        near = self.call("Springfield", "USA", city="Springfield", city_latin="Springfield", country_code="US", lat=42.10, lon=-72.59).json()
        self.assertEqual((near["status"], near["source"]), ("resolved", "geonames"))
        self.assertLess(places.distance_km(near["lat"], near["lon"], 42.1015, -72.5898), 30)
        # без AI (недостъпно) и без държава има няколко съвпадения: избор
        boom = mock.AsyncMock(side_effect=RuntimeError("недостъпно"))
        r = self.call("Paris", "САЩ", reply=boom)
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.json()["status"], "ambiguous")
        self.assertGreater(len(r.json()["candidates"]), 1)
        self.assertTrue(all({"lat", "lon", "country_code", "population", "timezone"} <= set(c) for c in r.json()["candidates"]))
        populations = [c["population"] for c in r.json()["candidates"]]
        self.assertEqual(populations, sorted(populations, reverse=True))

    def test_without_the_ai_an_unambiguous_place_is_still_found(self):
        boom = mock.AsyncMock(side_effect=RuntimeError("недостъпно"))
        r = self.call("Пловдив", "България", reply=boom)
        self.assertEqual((r.status_code, r.json()["verified"], r.json()["country_code"]), (200, True, "BG"))

    def test_a_small_place_is_unverified_with_the_ai_result(self):
        r = self.call("Банско", "България", city="Банско", country="България", city_latin="Bansko", country_code="BG", lat=41.8383, lon=23.4885)
        body = r.json()
        self.assertEqual((r.status_code, body["verified"], body["source"], body["status"]), (200, False, "ai", "resolved"))
        self.assertEqual((body["lat"], body["lon"]), (41.8383, 23.4885))

    def test_bulgaria_in_the_field_does_not_accept_a_place_from_another_country(self):
        r = self.call("Виена", "България", city="Виена", city_latin="Wien", country_code="AT", lat=48.2, lon=16.4)
        self.assertEqual(r.status_code, 404)

    def test_a_city_that_does_not_exist(self):
        for reply in ({"found": False}, {"found": True, "lat": 0, "lon": 0}):
            r = self.call("Нищоград", "Нищоландия", reply=mock.AsyncMock(return_value=json.dumps(reply)))
            self.assertEqual(r.status_code, 404)

    def test_the_prompt_asks_for_a_latin_name_and_a_country_code(self):
        self.assertIn("city_latin", geocode_api.SYSTEM_PROMPT)
        self.assertIn("country_code", geocode_api.SYSTEM_PROMPT)


if __name__ == "__main__":
    unittest.main()
