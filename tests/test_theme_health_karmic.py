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
import copy
import os
import re
import sys
import unittest
from unittest import mock

import testenv  # noqa: F401  (трябва да е преди main)
from testenv import CHART  # noqa: E402

import ai_interpreter  # noqa: E402
import birthtime  # noqa: E402
import engine  # noqa: E402
import card_engine  # noqa: E402
import factpack  # noqa: E402
import generation  # noqa: E402
import main  # noqa: E402
import natal_pair  # noqa: E402
import period_cards  # noqa: E402
import period_report  # noqa: E402
import natal_single as nsingle  # noqa: E402
import regression_support as rs  # noqa: E402
import relationship  # noqa: E402
import report_snapshot  # noqa: E402
import safety  # noqa: E402
import text_check as tc  # noqa: E402
import theme_style  # noqa: E402
from schemas import ChartRequest  # noqa: E402
from test_period_cards import FlowBase  # noqa: E402

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "scripts"))
import verify_cards as vc  # noqa: E402
import verify_period as vp  # noqa: E402

OTHER_THEMES = ("general", "money")                      # теми без собствен стил: заявката е същата като преди
FRANK_THEMES = ("love", "career")                         # открит и пълен анализ (20.10.2026)
SIGNS_BG = ("Овен", "Телец", "Близнаци", "Рак", "Лъв", "Дева", "Везни", "Скорпион", "Стрелец", "Козирог", "Водолей", "Риби")
PLANETS_BG = ("Слънце", "Луна", "Меркурий", "Венера", "Марс", "Юпитер", "Сатурн", "Уран", "Нептун", "Плутон", "Хирон")
SIGNS_EN = ("Aries", "Taurus", "Gemini", "Cancer", "Leo", "Virgo", "Libra", "Scorpio", "Sagittarius", "Capricorn", "Aquarius", "Pisces")
PLANETS_EN = ("Sun", "Moon", "Mercury", "Venus", "Mars", "Jupiter", "Saturn", "Uranus", "Neptune", "Pluto", "Chiron")

# Конкретно, но като склонност: в тема „Здраве“ минава, в друга тема е „орган като известен проблем“
HEALTH_CARD = ("Стомахът и храносмилането са уязвима зона: възможни са киселини, подуване и нервен стомах при стрес. "
               "Редовният режим на хранене помага, а при повтарящи се оплаквания се консултирайте с лекар.")
HEALTH_GRAVE = "Има склонност към инфаркт при преумора, затова пазете сърцето си и правете редовни прегледи."
HEALTH_CERTAIN = "При преумора ще получите инфаркт, затова пазете сърцето си."
# Езикът на родовите модели: в тема „Карма и род“ минава, в друга тема е „реална семейна биография“
KARMIC_CARD = ("Тази позиция описва тих родов модел, който се предава през рода като памет за дълга и мълчанието. "
               "Душата ви идва да вземе урока за границите и да освободи старата тежест.")
KARMIC_BIOGRAPHY = "Майка ви е била строга и недостъпна, затова търсите одобрение."


def bound(theme):
    """Закача темата за блока, както го прави generation.run; връща функция за откачане."""
    token = theme_style.bind(theme)
    return lambda: theme_style.unbind(token)


class ThemeStyleModuleTest(unittest.TestCase):
    def test_only_health_karmic_love_and_career_have_a_style_block(self):
        for theme in ("health", "karmic", "love", "career"):
            self.assertTrue(theme_style.block(theme), theme)
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
        for needle in ("CONCRETE", "Do not water the topic down", "Start every card text from the body area", "never hidden behind", "tendency or potential",
                       "never as an illness the person has now", "never as a certain future illness",
                       "SERIOUS CONDITIONS ARE NAMED BY THEIR ORDINARY NAMES", "do not hide them behind soft euphemisms (complications, serious problems, strain on the system)", "They do NOT forbid naming a serious condition as an elevated risk or tendency of the chart", "a hard aspect (conjunction, square or opposition) that involves Saturn, Mars, Pluto, Uranus, Neptune or Chiron", "high blood pressure, heart attack, stroke", "diabetes, kidney disease", "cancer (a risk to follow with regular screening)", "none in a harmonious card (trine, sextile, a gift of the chart)", "name the main serious risks of the whole chart by those ordinary names", "the answer starts with those serious risks, named plainly", "never a diagnosis, never certain, never something the person has now", "never tied to a date as an event", "with no percentages", "no diagnoses",
                       "never the same sentence in several cards or in several monthly advices", "Never tell the person to wait or postpone", "no medicines or doses", "no stopping or delaying treatment", "no fear and no fatalism",
                       "adds no fact and removes no safety limit"):
            self.assertIn(needle, block, needle)

    def test_the_health_block_names_every_sign_and_planet_with_its_body_area(self):
        block = theme_style.HEALTH_BLOCK
        for name in SIGNS_EN + PLANETS_EN:
            self.assertIn(name, block, name)
        for name in SIGNS_BG + PLANETS_BG:          # 19.10.2026: кирилските имена в речника се преписваха в текста на картите (знак и дом във всяка карта)
            self.assertNotIn(name + " ", block.replace("Рак ", "рак "), name)
        for area in ("stomach", "heart", "intestines", "kidneys", "bones", "joints", "nerves", "liver", "skin", "lymph"):
            self.assertIn(area, block, area)

    def test_the_karmic_block_carries_the_leonis_lens_and_every_limit(self):
        block = theme_style.KARMIC_BLOCK
        for needle in ("Хули Леонис", "path of the soul", "Saturn is the teacher of karma", "true essence", "NAMING DISCIPLINE", "родова памет",
                       "родов модел", "ARCHETYPES", "lesson and a choice, never a punishment", "overrides the earlier wish for no mystic phrases",
                       "Vary the structure of the cards", "never with the same formula in several cards", "Start every card text from the lesson",
                       "adds no fact", "use only what the cards state", "Never describe what happened to real relatives",
                       "never name Хули Леонис, any other astrologer, author or book in the text",
                       "nothing concrete about real parents, ancestors, childhood, past lives or family history", "No curses",
                       "no fatalism", "free will always", "Spiritual work never replaces medical or psychological help",
                       "Do not use the word \"смърт\""):
            self.assertIn(needle, block, needle)

    def test_the_blocks_never_override_what_may_be_named_or_the_facts(self):
        for block in (theme_style.HEALTH_BLOCK, theme_style.KARMIC_BLOCK):
            self.assertIn("adds no fact", block)
        self.assertIn("Never print a sign, planet, house, angle or aspect name and never speak of the direction of planetary motion", theme_style.HEALTH_BLOCK)
        self.assertIn("never print a planet, sign, house, aspect or angle name and never speak of the direction of planetary motion", theme_style.KARMIC_BLOCK)

    def test_the_blocks_give_no_permission_to_name_planets(self):
        # 19.10.2026, живата проба: „само планетите на самата карта“ беше прочетено като разрешение и осем карти на наталното „Здраве“ бяха поправени
        for block in (theme_style.HEALTH_BLOCK, theme_style.KARMIC_BLOCK, theme_style.LOVE_BLOCK, theme_style.CAREER_BLOCK):
            self.assertNotIn("only the planets of that very card", block)
            self.assertNotIn("you may name", block)
        self.assertIn("rules above about the facts, about what may be named in each section", theme_style.KARMIC_BLOCK)

    def test_theme_focus_changed_only_for_the_four_themes(self):
        focus = ai_interpreter.THEME_FOCUS
        self.assertIn("name concretely the body areas", focus["health"])
        self.assertIn("never diagnoses", focus["health"])
        self.assertIn("Хули Леонис", focus["karmic"])
        self.assertIn("never facts about ancestors or parents", focus["karmic"])
        self.assertEqual(focus["general"], "General life overview: main patterns, tensions, resources and priorities.")
        self.assertEqual(focus["money"], "Money and success: resources, priorities, habits and the alternatives named in the question (no investment advice).")
        for theme in FRANK_THEMES:
            self.assertIn("told frankly and completely: real strengths", focus[theme], theme)
        self.assertIn("the concrete action asked about", focus["career"])
        self.assertIn("the exact context of the relationship", focus["love"])


