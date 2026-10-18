"""
Фаза 22 (регресионен одит 11.10.2026): R02 и R04 - факти с конкретен човек, момент и движение.

Входните твърдения са точно тези от таблицата на одита; верните контролни изречения се приемат. Синтетични данни:
Тест А — одит (15.02.1990 13:00, София) и Иван (12.07.1985 18:45, Виена), период октомври 2026.
Реалните 33 текста на одита са във fixtures/regression_audit_texts.json: всяка доказана грешка се улавя, а за останалите няма тежко
нарушение (така нови правила не добавят фалшиви тревоги).
"""
import time
import unittest
from datetime import datetime, timedelta, timezone

import pytz

import testenv  # noqa: F401  (трябва да е преди engine)
import bg_text
import regression_support as rs
import text_check as tc
from scanner import TransitScanner


def hard_codes(text, facts):
    return sorted({v.code for v in tc.check_text(text, facts) if v.severity == "hard"})


def wrappers(sentence):
    """Същото твърдение с HTML и Markdown обвивка (проверката не трябва да зависи от форматирането)."""
    return {"plain": sentence, "html": f"<p><strong>{sentence}</strong></p>", "markdown": f"- **{sentence}**"}


# (име, режим, двойка?, грешно твърдение, очакван код, верен контрол)
R02_CASES = [
    ("меркурий-възел", "natal", False, "Натален Меркурий на Иван е в съвпад с Лунния възел.", "aspect", "Натален Меркурий на Иван е в квадратура с Лунния възел."),
    ("меркурий-юпитер", "natal", False, "Меркурий е в квадратура с Юпитер.", "aspect", "Меркурий е в опозиция с Юпитер."),
    ("меркурий-сатурн", "natal", False, "Меркурий е в опозиция със Сатурн.", "aspect", "Меркурий е в квадратура със Сатурн."),
    ("марс-дом-8", "natal", False, "Наталният Марс на Иван е в 8-ми дом.", "house", "Наталният Марс на Иван е в 7-ми дом."),
    ("сатурн-сатурн-тире", "natal", True, "Сатурн на Тест А — одит е в тригон със Сатурн на Иван.", "aspect",
     "Сатурн на Тест А — одит е в секстил със Сатурн на Иван."),
    ("юпитер-ретрограден", "snapshot", False, "Транзитният Юпитер е ретрограден на 10 октомври 2026.", "event",
     "Транзитният Юпитер е директен на 10 октомври 2026."),
    ("юпитер-ретрограден-двойка", "snapshot", True, "Транзитният Юпитер е ретрограден на 10 октомври 2026.", "event",
     "Транзитният Юпитер е директен на 10 октомври 2026."),
    ("плутон-обръща-назад", "period", False, "На 16 октомври Плутон се обръща назад.", "event", "На 16 октомври Плутон се обръща напред."),
    ("плутон-става-ретрограден", "period", False, "На 16 октомври Плутон става ретрограден.", "event", "На 16 октомври Плутон става директен."),
    ("новолуние-дом-5-на-иван", "period", True, "Новолунието във Везни е в 5-ти дом на Иван.", "house", "Новолунието във Везни е в 9-ти дом на Иван."),
    ("новолуние-дом-9-на-а", "period", True, "Новолунието във Везни е в 9-ти дом на Тест А — одит.", "house",
     "Новолунието във Везни е в 5-ти дом на Тест А — одит."),
    ("марс-скорпион", "period", False, "През октомври 2026 Марс е в Скорпион.", "sign", "През октомври 2026 Марс е в Лъв."),
    ("марс-скорпион-моментен", "snapshot", False, "Транзитният Марс е в Скорпион.", "sign", "Транзитният Марс е в Лъв."),
    ("марс-дом-периодичен", "period", True, "Транзитният Марс е в 5-ти дом на Иван през октомври.", "house",
     "Транзитният Марс е в 8-ми дом на Иван през октомври."),
]


