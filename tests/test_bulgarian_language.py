"""
Български език в текстовете, които пише кодът (Фаза 30, 18.10.2026; решения на потребителя: „във Водолей“, проверка за английски думи).

1. Предлогът „в/във“: „във“ пред дума, започваща с в или ф („във Везни“, „във Водолей“), и пред 2-ри/2-ра/2-ро („във 2-ри дом“, втори).
   Правилото е на едно място (bg_text.prep_v) и се ползва във всички шаблони на кода: заглавията на натален за един и за двама, бележката за
   непознат час, домовете на периодните карти и таблицата на износа. Преди „Слънцето е в Водолей“ се виждаше на екрана веднага след генерацията
   (историята и износът го оправяха при четене само за В/Ф, но не и за „в 2-ри дом“), а износът пишеше „New Луна in Везни“ и „Венера става директен“.
2. Английска дума насред български текст („spontaneity“, „comfortable“) е тежко нарушение `language` (text_check.check_language): връща се на AI за
   поправка като всяко друго; име на човек, часова зона, MC/ASC/UTC и думите от въпроса на потребителя са позволени.

Нищо от тези тестове не вика реален AI, мрежа или текущата дата. Пускане: python -m unittest discover -s tests -p "test_bulgarian_language.py"
"""
import dataclasses
import os
import re
import sys
import unittest

import testenv  # noqa: F401  (трябва да е преди engine)
import regression_support as rs
import bg_text
import birthtime
import natal_pair
import natal_single
import period_cards
import period_report
import report_export
import text_check as tc
from test_period_cards import FlowBase, GOOD_CARD, cards_of
from test_phase24_snapshot import PERIOD_BODY, Base, docx_text

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "scripts"))
import verify_cards as vc  # noqa: E402
import verify_period as vp  # noqa: E402

ENGLISH_CARD = "Това изисква търпение и е comfortable за вас в разговорите."
ENGLISH_MONTH = "Този месец е добре да подбирате думите спокойно, за да се чувствате comfortable и да не бързате с решения."


class PrepositionRuleTest(unittest.TestCase):
    def test_vav_stands_before_v_and_f_and_before_the_second_house(self):
        for word in ("Везни", "Водолей", "Венера", "Фокус", "2-ри", "2-ра", "2-ро", "2-рия", "вода", "фирма"):
            self.assertEqual(bg_text.prep_v(word), "във", word)
        for word in ("Телец", "Скорпион", "Лъв", "Риби", "1-ви", "3-ти", "5-ти", "10-и", "11-ти", "12-ти", "22-ри", "20-и", "Рак", ""):
            self.assertEqual(bg_text.prep_v(word), "в", word)

    def test_in_form_puts_the_preposition_in_front(self):
        self.assertEqual(bg_text.in_form("Водолей"), "във Водолей")
        self.assertEqual(bg_text.in_form("Везни"), "във Везни")
        self.assertEqual(bg_text.in_form("Телец"), "в Телец")
        self.assertEqual(bg_text.in_form("2-ри"), "във 2-ри")
        self.assertEqual(bg_text.in_form("12-ти"), "в 12-ти")

    def test_the_fixer_of_the_ai_text_knows_the_second_house_too(self):
        fix = bg_text.bulgarianize
        self.assertEqual(fix("Слънцето е в Водолей (26°30')."), "Слънцето е във Водолей (26°30').")
        self.assertEqual(fix("Новолуние в Везни"), "Новолуние във Везни")
        self.assertEqual(fix("Меркурий е в 2-ри дом."), "Меркурий е във 2-ри дом.")
        self.assertEqual(fix("В 2-ри дом на Иван"), "Във 2-ри дом на Иван")
        for same in ("в 12-ти дом", "в 22-ри дом", "в 5-ти дом", "в 1-ви дом", "във 2-ри дом", "в Телец", "в 20 дом"):
            self.assertEqual(fix(same), same)


