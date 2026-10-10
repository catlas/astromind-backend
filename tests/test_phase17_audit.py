"""
Фаза 17: поправките от одита на 10–11.10.2026 (AM-N01 и AM-N05).

Реалните изречения са от одитния пакет (синтетични профили). Всеки тест, който проверява, че грешното изречение се хваща,
има и пара: същото изречение с вярната стойност не бива да е нарушение.
"""
import os
import unittest

import testenv  # noqa: F401  (трябва да е преди engine)
import ai_interpreter  # noqa: E402
import aspects_engine  # noqa: E402
import bg_text  # noqa: E402
import engine  # noqa: E402
import text_check as tc  # noqa: E402
from scanner import TransitScanner  # noqa: E402

A_BIRTH = dict(date="1990-02-15", time="13:00", lat=42.6977, lon=23.3219)          # София
IVAN_BIRTH = dict(date="1985-07-12", time="18:45", lat=48.2082, lon=16.3738)       # Виена
SOFIA = (42.6977, 23.3219)

_CACHE = {}


def chart(birth):
    key = ("chart", birth["date"])
    if key not in _CACHE:
        _CACHE[key] = engine.calculate_chart(**birth)
    return _CACHE[key]


def calendar(partner: bool):
    key = ("cal", partner)
    if key not in _CACHE:
        _CACHE[key] = TransitScanner(engine_instance=engine.get_engine()).build_calendar(
            chart(A_BIRTH) if partner else chart(IVAN_BIRTH), "2026-10-01", "2026-11-30",
            (A_BIRTH if partner else IVAN_BIRTH)["lat"], (A_BIRTH if partner else IVAN_BIRTH)["lon"],
            partner_chart=chart(IVAN_BIRTH) if partner else None)
    return _CACHE[key]


def pair_facts():
    return tc.build_facts(mode="natal", user_name="Тест А одит", natal_chart=chart(A_BIRTH),
                          partner_name="Иван", partner_chart=chart(IVAN_BIRTH))


def period_facts():
    return tc.build_facts(mode="period", user_name="Иван", natal_chart=chart(IVAN_BIRTH), calendar=calendar(False),
                          report_date="2026-10-08")


def codes(text, facts):
    return [(v.code, v.severity) for v in tc.check_text(text, facts)]


class AspectSentenceTest(unittest.TestCase):
    """AM-N01: Сатурн–Сатурн при 59,18° е секстил, не тригон."""

    @classmethod
    def setUpClass(cls):
        cls.facts = pair_facts()

    def test_the_delivered_trine_sentence_is_rejected(self):
        text = ("Ключов ресурс е тригонът между Сатурн на Тест А одит и Сатурн на Иван (59.18°, орбис 0.82°) – "
                "съвместна дисциплина и устойчивост.")
        self.assertIn(("aspect", "hard"), codes(text, self.facts))

    def test_the_same_sentence_with_the_right_aspect_passes(self):
        text = ("Ключов ресурс е секстилът между Сатурн на Тест А одит и Сатурн на Иван (59.18°, орбис 0.82°) – "
                "съвместна дисциплина и устойчивост.")
        self.assertEqual(codes(text, self.facts), [])

    def test_lower_case_planets_do_not_bypass_the_check(self):
        self.assertIn(("aspect", "hard"), codes("сатурн на Тест А одит е в тригон със сатурн на Иван.", self.facts))
        self.assertEqual(codes("сатурн на Тест А одит е в секстил със сатурн на Иван.", self.facts), [])

    def test_a_multi_word_name_is_recognised_as_the_owner(self):
        facts = self.facts
        rows = [r for r in tc.aspect_rows(facts) if r["kind"] == "synastry" and {r["p1"], r["p2"]} == {"Saturn"}]
        self.assertTrue(rows)
        seg = "секстил между Сатурн на Тест А одит и Сатурн на Иван"
        mentions = tc.find_mentions(seg)
        claims = tc.aspect_claims(seg, mentions, tc.planet_groups(seg, mentions), facts)
        self.assertEqual([(c["p1"], c["p2"], c["o1"], c["o2"]) for c in claims], [("Saturn", "Saturn", "user", "partner")])

    def test_the_whole_planet_name_regex_is_still_bounded(self):
        self.assertEqual(tc.find_mentions("Сатурнов цикъл"), [])