class R02FixtureTest(unittest.TestCase):
    def test_every_wrong_claim_is_caught_in_every_format_and_the_correct_one_passes(self):
        for name, mode, pair, wrong, code, right in R02_CASES:
            facts = rs.facts(mode, pair)
            for form, text in wrappers(wrong).items():
                self.assertIn(code, hard_codes(text, facts), msg=f"{name} ({form}) не е уловено")
            for form, text in wrappers(right).items():
                self.assertEqual(hard_codes(text, facts), [], msg=f"{name} ({form}): верният контрол е блокиран")

    def test_lowercase_names_are_checked_like_capitalised_ones(self):
        facts = rs.facts("period", True)
        self.assertIn("house", hard_codes("новолунието във везни е в 5-ти дом на Иван.", facts))
        self.assertEqual(hard_codes("новолунието във везни е в 9-ти дом на Иван.", facts), [])
        self.assertIn("sign", hard_codes("През октомври 2026 марс е в скорпион.", rs.facts("period", False)))
        self.assertIn("sign", hard_codes("Транзитният марс е в скорпион.", rs.facts("snapshot", False)))
        self.assertEqual(hard_codes("Транзитният марс е в лъв.", rs.facts("snapshot", False)), [])

    def test_ordinary_words_are_not_read_as_signs(self):
        for text in ("Рак е сериозна диагноза, а рибите живеят във вода.", "В дева Мария вярват мнозина.",
                     "Лъв и риби в зоопарка, без планети."):
            self.assertEqual(hard_codes(text, rs.facts("natal", False)), [], msg=text)

    def test_the_correct_mars_moon_passage_is_not_read_as_mars_mars(self):
        text = "30 октомври – 4 ноември: Марс в квадратура с Луната на Иван, около 04 ноември."
        self.assertNotIn("Mars–Mars", " ".join(v.detail for v in tc.check_text(text, rs.facts("period", False))))


class MotionTest(unittest.TestCase):
    def test_correct_motion_statements_are_accepted(self):
        facts = rs.facts("period", False)
        for text in ("Плутон е ретрограден до 16 октомври и директен след това.", "От 24 октомври Меркурий е ретрограден.",
                     "Венера е ретроградна от 3 октомври.", "Ретроградният Меркурий от 24 октомври изисква внимание.",
                     "Меркурий не е ретрограден през първата половина на октомври.", "Юпитер ще остане директен през целия период.",
                     "Директно говорене с шефа е най-добрият подход."):
            self.assertEqual(hard_codes(text, facts), [], msg=text)

    def test_a_station_is_checked_by_direction_and_by_date(self):
        facts = rs.facts("period", False)
        self.assertIn("event", hard_codes("На 16 октомври Плутон се обръща назад.", facts))
        self.assertIn("event", hard_codes("Плутон става директен на 17 октомври.", facts))
        self.assertEqual(hard_codes("Плутон става директен на 16 октомври.", facts), [])

    def test_the_natal_retrograde_jupiter_does_not_excuse_the_transit_claim(self):
        facts = rs.facts("snapshot", False)
        self.assertTrue(facts.motion["user"].get("Jupiter") is not None)
        self.assertFalse(facts.motion["sky"]["Jupiter"])
        self.assertIn("event", hard_codes("Транзитният Юпитер е ретрограден.", facts))

    def test_a_future_statement_in_a_moment_analysis_is_not_judged_by_the_moment(self):
        facts = rs.facts("snapshot", False)
        self.assertEqual(hard_codes("Меркурий ще стане ретрограден след две седмици.", facts), [])


