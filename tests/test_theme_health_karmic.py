"""
Две теми получиха свой стил (19.10.2026, по искане на собственика; виж theme_style.py):
- „Здраве“: конкретно назоваване на потенциалните слаби места (зона на тялото, система, вид оплаквания) като склонност, не като диагноза;
- „Карма и род“: системата на Хули Леонис (път на душата, уроци на Сатурн, Луна, възли, ретрограден, родови модели), по-мистичен и духовен глас.

Тези тестове пазят три неща: (1) двете теми наистина получават новия стил и новите проверки; (2) всички други теми (общ, кариера, пари, любов)
остават точно както бяха; (3) безопасната рамка и текстовете „Застраховка“ (safety.HEALTH_NOTE_HTML и safety.DISCLAIMER_HTML) не се променят.
Ако някой от тях се счупи, промяната отслабва безопасността или пипа другите теми и не бива да се прави без изрично искане на собственика.

Пускане: python -m unittest discover -s tests -p "test_theme_health_karmic.py"
"""
import asyncio
import unittest
from unittest import mock

import testenv  # noqa: F401  (трябва да е преди main)
from testenv import CHART  # noqa: E402

import ai_interpreter  # noqa: E402
import birthtime  # noqa: E402
import card_engine  # noqa: E402
import factpack  # noqa: E402
import generation  # noqa: E402
import main  # noqa: E402
import natal_pair  # noqa: E402
import natal_single as nsingle  # noqa: E402
import regression_support as rs  # noqa: E402
import relationship  # noqa: E402
import report_snapshot  # noqa: E402
import safety  # noqa: E402
import text_check as tc  # noqa: E402
import theme_style  # noqa: E402
from schemas import ChartRequest  # noqa: E402

OTHER_THEMES = ("general", "career", "money", "love")
SIGNS_BG = ("Овен", "Телец", "Близнаци", "Рак", "Лъв", "Дева", "Везни", "Скорпион", "Стрелец", "Козирог", "Водолей", "Риби")
PLANETS_BG = ("Слънце", "Луна", "Меркурий", "Венера", "Марс", "Юпитер", "Сатурн", "Уран", "Нептун", "Плутон", "Хирон")

# Конкретно, но като склонност: в тема „Здраве“ минава, в друга тема е „орган като известен проблем“
HEALTH_CARD = ("Стомахът и храносмилането са уязвима зона: възможни са киселини, подуване и нервен стомах при стрес. "
               "Редовният режим на хранене помага, а при повтарящи се оплаквания се консултирайте с лекар.")
HEALTH_GRAVE = "Има склонност към инфаркт при преумора, затова пазете сърцето си."
HEALTH_GRAVE_FIXED = "Сърдечносъдовата система се натоварва при преумора, затова пазете сърцето си и почивайте достатъчно."
# Езикът на родовите модели: в тема „Карма и род“ минава, в друга тема е „реална семейна биография“
KARMIC_CARD = ("Тази позиция описва тих родов модел, който се предава през рода като памет за дълга и мълчанието. "
               "Душата ви идва да вземе урока за границите и да освободи старата тежест.")
KARMIC_BIOGRAPHY = "Майка ви е била строга и недостъпна, затова търсите одобрение."


def bound(theme):
    """Закача темата за блока, както го прави generation.run; връща функция за откачане."""
    token = theme_style.bind(theme)
    return lambda: theme_style.unbind(token)