class FormatKeptTest(FlowBase):
    """Живата проба (20.10.2026): „Здраве“ за 3 месеца се отказваше на етапа „формат на общия преглед“ — AI пишеше свои заглавия вместо таговете.
    Всеки стилов блок завършва с изискване за формата, а подканата на прегледа завършва с точния списък на таговете (втори опит — с напомняне)."""

    def test_every_style_block_ends_with_the_instruction_to_keep_the_format(self):
        for theme in ("health", "karmic", "love", "career"):
            block = theme_style.block(theme)
            self.assertTrue(block.endswith(theme_style.FORMAT_KEEP), theme)
        for needle in ("changes only the content, the tone and the completeness", "tagged sections", "each alone on its own line", "no other headings"):
            self.assertIn(needle, theme_style.FORMAT_KEEP, needle)

    def test_the_overview_prompt_ends_with_the_exact_tags_and_the_second_attempt_reminds(self):
        seen = []
        standard = self.ai()

        async def fake(system_prompt, user_prompt, max_tokens=None, temperature=None, **kwargs):
            if system_prompt.startswith("MODE: PERIOD OVERVIEW FROM VERIFIED BLOCKS"):
                seen.append(user_prompt)
                if len(seen) == 1:
                    return "ОБЩ ХАРАКТЕР НА ПЕРИОДА\n\nСвободен текст без тагове."
            return await standard(system_prompt, user_prompt, max_tokens, temperature, **kwargs)

        steps = self.run_report(fake)
        self.assertEqual(steps[-1]["type"], "finished")                                      # вторият опит се възстановява
        self.assertEqual(len(seen), 2)
        tags = "[[SUMMARY]], [[ADVICE]], [[CONCLUSION]]"
        self.assertTrue(seen[0].rstrip().endswith(f"Output ONLY the tagged sections {tags}, each tag alone on its own line, and no other headings or text."))
        self.assertNotIn("did not contain the required section tags", seen[0])
        self.assertTrue(seen[1].rstrip().endswith(f"Answer again with ONLY the tagged sections {tags}."))

    def test_the_answer_tag_is_in_the_list_when_there_is_a_question(self):
        seen = []
        standard = self.ai()

        async def fake(system_prompt, user_prompt, max_tokens=None, temperature=None, **kwargs):
            if system_prompt.startswith("MODE: PERIOD OVERVIEW FROM VERIFIED BLOCKS"):
                seen.append(user_prompt)
            return await standard(system_prompt, user_prompt, max_tokens, temperature, **kwargs)

        try:
            self.run_report(fake, question="Как да пазя здравето си през този период?")
        except period_report.ForecastGenerationError:
            pass                                                                             # стандартният отговор на подменения AI не покрива въпроса: тук се гледа само подканата
        self.assertIn("[[SUMMARY]], [[ADVICE]], [[ANSWER]], [[CONCLUSION]]", seen[0])