class TemplatesTest(unittest.TestCase):
    """Заглавията и бележките, които пише кодът (не AI): всяка форма е написана по правилото."""

    def test_natal_placements_use_vav_before_libra_aquarius_and_the_second_house(self):
        self.assertEqual(natal_pair.placement_heading("Sun", "Иван", "Aquarius", "26°30'", 9), "Слънцето на Иван е във Водолей (26°30'), в 9-ти дом.")
        self.assertEqual(natal_pair.placement_heading("Mercury", "Иван", "Libra", "4°43'", 2), "Меркурий на Иван е във Везни (4°43'), във 2-ри дом.")
        self.assertEqual(natal_pair.placement_heading("Moon", "Иван", "Taurus", "19°14'", 12), "Луната на Иван е в Телец (19°14'), в 12-ти дом.")
        self.assertEqual(natal_pair.placement_heading("Moon", "Иван", "Pisces", "1°14'", None), "Луната на Иван е в Риби (1°14').")

    def test_the_planet_in_the_house_of_the_other_person(self):
        self.assertEqual(natal_pair.overlay_heading("Sun", "Иван", 2, "Тест А одит"), "Слънцето на Иван попада във 2-ри дом на Тест А одит.")
        self.assertEqual(natal_pair.overlay_heading("Sun", "Иван", 12, "Тест А одит"), "Слънцето на Иван попада в 12-ти дом на Тест А одит.")
        for house in range(1, 13):
            heading = natal_pair.overlay_heading("Venus", "Иван", house, "Мария")
            self.assertEqual(" във " in heading, house == 2, heading)

    def test_the_angles_of_the_natal_chart(self):
        self.assertEqual(natal_single.angle_heading("ASC", "Иван", "Aquarius", "3°10'"), "Асцендентът на Иван е във Водолей (3°10').")
        self.assertEqual(natal_single.angle_heading("MC", "Иван", "Libra", "3°10'"), "MC на Иван е във Везни (3°10').")
        self.assertEqual(natal_single.angle_heading("MC", "Иван", "Cancer", "3°10'"), "MC на Иван е в Рак (3°10').")

    def test_the_note_for_an_unknown_birth_time(self):
        chart = {"sign_ranges": {"Moon": {"signs": ["Libra", "Scorpio"]}, "Sun": {"signs": ["Aquarius"]}, "Mercury": {"signs": ["Capricorn", "Aquarius"]}}}
        self.assertEqual(birthtime.sign_sentences(chart), ["Луната е във Везни или Скорпион, според часа",
                                                           "Слънцето е във Водолей (знакът е сигурен, точният градус не е)",
                                                           "Меркурий е в Козирог или Водолей, според часа"])
        self.assertEqual(birthtime.sign_sentences({"sign_ranges": {"Moon": {"signs": ["Scorpio", "Libra"]}}}), ["Луната е в Скорпион или Везни, според часа"])

    def test_the_houses_of_a_period_card(self):
        self.assertEqual(period_cards._houses_clause({"natal_house": {"User": 2, "Partner": 12}}, "Иван", "Мария"), " (във 2-ри дом на Иван и в 12-ти дом на Мария)")
        self.assertEqual(period_cards._houses_clause({"natal_house": {"User": 5}}, "Иван", "Мария"), " (в 5-ти дом на Иван)")
        self.assertEqual(period_cards._in_sign("Водолей"), "във Водолей")
        self.assertEqual(period_cards._in_sign("Телец"), "в Телец")

    def test_no_code_written_heading_of_the_fixed_cases_has_the_plain_preposition_before_v_or_f(self):
        """Всички заглавия, които кодът строи за фиксираните случаи на сверката (натален за един и за двама, за дата): нито „в Водолей“, нито „в 2-ри дом“."""
        seen = 0
        for case, groups in vc.CASES.items():
            natal = case in ("natal_pair", "natal_single")
            for keys in groups:
                for variant in (vc.VARIANTS if natal else vc.VARIANTS[:1]):
                    for moment in ([None] if natal else vc.MOMENTS[:1]):
                        text, _people, _names = vc.offline_text(case, keys, moment, variant)
                        seen += 1
                        for bad in (" в Водолей", " в Везни", " в 2-ри дом", " в 2-ра ", " в Ф"):
                            self.assertNotIn(bad, text, (case, keys, variant[:2], bad))
        self.assertGreater(seen, 40)