class ThemeStyleModuleTest(unittest.TestCase):
    def test_only_health_and_karmic_have_a_style_block(self):
        self.assertTrue(theme_style.block("health"))
        self.assertTrue(theme_style.block("karmic"))
        for theme in OTHER_THEMES + ("", None, "unknown"):
            self.assertEqual(theme_style.block(theme), "", theme)

    def test_bind_sets_both_variables_and_unbind_puts_them_back(self):
        self.assertEqual((theme_style.current.get(), theme_style.current_theme.get()), ("", ""))
        token = theme_style.bind("health")
        self.assertEqual(theme_style.current.get(), theme_style.HEALTH_BLOCK)
        self.assertEqual(theme_style.current_theme.get(), "health")
        self.assertTrue(theme_style.is_health() and not theme_style.is_karmic())
        theme_style.unbind(token)
        self.assertEqual((theme_style.current.get(), theme_style.current_theme.get()), ("", ""))
        token = theme_style.bind("karmic")
        self.assertTrue(theme_style.is_karmic() and not theme_style.is_health())
        theme_style.unbind(token)
        token = theme_style.bind("general")
        self.assertEqual((theme_style.current.get(), theme_style.current_theme.get()), ("", "general"))
        theme_style.unbind(token)

    def test_the_health_block_asks_for_concrete_weak_points_and_keeps_every_safety_limit(self):
        block = theme_style.HEALTH_BLOCK
        for needle in ("CONCRETE", "Do not water the topic down", "never hidden behind", "tendency or potential",
                       "never as an illness the person has now", "never as a certain future illness",
                       "Never name a grave disease", "Never tie an illness or injury to a date as an event", "no percentages", "no diagnosis",
                       "a doctor should be seen", "never tell the person to wait or postpone", "no medicines or doses", "no stopping or delaying treatment", "no fear and no fatalism",
                       "adds no fact and removes no safety limit"):
            self.assertIn(needle, block, needle)

    def test_the_health_block_names_every_sign_and_planet_with_its_body_area(self):
        block = theme_style.HEALTH_BLOCK
        for name in SIGNS_BG + PLANETS_BG:
            self.assertIn(name, block, name)
        for area in ("stomach", "heart", "intestines", "kidneys", "bones", "joints", "nerves", "liver", "skin", "lymph"):
            self.assertIn(area, block, area)

    def test_the_karmic_block_carries_the_leonis_lens_and_every_limit(self):
        block = theme_style.KARMIC_BLOCK
        for needle in ("Хули Леонис", "path of the soul", "Saturn is the teacher of karma", "true essence", "retrograde", "родова памет",
                       "родов модел", "ARCHETYPES", "lesson and a choice, never a punishment", "overrides the earlier wish for no mystic phrases",
                       "adds no fact", "use only what the cards state", "Never describe what happened to real relatives",
                       "never name Хули Леонис, any other astrologer, author or book in the text",
                       "nothing concrete about real parents, ancestors, childhood, past lives or family history", "No curses",
                       "no fatalism", "free will always", "Spiritual work never replaces medical or psychological help",
                       "Do not use the word \"смърт\""):
            self.assertIn(needle, block, needle)

    def test_the_blocks_never_override_what_may_be_named_or_the_facts(self):
        for block in (theme_style.HEALTH_BLOCK, theme_style.KARMIC_BLOCK):
            self.assertIn("adds no fact", block)
        self.assertIn("you never print the sign, planet or house names where the section rules forbid them", theme_style.HEALTH_BLOCK)
        self.assertIn("rules above about the facts, about what may be named in each section", theme_style.KARMIC_BLOCK)

    def test_theme_focus_changed_only_for_the_two_themes(self):
        focus = ai_interpreter.THEME_FOCUS
        self.assertIn("name concretely the body areas", focus["health"])
        self.assertIn("never diagnoses", focus["health"])
        self.assertIn("Хули Леонис", focus["karmic"])
        self.assertIn("never facts about ancestors or parents", focus["karmic"])
        self.assertEqual(focus["general"], "General life overview: main patterns, tensions, resources and priorities.")
        self.assertEqual(focus["career"], "Career and work: responsibilities, communication, negotiation and the concrete action asked about.")
        self.assertEqual(focus["money"], "Money and success: resources, priorities, habits and the alternatives named in the question (no investment advice).")
        self.assertEqual(focus["love"], "Relationships and love: emotions, communication, boundaries and the exact context of the relationship.")


class RulesForThemeTest(unittest.TestCase):
    def test_every_other_theme_gets_exactly_the_old_rules(self):
        for theme in OTHER_THEMES + ("", None, "unknown"):
            self.assertIs(safety.rules_for(theme), safety.SAFETY_RULES, theme)

    def test_health_replaces_exactly_one_rule(self):
        old, new = safety.SAFETY_RULES.split("\n"), safety.rules_for("health").split("\n")
        self.assertEqual(len(old), len(new))
        changed = [(a, b) for a, b in zip(old, new) if a != b]
        self.assertEqual(len(changed), 1)
        self.assertTrue(changed[0][0].startswith("- Не описвай органи или болести"))
        self.assertEqual(changed[0][1], safety.HEALTH_RULE)
        self.assertNotIn("Не описвай органи или болести", safety.rules_for("health"))

    def test_karmic_replaces_exactly_one_rule(self):
        old, new = safety.SAFETY_RULES.split("\n"), safety.rules_for("karmic").split("\n")
        self.assertEqual(len(old), len(new))
        changed = [(a, b) for a, b in zip(old, new) if a != b]
        self.assertEqual(len(changed), 1)
        self.assertTrue(changed[0][0].startswith("- Не твърди като факт нищо за родители"))
        self.assertEqual(changed[0][1], safety.KARMIC_RULE)
        self.assertNotIn("Не твърди като факт нищо за родители", safety.rules_for("karmic"))

    def test_the_hard_limits_stay_in_every_variant(self):
        limits = ("НИКОГА не предсказвай смърт", "НИКОГА не поставяй медицински диагнози, не препоръчвай лекарства и не съветвай спиране на лечение",
                  "НИКОГА не съветвай да се отложи консултация", "Не плаши, не използвай фатализъм", "112", "Не давай правни съвети",
                  "НИКОГА не давай конкретни финансови", "Не давай проценти")
        for theme in ("health", "karmic", "general"):
            for needle in limits:
                self.assertIn(needle, safety.rules_for(theme), (theme, needle))

    def test_the_health_rule_keeps_the_boundaries_and_the_karmic_rule_keeps_the_boundaries(self):
        for needle in ("Не ги представяй като диагноза", "като болест, която човекът има сега", "като сигурна бъдеща болест", "Не назовавай тежки заболявания",
                       "не посочвай дати", "не давай лекарства", "кога да се потърси лекар"):
            self.assertIn(needle, safety.HEALTH_RULE, needle)
        for needle in ("символ и тълкуване на картата", "реални родители, предци, детство, минали животи или семейна история",
                       "освен ако потребителят изрично го е написал", "Не обвинявай родител или род", "проклятие", "неизбежна съдба"):
            self.assertIn(needle, safety.KARMIC_RULE, needle)


