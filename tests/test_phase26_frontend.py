"""
Фаза 26 (R07, R08): логиката на екраните за място и отношения, изпълнена с node върху самите файлове на фронтенда.

Сценарии: Париж -> Springfield -> двусмислие/отказ (числата за Париж не важат за Springfield), AI непознат град иска изрично
потвърждение, закъснял отговор не презаписва новите полета (редът на отговорите), всички градове от фиксирания списък се избират с
очакваните числа и Europe/Sofia, собственик е само основният профил, роля родител/дете се предлага само когато я обосновава групата.
"""
import json
import os
import re
import shutil
import subprocess
import tempfile
import unittest

import testenv  # noqa: F401  (пътища и среда)
import engine
import places

FRONTEND = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), "src", "utils")


def run_node(script):
    """Копира birthPlace, bulgarianCities и relations като .mjs (с поправени импорти) и изпълнява скрипта."""
    work = tempfile.mkdtemp()
    try:
        for name in ("birthPlace", "bulgarianCities", "relations"):
            source = open(os.path.join(FRONTEND, f"{name}.js"), encoding="utf-8").read()
            source = source.replace("'./bulgarianCities'", "'./bulgarianCities.mjs'")
            open(os.path.join(work, f"{name}.mjs"), "w", encoding="utf-8").write(source)
        full = f"import * as P from '{work}/birthPlace.mjs'; import * as R from '{work}/relations.mjs'; import {{ bulgarianCities }} from '{work}/bulgarianCities.mjs';\n{script}"
        out = subprocess.run(["node", "--input-type=module", "-e", full], capture_output=True, text=True, timeout=60)
        if out.returncode != 0:
            raise AssertionError(out.stderr)
        return json.loads(out.stdout)
    finally:
        shutil.rmtree(work, ignore_errors=True)


@unittest.skipUnless(shutil.which("node") and os.path.exists(os.path.join(FRONTEND, "birthPlace.js")), "Node.js или файловете на екраните не са налични")
class PlaceLogicTest(unittest.TestCase):
    def test_paris_then_springfield_does_not_keep_the_paris_point(self):
        result = run_node("""
const paris = P.applyGeocodeResult({ mode: 'other', city: 'Paris', country: 'France', lat: '', lon: '', meta: null },
  { status: 'resolved', verified: true, lat: 48.8534, lon: 2.3488, city: 'Париж', country: 'Франция', country_code: 'FR',
    geoname_id: 2988507, canonical_city: 'Paris', timezone: 'Europe/Paris', data_version: 'abc123def456' });
const edited = P.editPlaceText(P.editPlaceText(paris, 'city', 'Springfield'), 'country', 'USA');
const ambiguous = P.applyGeocodeResult(edited, { status: 'ambiguous', candidates: [{ id: 1 }, { id: 2 }] });
console.log(JSON.stringify({ paris, edited, ambiguous, parisOk: P.parseCoordinates(paris), editedCheck: P.parseCoordinates(edited),
  ambiguousCheck: P.parseCoordinates(ambiguous) }));
""")
        self.assertEqual(result["paris"]["meta"]["geoname_id"], 2988507)
        self.assertEqual((result["paris"]["meta"]["timezone"], result["paris"]["meta"]["canonical_city"], result["paris"]["meta"]["source"]),
                         ("Europe/Paris", "Paris", "geonames"))
        self.assertEqual(result["parisOk"], {"lat": 48.8534, "lon": 2.3488})
        for key in ("edited", "ambiguous"):
            self.assertEqual((result[key]["lat"], result[key]["lon"], result[key]["meta"]), ("", "", None), msg=key)
        self.assertIn("error", result["editedCheck"])
        self.assertIn("error", result["ambiguousCheck"])

    def test_an_ai_place_needs_explicit_confirmation_or_manual_coordinates(self):
        result = run_node("""
const base = { mode: 'other', city: 'Нямамград', country: 'България', lat: '', lon: '', meta: null };
const ai = P.applyGeocodeResult(base, { status: 'resolved', verified: false, source: 'ai', lat: 42.5, lon: 25.5, city: 'Нямамград' });
const confirmed = P.confirmAiPlace(ai);
const manual = P.editCoordinate(P.editCoordinate(P.editPlaceText(ai, 'city', 'Нямамград'), 'lat', '42.51'), 'lon', '25.52');
console.log(JSON.stringify({ ai, aiCheck: P.parseCoordinates(ai), blocker: P.placeBlocker(ai), confirmed, confirmedCheck: P.parseCoordinates(confirmed),
  manual, manualCheck: P.parseCoordinates(manual), message: P.AI_UNCONFIRMED }));
""")
        self.assertEqual(result["ai"]["meta"], {"source": "ai", "verified": False, "confirmed": False})
        self.assertEqual(result["aiCheck"], {"error": result["message"]})
        self.assertEqual(result["blocker"], result["message"])
        self.assertEqual(result["confirmedCheck"], {"lat": 42.5, "lon": 25.5})
        self.assertEqual(result["confirmed"]["meta"]["confirmed"], True)
        self.assertEqual((result["manual"]["meta"]["source"], result["manual"]["meta"]["confirmed"]), ("manual", True))
        self.assertEqual(result["manualCheck"], {"lat": 42.51, "lon": 25.52})

    def test_an_error_leaves_no_old_point_and_a_list_city_is_verified(self):
        result = run_node("""
const verified = P.selectListCity(bulgarianCities[0]);
const failed = P.applyGeocodeResult(verified, null);
const chosen = P.choosePlace({ mode: 'other', city: 'Springfield', country: 'USA', lat: '', lon: '', meta: null },
  { id: 4250542, city: 'Springfield', country_code: 'US', timezone: 'America/Chicago', lat: 39.8017, lon: -89.6436 });
console.log(JSON.stringify({ verified, failed, chosen }));
""")
        self.assertEqual(result["verified"]["meta"]["timezone"], "Europe/Sofia")
        self.assertEqual((result["failed"]["lat"], result["failed"]["meta"]), ("", None))
        self.assertEqual((result["chosen"]["meta"]["geoname_id"], result["chosen"]["meta"]["timezone"], result["chosen"]["lat"]),
                         (4250542, "America/Chicago", "39.8017"))

    def test_every_city_of_the_fixed_list_is_selected_with_its_numbers_and_the_sofia_zone(self):
        cities = run_node("console.log(JSON.stringify(bulgarianCities.map((c) => ({ name: c.name, lat: c.lat, lon: c.lon, picked: P.selectListCity(c) }))));")
        self.assertEqual(len(cities), 53)
        self.assertEqual(len({c["name"] for c in cities}), 53)
        tf = engine.get_engine().tf
        for city in cities:
            picked = city["picked"]
            self.assertEqual((picked["lat"], picked["lon"], picked["city"]), (str(city["lat"]), str(city["lon"]), city["name"]))
            self.assertEqual(picked["meta"]["timezone"], "Europe/Sofia", msg=city["name"])
            self.assertEqual(tf.timezone_at(lat=city["lat"], lng=city["lon"]), "Europe/Sofia", msg=city["name"])
            self.assertTrue(41.1 <= city["lat"] <= 44.4 and 22.2 <= city["lon"] <= 28.8, msg=city["name"])

    def test_the_fixed_list_agrees_with_the_geonames_data_where_the_place_is_in_it(self):
        cities = run_node("console.log(JSON.stringify(bulgarianCities.map((c) => ({ name: c.name, lat: c.lat, lon: c.lon }))));")
        checked = 0
        for city in cities:
            found = [p for p in places.search(city["name"], "BG") if p.country == "BG"]
            if found:
                checked += 1
                nearest = min(places.distance_km(city["lat"], city["lon"], p.lat, p.lon) for p in found)
                self.assertLess(nearest, 15, msg=f"{city['name']}: {nearest:.1f} km от GeoNames")
        self.assertGreater(checked, 35)                                                    # повечето градове са в базата над 15 000 жители