class ExportWordingTest(Base):
    """Таблицата „Календар на периода“ в износа: събитията на български, с правилния предлог и род (преди: „New Луна in Везни“)."""

    EVENTS = (("New Moon in Libra", "Новолуние във Везни"), ("Full Moon in Taurus", "Пълнолуние в Телец"),
              ("Solar Eclipse in Leo", "Слънчево затъмнение в Лъв"), ("Lunar Eclipse in Aquarius", "Лунно затъмнение във Водолей"),
              ("Venus turns Retrograde in Scorpio", "Венера става ретроградна в Скорпион"), ("Venus turns Direct in Libra", "Венера става директна във Везни"),
              ("Moon turns Direct in Virgo", "Луна става директна в Дева"), ("Pluto turns Direct in Aquarius", "Плутон става директен във Водолей"),
              ("Mars turns Retrograde in Leo", "Марс става ретрограден в Лъв"), ("Sun enters Scorpio", "Слънце влиза в Скорпион"),
              ("Mercury enters Aquarius", "Меркурий влиза във Водолей"), ("Venus enters Libra (moving retrograde)", "Венера влиза във Везни (на ретрограден ход)"))

    def test_every_kind_of_event_is_written_in_bulgarian(self):
        for english, bulgarian in self.EVENTS:
            self.assertEqual(report_export._event_text({"event": english}), bulgarian, english)

    def test_an_unknown_kind_of_event_keeps_the_old_word_by_word_replacement(self):
        self.assertEqual(report_export._event_text({"event": "Chiron dances in Pisces"}), "Хирон dances in Риби")
        self.assertEqual(report_export._event_text({}), "")

    def test_the_real_calendar_rows_have_no_english_word(self):
        cal = rs.calendar(True)
        rows = report_export._calendar_rows({"calendar": {"events": cal.public_events()}})
        self.assertTrue(rows)
        for row in rows:
            latin = [w for w in re.findall(r"[A-Za-z]{3,}", row["what"] + " " + row["kind"])]
            self.assertEqual(latin, [], row)
        kinds = {r["kind"] for r in rows}
        self.assertTrue({"Лунация", "Станция", "Влизане в знак", "Аспект"} <= kinds, kinds)

    def test_the_exported_period_report_says_it_in_bulgarian_in_markdown_and_docx(self):
        headers, report_id = self.generate("bg-export@test.bg", PERIOD_BODY)
        md = self.client.get(f"/reports/{report_id}/export?format=md", headers=headers).text
        table = [line for line in md.splitlines() if re.match(r"\| \d{2}\.\d{2}\.2026 \d{2}:\d{2} \| (Станция|Влизане в знак|Лунация|Затъмнение) \|", line)]
        self.assertTrue(table)
        for line in table:
            self.assertEqual(re.findall(r"[A-Za-z]{3,}", line), [], line)
        self.assertIn("Пълнолуние в Телец", md)
        self.assertIn("Венера влиза във Везни (на ретрограден ход)", md)
        self.assertNotIn("Луна in", md)
        docx = docx_text(self.client.get(f"/reports/{report_id}/export?format=docx", headers=headers).content)
        self.assertIn("Пълнолуние в Телец", docx)
        self.assertNotIn("Луна in", docx)


class VerifiersReadBothFormsTest(unittest.TestCase):
    """Независимите сверки четат и новия вид („във 2-ри дом“), и старите записани текстове („в 2-ри дом“)."""

    def test_the_placement_and_overlay_lines(self):
        for line, house in (("Меркурий на Иван е във Везни (4°43'), във 2-ри дом.", "2-ри"), ("Меркурий на Иван е във Везни (4°43'), в 2-ри дом.", "2-ри"),
                            ("Слънцето на Иван е във Водолей (26°30'), в 9-ти дом.", "9-ти"), ("Слънцето на Иван е в Водолей (26°30'), в 9-ти дом.", "9-ти")):
            m = vc.PLACEMENT_RE.match(line)
            self.assertIsNotNone(m, line)
            self.assertEqual(m.group(6), house)
        for line, house in (("Слънцето на Иван попада във 2-ри дом на Тест А одит.", "2-ри"), ("Слънцето на Иван попада в 2-ри дом на Тест А одит.", "2-ри"),
                            ("Слънцето на Иван попада в 12-ти дом на Тест А одит.", "12-ти")):
            m = vc.OVERLAY_RE.match(line)
            self.assertIsNotNone(m, line)
            self.assertEqual(m.group(3), house)

    def test_the_houses_clause_of_a_period_card(self):
        for clause in ("във 2-ри дом на Иван", "в 2-ри дом на Иван"):
            m = vp.HOUSE_PART_RE.match(clause)
            self.assertIsNotNone(m, clause)
            self.assertEqual(m.group(1), "2-ри")