class PromptAssemblyTest(unittest.TestCase):
    """Какво получава AI: правилата и блокът на темата влизат във всяка заявка, а за другите теми заявката е същата като преди."""

    def system_prompt(self, theme, names=None, relation=None):
        captured = {}

        class FakeResponse:
            status_code = 200

            def json(self):
                return {"choices": [{"message": {"content": "отговор"}, "finish_reason": "stop"}], "usage": {}}

        class FakeClient:
            def __init__(self, *a, **k):
                pass

            async def __aenter__(self):
                return self

            async def __aexit__(self, *a):
                return False

            async def post(self, url, headers=None, json=None, **k):
                captured["system"] = json["messages"][0]["content"]
                return FakeResponse()

        async def run():
            tokens = [theme_style.bind(theme)]
            if relation:
                tokens.append(relationship.bind(relation))
            if names:
                tokens.append(birthtime.bind(names))
            try:
                await main.ai_interpreter._call_api("MODE: TEST. Be direct and concrete. No filler, no mystic phrases.", "потребител", 100)
            finally:
                if names:
                    birthtime.unbind(tokens.pop())
                if relation:
                    relationship.unbind(tokens.pop())
                theme_style.unbind(tokens.pop())

        ai = main.ai_interpreter
        with mock.patch.object(ai, "ollama_key", "k"), mock.patch.object(ai, "ollama_url", "http://x"), \
                mock.patch("ai_interpreter.httpx.AsyncClient", FakeClient):
            asyncio.run(run())
        return captured["system"]

    def test_health_gets_its_rule_and_its_block_after_the_quality_rules(self):
        prompt = self.system_prompt("health")
        self.assertIn(safety.rules_for("health"), prompt)
        self.assertIn(theme_style.HEALTH_BLOCK, prompt)
        self.assertNotIn("Не описвай органи или болести", prompt)
        self.assertNotIn(theme_style.KARMIC_BLOCK, prompt)
        self.assertLess(prompt.index(safety.rules_for("health")), prompt.index(safety.QUALITY_RULES))
        self.assertLess(prompt.index(safety.QUALITY_RULES), prompt.index(theme_style.HEALTH_BLOCK))

    def test_karmic_gets_its_rule_and_its_block_after_the_quality_rules(self):
        prompt = self.system_prompt("karmic")
        self.assertIn(safety.rules_for("karmic"), prompt)
        self.assertIn(theme_style.KARMIC_BLOCK, prompt)
        self.assertNotIn("Не твърди като факт нищо за родители", prompt)
        self.assertNotIn(theme_style.HEALTH_BLOCK, prompt)
        self.assertLess(prompt.index(safety.QUALITY_RULES), prompt.index(theme_style.KARMIC_BLOCK))

    def test_every_other_theme_gets_the_request_it_always_got(self):
        for theme in OTHER_THEMES + ("",):
            prompt = self.system_prompt(theme)
            self.assertIn(safety.SAFETY_RULES, prompt, theme)
            self.assertNotIn("THEME STYLE", prompt, theme)
            self.assertTrue(prompt.endswith(safety.QUALITY_RULES), theme)

    def test_the_unknown_birth_time_rule_still_comes_last_and_prevails(self):
        for theme in ("health", "karmic"):
            prompt = self.system_prompt(theme, names=["Мария"], relation="friend")
            block = theme_style.block(theme)
            self.assertLess(prompt.index("RELATIONSHIP CONTEXT"), prompt.index(block), theme)
            self.assertLess(prompt.index(block), prompt.index("BIRTH TIME UNKNOWN"), theme)
            self.assertTrue(prompt.rstrip().endswith(birthtime.block(["Мария"]).rstrip()), theme)

    def test_the_block_overrides_the_mode_prompts_wish_for_no_mystic_phrases_only_in_karmic(self):
        self.assertIn("No filler, no mystic phrases.", self.system_prompt("karmic"))        # правилото на режима е там, блокът го отменя изрично
        self.assertIn("overrides the earlier wish for no mystic phrases", self.system_prompt("karmic"))
        self.assertNotIn("overrides the earlier wish for no mystic phrases", self.system_prompt("health"))
        self.assertNotIn("overrides the earlier wish for no mystic phrases", self.system_prompt("general"))

    def test_the_prompt_fingerprint_changes_only_for_the_two_themes(self):
        import hashlib
        old = hashlib.sha256((safety.SAFETY_RULES + safety.QUALITY_RULES).encode("utf-8")).hexdigest()[:12]
        for theme in OTHER_THEMES + ("", None):
            self.assertEqual(report_snapshot.prompt_fingerprint(theme), old, theme)
        self.assertEqual(report_snapshot.prompt_fingerprint(), old)
        fingerprints = {old, report_snapshot.prompt_fingerprint("health"), report_snapshot.prompt_fingerprint("karmic")}
        self.assertEqual(len(fingerprints), 3)
        self.assertEqual(report_snapshot.versions("health")["prompt"], report_snapshot.prompt_fingerprint("health"))
        self.assertEqual(report_snapshot.versions()["prompt"], old)