@unittest.skipUnless(shutil.which("node") and os.path.exists(os.path.join(FRONTEND, "relations.js")), "Node.js или файловете на екраните не са налични")
class RelationLogicTest(unittest.TestCase):
    def test_the_owner_is_the_primary_profile_only(self):
        result = run_node("""
const owner = { id: 1, name: 'Аз', relation: 'self', is_primary: true };
const second = { id: 2, name: 'Втори аз', relation: 'self', is_primary: false };      // ръчно записан „Аз“, не е собственик
const friend = { id: 3, name: 'Иван', relation: 'friend', is_primary: false };
const child = { id: 4, name: 'Дете', relation: 'child', is_primary: false };
const partner = { id: 5, name: 'Мария', relation: 'partner', is_primary: false };
const colleague = { id: 6, name: 'Петър', relation: 'other', is_primary: false };
console.log(JSON.stringify({
  ownerFriend: R.suggestContext(owner, friend), ownerChild: R.suggestContext(owner, child), ownerPartner: R.suggestContext(owner, partner),
  ownerOther: R.suggestContext(owner, colleague), twoFriends: R.suggestContext(friend, partner), secondSelf: R.suggestContext(second, friend),
  parentChild: R.suggestParentRole(owner, child), parentFriend: R.suggestParentRole(owner, friend), parentNotOwner: R.suggestParentRole(second, child),
  parentTwo: R.suggestParentRole(friend, child), options: R.PARENT_OPTIONS('Мария', 'Иван'),
}));
""")
        self.assertEqual((result["ownerFriend"], result["ownerChild"], result["ownerPartner"], result["ownerOther"]),
                         ("friend", "parent_child", "romantic", ""))
        self.assertEqual((result["twoFriends"], result["secondSelf"]), ("", ""))             # relation='self' не е втори критерий
        self.assertEqual((result["parentChild"], result["parentFriend"], result["parentNotOwner"], result["parentTwo"]), ("user", "", "", ""))
        self.assertEqual([o["value"] for o in result["options"]], ["", "user", "partner"])
        self.assertIn("Мария е родителят", result["options"][1]["label"])
        self.assertIn("Иван е родителят", result["options"][2]["label"])


class SourceGuardTest(unittest.TestCase):
    """Проверки върху изходния код на екраните: нещата, които не се изпълняват с node."""

    def source(self, name):
        return open(os.path.join(os.path.dirname(FRONTEND), name), encoding="utf-8").read()

    def test_a_new_manual_profile_is_other_and_the_generator_gates_the_place(self):
        generate = self.source("pages/GenerateReport.jsx")
        self.assertIn("relation: 'other'", generate)
        self.assertNotRegex(generate, r"relation: 'self'")
        self.assertIn("placeBlocker(birthPlace)", generate)
        self.assertIn("placeBlocker(partnerPlace)", generate)
        self.assertIn("requestData.parent_role", generate)
        self.assertIn("parentRole", generate)

    def test_the_component_ignores_stale_answers_and_invalidates_on_edit(self):
        component = self.source("components/BirthPlace.jsx")
        self.assertIn("sequence.current += 1", component)
        self.assertIn("mine !== sequence.current", component)
        self.assertIn("editPlaceText", component)
        self.assertIn("Потвърждавам тези координати", component)
        profiles = self.source("pages/Profiles.jsx")
        self.assertIn("hasOwner ? 'other' : 'self'", profiles)


if __name__ == "__main__":
    unittest.main()