class LanguageCheckTest(unittest.TestCase):
    def setUp(self):
        self.facts = rs.facts("period", True)

    def words(self, text, facts=None):
        return [re.search(r"„(.+?)“", v.detail).group(1) for v in tc.check_language(text, facts or self.facts)]

    def test_the_two_real_words_from_the_final_acceptance_are_found(self):
        self.assertEqual(self.words("Отношенията ви са сериозни, но може да им липсва лекота и spontaneity. Полезно е понякога."), ["spontaneity"])
        self.assertEqual(self.words("Говорете за темпото – колко бързо и колко дълбоко всеки се чувства comfortable, за да не се получава натиск."), ["comfortable"])

    def test_it_is_a_hard_violation_with_a_bulgarian_instruction(self):
        found = tc.check_language("Днес е comfortable.", self.facts)
        self.assertEqual([(v.code, v.severity) for v in found], [("language", "hard")])
        self.assertIn("„comfortable“", found[0].detail)
        self.assertIn("на български", found[0].detail)
        self.assertIn("comfortable", found[0].excerpt)

    def test_the_whole_check_returns_it_and_the_repair_prompt_names_the_word(self):
        found = [v for v in tc.check_text("Днес се чувствате comfortable.", self.facts) if v.code == "language"]
        self.assertEqual(len(found), 1)
        self.assertIn("comfortable", tc.violations_for_prompt(found))

    def test_one_violation_for_every_different_word_and_one_for_a_repeated_word(self):
        self.assertEqual(self.words("Spontaneity и spontaneity и SPONTANEITY, а също comfortable."), ["Spontaneity", "comfortable"])
        self.assertEqual(self.words("North Node и Chiron"), ["North", "Node", "Chiron"])

    def test_html_tags_are_not_words(self):
        self.assertEqual(self.words("<p>Това е <strong>добре</strong> написано.</p>"), [])
        self.assertEqual(self.words("<p>Това е <b>comfortable</b>.</p>"), ["comfortable"])

    def test_the_internal_labels_of_the_prompt_are_not_english_words_of_the_ai(self):
        """Служебните етикети на подканата text_guard маха преди проверката (clean_labels), затова check_language не ги брои за английска дума."""
        text = "Планетите на Иван върху вашите домове („PARTNER PLANETS IN USER'S NATAL HOUSES\") са важни. Още (ЗАДЪЛЖИТЕЛНА СЕКЦИЯ) и state_in_month: активен."
        self.assertEqual(self.words(text), [])
        self.assertEqual(self.words(text + " Но това е comfortable."), ["comfortable"])

    def test_allowed_latin_is_not_an_english_word(self):
        for text in ("Всички часове са по местно време (Europe/Sofia).", "Асцендент ASC и MC са в 17°22'.", "Времето е UTC, GMT и DST.", "Файл в PDF или DOCX и SMS.",
                     "Луната е в III дом, а Слънцето в XII дом.", "AI не гарантира.", "Wi-Fi и e-mail.", "www.example.com, user@host.bg и https://example.org/path",
                     "Часът е 18:45, а градусите 17°22' и 3°10'.", "OK, TV и PC са кратки.", "", "Само кирилица без латиница."):
            self.assertEqual(self.words(text), [], text)

    def test_the_names_of_the_people_are_allowed(self):
        facts = dataclasses.replace(rs.facts("period", True), names={"user": "Maria Ivanova", "partner": "Leo"})        # копие: rs.facts() връща общ кеширан обект
        self.assertEqual(self.words("Разговорът на Maria с Leo и Ivanova е важен, но comfortable не е.", facts), ["comfortable"])
        self.assertNotIn("Maria Ivanova", rs.facts("period", True).names.values())                       # общият обект на другите тестове не е пипнат

    def test_the_names_bound_for_the_generation_are_allowed(self):
        token = bg_text.bind(["John Smith"])
        try:
            self.assertEqual(self.words("Срещата с John Smith е важна."), [])
        finally:
            bg_text.unbind(token)
        self.assertEqual(self.words("Срещата с John Smith е важна."), ["John", "Smith"])

    def test_the_words_of_the_question_of_the_user_are_allowed(self):
        facts = tc.build_facts(mode="natal", user_name=rs.A_NAME, natal_chart=rs.chart("A"), question="Да използвам ли Instagram за работа?")
        self.assertEqual(facts.latin_ok, {"instagram"})
        self.assertEqual(self.words("Instagram е добър за работата ви, но не е comfortable.", facts), ["comfortable"])
        self.assertEqual(self.words("Instagram е добър."), ["Instagram"])                       # без въпроса думата е чужда

    def test_the_question_words_survive_the_restriction_to_the_events_of_a_card(self):
        facts = tc.build_facts(mode="natal", user_name=rs.A_NAME, natal_chart=rs.chart("A"), question="Да използвам ли Instagram за работа?")
        restricted = dataclasses.replace(facts, question="")                                   # както restrict_events() за блок на периода
        self.assertEqual(self.words("Instagram помага.", restricted), [])

    def test_it_does_not_touch_real_delivered_texts(self):
        """Реални доставени отчети от 12 и 18.10.2026 (без двата с английската дума): нито една забележка."""
        import glob
        import os
        base = os.path.join(os.path.dirname(os.path.abspath(__file__)), "fixtures")
        known = {"live_2026_10_18_final_natal_single_A_no_time.txt": ["spontaneity"], "live_2026_10_18_final_natal_pair_A_IVAN_love.txt": ["comfortable"]}
        checked = 0
        for path in sorted(glob.glob(os.path.join(base, "live_2026_10_1*.txt"))):
            with open(path, encoding="utf-8") as f:
                text = f.read()
            self.assertEqual(self.words(text), known.get(os.path.basename(path), []), os.path.basename(path))
            checked += 1
        self.assertGreater(checked, 25)

    def test_the_checker_version_names_the_new_rule(self):
        self.assertEqual(tc.CHECKER_VERSION, "28")