class FrankThemesTest(unittest.TestCase):
    """„Любов“ и „Кариера“ (20.10.2026): открит и пълен анализ вместо нежния; точността (факти, проверки, без измислици) остава."""

    def test_the_two_blocks_ask_for_the_plain_truth_and_keep_the_limits_that_protect_accuracy(self):
        for theme, block in (("love", theme_style.LOVE_BLOCK), ("career", theme_style.CAREER_BLOCK)):
            for needle in ("FRANK AND COMPLETE", "not a gentle one", "adds no fact and removes none of the limits that protect accuracy", "NAMING DISCIPLINE",
                           "never print a planet, sign, house, aspect or angle name", "Say it straight", "No euphemisms", "uncomfortable cards the same weight",
                           "Credibility", "present your interpretation as calculated fact", "give no percentages", "no dates", "never invent",
                           "closing blocks are complete and decisive", "starts with the answer and repeats the key words of the question (its topic and its verb)", "Start every card text from", "Do not give every card the same pattern",
                           "who tells the truth"):
                self.assertIn(needle, block, (theme, needle))
        for needle in ("jealousy and possessiveness", "never predictions of a breakup or a betrayal", "Never state another person's real motive",
                       "overall verdict (strong, mixed or difficult)", "the kind of relationship the user chose",
                       "the relationship rules above (the kind of relationship, minors) prevail over this block"):
            self.assertIn(needle, theme_style.LOVE_BLOCK, needle)
        for needle in ("procrastination, perfectionism, overwork", "no promises of a promotion, success or income", "an aspect alone never justifies an absolute order",
                       "never invent companies, positions or events", "overall assessment of the career potential"):
            self.assertIn(needle, theme_style.CAREER_BLOCK, needle)

    def test_the_repair_of_an_answer_that_ignores_the_question_names_the_words_to_reuse(self):
        # живата проба (20.10.2026): „Кариера“ за дата отказа, защото отговорът не повтаряше нито една основа от въпроса, а съветът казваше „със свои думи“
        check = card_engine.question_coverage("Какво да направя за кариерата си в този момент?")
        off = "Фокусирайте се върху стабилността и ясните приоритети в работата, като поемате отговорностите една по една и без излишна бързина. " * 2
        found = check(off)
        self.assertEqual([v.code for v in found], ["complete"])
        for word in ("направя", "кариерата", "момент"):
            self.assertIn(word, found[0].detail)
        self.assertNotIn("със свои думи", found[0].detail)
        self.assertEqual(check("В този момент " + off), [])
        self.assertEqual([v.code for v in check("кратко")], ["complete"])
        self.assertEqual(card_engine.question_coverage("")(off), [])             # без въпрос няма какво да се иска

    def test_the_two_rules_that_push_towards_comfort_are_replaced_and_every_other_rule_stays(self):
        old = safety.SAFETY_RULES.split("\n")
        for theme in FRANK_THEMES:
            new = safety.rules_for(theme).split("\n")
            self.assertEqual(len(old), len(new), theme)
            changed = [(a, b) for a, b in zip(old, new) if a != b]
            self.assertEqual(len(changed), 2, theme)
            self.assertTrue(changed[0][0].startswith("- Астрологията е инструмент за себепознание"), theme)
            self.assertEqual(changed[0][1], safety.FRANK_TENDENCY_RULE)
            self.assertTrue(changed[1][0].startswith("- Не плаши, не използвай фатализъм"), theme)
            self.assertEqual(changed[1][1], safety.FRANK_FEAR_RULE)
            text = safety.rules_for(theme)
            for needle in ("НИКОГА не предсказвай смърт, сериозна болест, инцидент, развод или загуба на близък като сигурно събитие и не посочвай дати за тях",
                           "НИКОГА не поставяй медицински диагнози", "НИКОГА не съветвай да се отложи консултация", "Не твърди със сигурност пол на бебе, бременност или изневяра",
                           "Не давай правни съвети", "НИКОГА не давай конкретни финансови", "Не давай проценти", "Не показвай вътрешни етикети", "112",
                           "Не твърди като факт нищо за родители"):
                self.assertIn(needle, text, (theme, needle))
            self.assertIn("без смекчаване", text)
            self.assertNotIn("Подчертавай свободната воля на човека", text)

    def test_the_frank_themes_get_their_block_and_rules_in_the_request(self):
        for theme, block in (("love", theme_style.LOVE_BLOCK), ("career", theme_style.CAREER_BLOCK)):
            prompt = PromptAssemblyTest().system_prompt(theme)
            self.assertIn(safety.rules_for(theme), prompt, theme)
            self.assertIn(block, prompt, theme)
            self.assertLess(prompt.index(safety.QUALITY_RULES), prompt.index(block), theme)
            self.assertNotIn(theme_style.HEALTH_BLOCK, prompt)

    def test_the_fingerprint_is_own_for_each_of_the_four_themes_and_old_for_the_others(self):
        import hashlib
        old = hashlib.sha256((safety.SAFETY_RULES + safety.QUALITY_RULES).encode("utf-8")).hexdigest()[:12]
        prints = {report_snapshot.prompt_fingerprint(t) for t in ("health", "karmic", "love", "career")}
        self.assertEqual(len(prints | {old}), 5)
        for theme in OTHER_THEMES + ("", None):
            self.assertEqual(report_snapshot.prompt_fingerprint(theme), old, theme)

    def test_the_documented_safety_and_accuracy_checks_are_not_weakened(self):
        # заключителният филтър за диагноза и смърт и проверката за отлагане на лекар важат и в тези теми
        restore = bound("love")
        try:
            text, flags = safety.check_output("<p>Той ще почине през март.</p><p>Спокоен текст.</p>", "love")
        finally:
            restore()
        self.assertIn("death_prediction", flags)
        self.assertNotIn("почине", text)
        self.assertEqual([c for c, _ in safety.health_findings("Отложете прегледа при лекар до 14 ноември.", "career")], ["health_delay"])


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
        for needle in ("не като диагноза", "не като болест, която човекът има сега", "не като сигурна бъдеща болест", "включително тежки (например рак, инфаркт, инсулт, диабет)",
                       "повишен риск", "винаги когато картата показва напрежение в съответната система", "не ги заобикаляй с общи думи", "без дати за болест или травма", "без лекарства", "кога да се потърси лекар"):
            self.assertIn(needle, safety.HEALTH_RULE, needle)
        for needle in ("символ и тълкуване на картата, с различни думи във всяка карта", "реални родители, предци, детство, минали животи или семейна история",
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

    def test_the_prompt_fingerprint_changes_only_for_the_four_themes(self):
        import hashlib
        old = hashlib.sha256((safety.SAFETY_RULES + safety.QUALITY_RULES).encode("utf-8")).hexdigest()[:12]
        for theme in OTHER_THEMES + ("", None):
            self.assertEqual(report_snapshot.prompt_fingerprint(theme), old, theme)
        self.assertEqual(report_snapshot.prompt_fingerprint(), old)
        fingerprints = {old, report_snapshot.prompt_fingerprint("health"), report_snapshot.prompt_fingerprint("karmic"),
                        report_snapshot.prompt_fingerprint("love"), report_snapshot.prompt_fingerprint("career")}
        self.assertEqual(len(fingerprints), 5)
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

    def test_a_serious_condition_named_as_a_risk_is_allowed_since_20_10_2026(self):
        # по искане на собственика (20.10.2026): рак, инфаркт, инсулт, диабет се назовават, когато има вероятност; като риск, не като диагноза или сигурност
        for sentence in ("Има склонност към инфаркт при преумора, затова са важни редовните прегледи на сърцето.", "Има повишен риск от рак на стомаха, който се проверява навреме.",
                         "Възможен е диабет при неправилно хранене, затова следете кръвната захар.", "Картата подсказва риск от инсулт при високо налягане.",
                         "При дългогодишен стрес рискът от инфаркт расте, затова пазете сърцето си."):
            self.assertEqual(safety.health_findings(sentence, "health"), [], sentence)

    def test_a_certain_or_present_serious_illness_is_a_violation(self):
        for sentence in ("Ще получите инфаркт.", "Ще развиете диабет.", "Имате диабет възпаление на панкреаса.", "Страдате от инсулт.", "Ще получите рак на стомаха.",
                         "Ще имате тумор на щитовидната жлеза.", "Имате рак на стомаха.", "Страдате от рак."):
            self.assertEqual([code for code, _ in safety.health_findings(sentence, "health")], ["health_certain"], sentence)

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
            self.assertEqual(health_details(HEALTH_GRAVE), [])                                       # тежко заболяване като риск: не е нарушение
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

    def test_the_checker_passes_the_users_question_to_the_name_check(self):
        # името, което потребителят сам е написал във въпроса, не е нарушение; без него в въпроса е нарушение (през целия check_text)
        for question, expected in (("Искам тълкуване по системата на Хули Леонис.", 0), ("Как да поставям граници?", 1)):
            facts = tc.build_facts(mode="natal", user_name=rs.A_NAME, natal_chart=rs.chart("A"), report_date="2026-10-17", question=question)
            restore = bound("karmic")
            try:
                found = [v.code for v in tc.check_text(self.NAMED[0], facts)]
            finally:
                restore()
            self.assertEqual(found.count("attribution"), expected, question)

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
        for theme, block in (("health", theme_style.HEALTH_BLOCK), ("karmic", theme_style.KARMIC_BLOCK), ("love", theme_style.LOVE_BLOCK),
                             ("career", theme_style.CAREER_BLOCK), ("general", ""), ("money", "")):
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

    def test_a_serious_condition_as_a_risk_passes_and_a_certain_one_is_repaired(self):
        script = Script({"*": HEALTH_CARD, non_key_id("health"): HEALTH_GRAVE})
        text, checks = run_flow("health", script)
        self.assertEqual((quality(checks), script.calls["repair"]), ("full", 0))
        self.assertIn("склонност към инфаркт", text)
        victim = non_key_id("health")
        script = Script({"*": HEALTH_CARD, victim: HEALTH_CERTAIN}, repair={"ще получите инфаркт": HEALTH_GRAVE})
        text, checks = run_flow("health", script)
        self.assertEqual((quality(checks), script.calls["repair"]), ("full", 1))
        self.assertNotIn("ще получите инфаркт", text)

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


class KarmicRetrogradeTest(unittest.TestCase):
    """Ретроградните планети в заглавията на наталните карти на „Карма и род“ (20.10.2026, по искане на собственика): кармичен показател в системата
    на Хули Леонис. Маркерът е факт от кода: само в „Карма и род“, само за ретроградни тела по скоростта в картата, със сверка в проверката и в сверките."""
    PEOPLE = (("A", lambda: rs.chart("A"), rs.A_NAME), ("Ivan", lambda: rs.chart("Ivan"), rs.IVAN), ("child", lambda: rs.child(), "Мария"))

    def test_the_word_and_its_gender(self):
        heading = natal_pair.placement_heading
        self.assertEqual(heading("Venus", "Иван", "Libra", "4°43'", 2, True), "Венера на Иван е във Везни (4°43'), във 2-ри дом, ретроградна.")
        self.assertEqual(heading("Saturn", "Иван", "Scorpio", "21°36'", 11, True), "Сатурн на Иван е в Скорпион (21°36'), в 11-ти дом, ретрограден.")
        self.assertEqual(heading("Mars", "Иван", "Aries", "1°14'", None, True), "Марс на Иван е в Овен (1°14'), ретрограден.")
        self.assertEqual(heading("Saturn", "Иван", "Scorpio", "21°36'", 11), "Сатурн на Иван е в Скорпион (21°36'), в 11-ти дом.")

    def test_only_karma_marks_and_exactly_the_retrograde_bodies(self):
        marked = 0
        for label, chart, name in ((l, c(), n) for l, c, n in self.PEOPLE):
            for theme in ("general", "love", "career", "money", "health", "karmic"):
                for card in nsingle.build_cards(context(theme, chart, name)):
                    if card.row["kind"] != "placement":
                        continue
                    planet = card.row["planet"]
                    expected = theme == "karmic" and planet in factpack.RETROGRADE_BODIES and factpack.is_retrograde(chart["planets"][planet])
                    self.assertEqual(card.row["retrograde"], expected, (label, theme, planet))
                    self.assertEqual("ретроград" in card.heading, expected, (label, theme, planet, card.heading))
                    marked += expected
        self.assertGreaterEqual(marked, 3)                                                             # тестът не е празен: има реални ретроградни планети в избраните карти

    def test_the_node_and_the_luminaries_are_never_marked(self):
        for _label, chart, name in ((l, c(), n) for l, c, n in self.PEOPLE):
            for card in nsingle.build_cards(context("karmic", chart, name)):
                if card.row["kind"] == "placement" and card.row["planet"] in ("Node", "Sun", "Moon"):
                    self.assertFalse(card.row["retrograde"])
                    self.assertNotIn("ретроград", card.heading)

    def test_a_karmic_pair_marks_both_people_and_a_love_pair_marks_nobody(self):
        ca, cb = rs.chart("A"), rs.chart("Ivan")
        for theme, anybody in (("karmic", True), ("love", False)):
            ctx = card_engine.Context(natal_chart=ca, partner_chart=cb, transit_chart={}, user_name=rs.A_NAME, partner_name=rs.IVAN, first="А", second="Б",
                                      question="", target_date="", theme="x", utc_today="2026-10-20", report_type=theme, relation="")
            cards = [c for c in natal_pair.build_cards(ctx) if c.row.get("kind") == "placement"]
            for card in cards:
                chart = ca if card.row["owner"] == "user" else cb
                expected = theme == "karmic" and card.row["planet"] in factpack.RETROGRADE_BODIES and factpack.is_retrograde(chart["planets"][card.row["planet"]])
                self.assertEqual("ретроград" in card.heading, expected, (theme, card.row))
            self.assertEqual(any("ретроград" in c.heading for c in cards), anybody, theme)

    def test_without_a_birth_time_a_planet_that_changes_direction_during_the_day_is_not_marked(self):
        chart = rs.unknown_ivan()
        self.assertIn("Saturn", {c.row["planet"] for c in nsingle.build_cards(context("karmic", chart, rs.IVAN)) if c.row.get("retrograde")})
        changed = copy.deepcopy(chart)
        changed["motion_changes"] = ["Saturn"]
        cards = nsingle.build_cards(context("karmic", changed, rs.IVAN))
        self.assertTrue([c for c in cards if c.row["kind"] == "placement" and c.row["planet"] == "Saturn"])
        self.assertFalse([c for c in cards if c.row.get("retrograde")])                                # Сатурн е в картата, но без твърдение за посока
        known = copy.deepcopy(rs.chart("Ivan"))
        known["motion_changes"] = ["Saturn"]                                                           # с известен час полето не променя нищо
        self.assertTrue([c for c in nsingle.build_cards(context("karmic", known, rs.IVAN)) if c.row.get("retrograde")])

    def test_the_engine_lists_the_planets_that_change_direction_on_a_day_without_a_time(self):
        eng = engine.get_engine()
        station = eng.calculate_chart("2026-10-24", "12:00", 42.6977, 23.3219, time_known=False)       # Меркурий става ретрограден на 24.10.2026 в 10:13 (по Софийско време)
        self.assertIn("Mercury", station["motion_changes"])
        quiet = eng.calculate_chart("2026-10-10", "12:00", 42.6977, 23.3219, time_known=False)
        self.assertNotIn("Mercury", quiet["motion_changes"])
        self.assertNotIn("motion_changes", eng.calculate_chart("2026-10-24", "12:00", 42.6977, 23.3219))   # с час посоката е ясна: полето е само за картите без час

    def test_the_checker_accepts_the_headings_and_a_false_retrograde_statement_is_a_violation(self):
        for _label, chart, name in ((l, c(), n) for l, c, n in self.PEOPLE):
            facts = tc.build_facts(mode="natal", user_name=name, natal_chart=chart, report_date="2026-10-20")
            headings = "\n".join(c.heading for c in nsingle.build_cards(context("karmic", chart, name)))
            self.assertEqual([(v.code, v.detail) for v in tc.check_text(headings, facts) if v.severity == "hard"], [])
        facts = tc.build_facts(mode="natal", user_name=rs.A_NAME, natal_chart=rs.chart("A"), report_date="2026-10-20")
        retro = [p for p in factpack.RETROGRADE_BODIES if factpack.is_retrograde(rs.chart("A")["planets"][p])]
        direct = [p for p in factpack.RETROGRADE_BODIES if p not in retro]
        self.assertTrue(retro and direct)
        say = {"Saturn": "Сатурн", "Chiron": "Хирон", "Jupiter": "Юпитер", "Mars": "Марс", "Mercury": "Меркурий", "Venus": "Венера", "Uranus": "Уран",
               "Neptune": "Нептун", "Pluto": "Плутон"}
        ok = [v.code for v in tc.check_text(f"{say[retro[0]]} е ретрограден в картата на {rs.A_NAME}.", facts) if v.severity == "hard"]
        wrong = [v.code for v in tc.check_text(f"{say[direct[0]]} е ретрограден в картата на {rs.A_NAME}.", facts) if v.severity == "hard"]
        self.assertEqual((ok, wrong), ([], ["event"]))

    def test_the_independent_verifier_checks_the_marker_in_both_directions_and_the_gender(self):
        text, people, names = vc.offline_text("natal_single", ("A",), None, ("karmic", "", ""))
        for person, name in zip(people, names):
            person["name"] = name
        problems, counts = vc.verify(text, people, None, "natal_single", theme="karmic")
        self.assertEqual(problems, [])
        self.assertGreaterEqual(counts["retrograde"], 1)                                                # Хирон е ретрограден в картата на „Тест А — одит“
        chiron = next(line for line in text.splitlines() if line.startswith("### Хирон на ") and "ретрограден" in line)
        # липсва маркерът на ретрограден планета (само при тема „karmic“)
        problems, _ = vc.verify(text.replace(chiron, chiron.replace(", ретрограден.", ".")), people, None, "natal_single", theme="karmic")
        self.assertTrue([p for p in problems if "липсва ретроградност" in p])
        problems, _ = vc.verify(text.replace(chiron, chiron.replace(", ретрограден.", ".")), people, None, "natal_single")      # без тема не се иска маркер
        self.assertEqual(problems, [])
        # маркер на планета, която не е ретроградна
        saturn = next(line for line in text.splitlines() if line.startswith("### Сатурн на ") and "ретрограден" not in line)
        problems, _ = vc.verify(text.replace(saturn, saturn[:-1] + ", ретрограден."), people, None, "natal_single", theme="karmic")
        self.assertTrue([p for p in problems if "планетата не е ретроградна" in p])
        # неправилен род на думата
        problems, _ = vc.verify(text.replace(chiron, chiron.replace(", ретрограден.", ", ретроградна.")), people, None, "natal_single", theme="karmic")
        self.assertTrue([p for p in problems if "родът на думата" in p])

    def test_the_block_teaches_the_retrograde_lens_and_its_limits(self):
        block = theme_style.KARMIC_BLOCK
        for needle in ("Retrograde planets are one of the important karmic indicators", "\"ретрограден\" or \"ретроградна\"", "an energy turned inward",
                       "like a prisoner who is also his own guard", "without naming the planet", "Never say that a planet is retrograde when its heading does not",
                       "never state how many planets are retrograde"):
            self.assertIn(needle, block, needle)


class ClosingNotesUnitTest(unittest.TestCase):
    """Бележките „Застраховка“ (20.10.2026): в отчет от много части стоят веднъж, накрая; в единичен анализ — както досега."""

    def test_a_single_analysis_is_closed_exactly_as_before(self):
        text, flags = safety.check_output("<p>Текст.</p>", "health")
        self.assertEqual(text, "<p>Текст.</p>\n" + safety.HEALTH_NOTE_HTML + "\n" + safety.DISCLAIMER_HTML)
        self.assertEqual(safety.check_output("<p>Текст.</p>", "money")[0], "<p>Текст.</p>\n" + safety.DISCLAIMER_HTML)
        self.assertEqual(safety.check_output("<p>Текст.</p>", "general")[0], "<p>Текст.</p>")

    def test_a_part_of_a_longer_report_gets_no_notes_and_the_last_one_gets_them_once(self):
        part, flags = safety.check_output("<p>Текст.</p>", "health", notes=False)
        self.assertEqual((part, flags), ("<p>Текст.</p>", []))
        end = safety.add_closing_notes(part, "health")
        self.assertEqual(end, "<p>Текст.</p>\n" + safety.HEALTH_NOTE_HTML + "\n" + safety.DISCLAIMER_HTML)
        self.assertEqual(safety.add_closing_notes("<p>Текст.</p>", "money"), "<p>Текст.</p>\n" + safety.DISCLAIMER_HTML)
        self.assertEqual(safety.add_closing_notes("<p>Текст.</p>", "general"), "<p>Текст.</p>")

    def test_a_disclaimer_caused_by_a_flag_is_not_doubled_and_the_order_stays(self):
        flagged, flags = safety.check_output("<p>Страдате от диабет.</p><p>Друго.</p>", "health", notes=False)
        self.assertIn("diagnosis", flags)
        self.assertEqual(flagged.count("astro-disclaimer"), 1)
        end = safety.add_closing_notes(flagged, "health")
        self.assertEqual((end.count(safety.HEALTH_NOTE_HTML), end.count(safety.DISCLAIMER_HTML)), (1, 1))
        self.assertLess(end.index(safety.HEALTH_NOTE_HTML), end.index(safety.DISCLAIMER_HTML))
        self.assertTrue(end.endswith(safety.DISCLAIMER_HTML))
        general = safety.add_closing_notes(safety.check_output("<p>Страдате от диабет.</p>", "general", notes=False)[0], "general")
        self.assertEqual(general.count(safety.DISCLAIMER_HTML), 1)                     # без тема за бележки: само уговорката заради флага


class PeriodNotesOnceTest(FlowBase):
    """Периодният отчет за „Здраве“ и „Пари“ носи бележките веднъж, накрая (преди: след прегледа и след всеки месец)."""

    def run_theme(self, report_type):
        kwargs = dict(calendar=rs.calendar(False, "2026-12-15"), natal_chart=rs.chart("Ivan"), partner_chart=None, report_type=report_type, user_name=rs.IVAN,
                      partner_name=None, question="", report_date="2026-10-11", retry_pause=0, relation="")

        async def go():
            return [step async for step in period_cards.run_period_report(self.interp, **kwargs)]
        restore = bound(report_type)
        try:
            with mock.patch.object(self.interp, "_call_api", self.ai()):
                return asyncio.run(go())
        finally:
            restore()

    def test_the_two_health_notes_stand_once_at_the_very_end(self):
        steps = self.run_theme("health")
        final = steps[-1]
        self.assertEqual(final["type"], "finished")
        self.assertGreaterEqual(len(final["month_texts"]), 2)
        self.assertNotIn("astro-disclaimer", final["overview"])
        for _title, text in final["month_texts"][:-1]:
            self.assertNotIn("astro-disclaimer", text)
        last = final["month_texts"][-1][1]
        self.assertEqual((last.count(safety.HEALTH_NOTE_HTML), last.count(safety.DISCLAIMER_HTML)), (1, 1))
        self.assertTrue(last.endswith(safety.DISCLAIMER_HTML))
        saved = period_report.saved_content(final["overview"], final["month_texts"])
        self.assertEqual(saved.count("astro-disclaimer"), 2)
        self.assertTrue(saved.rstrip().endswith(safety.DISCLAIMER_HTML))
        streamed = [step for step in steps if step["type"] == "month_complete"]
        self.assertEqual([("astro-disclaimer" in step["text"]) for step in streamed], [False] * (len(streamed) - 1) + [True])
        self.assertNotIn("astro-disclaimer", next(step for step in steps if step["type"] == "overview_complete")["text"])

    def test_money_gets_its_one_disclaimer_at_the_end_and_the_other_themes_none(self):
        final = self.run_theme("money")[-1]
        saved = period_report.saved_content(final["overview"], final["month_texts"])
        self.assertEqual((saved.count(safety.DISCLAIMER_HTML), saved.count(safety.HEALTH_NOTE_HTML)), (1, 0))
        self.assertTrue(saved.rstrip().endswith(safety.DISCLAIMER_HTML))
        for theme in ("general", "career", "love", "karmic"):
            saved = period_report.saved_content(*(lambda f: (f["overview"], f["month_texts"]))(self.run_theme(theme)[-1]))
            self.assertNotIn("astro-disclaimer", saved, theme)


class LivePhase31TextsTest(unittest.TestCase):
    """Четири реални отчета с кода на Фаза 31 (19.10.2026, продукция, реален AI, безплатна бета), записани точно както са доставени върху екрана веднага
    след генерацията: натален и период за „Здраве“ и за „Карма и род“ за профила „Тест А — одит“. Всички са „пълни“ (без резервен и частичен анализ);
    независимите сверки ги четат без несъответствие. Не е пробван на живо анализ за двама по тези две теми."""
    BASE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "fixtures")
    NAMES = ("health_natal_A", "karmic_natal_A", "health_period_A", "karmic_period_A")

    @classmethod
    def setUpClass(cls):
        _text, cls.period_people = vp.offline_text(("A",), "2026-10-19", "2026-11-30")

    def load(self, name):
        with open(os.path.join(self.BASE, f"live_2026_10_19_phase31_{name}.txt"), encoding="utf-8") as f:
            return f.read()

    def people(self, keys):
        people = vc.build_people(keys)
        for person, key in zip(people, keys):
            person["name"] = re.sub(r"\s*[—-]\s*", " ", vc.PEOPLE[key]["name"]).strip()
        return people

    def test_every_text_is_full_in_bulgarian_and_names_no_astrologer(self):
        facts = rs.facts("period", True)
        for name in self.NAMES:
            text = self.load(name)
            self.assertNotIn("Резервен анализ", text, name)
            self.assertNotIn("Частичен анализ", text, name)
            self.assertEqual([v.detail for v in tc.check_language(text, facts)], [], name)
            self.assertEqual(safety.attribution_findings(text), [], name)
            self.assertNotIn("смърт", text.lower(), name)

    def test_the_health_texts_name_the_weak_points_and_keep_the_limits_and_the_insurance_texts(self):
        for name in ("health_natal_A", "health_period_A"):
            text = self.load(name)
            self.assertEqual(safety.health_findings(text, "health"), [], name)                         # без сигурна болест и без тежко заболяване
            self.assertIn(safety.HEALTH_NOTE_HTML, text, name)
            self.assertIn(safety.DISCLAIMER_HTML, text, name)
            low = text.lower()
            for area in ("стомах", "стави", "кожа", "сън"):
                self.assertIn(area, low, (name, area))                                                 # конкретни зони, не „символична тема“
        # дума за отлагане на лекар заради периода няма; в периода е изрично казано обратното
        self.assertIn("без да ги отлагате заради периода", self.load("health_period_A"))

    def test_the_karmic_texts_speak_the_lens_without_facts_about_real_relatives(self):
        for name in ("karmic_natal_A", "karmic_period_A"):
            text = self.load(name)
            self.assertEqual(safety.biography_findings(text, "karmic"), [], name)
            low = text.lower()
            for word in ("родов", "душата", "урок"):
                self.assertIn(word, low, (name, word))
        natal = self.load("karmic_natal_A")
        self.assertIn("Възходящият възел на Тест А одит е във Водолей", natal)                         # възелът е сред картите (преди двойната тежест го нямаше)
        self.assertIn("Сатурн на Тест А одит е в Козирог", natal)
        self.assertIn("Хирон на Тест А одит е в Рак", natal)

    def test_the_independent_checks_accept_the_four_texts(self):
        for name in ("health_natal_A", "karmic_natal_A"):
            problems, counts = vc.verify(self.load(name), self.people(("A",)), None, "natal_single")
            self.assertEqual(problems, [], name)
            self.assertEqual((counts["placement"], counts["aspect"], counts["interpretations"], counts["closing"]), (8, 4, 12, 4), name)
        for name in ("health_period_A", "karmic_period_A"):
            problems, counts = vp.verify_period(self.load(name), self.period_people, "2026-10-19", "2026-11-30")
            self.assertEqual(problems, [], name)
            self.assertEqual((counts["cards"], counts["ingress"], counts["lunation"], counts["closing"]), (14, 3, 3, 7), name)