class OwnerAndEventTest(unittest.TestCase):
    def test_lunation_houses_belong_to_their_owner(self):
        facts = rs.facts("period", True)
        cases = [
            ("За Иван: новолунието във Везни е в 9-ти дом, а за Тест А — одит в 5-ти дом.", False),
            ("За Иван: новолунието във Везни е в 9-ти дом, а за Тест А — одит в 9-ти дом.", True),
            ("При Иван новолунието във Везни е в 5-ти дом.", True),
            ("Пълнолунието в Телец е в 11-ти дом на Тест А — одит и в 4-ти дом на Иван.", False),
            ("Пълнолунието в Телец е в 4-ти дом на Тест А — одит и в 11-ти дом на Иван.", True),
            ("Станцията на Меркурий в Скорпион е в 6-ти дом на Тест А — одит и в 11-ти дом на Иван.", False),
            ("Станцията на Меркурий в Скорпион е в 11-ти дом на Тест А — одит и в 6-ти дом на Иван.", True),
        ]
        for text, wrong in cases:
            self.assertEqual("house" in hard_codes(text, facts), wrong, msg=text)

    def test_a_dash_inside_a_name_does_not_cut_the_sentence(self):
        facts = rs.facts("natal", True)
        self.assertIn("aspect", hard_codes("Сатурн на Тест А — одит е в тригон със Сатурн на Иван.", facts))
        self.assertIn("aspect", hard_codes("Сатурн на Тест А – одит е в тригон със Сатурн на Иван.", facts))
        self.assertIn("aspect", hard_codes("Сатурн на Тест А одит е в тригон със Сатурн на Иван.", facts))
        self.assertIn("aspect", hard_codes("Сатурн на Тест А — одит и Сатурн на Иван са в тригон.", facts))
        self.assertEqual(hard_codes("Сатурн на Тест А — одит е в секстил със Сатурн на Иван.", facts), [])

    def test_dates_and_times_for_each_person_in_the_two_zones(self):
        pair = rs.facts("period", True)
        single = rs.facts("period", False)
        self.assertEqual(hard_codes("10 октомври, 04:24 – Марс в секстил с Венера на Иван.", pair), [])
        self.assertEqual(hard_codes("10 октомври, 03:24 – Марс в секстил с Венера.", single), [])
        self.assertIn("time", hard_codes("10 октомври, 03:24 – Марс в секстил с Венера на Иван.", pair))
        self.assertEqual(hard_codes("24 октомври, 17:28 – Марс в опозиция с Юпитер на Иван.", pair), [])
        self.assertEqual(hard_codes("24 октомври, 16:28 – Марс в опозиция с Юпитер.", single), [])
        self.assertIn("aspect", hard_codes("24 октомври, 16:28 – Марс в секстил с Юпитер.", single))

    def test_the_receiver_is_not_carried_between_the_two_people(self):
        pair = rs.facts("period", True)
        good = ("1 октомври, 12:43 – Марс в квадратура с Плутон на Иван. "
                "30 октомври, 10:15 – Марс в квадратура с Плутон на Тест А — одит.")
        swapped = ("1 октомври, 12:43 – Марс в квадратура с Плутон на Тест А — одит. "
                   "30 октомври, 10:15 – Марс в квадратура с Плутон на Иван.")
        self.assertEqual(hard_codes(good, pair), [])
        self.assertNotEqual(hard_codes(swapped, pair), [])

    def test_two_dates_in_one_line_are_bound_in_order(self):
        single = rs.facts("period", False)
        right = "24 октомври, 08:36 и 28 октомври, 09:23 – Марс в тригон с Уран и в съвпад с Меркурий."
        wrong = "24 октомври, 08:36 и 24 октомври, 09:23 – Марс в тригон с Уран и в съвпад с Меркурий."
        self.assertEqual(hard_codes(right, single), [])
        self.assertNotEqual(hard_codes(wrong, single), [])