class ExactDateBindingTest(unittest.TestCase):
    """AM-N01: „Опозицията Марс–Юпитер на 12 октомври (точен)“: точната дата е 24 октомври."""

    @classmethod
    def setUpClass(cls):
        cls.facts = period_facts()

    def test_the_wrong_date_in_front_of_the_word_exact_is_rejected(self):
        text = ("Конфликт: Опозицията Марс–Юпитер на 12 октомври (точен) и квадратурата Юпитер–Сатурн на 12 октомври (точен).")
        found = codes(text, self.facts)
        self.assertIn(("exact", "hard"), found)
        self.assertEqual(len([c for c in found if c[0] == "exact"]), 1)       # втората дата е вярна и не се брои

    def test_the_right_dates_pass(self):
        text = ("Конфликт: Опозицията Марс–Юпитер на 24 октомври (точен) и квадратурата Юпитер–Сатурн на 12 октомври (точен).")
        self.assertEqual(codes(text, self.facts), [])

    def test_the_word_before_the_aspect_still_binds_to_the_following_aspect(self):
        self.assertEqual(codes("12 октомври, 22:21 – точна квадратура Юпитер–Сатурн (11-ти дом).", self.facts), [])
        self.assertIn(("exact", "hard"), codes("Ресурс: точният секстил Марс–Венера на 24 октомври.", self.facts))


class EventHouseTest(unittest.TestCase):
    """AM-N01: дом на новолуние, пълнолуние и станция (според одита 9, 4 и 11 за Иван)."""

    @classmethod
    def setUpClass(cls):
        cls.facts = period_facts()

    def test_the_calendar_gives_the_natal_house_of_the_events(self):
        events = {e["event"]: e.get("natal_house") for e in calendar(False).events if e["type"] in ("LUNATION", "ECLIPSE")}
        self.assertEqual(events["New Moon in Libra"], {"User": 9})
        self.assertEqual(events["Full Moon in Taurus"], {"User": 4})
        mercury = [e for e in calendar(False).events if e["type"] == "RETROGRADE" and e["planet"] == "Mercury"
                   and e["direction"] == "retrograde"]
        self.assertEqual(mercury[0]["natal_house"], {"User": 11})

    def test_the_house_is_public_and_the_longitude_is_not(self):
        public = [calendar(False).public(e) for e in calendar(False).events if e["type"] == "LUNATION"]
        self.assertTrue(all("natal_house" in e and "_lon" not in e for e in public))

    def test_a_wrong_lunation_house_is_rejected_and_the_right_one_passes(self):
        self.assertIn(("house", "hard"), codes("Новолуние във Везни 17°22′ активира 10-и дом.", self.facts))
        self.assertEqual(codes("Новолуние във Везни 17°22′ активира 9-и дом.", self.facts), [])
        self.assertIn(("house", "hard"), codes("Пълнолуние в Телец 2°46′ е в 5-и дом.", self.facts))
        self.assertEqual(codes("Пълнолуние в Телец 2°46′ е в 4-и дом.", self.facts), [])

    def test_the_natal_planet_house_is_not_taken_for_the_transit_house(self):
        self.assertIn(("house", "hard"), codes("Ретрограден Меркурий в Скорпион в 8-ми дом на Иван слага темпото.", self.facts))
        self.assertEqual(codes("Ретрограден Меркурий в Скорпион в 11-и дом на Иван слага темпото.", self.facts), [])

    def test_a_second_event_or_planet_in_between_is_not_read_as_the_house_of_the_first(self):
        self.assertEqual(codes("Новолуние във Везни, а Сатурн е в 6-ти дом на Иван.", self.facts), [])

    def test_two_people_accept_either_house_and_unknown_time_gives_none(self):
        both = tc.build_facts(mode="period", user_name="Тест А одит", natal_chart=chart(A_BIRTH), partner_name="Иван",
                              partner_chart=chart(IVAN_BIRTH), calendar=calendar(True), report_date="2026-10-08")
        houses = {e["event"]: e.get("natal_house") for e in calendar(True).events if e["type"] == "LUNATION"}
        self.assertEqual(set(houses["New Moon in Libra"]), {"User", "Partner"})
        allowed = set(houses["New Moon in Libra"].values())
        self.assertEqual(codes("Новолуние във Везни е в %d-и дом." % min(allowed), both), [])
        other = next(h for h in range(1, 13) if h not in allowed)
        self.assertIn(("house", "hard"), codes("Новолуние във Везни е в %d-и дом." % other, both))
        eng = engine.get_engine()
        unknown = eng.calculate_chart(IVAN_BIRTH["date"], "ignored", IVAN_BIRTH["lat"], IVAN_BIRTH["lon"], time_known=False)
        cal = TransitScanner(engine_instance=eng).build_calendar(
            chart(A_BIRTH), "2026-10-01", "2026-10-31", A_BIRTH["lat"], A_BIRTH["lon"], partner_chart=unknown)
        for e in cal.events:
            if e["type"] == "LUNATION":
                self.assertEqual(set(e["natal_house"]), {"User"})              # без час няма дом за Иван