class LivePhase30TextsTest(unittest.TestCase):
    """Четири реални отчета с кода на Фаза 30 (18.10.2026, продукция, реален AI), записани точно както са доставени върху екрана веднага след
    генерацията: формите са правилни, няма латиница, а независимите сверки четат „във“. Натален без час не се сверява с verify_cards: в четири карти
    за аспект AI е повторил собствения орбис в тълкуването (фактите са верни; строгата сверка го води като несъответствие, продукционната проверка го допуска)."""
    BASE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "fixtures")

    @classmethod
    def setUpClass(cls):
        _text, cls.period_people = vp.offline_text(("IVAN", "A"), "2026-10-20", "2026-10-31")

    def load(self, name):
        with open(os.path.join(self.BASE, f"live_2026_10_18_phase30_{name}.txt"), encoding="utf-8") as f:
            return f.read()

    def people(self, keys):
        people = vc.build_people(keys)
        for person, key in zip(people, keys):
            person["name"] = re.sub(r"\s*[—-]\s*", " ", vc.PEOPLE[key]["name"]).strip()
        return people

    def test_the_forms_are_right_and_there_are_no_english_words(self):
        facts = rs.facts("period", True)
        for name in ("natal_single_A", "natal_pair_A_IVAN_love", "natal_single_A_no_time", "period_pair_A_IVAN_love"):
            text = self.load(name)
            self.assertNotIn("Резервен анализ", text, name)
            self.assertNotIn("Частичен анализ", text, name)
            self.assertEqual(re.findall(r"(?<![ъЪ])в (?:Водолей|Везни|2-ри|2-ра|2-ро)", text), [], name)
            self.assertEqual([v.detail for v in tc.check_language(text, facts)], [], name)
        self.assertIn("е във Водолей (26°30'), в 9-ти дом.", self.load("natal_single_A"))
        self.assertIn("Слънцето на Иван попада във 2-ри дом на Тест А одит.", self.load("natal_pair_A_IVAN_love"))
        self.assertIn("Луната е във Везни или Скорпион, според часа.", self.load("natal_single_A_no_time"))
        self.assertIn("Венера навлиза във Везни, идвайки от Скорпион", self.load("period_pair_A_IVAN_love"))

    def test_the_independent_checks_accept_the_new_forms(self):
        problems, counts = vc.verify(self.load("natal_single_A"), self.people(("A",)), None, "natal_single")
        self.assertEqual(problems, [])
        self.assertEqual((counts["placement"], counts["aspect"], counts["interpretations"], counts["closing"]), (8, 4, 12, 3))
        problems, counts = vc.verify(self.load("natal_pair_A_IVAN_love"), self.people(("A", "IVAN")), None, "natal_pair")
        self.assertEqual(problems, [])
        self.assertEqual((counts["placement"], counts["aspect"], counts["overlay"], counts["interpretations"], counts["closing"]), (6, 9, 6, 21, 4))
        problems, counts = vp.verify_period(self.load("period_pair_A_IVAN_love"), self.period_people, "2026-10-18", "2026-11-30")
        self.assertEqual(problems, [])
        self.assertEqual((counts["cards"], counts["ingress"], counts["lunation"], counts["closing"]), (16, 2, 3, 6))