class HealthChecksTest(unittest.TestCase):
    CONCRETE = (
        "Стомахът и храносмилането са уязвима зона: възможни са киселини и нервен стомах при стрес.",
        "Имате слаб стомах.",
        "Слабо място е сърцето при преумора.",
        "Сърцето ви е уязвимо към стрес.",
    )

    def test_a_concrete_weak_point_is_named_in_health_but_still_a_violation_in_every_other_theme(self):
        for sentence in self.CONCRETE:
            self.assertEqual(safety.health_findings(sentence, "health"), [], sentence)
            self.assertEqual([code for code, _ in safety.health_findings(sentence, "")], ["health_organ"], sentence)
            for theme in OTHER_THEMES + ("karmic",):
                self.assertEqual([code for code, _ in safety.health_findings(sentence, theme)], ["health_organ"], (theme, sentence))

    def test_a_certain_or_present_illness_is_a_violation_in_health(self):
        for sentence in ("Ще получите гастрит.", "Имате гастрит възпаление на стомаха.", "Страдате от хронично заболяване на стомаха.",
                         "Транзитът ще причини проблеми със сърцето.", "Ще имате възпаление на ставите.", "Ще страдате от язва."):
            self.assertEqual([code for code, _ in safety.health_findings(sentence, "health")], ["health_certain"], sentence)

    def test_ordinary_future_sentences_are_not_health_violations(self):
        for sentence in ("Така ще получите повече сили.", "Ще получите подкрепа и спокойствие.", "Това ще доведе до проблеми в отношенията.",
                         "Ще имате възможност да почивате повече."):
            self.assertEqual(safety.health_findings(sentence, "health"), [], sentence)

    def test_a_hedge_makes_the_future_sentence_a_tendency(self):
        for sentence in ("Ще получите гастрит, ако не се храните редовно.", "Стомахът може да е уязвим при стрес.",
                         "При стрес е възможно да получите гастрит."):
            self.assertEqual(safety.health_findings(sentence, "health"), [], sentence)

    def test_a_grave_disease_named_as_a_potential_is_a_violation_even_with_a_hedge(self):
        for sentence in ("Склонност към инфаркт при преумора.", "Има риск от рак на стомаха.", "Възможен е диабет при неправилно хранене.",
                         "Картата подсказва риск от инсулт.", "Може да има тумор на щитовидната жлеза.", "Тревожността може да се превърне в шизофрения."):
            self.assertEqual([code for code, _ in safety.health_findings(sentence, "health")], ["health_grave"], sentence)

    def test_a_grave_word_the_user_wrote_in_the_question_is_not_a_violation_but_another_one_is(self):
        question = "Имам диабет. Как да подобря здравето си?"
        answer = "Диабетът е тема, за която се консултирайте с лекаря си, а храненето и режимът помагат."
        self.assertEqual([c for c, _ in safety.health_findings(answer, "health")], ["health_grave"])
        self.assertEqual(safety.health_findings(answer, "health", question=question), [])
        self.assertEqual([c for c, _ in safety.health_findings("Склонност към инфаркт при преумора.", "health", question=question)], ["health_grave"])

    def test_the_question_exemption_works_through_the_checker(self):
        question = "Имам диабет. Как да подобря здравето си?"
        facts = tc.build_facts(mode="natal", user_name=rs.A_NAME, natal_chart=rs.chart("A"), report_date="2026-10-17", question=question)
        answer = "Диабетът е тема, за която се консултирайте с лекаря си, а храненето и режимът помагат."
        restore = bound("health")
        try:
            self.assertEqual([v.code for v in tc.check_text(answer, facts) if v.severity == "hard"], [])
            self.assertEqual([v.code for v in tc.check_text("Склонност към инфаркт при преумора.", facts) if v.severity == "hard"], ["health"])
        finally:
            restore()

    def test_the_zodiac_sign_with_a_capital_is_not_a_cancer_diagnosis(self):
        self.assertEqual(safety.health_findings("Слънцето е в Рак и стомахът е чувствителен.", "health"), [])
        self.assertEqual(safety.health_findings("Рак на мястото на тази позиция.", "health"), [])

    def test_a_sentence_with_a_safe_mark_is_not_a_violation(self):
        self.assertEqual(safety.health_findings("При внезапна болка в гърдите веднага се обадете на 112, не изчаквайте инфаркт.", "health"), [])

    def test_delaying_medical_help_stays_a_violation_in_health_and_everywhere(self):
        sentence = "Отложете прегледа при лекар до 14 ноември."
        for theme in ("health", "karmic", "general", ""):
            self.assertEqual([code for code, _ in safety.health_findings(sentence, theme)], ["health_delay"], theme)

    def test_the_explicit_theme_wins_over_the_bound_one(self):
        restore = bound("health")
        try:
            self.assertEqual(safety.health_findings(self.CONCRETE[1]), [])
            self.assertEqual([c for c, _ in safety.health_findings(self.CONCRETE[1], "general")], ["health_organ"])
        finally:
            restore()
        self.assertEqual([c for c, _ in safety.health_findings(self.CONCRETE[1])], ["health_organ"])

    def test_the_checker_uses_the_bound_theme_and_gives_the_right_advice(self):
        facts = tc.build_facts(mode="natal", user_name=rs.A_NAME, natal_chart=rs.chart("A"), report_date="2026-10-17")

        def health_details(text):
            return [v.detail for v in tc.check_text(text, facts) if v.code == "health" and v.severity == "hard"]

        self.assertEqual(len(health_details(HEALTH_CARD)), 1)                                   # без тема: орган като факт
        restore = bound("health")
        try:
            self.assertEqual(health_details(HEALTH_CARD), [])
            grave = health_details(HEALTH_GRAVE)
            self.assertEqual(grave, [safety.HEALTH_GRAVE_ADVICE])
            self.assertIn("сърдечносъдовата система", grave[0])
            certain = health_details("Ще получите гастрит.")
            self.assertEqual(certain, [safety.HEALTH_CERTAIN_ADVICE])
            self.assertIn("склонност", certain[0])
        finally:
            restore()