class CalendarFieldTest(unittest.TestCase):
    def test_no_exact_pass_replaces_the_ambiguous_turns_back(self):
        rows = [e for e in calendar(False).events if e["type"] == "TRANSIT" and not e["exact"] and not e.get("exact_outside_period")]
        self.assertTrue(rows)
        self.assertTrue(all(r.get("no_exact_pass") and "turns_back" not in r for r in rows))
        exact_rows = [e for e in calendar(False).events if e["type"] == "TRANSIT" and e["exact"]]
        self.assertTrue(exact_rows and not any("no_exact_pass" in r for r in exact_rows))
        text = ai_interpreter.AIInterpreter._get_bulgarian_language_rules()
        self.assertNotIn("turns_back", text)

    def test_the_labels_of_the_new_fields_are_cleaned_from_the_text(self):
        cleaned, removed = tc.clean_labels("Плутон no_exact_pass и natal_house е в Водолей.")
        self.assertNotIn("no_exact_pass", cleaned)
        self.assertNotIn("natal_house", cleaned)


class SynastryTest(unittest.TestCase):
    def test_the_contacts_are_the_same_after_swapping_the_two_people(self):
        a, b = chart(A_BIRTH), chart(IVAN_BIRTH)
        ab = aspects_engine.calculate_synastry_aspects(a, b)
        ba = aspects_engine.calculate_synastry_aspects(b, a)
        self.assertEqual(len(ab), len(ba))
        as_set = lambda rows, swap: {((r["planet2"], r["planet1"]) if swap else (r["planet1"], r["planet2"]), r["aspect"], r["orb"])
                                     for r in rows}
        self.assertEqual(as_set(ab, False), as_set(ba, True))

    def test_the_angles_of_both_people_take_part(self):
        ab = aspects_engine.calculate_synastry_aspects(chart(A_BIRTH), chart(IVAN_BIRTH))
        self.assertTrue(any(r["planet1"] in ("ASC", "MC") for r in ab))
        self.assertTrue(any(r["planet2"] in ("ASC", "MC") for r in ab))

    def test_a_person_with_unknown_time_has_no_angles_in_the_contacts(self):
        eng = engine.get_engine()
        unknown = eng.calculate_chart(IVAN_BIRTH["date"], "ignored", IVAN_BIRTH["lat"], IVAN_BIRTH["lon"], time_known=False)
        rows = aspects_engine.calculate_synastry_aspects(chart(A_BIRTH), unknown)
        self.assertFalse(any(r["planet2"] in ("ASC", "MC") for r in rows))
        self.assertTrue(any(r["planet1"] in ("ASC", "MC") for r in rows))


class SignBoundaryTest(unittest.TestCase):
    def test_a_longitude_just_before_the_boundary_stays_in_its_sign(self):
        self.assertEqual(engine.decimal_to_dms(29.997222)["str"], "Aries 29°59'")
        self.assertEqual(engine.decimal_to_dms(359.9999)["str"], "Pisces 29°59'")

    def test_ordinary_rounding_is_unchanged(self):
        self.assertEqual(engine.decimal_to_dms(14.9999)["str"], "Aries 15°00'")
        self.assertEqual(engine.decimal_to_dms(30.0)["str"], "Taurus 0°00'")
        self.assertEqual(engine.decimal_to_dms(0.0)["str"], "Aries 0°00'")