class LivePhase32TextsTest(unittest.TestCase):
    """Пет реални отчета с кода на 12a3052 (20.10.2026, продукция, реален AI, безплатна бета), записани точно както са доставени: „Здраве“ за три месеца
    (20.10–31.12), „Карма и род“ натален и за двама (с ретроградните планети в заглавията), „Любов“ за двама и „Кариера“ натален за профила „Тест А — одит“
    (и „Иван“). Независимите сверки ги четат без несъответствие, включително ретроградността, смятана по две позиции от ефемеридите."""
    BASE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "fixtures")
    PERIOD = ("2026-10-20", "2026-12-31")

    def load(self, name):
        with open(os.path.join(self.BASE, f"live_2026_10_20_phase32_{name}.txt"), encoding="utf-8") as f:
            return f.read()

    def people(self, keys):
        people = vc.build_people(keys)
        for person, key in zip(people, keys):
            person["name"] = re.sub(r"\s*[—-]\s*", " ", vc.PEOPLE[key]["name"]).strip()
        return people

    def test_the_three_month_health_period_is_complete_with_the_notes_once_and_last(self):
        text = self.load("health_period_A")
        self.assertNotIn("Резервен анализ", text)
        self.assertNotIn("[[", text)
        for section in ("### Най-силни моменти", "### Обща картина", "### Съгласуван план", "### Отговор на вашия въпрос", "### Заключение"):
            self.assertIn(section, text, section)
        self.assertEqual([m for m in ("Октомври", "Ноември", "Декември") if f"## 📅 {m} 2026" in text], ["Октомври", "Ноември", "Декември"])
        self.assertEqual(text.count('class="astro-disclaimer"'), 2)
        self.assertEqual(text.count(safety.HEALTH_NOTE_HTML), 1)
        self.assertEqual(text.count(safety.DISCLAIMER_HTML), 1)
        self.assertTrue(text.rstrip().endswith(safety.HEALTH_NOTE_HTML + "\n" + safety.DISCLAIMER_HTML))
        self.assertEqual(safety.health_findings(text, "health"), [])
        _text, period_people = vp.offline_text(("A",), *self.PERIOD)
        problems, counts = vp.verify_period(text, period_people, *self.PERIOD)
        self.assertEqual(problems, [])
        self.assertEqual((counts["cards"], counts["ingress"], counts["lunation"], counts["station"], counts["closing"]), (22, 5, 5, 4, 8))

    def test_the_karmic_headings_carry_the_retrograde_planets_and_the_independent_check_agrees(self):
        natal = self.load("karmic_natal_A")
        self.assertIn("Хирон на Тест А одит е в Рак (11°08'), в 1-ви дом, ретрограден.", natal)
        problems, counts = vc.verify(natal, self.people(("A",)), None, "natal_single", theme="karmic")
        self.assertEqual(problems, [])
        self.assertEqual((counts["placement"], counts["aspect"], counts["interpretations"], counts["closing"], counts["retrograde"]), (8, 4, 12, 4, 1))
        pair = self.load("karmic_pair_A_IVAN")
        self.assertIn("Сатурн на Иван е в Скорпион (21°36'), в 11-ти дом, ретрограден.", pair)
        problems, counts = vc.verify(pair, self.people(("A", "IVAN")), None, "natal_pair", theme="karmic")
        self.assertEqual(problems, [])
        self.assertEqual((counts["placement"], counts["aspect"], counts["overlay"], counts["interpretations"], counts["closing"], counts["retrograde"]), (6, 9, 6, 21, 4, 1))
        for text in (natal, pair):
            self.assertEqual(safety.attribution_findings(text), [])
            self.assertEqual(safety.biography_findings(text, "karmic"), [])
            self.assertNotIn("ретроград", text.split("\n## Обща картина")[0].replace("ретрограден.", "").replace("ретроградна.", ""))     # думата стои само в заглавията

    def test_the_verifier_rejects_a_missing_a_wrong_and_a_misgendered_marker_on_the_real_texts(self):
        natal = self.load("karmic_natal_A")
        marked = "Хирон на Тест А одит е в Рак (11°08'), в 1-ви дом, ретрограден."
        unmarked = marked.replace(", ретрограден.", ".")
        problems, _ = vc.verify(natal.replace(marked, unmarked), self.people(("A",)), None, "natal_single", theme="karmic")
        self.assertTrue(any("липсва ретроградност" in p for p in problems), problems)
        wrong = natal.replace("Сатурн на Тест А одит е в Козирог (20°47'), в 8-ми дом.", "Сатурн на Тест А одит е в Козирог (20°47'), в 8-ми дом, ретрограден.")
        problems, _ = vc.verify(wrong, self.people(("A",)), None, "natal_single", theme="karmic")
        self.assertTrue(any("планетата не е ретроградна" in p for p in problems), problems)
        gender = natal.replace(marked, marked.replace("ретрограден.", "ретроградна."))
        problems, _ = vc.verify(gender, self.people(("A",)), None, "natal_single", theme="karmic")
        self.assertTrue(any("родът на думата" in p for p in problems), problems)

    def test_the_love_and_career_texts_pass_the_independent_fact_checks(self):
        problems, counts = vc.verify(self.load("love_pair_A_IVAN"), self.people(("A", "IVAN")), None, "natal_pair")
        self.assertEqual(problems, [])
        self.assertEqual((counts["placement"], counts["aspect"], counts["overlay"], counts["interpretations"], counts["closing"]), (6, 9, 6, 21, 4))
        problems, counts = vc.verify(self.load("career_natal_A"), self.people(("A",)), None, "natal_single")
        self.assertEqual(problems, [])
        self.assertEqual((counts["placement"], counts["aspect"], counts["interpretations"], counts["closing"]), (8, 4, 12, 4))
        for name in ("love_pair_A_IVAN", "career_natal_A"):
            text = self.load(name)
            self.assertEqual(safety.attribution_findings(text), [], name)
            self.assertNotIn("ретроград", text, name)                     # маркерът е само за „Карма и род“


if __name__ == "__main__":
    unittest.main()