class KarmicChecksTest(unittest.TestCase):
    def test_the_language_of_lineage_patterns_is_allowed_in_karmic_but_not_elsewhere(self):
        for sentence in ("Моделът се предава през родовата линия като тиха памет.", "Тази тема се унаследява от рода.", KARMIC_CARD.split(". ")[0] + "."):
            self.assertEqual(safety.biography_findings(sentence, "karmic"), [], sentence)
            self.assertTrue(safety.biography_findings(sentence, ""), sentence)
            self.assertTrue(safety.biography_findings(sentence, "health"), sentence)

    def test_real_family_biography_is_a_violation_in_karmic_too(self):
        for sentence in ("Майка ви е била строга и недостъпна.", "Родител, който е бил недостъпен, оставя следа.",
                         "Предците ви са били изправени пред тежки избори.", KARMIC_BIOGRAPHY):
            self.assertTrue(safety.biography_findings(sentence, "karmic"), sentence)
            self.assertTrue(safety.biography_findings(sentence, ""), sentence)

    def test_a_hedge_still_makes_a_family_sentence_a_symbol(self):
        self.assertEqual(safety.biography_findings("Може да се свързва с родов модел: майка ви е била строга.", "karmic"), [])

    def test_the_checker_uses_the_bound_theme_and_the_karmic_advice(self):
        facts = tc.build_facts(mode="natal", user_name=rs.A_NAME, natal_chart=rs.chart("A"), report_date="2026-10-17")

        def details(text):
            return [v.detail for v in tc.check_text(text, facts) if v.code == "biography" and v.severity == "hard"]

        self.assertEqual(details(KARMIC_CARD), [safety.BIOGRAPHY_ADVICE])
        restore = bound("karmic")
        try:
            self.assertEqual(details(KARMIC_CARD), [])
            self.assertEqual(details(KARMIC_BIOGRAPHY), [safety.BIOGRAPHY_ADVICE_KARMIC])
            self.assertIn("родовия модел", safety.BIOGRAPHY_ADVICE_KARMIC)
        finally:
            restore()
        self.assertEqual(details(KARMIC_CARD), [safety.BIOGRAPHY_ADVICE])

    def test_the_old_pattern_is_unchanged_for_every_theme_but_karmic(self):
        # _BIO_RE е същият регулярен израз като преди разделянето: трите образеца в същия ред
        self.assertEqual(safety._BIO_RE.pattern, "|".join(safety._BIO_EVENTS + (safety._BIO_LINE,)))
        self.assertEqual(len(safety._BIO_EVENTS), 2)
        self.assertTrue(safety._BIO_LINE.startswith("(?:предава"))