class PromptRulesTest(unittest.TestCase):
    def setUp(self):
        self.rules = ai_interpreter.AIInterpreter._get_bulgarian_language_rules()

    def test_health_decision_and_consistency_rules_are_in_every_prompt(self):
        for needle in ("never diagnose", "няма данни за заболявания или инциденти", "не инвестирайте",
                       "never state a person's real motive", "must never contradict the plan",
                       "TRANSITING planet", "0° съвпад, 60° секстил"):
            self.assertIn(needle, self.rules)

    def test_the_karma_template_has_bulgarian_headings_and_no_dna_fact(self):
        import inspect
        source = inspect.getsource(ai_interpreter)
        for old in ("Ancestral Echoes (Moon/Saturn)", "Retrograde Review:**", "Healing Practice:**", "Forgiveness ritual"):
            self.assertNotIn(old, source)
        self.assertNotIn("Family DNA", source)
        self.assertIn("**Кармична тема:**", source)

    def test_stray_english_headings_are_translated_by_the_safety_net(self):
        text = "**Ancestral Echoes**\nтекст\n**Retrograde Review**\n**Healing Practice**"
        out = bg_text.bulgarianize(text)
        self.assertIn("Родови модели", out)
        self.assertIn("Преглед на ретроградните", out)
        self.assertIn("Практика за размисъл", out)
        self.assertNotIn("Healing", out)


if __name__ == "__main__":
    unittest.main()


class ContextStationTest(unittest.TestCase):
    """AM-N01: „Венера директна 13 ноември“ при вярна дата 14 ноември (02:27 Europe/Sofia): датите извън периода идват от календара."""

    @classmethod
    def setUpClass(cls):
        cls.cal = TransitScanner(engine_instance=engine.get_engine()).build_calendar(
            chart(A_BIRTH), "2026-10-01", "2026-10-31", A_BIRTH["lat"], A_BIRTH["lon"])
        cls.facts = tc.build_facts(mode="period", user_name="Тест А одит", natal_chart=chart(A_BIRTH), calendar=cls.cal,
                                   report_date="2026-10-08")

    def test_the_station_after_the_period_is_in_the_context_rows_only(self):
        venus = [e for e in self.cal.context_events if e["planet"] == "Venus" and e["direction"] == "direct"]
        self.assertEqual([(e["when"], e["outside_period"]) for e in venus], [("2026-11-14 02:27", True)])
        self.assertFalse(any(e.get("outside_period") for e in self.cal.events))
        self.assertTrue(all("outside_period" in e for e in self.cal.public_events_with_context()[len(self.cal.events):]))
        self.assertFalse(any(e.get("outside_period") for e in self.cal.public_events()))

    def test_context_rows_reach_the_months_nearby_but_do_not_make_a_month_eventful(self):
        october = self.cal.month_events("2026-10")
        self.assertTrue(any(e.get("outside_period") for e in october))
        self.assertEqual(self.cal.months_with_events(), ["2026-10"])
        self.assertFalse(any(e.get("outside_period") for e in self.cal.month_events("2026-10", with_context=False)))

    def test_the_wrong_station_date_is_rejected_and_the_right_one_passes(self):
        self.assertIn(("event", "hard"), codes("Венера е ретроградна до 13 ноември, когато става директна.", self.facts))
        self.assertEqual(codes("Венера е ретроградна до 14 ноември, когато става директна.", self.facts), [])

    def test_the_station_inside_the_period_is_still_checked(self):
        self.assertIn(("event", "hard"), codes("Плутон става директен на 17 октомври.", self.facts))
        self.assertEqual(codes("Плутон става директен на 16 октомври.", self.facts), [])

    def test_the_prompt_tells_the_ai_about_context_rows(self):
        import inspect
        self.assertGreaterEqual(inspect.getsource(ai_interpreter).count("outside_period"), 2)