class DignityAndIngressTest(unittest.TestCase):
    @staticmethod
    def dignity_sign_errors(text, facts):
        seg_errors = []
        for seg in tc.segments(text):
            seg_errors += tc.check_dignity(seg, tc.find_mentions(seg), facts)
        return seg_errors

    def test_dignities_follow_the_traditional_table(self):
        facts = rs.facts("natal", False)
        for text, wrong in (("Венера във Везни е в екзалтация.", True), ("Венера във Везни е в управление.", False),
                            ("Венера в Риби е в екзалтация.", False), ("Слънцето в Овен е в падение.", True),
                            ("Венера във Везни (управление), а екзалтацията ѝ е в Риби.", False),
                            ("Венера във Везни (управление), а екзалтацията ѝ е в Овен.", True),
                            ("Марс в Рак е в падение.", False), ("Сатурн във Везни е екзалтиран.", False)):
            self.assertEqual(bool(self.dignity_sign_errors(text, facts)), wrong, msg=text)

    def test_the_source_sign_of_an_ingress_comes_from_the_calculation(self):
        facts = rs.facts("period", False, "2026-11-30")
        ingress = next(e for e in facts.calendar if e["type"] == "INGRESS" and e["planet"] == "Mars")
        self.assertEqual((ingress["sign"], ingress["from_sign"]), ("Virgo", "Leo"))
        self.assertIn("event", hard_codes("Марс влиза във Дева на 26 ноември от Скорпион.", facts))
        self.assertIn("event", hard_codes("Марс напуска Скорпион и влиза във Дева на 26 ноември.", facts))
        self.assertEqual(hard_codes("Марс влиза във Дева на 26 ноември, 00:37, идвайки от Лъв.", facts), [])
        self.assertEqual(hard_codes("Марс напуска Лъв и влиза във Дева на 26 ноември.", facts), [])