class AttributionTest(unittest.TestCase):
    """Системата на Хули Леонис е призма за гласа; текстът на анализа не го назовава и не му приписва твърдения."""

    NAMED = ("Според Хули Леонис Сатурн е учителят на кармата.", "Това е по Леонис един урок на душата.", "В системата на хули леонис това е урок.",
             "As Xyli Leonis teaches, the lesson is patience.")

    def test_a_named_astrologer_is_found_in_any_theme(self):
        for sentence in self.NAMED:
            self.assertEqual(len(safety.attribution_findings(sentence)), 1, sentence)

    def test_ordinary_sentences_and_other_words_are_not_attributions(self):
        for sentence in ("Урокът на Сатурн е в търпението.", "Душата ви идва да вземе урока за границите.", "Леонид е име на човек.",
                         "Родовата памет носи урок за границите."):
            self.assertEqual(safety.attribution_findings(sentence), [], sentence)

    def test_a_name_the_user_wrote_in_the_question_is_not_a_violation(self):
        question = "Искам тълкуване по системата на Хули Леонис."
        self.assertEqual(len(safety.attribution_findings(self.NAMED[0], question)), 0)
        self.assertEqual(len(safety.attribution_findings(self.NAMED[0], "Как да поставям граници?")), 1)
        self.assertEqual(len(safety.attribution_findings(self.NAMED[0])), 1)

    def test_the_checker_makes_it_a_hard_violation_with_advice_for_every_theme(self):
        facts = tc.build_facts(mode="natal", user_name=rs.A_NAME, natal_chart=rs.chart("A"), report_date="2026-10-17")
        for theme in ("karmic", "health", "general"):
            restore = bound(theme)
            try:
                found = [(v.code, v.severity, v.detail) for v in tc.check_text(self.NAMED[0], facts)]
            finally:
                restore()
            self.assertIn(("attribution", "hard", safety.ATTRIBUTION_ADVICE), found, theme)

    def test_a_named_astrologer_in_a_karmic_card_is_repaired(self):
        victim = non_key_id("karmic")
        fixed = "Душата ви идва да вземе урока за границите и да освободи старата тежест."
        script = Script({"*": KARMIC_CARD, victim: self.NAMED[0]}, repair={"Хули Леонис": fixed})
        text, checks = run_flow("karmic", script)
        self.assertEqual((quality(checks), script.calls["repair"]), ("full", 1))
        self.assertNotIn("Леонис", text)


class InsuranceTextsStayTest(unittest.TestCase):
    """Собственикът: текстът „Застраховка“ (здравната бележка и общата уговорка) остава."""

    HEALTH_NOTE = ('<p class="astro-disclaimer"><i>Здравните теми в астрологията описват склонности, не диагнози. '
                   'При каквито и да е симптоми се обърнете към лекар.</i></p>')
    DISCLAIMER = ('<p class="astro-disclaimer"><i>Този анализ е за себепознание и размисъл. Той не е медицински, '
                  'финансов или правен съвет и не предсказва неизбежни събития. За решения, свързани със здраве, '
                  'пари или право, се консултирайте със специалист.</i></p>')

    def test_the_two_texts_are_exactly_the_same_as_before(self):
        self.assertEqual(safety.HEALTH_NOTE_HTML, self.HEALTH_NOTE)
        self.assertEqual(safety.DISCLAIMER_HTML, self.DISCLAIMER)

    def test_a_health_analysis_still_ends_with_both_texts(self):
        text, flags = safety.check_output("<p>Стомахът е чувствителна зона при стрес.</p>", "health")
        self.assertEqual(flags, [])
        self.assertIn(self.HEALTH_NOTE, text)
        self.assertTrue(text.endswith(self.DISCLAIMER))
        self.assertLess(text.index(self.HEALTH_NOTE), text.index(self.DISCLAIMER))

    def test_the_other_themes_are_closed_as_before(self):
        for theme, tail in (("money", self.DISCLAIMER), ("general", ""), ("karmic", ""), ("career", ""), ("love", "")):
            text, flags = safety.check_output("<p>Спокоен текст.</p>", theme)
            self.assertEqual(flags, [], theme)
            self.assertEqual(text.endswith(self.DISCLAIMER), bool(tail), theme)
            self.assertNotIn(self.HEALTH_NOTE, text)

    def test_the_severe_filters_still_remove_a_diagnosis_in_a_health_analysis(self):
        restore = bound("health")
        try:
            text, flags = safety.check_output("<p>Страдате от диабет. Стомахът е чувствителна зона.</p>", "health")
        finally:
            restore()
        self.assertIn("diagnosis", flags)
        self.assertNotIn("диабет", text.split('<p class="astro-disclaimer">')[0])
        self.assertIn(self.DISCLAIMER, text)


class GenerationBindsTheThemeTest(unittest.TestCase):
    """generation.run закача темата за целия анализ и я откача накрая. Проверката е в същата задача (не през asyncio.run на всяка стъпка):
    asyncio.run дава на корутината копие на контекста, затова откачането би изглеждало вярно и без unbind."""

    def run_generation(self, report_type, fail=False):
        seen = {}

        async def fake_plain(request, user_id, emit):
            seen["during"] = (theme_style.current.get(), theme_style.current_theme.get())
            if fail:
                raise RuntimeError("проба")
            return "готово"

        async def scenario():
            request = ChartRequest(**{**CHART, "report_type": report_type})
            before = (theme_style.current.get(), theme_style.current_theme.get())
            try:
                result = await generation.run(request, 1, lambda event: None)
            except generation.GenerationFailure:
                result = None
            seen["before"], seen["after"] = before, (theme_style.current.get(), theme_style.current_theme.get())
            return result

        with mock.patch.object(generation, "run_plain", fake_plain):
            result = asyncio.run(scenario())
        return seen, result

    def test_the_theme_is_bound_for_the_whole_generation_and_unbound_after_it(self):
        for theme, block in (("health", theme_style.HEALTH_BLOCK), ("karmic", theme_style.KARMIC_BLOCK), ("general", ""), ("career", ""),
                             ("money", ""), ("love", "")):
            seen, result = self.run_generation(theme)
            self.assertEqual(seen["during"], (block, theme), theme)
            self.assertEqual((seen["before"], seen["after"]), (("", ""), ("", "")), theme)
            self.assertEqual(result, "готово")

    def test_a_failing_generation_still_unbinds_the_theme(self):
        seen, result = self.run_generation("health", fail=True)
        self.assertEqual(seen["during"], (theme_style.HEALTH_BLOCK, "health"))
        self.assertIsNone(result)
        self.assertEqual(seen["after"], ("", ""))

    def test_a_request_without_a_type_is_general(self):
        seen = {}

        async def fake_plain(request, user_id, emit):
            seen["theme"] = theme_style.current_theme.get()
            return "готово"

        request = ChartRequest(**{**CHART, "report_type": None})
        with mock.patch.object(generation, "run_plain", fake_plain):
            asyncio.run(generation.run(request, 1, lambda event: None))
        self.assertEqual(seen["theme"], "general")


