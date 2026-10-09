"""
Фаза 8: верни данни към AI. Всичко е офлайн: AI не се вика, подканата се прихваща.

Еталонът (tests/fixtures/phase8_reference.json) е независимо изчисление (Swiss Ephemeris, Moshier,
зони през zoneinfo) върху синтетичните карти A и Иван. Производственият двигател ползва други файлове
с ефемериди и друг код за часовите зони, затова съвпадението е истинска проверка.

Пускане: python -m unittest discover -s tests -p "test_phase8_facts.py"
"""
import asyncio
import json
import os
import re
import unittest
from unittest import mock

import testenv  # noqa: F401  (трябва да е преди main)
from fastapi.testclient import TestClient  # noqa: E402

import ai_interpreter  # noqa: E402
import engine  # noqa: E402
import factpack  # noqa: E402
import limits  # noqa: E402
import main  # noqa: E402
from aspects_engine import TRANSIT_SNAPSHOT_MAX_ORB  # noqa: E402
from rate_limit import limiter  # noqa: E402
from scanner import TransitScanner  # noqa: E402
from testenv import register_and_login  # noqa: E402

FIXTURE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "fixtures", "phase8_reference.json")
REF = json.load(open(FIXTURE, encoding="utf-8"))
NAME_MAP = {"TrueNode": "Node"}          # в еталона възелът е TrueNode, в двигателя Node
THEMES = ["general", "health", "career", "money", "love", "karmic"]
PERIOD = ("2026-10-01", "2026-11-30")

A_BIRTH = dict(date="1990-02-15", time="13:00", lat=42.6977, lon=23.3219)
IVAN_BIRTH = dict(date="1985-07-12", time="18:45", lat=48.2082, lon=16.3738)
SIGNS = ["Aries", "Taurus", "Gemini", "Cancer", "Leo", "Virgo", "Libra", "Scorpio", "Sagittarius",
         "Capricorn", "Aquarius", "Pisces"]


def mapped(d):
    return {NAME_MAP.get(k, k): v for k, v in d.items()}


def angle_between(a, b):
    return abs((a - b + 180) % 360 - 180)


def wrap180(x):
    return (x + 180) % 360 - 180


def parse_sections(user_prompt):
    """{заглавие: данни} за всеки блок '--- ЗАГЛАВИЕ ---', чиито данни са JSON на един ред."""
    sections = {}
    chunks = re.split(r"^--- (.+?) ---$", user_prompt, flags=re.M)
    for i in range(1, len(chunks), 2):
        title, body = chunks[i], chunks[i + 1]
        for line in body.splitlines():
            line = line.strip()
            if line.startswith(("{", "[")):
                try:
                    sections[title] = json.loads(line)
                except ValueError:
                    pass
                break
    return sections


def find_section(sections, startswith):
    for title, payload in sections.items():
        if title.startswith(startswith):
            return payload
    raise AssertionError(f"Няма секция '{startswith}'. Налични: {list(sections)}")