class LanguageFlowTest(FlowBase):
    """Какво става с английска дума в тълкуване на карта и в съвет за месеца, с подменения AI (като другите нарушения)."""

    def test_a_card_with_an_english_word_is_repaired_once_and_the_result_stays_full(self):
        spent = {"n": 0}

        def card(card_id):
            if card_id == "M1E1" and not spent["n"]:
                spent["n"] += 1
                return ENGLISH_CARD
        steps = self.run_report(self.ai(card=card))
        final = steps[-1]
        entry = next(c for c in final["checks"] if c["stage"] == "period_cards")
        self.assertEqual((entry["quality"], entry["removed"]), ("full", ""))
        self.assertNotIn("comfortable", final["month_texts"][0][1])
        self.assertIn("repaired", [c.get("result") for c in final["checks"]])

    def test_the_repair_request_names_the_english_word(self):
        asked = []

        def repair(prompt):
            asked.append(prompt)
            return GOOD_CARD
        self.run_report(self.ai(card=lambda i: ENGLISH_CARD if i == "M1E1" else None, repair=repair))
        self.assertTrue(asked)
        self.assertIn("comfortable", asked[0])
        self.assertIn("[language]", asked[0])

    def test_a_secondary_card_that_keeps_the_english_word_is_dropped_and_the_text_never_appears(self):
        cal = rs.calendar(False)
        drop = [c.id for c in cards_of(cal, False, limit=period_cards.CARDS_PER_MONTH) if not c.key][0]
        steps = self.run_report(self.ai(card=lambda i: ENGLISH_CARD if i == drop else None, repair=lambda _p: ENGLISH_CARD))
        final = steps[-1]
        entry = next(c for c in final["checks"] if c["stage"] == "period_cards")
        self.assertEqual(entry["quality"], "partial")
        self.assertIn(drop, entry["removed"])
        self.assertIn("language", entry["codes"])
        whole = final["overview"] + "".join(t for _t, t in final["month_texts"])
        self.assertNotIn("comfortable", whole)

    def test_a_key_card_that_keeps_the_english_word_refuses_the_report(self):
        cal = rs.calendar(False)
        key = next(c.id for c in cards_of(cal, False, limit=period_cards.CARDS_PER_MONTH) if c.key)
        with self.assertRaises(period_report.ForecastGenerationError) as caught:
            self.run_report(self.ai(card=lambda i: ENGLISH_CARD if i == key else None, repair=lambda _p: ENGLISH_CARD))
        self.assertTrue(caught.exception.stage.endswith("key_card"), caught.exception.stage)

    def test_a_month_advice_with_an_english_word_is_recomposed_from_the_verified_cards(self):
        steps = self.run_report(self.ai(month=lambda _s: ENGLISH_MONTH, repair=lambda _p: ENGLISH_MONTH))
        final = steps[-1]
        entry = next(c for c in final["checks"] if c["stage"] == "period_cards")
        self.assertEqual((entry["quality"], entry["removed"]), ("fallback", ""))
        self.assertNotIn("comfortable", final["month_texts"][0][1])
        self.assertIn("Резервен анализ", final["overview"])


if __name__ == "__main__":
    unittest.main()