class KarmicSelectionTest(unittest.TestCase):
    """„Карма и род“ показва сърцето на системата: възел, Сатурн, Хирон, Плутон, Луна, Слънце. Другите теми избират карти както досега."""

    def test_only_the_karmic_planets_weigh_double(self):
        for theme in ("general", "love", "career", "money", "health"):
            self.assertEqual(natal_pair.Relevance(theme, "", "").theme_weight, 1, theme)
        rel = natal_pair.Relevance("karmic", "", "")
        self.assertEqual(rel.theme_weight, 2)
        for planet in ("Node", "Saturn", "Pluto", "Chiron", "Moon", "Sun"):
            self.assertIn(planet, natal_pair.THEME_PLANETS["karmic"])
        self.assertEqual((rel.planet("Saturn"), rel.planet("Moon"), rel.planet("Venus")), (2, 3, 1))      # тема; тема + „общо“ отношение; само отношението

    def test_the_other_themes_keep_their_planets(self):
        self.assertEqual(natal_pair.THEME_PLANETS["health"], {"Moon", "Mars", "Saturn", "Sun"})
        self.assertEqual(natal_pair.THEME_PLANETS["general"], {"Sun", "Moon", "Venus", "Mars", "Mercury"})
        self.assertEqual(natal_pair.THEME_HOUSES["karmic"], {12, 4, 8})

    def test_a_single_karmic_analysis_features_the_core_of_the_system(self):
        for chart, name in ((rs.chart("A"), rs.A_NAME), (rs.chart("Ivan"), rs.IVAN), (rs.unknown_ivan(), rs.IVAN)):
            placements = {c.row["planet"] for c in nsingle.build_cards(context("karmic", chart, name)) if c.row["kind"] == "placement"}
            self.assertTrue({"Node", "Saturn", "Chiron", "Pluto", "Sun"} <= placements, (name, placements))
        known = {c.row["planet"] for c in nsingle.build_cards(context("karmic")) if c.row["kind"] == "placement"}
        self.assertEqual(known, {"Sun", "Moon", "Saturn", "Node", "Chiron", "Pluto"})

    def test_a_karmic_pair_features_saturn_for_each_person_and_a_contact_of_the_slow_planets(self):
        pairs = ((rs.chart("A"), rs.A_NAME, rs.chart("Ivan"), rs.IVAN), (rs.chart("A"), rs.A_NAME, rs.child(), "Мария"))
        for ca, na, cb, nb in pairs:
            for relation in ("", "romantic", "family", "work"):
                ctx = card_engine.Context(natal_chart=ca, partner_chart=cb, transit_chart={}, user_name=na, partner_name=nb, first="А", second="Б",
                                          question="", target_date="", theme="x", utc_today="2026-10-19", report_type="karmic", relation=relation)
                cards = natal_pair.build_cards(ctx)
                for owner in ("user", "partner"):
                    planets = {c.row["planet"] for c in cards if c.row.get("kind") == "placement" and c.row["owner"] == owner}
                    self.assertIn("Saturn", planets, (nb, relation, owner))
                slow = {"Node", "Saturn", "Pluto", "Chiron"}
                self.assertTrue([c for c in cards if c.row.get("kind") == "synastry" and ({c.row["p1"], c.row["p2"]} & slow)], (nb, relation))


# ---- Истинският ход по карти (натален анализ за един), AI е подменен със сценарий ----
SUMMARY = "Вие сте човек, който съчетава спокойствие и упоритост: когато не бързате, постигате много и се чувствате сигурни."
ADVICE = "- Давайте си време за важните решения.\n- Говорете открито за очакванията си."
CONCLUSION = "Имате добра основа за стабилност и растеж и ще постигате най-много, когато вървите бавно и последователно."


def context(report_type, chart=None, name=rs.A_NAME):
    return card_engine.Context(
        natal_chart=chart or rs.chart("A"), partner_chart=None, transit_chart={}, user_name=name, partner_name=None,
        first=factpack.display_name(name, factpack.FIRST_PERSON_DEFAULT), second="", question="", target_date="", theme="x",
        utc_today="2026-10-17", report_type=report_type)