class Phase8Base(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.eng = engine.AstrologyEngine()
        cls.a = cls.eng.calculate_chart(**A_BIRTH)
        cls.ivan = cls.eng.calculate_chart(**IVAN_BIRTH)
        cls.t_pair = cls.eng.calculate_chart(date="2026-10-08", time="12:00", lat=A_BIRTH["lat"], lon=A_BIRTH["lon"])
        cls.t_ivan = cls.eng.calculate_chart(date="2026-10-08", time="12:00", lat=IVAN_BIRTH["lat"], lon=IVAN_BIRTH["lon"])


class EngineMatchesIndependentReferenceTest(Phase8Base):
    """Изходната точка: нашият двигател съвпада с независимия контрол."""

    def check_chart(self, chart, ref, arcmin=1.0):
        for name, lon in mapped(ref["longitude"]).items():
            with self.subTest(body=name):
                self.assertLessEqual(angle_between(chart["planets"][name]["longitude"], lon) * 60, arcmin)
        for i, cusp in enumerate(ref["cusps"], start=1):
            with self.subTest(cusp=i):
                self.assertLessEqual(angle_between(chart["houses"][f"House{i}"], cusp) * 60, 2.0)
        for name, house in mapped(ref["house"]).items():
            with self.subTest(house_of=name):
                self.assertEqual(chart["planets"][name]["house"], house)

    def test_natal_a(self):
        self.check_chart(self.a, REF["A"])

    def test_natal_ivan(self):
        self.check_chart(self.ivan, REF["IVAN"])

    def test_transit_moments_are_the_same_physical_instants(self):
        for chart, key in ((self.t_ivan, "T_IVAN"), (self.t_pair, "T_PAIR")):
            with self.subTest(moment=key):
                for name, lon in mapped(REF[key]["longitude"]).items():
                    self.assertLessEqual(angle_between(chart["planets"][name]["longitude"], lon) * 60, 1.0, name)


class FactpackTest(Phase8Base):
    def test_house_table_has_sign_degree_and_ruler_for_all_twelve_houses(self):
        table = factpack.house_table(self.ivan)
        self.assertEqual(sorted(table), sorted(f"House{i}" for i in range(1, 13)))
        # Куспидите и управителите, които AI по-рано сбъркваше (одит: LIVE-01/02/03)
        self.assertEqual(table["House6"], {"cusp": "Gemini 3°22'", "ruler": "Mercury"})
        self.assertEqual(table["House8"], {"cusp": "Cancer 29°46'", "ruler": "Moon"})
        self.assertEqual(table["House4"], {"cusp": "Aries 19°39'", "ruler": "Mars"})
        self.assertEqual(table["House2"], {"cusp": "Capricorn 29°46'", "ruler": "Saturn"})
        for i, cusp in enumerate(REF["IVAN"]["cusps"], start=1):
            expected_sign = SIGNS[int(cusp // 30)]
            self.assertTrue(table[f"House{i}"]["cusp"].startswith(expected_sign), (i, table[f"House{i}"], cusp))

    def test_modern_rulers_for_scorpio_aquarius_pisces(self):
        # Решение на потребителя: само модерни управители
        table = factpack.house_table(self.a)
        self.assertEqual(table["House6"]["ruler"], "Pluto")      # куспида в Скорпион
        self.assertEqual(table["House10"]["ruler"], "Neptune")   # куспида в Риби
        rulers = factpack.house_rulers(self.a)
        self.assertEqual(sorted(rulers), sorted(f"house_{i}_ruler" for i in range(1, 13)))

    def test_natal_view_has_no_raw_numbers_and_correct_houses(self):
        view = factpack.natal_view(self.ivan)
        for name, planet in view["planets"].items():
            self.assertEqual(set(planet), {"zodiac_sign", "formatted_pos", "house", "retrograde"}, name)
        for name, house in mapped(REF["IVAN"]["house"]).items():
            self.assertEqual(view["planets"][name]["house"], house, name)
        dumped = json.dumps(view)
        for raw_key in ("longitude", "speed", "distance"):
            self.assertNotIn(f'"{raw_key}"', dumped)

    def test_retrograde_flags_and_count_come_from_the_same_data(self):
        view = factpack.natal_view(self.ivan)
        ref_retro = {NAME_MAP.get(k, k) for k, v in REF["IVAN"]["speed"].items() if v < 0}
        for name in REF["IVAN"]["longitude"]:
            name = NAME_MAP.get(name, name)
            self.assertEqual(view["planets"][name]["retrograde"], name in ref_retro, name)
        self.assertEqual(view["retrograde_count"], len(view["retrograde_planets"]))
        self.assertTrue(set(view["retrograde_planets"]) <= set(factpack.RETROGRADE_BODIES))

    def test_transit_view_never_carries_a_moment_chart_house(self):
        view = factpack.transit_view(self.t_ivan)
        self.assertNotIn('"house"', json.dumps(view))
        self.assertTrue(view["datetime_utc"].startswith("2026-10-08T10:00"))   # 12:00 Виена

    def test_overlays_match_reference_both_directions(self):
        cases = [
            (self.a, self.ivan, "IVAN_in_A"), (self.ivan, self.a, "A_in_IVAN"),
            (self.a, self.t_pair, "T_PAIR_in_A"), (self.ivan, self.t_pair, "T_PAIR_in_IVAN"),
            (self.ivan, self.t_ivan, "T_IVAN_in_IVAN"),
        ]
        for houses_chart, planets_chart, key in cases:
            with self.subTest(overlay=key):
                got = factpack.overlay(houses_chart, planets_chart)
                for name, house in mapped(REF["overlays"][key]).items():
                    self.assertEqual(got[name], house, name)

    def test_the_two_synastry_directions_differ(self):
        # Иван → A и A → Иван са различни факти (одит: LIVE-09)
        self.assertNotEqual(factpack.overlay(self.a, self.ivan), factpack.overlay(self.ivan, self.a))
        self.assertEqual(factpack.overlay(self.a, self.ivan)["Sun"], 2)

    def test_transit_aspects_match_independent_computation(self):
        natal_ref, moment_ref = REF["IVAN"], REF["T_IVAN"]
        angles = {"ASC": natal_ref["angles"]["ASC"], "MC": natal_ref["angles"]["MC"]}
        natal_points = {**mapped(natal_ref["longitude"]), **angles}
        transit_points = mapped(moment_ref["longitude"])
        speeds = mapped(moment_ref["speed"])
        expected = {}
        for t_name, t_lon in transit_points.items():
            for n_name, n_lon in natal_points.items():
                dist = angle_between(t_lon, n_lon)
                for aspect, exact in (("conjunction", 0), ("opposition", 180), ("square", 90), ("trine", 120), ("sextile", 60)):
                    orb = abs(dist - exact)
                    if orb <= TRANSIT_SNAPSHOT_MAX_ORB:
                        nearest = min((wrap180(t_lon - e) for e in {(n_lon + exact) % 360, (n_lon - exact) % 360}), key=abs)
                        expected[(t_name, n_name, aspect)] = (orb, nearest * speeds[t_name] < 0)
        got = factpack.transit_aspects(self.ivan, self.t_ivan)
        got_map = {(r["transit_planet"], r["natal_planet"], r["aspect"]): r for r in got
                   if "Chiron" not in (r["transit_planet"], r["natal_planet"])}
        near_threshold = {k for k, (orb, _) in expected.items() if abs(orb - TRANSIT_SNAPSHOT_MAX_ORB) < 0.05}
        for key, (orb, applying) in expected.items():
            if key in near_threshold:
                continue
            with self.subTest(expected=key):
                self.assertIn(key, got_map)
                self.assertAlmostEqual(got_map[key]["orb"], orb, delta=0.05)
                self.assertEqual(got_map[key].get("applying"), applying)
        for key in got_map:
            if key in near_threshold:
                continue
            with self.subTest(unexpected=key):
                self.assertIn(key, expected)
        # Конкретните грешки от одита
        pairs = {(k[0], k[1]) for k in got_map}
        self.assertIn(("Neptune", "Neptune"), pairs)                  # квадратура ~0,9°
        self.assertNotIn(("Saturn", "Moon"), pairs)                   # LIVE-06: такъв аспект няма
        self.assertNotIn(("Saturn", "Mercury"), pairs)                # LIVE-05: ъгълът е ~125,8°
        for row in got:
            self.assertLessEqual(row["orb"], TRANSIT_SNAPSHOT_MAX_ORB)

    def test_synastry_aspects_name_the_owner_of_each_planet(self):
        rows = factpack.synastry_aspects(self.a, self.ivan, "A", "Иван")
        self.assertTrue(rows)
        for row in rows:
            self.assertEqual((row["person1"], row["person2"]), ("A", "Иван"))
            if row["planet2"] in self.ivan["planets"] and row["planet1"] in self.a["planets"]:
                angle = angle_between(self.a["planets"][row["planet1"]]["longitude"],
                                      self.ivan["planets"][row["planet2"]]["longitude"])
                exact = {"conjunction": 0, "sextile": 60, "square": 90, "trine": 120, "opposition": 180}[row["aspect"]]
                self.assertAlmostEqual(abs(angle - exact), row["orb"], delta=0.02)

    def test_display_name_cleaning_and_defaults(self):
        self.assertEqual(factpack.display_name(None, "Първи човек"), "Първи човек")
        self.assertEqual(factpack.display_name("   ", "Втори човек"), "Втори човек")
        self.assertEqual(factpack.display_name("Иван\n{ignore}\u0000 всичко", "x"), "Иван ignore всичко")
        self.assertEqual(len(factpack.display_name("а" * 500, "x")), 60)

    def test_gender_label_only_known_values(self):
        for raw, expected in (("male", "male"), ("female", "female"), ("Мъж", "male"), ("жена", "female"),
                              ("other", "unknown"), ("unknown", "unknown"), ("", "unknown"), (None, "unknown")):
            self.assertEqual(factpack.gender_label(raw), expected, raw)


FORBIDDEN_IN_INSTRUCTIONS = [
    (re.compile(r"\"?Sun\"?\\?\"?\s*:\s*8"), "числов пример за наслагване (Слънце 8)"),
    (re.compile(r"\"?Mars\"?\\?\"?\s*:\s*12"), "числов пример за наслагване (Марс 12)"),
    (re.compile(r"\"?Moon\"?\\?\"?\s*:\s*1\b"), "числов пример за наслагване (Луна 1)"),
    (re.compile(r"\b(her|hers|she)\b", re.I), "женско местоимение"),
    (re.compile(r"MANDATORY SECTION"), "вътрешен етикет „MANDATORY SECTION“"),
    (re.compile(r"probability of the event|Оцени вероятността", re.I), "инструкция за вероятност"),
    (re.compile(r"ЕВГЕНИ|КРАСИМИРА|ЦАРИНА|АНДОНОВА"), "реални имена в примерите"),
    (re.compile(r"Backend does not provide cross-chart aspects|NO ASPECT CALCULATIONS"), "невярно твърдение, че аспекти липсват"),
    # Примерни изречения с конкретна планета, знак, градус, дом или дата: AI ги преписва като факти.
    (re.compile(r"Jupiter return|Saturn pressure|The Protective Shell|wound of the leader"), "примерна оценка на транзит или аспект"),
    (re.compile(r"Mars in your 1st house|Sun in your 10th house|Jupiter in your 2nd house|Pluto opposition your Venus"),
     "примерно наслагване или аспект с конкретна планета и дом"),
    (re.compile(r"Moon in Capricorn|Луна в Козирог|MC in Овен|ruler Mars|MC Ruler: Venus"), "примерно разположение на планета или MC"),
    (re.compile(r"Cancer 14°|Aries 23°|14°22|23°02|Cancer 29°46"), "примерен градус"),
    (re.compile(r"ЯНУАРИ 2026|March 28, 2025|January 27, 2026|Full Moon in Cancer"), "примерна дата или лунация"),
    (re.compile(r"angle_deg': 120|Sun Square Pluto|Venus trine Neptune|Money Ruler \(2nd House\): Sun"), "примерен аспект или управител"),
    (re.compile(r"if 2nd House cusp is in Leo|if 8th House cusp is in Aquarius|\"house_2_ruler\": \"Sun\""), "примерен куспид с управител"),
]

# Същото за потребителската част: само изрази, които не могат да се появят в самите данни.
FORBIDDEN_IN_USER_INSTRUCTIONS = [
    (re.compile(r"The Protective Shell|fiery Sun in Aries|Ascendant in Cancer"), "примерен Асцендент в инструкциите"),
    (re.compile(r"Jupiter return|Saturn pressure|Mars transits \(conflict"), "примерен транзит в инструкциите"),
    (re.compile(r"АНАЛИЗ ЗА ЯНУАРИ 2026"), "примерен месец в заглавието"),
]


class PromptPayloadTest(Phase8Base):
    """Подканите за всичките 36 комбинации: 6 теми x 3 вида x 2 цели (един човек / двама)."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.interp = ai_interpreter.AIInterpreter(api_key="test-dummy")
        # реалният календар за периода, веднъж за целия клас
        scanner = TransitScanner()
        cls.events_ivan = scanner.scan_period(natal_chart=cls.ivan, start_date=PERIOD[0], end_date=PERIOD[1],
                                              lat=IVAN_BIRTH["lat"], lon=IVAN_BIRTH["lon"])
        cls.events_pair = TransitScanner().scan_period(natal_chart=cls.a, start_date=PERIOD[0], end_date=PERIOD[1],
                                                       lat=A_BIRTH["lat"], lon=A_BIRTH["lon"], partner_chart=cls.ivan)

    def capture(self, **kwargs):
        calls = []

        async def fake_call(_self, system_prompt, user_prompt, max_tokens, **kw):
            calls.append((system_prompt, user_prompt, kw))
            return "<p>ok</p>"

        with mock.patch.object(ai_interpreter.AIInterpreter, "_call_api", new=fake_call):
            asyncio.run(self.interp.interpret_chart(**kwargs))
        return calls

    def case(self, target, mode, theme):
        pair = target == "pair"
        main_chart, main_name = (self.a, "A") if pair else (self.ivan, "Иван")
        kw = dict(natal_chart=main_chart, report_type=theme, user_name=main_name, question="Тест на въпроса",
                  gender=None)
        if pair:
            kw.update(partner_chart=self.ivan, partner_name="Иван", partner_gender="male")
        if mode == "snapshot":
            kw.update(transit_chart=self.t_pair if pair else self.t_ivan, target_date="2026-10-08 12:00")
        elif mode == "period":
            kw.update(timeline_events=self.events_pair if pair else self.events_ivan)
        return self.capture(**kw)

    def test_all_36_combinations_are_clean_and_labelled(self):
        for target in ("single", "pair"):
            for mode in ("natal", "snapshot", "period"):
                for theme in THEMES:
                    with self.subTest(target=target, mode=mode, theme=theme):
                        calls = self.case(target, mode, theme)
                        self.assertEqual(len(calls), 2 if mode == "period" else 1)
                        for system, user, kw in calls:
                            for pattern, what in FORBIDDEN_IN_INSTRUCTIONS:
                                self.assertIsNone(pattern.search(system), f"{what} в системната подкана")
                            for pattern, what in FORBIDDEN_IN_USER_INSTRUCTIONS:
                                self.assertIsNone(pattern.search(user), f"{what} в потребителската подкана")
                            self.assertNotIn("MANDATORY SECTION", user)
                            self.assertIn("--- PEOPLE ---", user)
                            self.assertNotIn('"house_impact"', user)
                            for raw in ('"longitude"', '"speed"', '"distance"'):
                                self.assertNotIn(raw, user)
                            self.assertIn("gender-neutral", user)
                            if target == "pair":
                                self.assertIn('"second_person"', user)
                                self.assertIn('"gender":"male"', user)          # известният пол на Иван
                                self.assertIn('"gender":"unknown"', user)       # A няма известен пол
                            else:
                                self.assertNotIn('"second_person"', user)

    def test_every_natal_block_has_twelve_cusps_with_ruler(self):
        for target in ("single", "pair"):
            for theme in THEMES:
                with self.subTest(target=target, theme=theme):
                    _, user, _ = self.case(target, "natal", theme)[0]
                    sections = parse_sections(user)
                    charts = [p for t, p in sections.items() if t.endswith("NATAL CHART")]
                    self.assertEqual(len(charts), 2 if target == "pair" else 1)
                    for chart in charts:
                        self.assertEqual(len(chart["houses"]), 12)
                        for row in chart["houses"].values():
                            self.assertEqual(set(row), {"cusp", "ruler"})
                            self.assertTrue(row["cusp"].split()[0] in SIGNS)

    def test_pair_natal_has_both_overlay_directions_with_reference_numbers(self):
        for theme in THEMES:
            with self.subTest(theme=theme):
                _, user, _ = self.case("pair", "natal", theme)[0]
                sections = parse_sections(user)
                primary = find_section(sections, "PARTNER PLANETS IN USER'S NATAL HOUSES (CALCULATED)")
                reverse = find_section(sections, "A PLANETS IN ИВАН'S NATAL HOUSES (CALCULATED)")
                for name, house in mapped(REF["overlays"]["IVAN_in_A"]).items():
                    self.assertEqual(primary[name], house, name)
                for name, house in mapped(REF["overlays"]["A_in_IVAN"]).items():
                    self.assertEqual(reverse[name], house, name)
                self.assertEqual(primary["Sun"], 2)       # Слънцето на Иван е във 2-ри дом на A, не в 8-ми
                aspects = find_section(sections, "SYNASTRY ASPECTS (CALCULATED)")
                self.assertTrue(all(row["person1"] == "A" and row["person2"] == "Иван" for row in aspects))

    def test_single_snapshot_houses_and_aspects_match_reference(self):
        for theme in THEMES:
            with self.subTest(theme=theme):
                system, user, _ = self.case("single", "snapshot", theme)[0]
                sections = parse_sections(user)
                houses = find_section(sections, "TRANSIT PLANETS IN USER'S NATAL HOUSES (CALCULATED)")
                for name, house in mapped(REF["overlays"]["T_IVAN_in_IVAN"]).items():
                    self.assertEqual(houses[name], house, name)
                self.assertEqual((houses["Sun"], houses["Mercury"], houses["Saturn"]), (9, 10, 3))
                positions = find_section(sections, "TRANSIT PLANETARY POSITIONS")
                self.assertNotIn("house", json.dumps(positions))
                aspects = find_section(sections, "TRANSIT ASPECTS TO USER'S NATAL CHART (CALCULATED)")
                self.assertTrue(any(r["transit_planet"] == "Neptune" and r["natal_planet"] == "Neptune" for r in aspects))
                self.assertFalse(any(r["transit_planet"] == "Saturn" and r["natal_planet"] in ("Moon", "Mercury") for r in aspects))
                self.assertIn("TRANSIT ASPECTS TO USER'S NATAL CHART (CALCULATED)", system)

    def test_pair_snapshot_has_theme_houses_and_aspects_for_both_people(self):
        for theme in THEMES:
            with self.subTest(theme=theme):
                system, user, _ = self.case("pair", "snapshot", theme)[0]
                self.assertIn(f"REPORT THEME: {ai_interpreter.THEME_FOCUS[theme]}", system)
                sections = parse_sections(user)
                for owner, key in (("A", "T_PAIR_in_A"), ("ИВАН", "T_PAIR_in_IVAN")):
                    houses = find_section(sections, f"TRANSIT PLANETS IN {owner}'S NATAL HOUSES (CALCULATED)")
                    for name, house in mapped(REF["overlays"][key]).items():
                        self.assertEqual(houses[name], house, (owner, name))
                    find_section(sections, f"TRANSIT ASPECTS TO {owner}'S NATAL CHART (CALCULATED)")
                find_section(sections, "A NATAL ASPECTS (CALCULATED)")
                find_section(sections, "ИВАН NATAL ASPECTS (CALCULATED)")       # преди липсваше за втория човек
                self.assertNotIn("house", json.dumps(find_section(sections, "TRANSIT PLANETARY POSITIONS")))

    def test_period_prompts_have_separate_house_fields_and_both_overlays(self):
        for target in ("single", "pair"):
            for theme in THEMES:
                with self.subTest(target=target, theme=theme):
                    for system, user, _ in self.case(target, "period", theme):
                        sections = parse_sections(user)
                        events = find_section(sections, "TIMELINE EVENTS FOR")
                        transits = [e for e in events if e["type"] == "TRANSIT"]
                        self.assertTrue(transits)
                        for event in transits:
                            self.assertIn("transit_planet_natal_house", event)
                            self.assertIn("natal_planet_natal_house", event)
                            self.assertNotIn("house_impact", event)
                            self.assertNotIn("(House", event["description"])
                        if target == "pair":
                            find_section(sections, "PARTNER PLANETS IN USER'S NATAL HOUSES (CALCULATED)")
                            find_section(sections, "A PLANETS IN ИВАН'S NATAL HOUSES (CALCULATED)")
                            self.assertTrue(any(e["target"] == "Partner" for e in transits))
                        self.assertNotIn("Оцени вероятността", system)
                        self.assertIn("NO PERCENTAGES", system)

    def test_unnamed_people_get_neutral_names_not_user_or_account(self):
        calls = self.capture(natal_chart=self.a, partner_chart=self.ivan, report_type="love", question="")
        user = calls[0][1]
        self.assertIn(factpack.FIRST_PERSON_DEFAULT, user)
        self.assertIn(factpack.SECOND_PERSON_DEFAULT, user)
        self.assertNotIn('"name":"User"', user)

    def test_lower_temperature_by_default_and_overridable(self):
        calls = self.case("single", "natal", "general")
        self.assertEqual(self.interp.default_temperature, 0.4)
        self.assertEqual(calls[0][2], {})        # без изрична температура: ползва се настройката по подразбиране

    def test_safety_rules_forbid_biography_percentages_and_guessing_gender(self):
        from safety import SAFETY_RULES
        for fragment in ("родители, предци", "проценти", "вътрешни етикети", "не гадай пол"):
            self.assertIn(fragment, SAFETY_RULES)


class ScannerFieldsTest(Phase8Base):
    def test_transit_events_carry_two_distinct_house_fields(self):
        events = TransitScanner().scan_period(natal_chart=self.ivan, start_date="2026-10-01", end_date="2026-10-03",
                                              lat=IVAN_BIRTH["lat"], lon=IVAN_BIRTH["lon"])
        transits = [e for e in events if e["type"] == "TRANSIT"]
        self.assertTrue(transits)
        for event in transits:
            self.assertNotIn("house_impact", event)
            self.assertEqual(event["natal_planet_natal_house"], self.ivan["planets"][event["natal_planet"]]["house"])
            self.assertIn(event["transit_planet_natal_house"], range(1, 13))


class PeriodLimitTest(unittest.TestCase):
    def test_limit_function(self):
        err = limits.forecast_period_error
        self.assertIsNone(err("2026-10-01", "2026-11-30", False))           # 2 месеца, един човек
        self.assertIsNone(err("2026-10-01", "2026-12-31", False))           # точно 3 месеца
        self.assertIn("най-много 3 месеца", err("2026-10-01", "2027-01-01", False))
        self.assertIsNone(err("2026-10-01", "2026-11-30", True))            # 2 месеца, двама
        self.assertIn("най-много 2 месеца за двама", err("2026-10-01", "2026-12-01", True))
        self.assertIsNone(err("2026-10-15", "2026-11-15", True))            # два календарни месеца
        self.assertIn("след началната", err("2026-10-10", "2026-10-01", False))
        self.assertIn("Невалидна", err("2026-13-01", "2026-12-01", False))
        self.assertIn("Невалидна", err("", "2026-12-01", False))

    def test_limit_is_in_config_for_the_screen(self):
        client = TestClient(main.app)
        data = client.get("/billing/config").json()
        self.assertEqual(data["limits"], {"forecast_max_months_single": 3, "forecast_max_months_pair": 2})


class PeriodLimitEndpointsTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.client = TestClient(main.app)

    def setUp(self):
        limiter.reset()

    def payload(self, end, partner=False):
        body = {**A_BIRTH, "name": "Аз", "is_dynamic": True, "target_date": "2026-10-01", "end_date": end,
                "report_type": "general"}
        if partner:
            body.update(partner_name="Иван", partner_date="1985-07-12", partner_time="18:45",
                        partner_lat=48.2082, partner_lon=16.3738)
        return body

    def test_stream_rejects_too_long_periods_without_calling_ai(self):
        h = register_and_login(self.client, "period-stream@test.bg")
        with mock.patch.object(main.ai_interpreter, "_process_monthly_chunk") as chunk:
            r = self.client.post("/interpret-stream", json=self.payload("2027-12-31"), headers=h)
            self.assertIn("най-много 3 месеца за един човек", r.text)
            limiter.reset()
            r = self.client.post("/interpret-stream", json=self.payload("2026-12-31", partner=True), headers=h)
            self.assertIn("най-много 2 месеца за двама души", r.text)
            chunk.assert_not_called()

    def test_non_stream_endpoint_returns_400(self):
        h = register_and_login(self.client, "period-plain@test.bg")
        with mock.patch.object(main.ai_interpreter, "interpret_chart") as interpret:
            r = self.client.post("/interpret", json=self.payload("2028-01-31"), headers=h)
            self.assertEqual(r.status_code, 400)
            self.assertIn("най-много 3 месеца", r.json()["detail"])
            interpret.assert_not_called()


if __name__ == "__main__":
    unittest.main()