class TimelineTest(unittest.TestCase):
    def test_the_sign_and_motion_intervals(self):
        cal = rs.calendar(False)
        mars = cal.timeline["Mars"]
        self.assertEqual({(r["sign"], r["motion"]) for r in mars}, {("Leo", "direct")})
        pluto = cal.timeline["Pluto"]
        self.assertEqual([(r["motion"], r["to"] if i == 0 else r["from"]) for i, r in enumerate(pluto)][:2],
                         [("retrograde", "2026-10-16 04:40"), ("direct", "2026-10-16 04:40")])
        venus = cal.timeline["Venus"]
        self.assertEqual([(r["sign"], r["motion"]) for r in venus],
                         [("Scorpio", "direct"), ("Scorpio", "retrograde"), ("Libra", "retrograde")])

    def test_intervals_are_continuous_and_cover_the_period(self):
        for pair in (False, True):
            cal = rs.calendar(pair)
            for body, rows in cal.timeline.items():
                self.assertLessEqual(rows[0]["from"][:10], cal.start, msg=body)
                self.assertGreaterEqual(rows[-1]["to"][:10], cal.end, msg=body)
                for a, b in zip(rows, rows[1:]):
                    self.assertEqual(a["to"], b["from"], msg=body)

    def test_the_house_intervals_cross_the_cusps_of_each_person(self):
        cal = rs.calendar(True)
        mars_a = cal.house_timeline["User"]["Mars"]
        self.assertEqual([(r["house"], r["to"] if i == 0 else None) for i, r in enumerate(mars_a)][0], (2, "2026-10-10 14:02"))
        self.assertEqual([r["house"] for r in mars_a], [2, 3])
        self.assertEqual([r["house"] for r in cal.house_timeline["Partner"]["Mars"]], [8])
        self.assertEqual([r["house"] for r in cal.house_timeline["Partner"]["Sun"]], [9, 10])

    def test_a_person_without_a_birth_time_has_no_house_intervals(self):
        chart = rs.unknown_ivan()
        cal = TransitScanner(engine_instance=rs.engine.get_engine()).build_calendar(
            chart, "2026-10-01", "2026-10-31", rs.IVAN_BIRTH["lat"], rs.IVAN_BIRTH["lon"], tz_name="Europe/Vienna")
        self.assertEqual(cal.house_timeline, {})

    def test_event_times_are_correct_around_the_daylight_saving_change(self):
        """Часовата промяна в Европа е 25.10.2026 в 01:00 UTC: местният час преди и след нея е с различно отместване."""
        eng = rs.engine.get_engine()
        for zone, lat, lon in (("Europe/Sofia", rs.A_BIRTH["lat"], rs.A_BIRTH["lon"]), ("Europe/Vienna", rs.IVAN_BIRTH["lat"], rs.IVAN_BIRTH["lon"])):
            cal = TransitScanner(engine_instance=eng).build_calendar(
                rs.chart("A"), "2026-10-22", "2026-10-28", lat, lon, tz_name=zone)
            tz = pytz.timezone(zone)
            seen_offsets = set()
            for e in cal.events:
                if e["type"] == "TRANSIT":
                    continue
                utc = datetime.strptime(e["_utc"], "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
                local = (utc + timedelta(seconds=30)).replace(second=0).astimezone(tz)          # календарът закръгля до най-близката минута
                self.assertEqual(e["when"], local.strftime("%Y-%m-%d %H:%M"), msg=f"{zone} {e['id']}")
                seen_offsets.add(local.utcoffset())
            self.assertEqual(len(seen_offsets), 2, msg=f"{zone}: събития и преди, и след промяната")


class RealAuditTextsTest(unittest.TestCase):
    """Реалните доставени текстове от одита: доказаните грешки се улавят; в останалите няма тежко нарушение."""
    EXPECTED = {
        "2": {"aspect", "house", "health"},   # Меркурий-Възел/Юпитер/Сатурн, Марс в дом 8; отлагане на здравни решения (Фаза 23)
        "5": {"language"},             # думите „favorite“, „close“, „Markdown“ са от копирането на екрана (бутоните на страницата), не от AI (Фаза 30, 18.10.2026)
        "7": {"date"},                 # повторна дата 24 октомври за Марс-Венера
        "8": {"sign"},                 # транзитен Марс в Скорпион
        "10": {"event", "language"},   # транзитният Юпитер като ретрограден; „close“ и „Markdown“ са от екрана
        "13": {"editor"},              # самокорекция на редактор „… – всъщност …“ (Фаза 23)
        "16": {"biography"},           # родител, който е бил емоционално недостъпен, като факт (Фаза 23)
        "25": {"percent", "language"}, # „В 80% от случаите“ (Фаза 23); „Medium Coeli (MC)“ е латински термин, написан от AI (Фаза 30)
        "28": {"time"},                # „след 14:00“ без изчислен прозорец (Фаза 23)
        "40": {"language"},            # „close“ и „Markdown“ са от екрана
    }

    def test_each_text_matches_the_audit_findings(self):
        for rid, row in sorted(rs.texts().items(), key=lambda kv: int(kv[0])):
            found = set(hard_codes(row["text"], rs.facts_for(int(rid))))
            self.assertEqual(found, self.EXPECTED.get(rid, set()), msg=f"текст №{rid}")


class DateLeadAndTermsTest(unittest.TestCase):
    def test_a_long_chain_of_date_leads_stays_fast(self):
        facts = rs.facts("period", True)
        started = time.perf_counter()
        tc.check_text("15 октомври – 20 октомври, " * 3000, facts)
        self.assertLess(time.perf_counter() - started, 6.0)

    def test_a_date_lead_keeps_its_event(self):
        pieces = tc.segments("24 октомври, 03:24 – точен секстил Марс–Венера")
        self.assertEqual(len(pieces), 1)
        self.assertIn("Марс", pieces[0])

    def test_known_wrong_words_are_fixed_and_names_are_protected(self):
        self.assertEqual(bg_text.bulgarianize("Накрасо: Венера във Либра, а Меркурий в Либера."),
                         "Накратко: Венера във Везни, а Меркурий във Везни.")
        token = bg_text.bind(["Либра"])
        try:
            self.assertEqual(bg_text.bulgarianize("Либра е името ми."), "Либра е името ми.")
        finally:
            bg_text.unbind(token)


if __name__ == "__main__":
    unittest.main()