class Script:
    def __init__(self, card_text, repair=None):
        self.card_text, self.repair = card_text, repair or {}
        self.calls = {"main": 0, "repair": 0, "restore": 0}

    async def __call__(self, system_prompt, user_prompt, max_tokens, **kwargs):
        if "COMPOSITION FROM VERIFIED BLOCKS" in system_prompt:
            return "\n\n".join(f"[[{k}]]\n{v}" for k, v in {"SUMMARY": SUMMARY, "ADVICE": ADVICE, "CONCLUSION": CONCLUSION}.items())
        if "MODE: ONE-PERSON NATAL ANALYSIS. You INTERPRET" in system_prompt:
            self.calls["main"] += 1
            cards = nsingle.build_cards(context(self.report_type))
            parts = [f"[[{c.id}]]\n{self.card_text.get(c.id, self.card_text.get('*'))}" for c in cards]
            return "\n\n".join(parts + [f"[[{k}]]\n{v}" for k, v in {"SUMMARY": SUMMARY, "ADVICE": ADVICE, "CONCLUSION": CONCLUSION}.items()])
        if "You write ONE short interpretation" in system_prompt:
            self.calls["restore"] += 1
            return self.card_text.get("*")
        self.calls["repair"] += 1
        for key, fixed in self.repair.items():
            if key in user_prompt:
                return fixed
        return user_prompt

    report_type = "general"


def run_flow(report_type, script):
    script.report_type = report_type
    interp = ai_interpreter.AIInterpreter(api_key="test-dummy")
    interp._call_api = script
    checks = []
    restore = bound(report_type)
    try:
        text = asyncio.run(nsingle.interpret(interp, natal_chart=rs.chart("A"), user_name=rs.A_NAME, question="", report_type=report_type,
                                             checks=checks, theme="General.", utc_today="2026-10-17"))
    finally:
        restore()
    return text, checks


def quality(checks):
    return next((c["quality"] for c in checks if c.get("quality")), None)


def non_key_id(report_type):
    """Карта, която не е ключова: ако не мине проверката, анализът не се отказва."""
    built = nsingle.build_cards(context(report_type))
    card_engine.mark_key_cards(built, nsingle.MODE)
    return next(c.id for c in reversed(built) if not c.key)


class FlowTest(unittest.TestCase):
    def test_a_concrete_health_text_passes_every_check_without_a_repair(self):
        script = Script({"*": HEALTH_CARD})
        text, checks = run_flow("health", script)
        self.assertEqual((quality(checks), script.calls["repair"], script.calls["restore"]), ("full", 0, 0))
        self.assertIn("Стомахът и храносмилането са уязвима зона", text)
        self.assertIn("киселини, подуване и нервен стомах", text)

    def test_the_same_concrete_text_is_not_allowed_in_another_theme(self):
        facts = tc.build_facts(mode="natal", user_name=rs.A_NAME, natal_chart=rs.chart("A"), report_date="2026-10-17")
        for theme in ("general", "career"):
            restore = bound(theme)
            try:
                codes = [v.code for v in tc.check_text(HEALTH_CARD, facts) if v.severity == "hard"]
            finally:
                restore()
            self.assertEqual(codes, ["health"], theme)

    def test_a_grave_disease_in_health_is_repaired_into_a_general_wording(self):
        victim = non_key_id("health")
        script = Script({"*": HEALTH_CARD, victim: HEALTH_GRAVE}, repair={"инфаркт": HEALTH_GRAVE_FIXED})
        text, checks = run_flow("health", script)
        self.assertEqual((quality(checks), script.calls["repair"]), ("full", 1))
        self.assertNotIn("инфаркт", text)
        self.assertIn("Сърдечносъдовата система се натоварва", text)

    def test_a_lineage_text_passes_in_karmic_without_a_repair(self):
        script = Script({"*": KARMIC_CARD})
        text, checks = run_flow("karmic", script)
        self.assertEqual((quality(checks), script.calls["repair"], script.calls["restore"]), ("full", 0, 0))
        self.assertIn("тих родов модел, който се предава през рода", text)

    def test_the_same_lineage_text_is_a_violation_in_another_theme(self):
        facts = tc.build_facts(mode="natal", user_name=rs.A_NAME, natal_chart=rs.chart("A"), report_date="2026-10-17")
        restore = bound("love")
        try:
            codes = [v.code for v in tc.check_text(KARMIC_CARD, facts) if v.severity == "hard"]
        finally:
            restore()
        self.assertEqual(codes, ["biography"])

    def test_real_family_biography_in_karmic_is_repaired(self):
        victim = non_key_id("karmic")
        fixed = "Картата подсказва родов модел на строгост, който може да се свързва с търсенето на одобрение."
        script = Script({"*": KARMIC_CARD, victim: KARMIC_BIOGRAPHY}, repair={"Майка ви е била строга": fixed})
        text, checks = run_flow("karmic", script)
        self.assertEqual((quality(checks), script.calls["repair"]), ("full", 1))
        self.assertNotIn("Майка ви е била строга", text)


if __name__ == "__main__":
    unittest.main()
