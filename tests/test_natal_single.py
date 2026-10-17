"""
Натален анализ за един човек (natal_single.py): същата машина като при другите режими (card_engine.py), с карти за позициите, ъглите
(само при известен час) и вътрешните аспекти. Подборът следва темата и въпроса, а не само орбиса. AI е подменен със сценарий; картите,
проверката, поправките, възстановяването, резервният и частичният анализ и таксуването са истинският код.
"""
import asyncio
import os
import re
import unittest
from unittest import mock

import testenv  # noqa: F401  (трябва да е преди модулите)
from fastapi.testclient import TestClient  # noqa: E402

import ai_interpreter  # noqa: E402
import billing  # noqa: E402
import card_engine  # noqa: E402
import factpack  # noqa: E402
import main  # noqa: E402
import natal_single as nsingle  # noqa: E402
from pair_snapshot import _ORDINAL  # noqa: E402
import regression_support as rs  # noqa: E402
import text_check as tc  # noqa: E402
import text_guard  # noqa: E402
from database import SessionLocal, User  # noqa: E402
from rate_limit import limiter  # noqa: E402
from testenv import register_and_login  # noqa: E402

GOOD = "Така у вас се съчетават търпение и вътрешна сила, които помагат да вървите стъпка по стъпка."
WRONG = "Това усилва близостта в 7-ми дом и носи топлина във вашия живот."                   # дом в блок на карта: нарушение
ANGLE = "Така първото впечатление, което създавате, е спокойно, и Асцендентът ви помага да печелите доверие."   # ъгъл в блок на карта
NAMING = "Венера е в 7-ми дом, затова любовта ви е топла и спокойна."                         # планета и дом в затварящ блок
NAMING_ANGLE = "Асцендентът ви прави общителни и отворени към хората, затова ви е лесно да започвате разговор."
SUMMARY = "Вие сте човек, който съчетава спокойствие и упоритост: когато не бързате, постигате много и се чувствате сигурни."
ADVICE = "- Давайте си време за важните решения.\n- Говорете открито за очакванията си."
ANSWER = ("Най-добре е да подхождате към парите с търпение: първо подредете какво имате и какво ви трябва, после вземайте решения на малки стъпки. "
          "Избягвайте бързите разходи под напрежение и оставяйте място за резерв. Така парите ще бъдат ваш съюзник, а не източник на тревога.")
CONCLUSION = "Имате добра основа за стабилност и растеж и ще постигате най-много, когато вървите бавно и последователно."
QUESTION = "Как да се справя с парите и разходите?"
FIRST = factpack.display_name(rs.A_NAME, factpack.FIRST_PERSON_DEFAULT)


def context(report_type="money", question="", natal=None, name=rs.A_NAME):
    return card_engine.Context(
        natal_chart=natal or rs.chart("A"), partner_chart=None, transit_chart={}, user_name=name, partner_name=None,
        first=factpack.display_name(name, factpack.FIRST_PERSON_DEFAULT), second="", question=question, target_date="", theme="x",
        utc_today="2026-10-17", report_type=report_type)


def cards(**kwargs):
    return nsingle.build_cards(context(**kwargs))


class Script:
    def __init__(self, card_text=None, composition=None, repair=None, compose=None, restore=None):
        self.card_text, self.repair, self.restore = card_text or {}, repair or {}, restore or {}
        self.composition = composition or {"SUMMARY": SUMMARY, "ADVICE": ADVICE, "ANSWER": ANSWER, "CONCLUSION": CONCLUSION}
        self.compose_blocks = compose
        self.args = {}                                  # question, report_type, natal: както ги получава анализът
        self.calls = {"main": 0, "compose": 0, "repair": 0, "restore": 0}
        self.prompts, self.systems = [], []

    def tagged(self, with_cards, blocks):
        parts = [f"[[{c.id}]]\n{self.card_text.get(c.id, GOOD)}" for c in (cards(**self.args) if with_cards else [])]
        return "\n\n".join(parts + [f"[[{k}]]\n{v}" for k, v in blocks.items() if v])

    async def __call__(self, system_prompt, user_prompt, max_tokens, **kwargs):
        if "COMPOSITION FROM VERIFIED BLOCKS" in system_prompt:
            self.calls["compose"] += 1
            self.prompts.append(user_prompt)
            return self.tagged(False, self.compose_blocks or self.composition)
        if "MODE: ONE-PERSON NATAL ANALYSIS. You INTERPRET" in system_prompt:
            self.calls["main"] += 1
            self.systems.append(system_prompt)
            return self.tagged(True, self.composition)
        if "You write ONE short interpretation" in system_prompt:
            self.calls["restore"] += 1
            self.systems.append(system_prompt)
            return self.restore.get(re.search(r"CARD ([A-Z]\d+):", user_prompt).group(1), GOOD)
        self.calls["repair"] += 1
        for key, fixed in self.repair.items():
            if key in user_prompt:
                return fixed
        return re.search(r"```(.*?)```", user_prompt, re.S).group(1) if "```" in user_prompt else user_prompt


def run(script, question="", report_type="money", natal=None, name=rs.A_NAME):
    interp = ai_interpreter.AIInterpreter(api_key="test-dummy")
    interp._call_api = script
    script.args = {"question": question, "report_type": report_type, "natal": natal, "name": name}
    checks = []
    text = asyncio.run(nsingle.interpret(interp, natal_chart=natal or rs.chart("A"), user_name=name, question=question, report_type=report_type,
                                         checks=checks, theme="General.", utc_today="2026-10-17"))
    return text, checks


def quality(checks):
    return next((c["quality"] for c in checks if c.get("quality")), None)


def key_ids(**kwargs):
    built = cards(**kwargs)
    card_engine.mark_key_cards(built, nsingle.MODE)
    return {c.id for c in built if c.key}


def non_key(**kwargs):
    keys = key_ids(**kwargs)
    return [c for c in cards(**kwargs) if c.id not in keys]


class SelectionTest(unittest.TestCase):
    def headings(self, **kwargs):
        return {c.heading for c in cards(**kwargs)}

    def test_one_group_with_positions_angles_and_aspects_all_about_the_same_person(self):
        built = cards()
        self.assertEqual({c.group for c in built}, {"A"})
        self.assertEqual([c.id for c in built], [f"A{i}" for i in range(1, len(built) + 1)])
        kinds = [c.row["kind"] for c in built]
        self.assertEqual(sorted(set(kinds)), ["angle", "natal", "placement"])
        self.assertEqual(kinds, sorted(kinds, key={"placement": 0, "angle": 1, "natal": 2}.get))        # редът на четене: позиции, ъгли, аспекти
        for card in built:
            self.assertIn(f"на {FIRST}", card.heading)
        self.assertEqual({c.row["planet"] for c in built if c.row["kind"] == "angle"}, {"ASC", "MC"})

    def test_a_placement_card_matches_the_chart_and_the_angle_card_matches_the_angles(self):
        chart = rs.chart("A")
        for card in cards():
            row = card.row
            if row["kind"] == "placement":
                info = chart["planets"][row["planet"]]
                self.assertEqual((row["sign"], row["house"]), (info["zodiac_sign"], info["house"]))
                self.assertIn(f"({info['formatted_pos'].split(' ', 1)[1]})", card.heading)
                self.assertIn(f"в {_ORDINAL[row['house']]} дом", card.heading)
            if row["kind"] == "angle":
                key = "Ascendant" if row["planet"] == "ASC" else "MC"
                self.assertEqual(row["sign"], chart["angles"][f"{key}_sign"])
                self.assertIn(chart["angles"][f"{key}_formatted"].split(" ", 1)[1], card.heading)

    def test_the_selection_follows_the_theme_and_the_question_not_only_the_orb(self):
        love, career = self.headings(report_type="love"), self.headings(report_type="career")
        self.assertNotEqual(love, career)
        self.assertTrue(any("Венера" in h for h in love))
        self.assertTrue(any("Сатурн" in h or "Юпитер" in h or "Меркурий" in h for h in career))
        plain = self.headings(report_type="general", question="")
        money = self.headings(report_type="general", question="Как да планирам разходите и бюджета си?")
        self.assertNotEqual(plain, money)                                    # въпросът за пари сменя подбора
        self.assertTrue(all(c.score is not None for c in cards()))

    def test_the_houses_of_the_theme_raise_a_planet_without_changing_any_fact(self):
        chart = rs.chart("A")
        built = cards(report_type="career")
        self.assertGreater(len([c for c in built if c.row["kind"] == "placement"]), 3)
        for card in built:
            if card.row["kind"] == "placement":
                self.assertEqual(card.row["house"], chart["planets"][card.row["planet"]]["house"])

    def test_a_person_without_a_time_has_no_houses_no_angles_and_no_uncertain_moon(self):
        unknown = rs.unknown_ivan()
        built = cards(natal=unknown, name=rs.IVAN)
        self.assertTrue(built)
        for card in built:
            self.assertNotEqual(card.row["kind"], "angle")
            if card.row["kind"] == "placement":
                self.assertIsNone(card.row["house"])
                self.assertNotIn("дом", card.heading)
        uncertain = set((unknown.get("sign_ranges") or {}).keys())
        self.assertTrue(uncertain)
        self.assertFalse([c for c in built if c.row["kind"] == "placement" and c.row["planet"] in uncertain])
        self.assertFalse([c for c in built if c.row["kind"] == "natal" and ({c.row["p1"], c.row["p2"]} & {"ASC", "MC"})])

    def test_every_heading_passes_the_checker_for_several_people_themes_and_questions(self):
        people = {"A": (rs.chart("A"), rs.A_NAME), "Ivan": (rs.chart("Ivan"), rs.IVAN), "unknown": (rs.unknown_ivan(), rs.IVAN),
                  "child": (rs.child(), "Мария")}
        for label, (chart, name) in people.items():
            facts = tc.build_facts(mode="natal", user_name=name, natal_chart=chart, report_date="2026-10-17")
            for theme in ("general", "love", "career", "money", "health", "karmic"):
                for question in ("", QUESTION):
                    with self.subTest(person=label, theme=theme, question=bool(question)):
                        built = nsingle.build_cards(context(report_type=theme, question=question, natal=chart, name=name))
                        text = "\n".join(c.heading for c in built)
                        self.assertEqual([(v.code, v.detail) for v in tc.check_text(text, facts) if v.severity == "hard"], [])


class NatalFlowTest(unittest.TestCase):
    def refused(self, script, question=""):
        with self.assertRaises(text_guard.TextCheckError) as caught:
            run(script, question=question)
        return caught.exception

    def test_a_clean_answer_is_full_with_headings_written_by_the_code(self):
        script = Script()
        text, checks = run(script, question=QUESTION)
        self.assertEqual((quality(checks), script.calls), ("full", {"main": 1, "compose": 0, "repair": 0, "restore": 0}))
        for card in cards(question=QUESTION):
            self.assertIn(card.heading, text)
        for heading in (f"## Натална карта на {FIRST}", "## Обща картина", "## Какво да имате предвид", "## Отговор на въпроса", "## Заключение"):
            self.assertIn(heading, text)
        self.assertNotIn("Частичен анализ", text)
        self.assertNotIn("Резервен анализ", text)

    def test_the_prompt_asks_for_polite_plural_and_forbids_facts_in_the_card_text(self):
        script = Script()
        run(script)
        system = script.systems[0]
        self.assertIn("polite plural", system)
        self.assertIn("do NOT name any planet, sign, house, aspect, angle", system)

    def test_a_card_text_that_names_a_house_is_repaired(self):
        victim = non_key(question=QUESTION)[0].id
        script = Script(card_text={victim: WRONG}, repair={"усилва близостта в 7-ми дом": GOOD})
        text, checks = run(script, question=QUESTION)
        self.assertEqual((quality(checks), script.calls["repair"]), ("full", 1))
        self.assertNotIn("7-ми дом и носи", text)

    def test_an_angle_named_in_a_card_of_a_planet_is_repaired_but_the_angle_card_may_not_name_the_other_angle(self):
        victim = next(c.id for c in non_key(question=QUESTION) if c.row["kind"] == "placement")
        script = Script(card_text={victim: ANGLE}, repair={"Асцендентът ви помага": GOOD})
        text, checks = run(script, question=QUESTION)
        self.assertEqual((quality(checks), script.calls["repair"]), ("full", 1))
        self.assertNotIn("Асцендентът ви помага", text)
        check = nsingle.MODE.card_check(next(c for c in cards() if c.row["kind"] == "angle" and c.row["planet"] == "ASC"))
        self.assertEqual(check("Така първото впечатление е спокойно и вие печелите доверие."), [])
        self.assertTrue(check("Така MC ви помага да печелите доверие."))

    def test_a_card_is_restored_before_it_is_left_out_and_a_failed_one_makes_a_partial_analysis(self):
        victim = non_key(question=QUESTION)[0].id
        script = Script(card_text={victim: WRONG}, repair={"усилва близостта в 7-ми дом": WRONG})
        text, checks = run(script, question=QUESTION)
        self.assertEqual((quality(checks), script.calls["restore"]), ("full", 1))
        self.assertIn("natal analysis", script.systems[-1])                       # възстановяването е за натален анализ, не за „избрана дата“
        self.assertNotIn("chosen date", script.systems[-1])
        script = Script(card_text={victim: WRONG}, repair={"усилва близостта в 7-ми дом": WRONG}, restore={victim: WRONG})
        text, checks = run(script, question=QUESTION)
        omitted = next(c for c in cards(question=QUESTION) if c.id == victim)
        self.assertEqual(quality(checks), "partial")
        self.assertNotIn(omitted.heading, text)
        self.assertNotIn(omitted.heading, script.prompts[0])
        self.assertEqual(sum(1 for c in cards(question=QUESTION) if c.heading in script.prompts[0]), len(cards(question=QUESTION)) - 1)

    def test_a_key_card_that_cannot_be_checked_is_a_refusal(self):
        key = sorted(key_ids())[0]
        script = Script(card_text={key: WRONG}, repair={"усилва близостта в 7-ми дом": WRONG}, restore={key: WRONG})
        self.assertEqual(self.refused(script).stage, "natalone:key_card")

    def test_the_closing_blocks_may_not_name_planets_houses_signs_aspects_or_angles(self):
        named = {"SUMMARY": NAMING, "ADVICE": ADVICE, "ANSWER": ANSWER, "CONCLUSION": CONCLUSION}
        script = Script(composition=named, compose=named, repair={"Венера е в 7-ми дом": NAMING})
        self.assertEqual(self.refused(script, QUESTION).stage, "natalone:compose:SUMMARY")
        fixed = Script(composition=named, repair={"Венера е в 7-ми дом": SUMMARY})
        text, checks = run(fixed, question=QUESTION)
        self.assertEqual(quality(checks), "full")
        self.assertNotIn("Венера е в 7-ми дом", text)
        angle = {"SUMMARY": NAMING_ANGLE, "ADVICE": ADVICE, "ANSWER": ANSWER, "CONCLUSION": CONCLUSION}
        script = Script(composition=angle, compose=angle, repair={"Асцендентът ви прави": NAMING_ANGLE})
        self.assertEqual(self.refused(script, QUESTION).stage, "natalone:compose:SUMMARY")

    def test_a_fallback_keeps_every_card_and_has_the_answer_and_the_conclusion(self):
        named = {"SUMMARY": NAMING, "ADVICE": ADVICE, "ANSWER": ANSWER, "CONCLUSION": CONCLUSION}
        script = Script(composition=named, compose={"SUMMARY": SUMMARY, "ADVICE": ADVICE, "ANSWER": ANSWER, "CONCLUSION": CONCLUSION},
                        repair={"Венера е в 7-ми дом": NAMING})
        text, checks = run(script, question=QUESTION)
        summary = next(c for c in checks if c.get("stage") == "natal_single")
        total = len(cards(question=QUESTION))
        self.assertEqual((quality(checks), summary["removed"], summary["kept"], summary["cards"]), ("fallback", "", total, total))
        self.assertIn("## Отговор на въпроса", text)
        self.assertIn("## Заключение", text)

    def test_an_answer_that_is_short_is_refused(self):
        short = {"SUMMARY": SUMMARY, "ADVICE": ADVICE, "ANSWER": "Да.", "CONCLUSION": CONCLUSION}
        self.assertEqual(self.refused(Script(composition=short, compose=short, repair={"Да.": "Да."}), QUESTION).stage, "natalone:compose:ANSWER")

    def test_too_many_failed_cards_stop_the_analysis(self):
        bad = {c.id: WRONG for c in cards()}
        script = Script(card_text=bad, repair={"усилва близостта в 7-ми дом": WRONG}, restore=bad)
        self.assertEqual(self.refused(script).stage, "natalone:too_many")

    def test_a_person_without_a_time_gets_a_full_analysis_without_invented_houses_or_angles(self):
        text, checks = run(Script(), natal=rs.unknown_ivan(), name=rs.IVAN)
        self.assertEqual(quality(checks), "full")
        self.assertNotIn("дом", text.split("## Обща картина")[0])
        self.assertNotIn("Асцендент", text)
        self.assertNotIn("MC", text)

    def test_the_whole_text_is_checked_once_more_and_passes(self):
        text, _checks = run(Script(), question=QUESTION)
        facts = tc.build_facts(mode="natal", user_name=rs.A_NAME, natal_chart=rs.chart("A"), report_date="2026-10-17", question=QUESTION)
        self.assertEqual([v for v in tc.check_text(text, card_engine.restrict(facts, cards(question=QUESTION))) if v.severity == "hard"], [])


class NatalRouteTest(unittest.TestCase):
    """interpret_chart: натален анализ за един човек на български минава по картите; другите езици остават по стария път."""

    def interp_call(self, script, **kwargs):
        interp = ai_interpreter.AIInterpreter(api_key="test-dummy")
        interp._call_api = script
        checks = []
        base = dict(natal_chart=rs.chart("A"), user_name=rs.A_NAME, report_type="money", language="bg", checks=checks, question="")
        base.update(kwargs)
        return asyncio.run(interp.interpret_chart(**base)), checks

    def test_a_natal_analysis_for_one_person_in_bulgarian_goes_through_the_cards(self):
        script = Script()
        script.args = {"question": "", "report_type": "money", "natal": None, "name": rs.A_NAME}
        text, checks = self.interp_call(script)
        self.assertEqual(script.calls["main"], 1)
        self.assertEqual(quality(checks), "full")
        self.assertIn(f"## Натална карта на {FIRST}", text)

    def test_the_old_path_is_still_used_for_other_languages(self):
        calls = []

        async def old(system_prompt, user_prompt, max_tokens, **kw):
            calls.append(system_prompt)
            return "Plain English text."
        with mock.patch.dict(os.environ, {"TEXT_CHECK_MODE": "off"}):
            text, _checks = self.interp_call(old, language="en")
        self.assertEqual(text, "Plain English text.")
        self.assertFalse(any("MODE: ONE-PERSON NATAL ANALYSIS" in s for s in calls))


class NatalJobsTest(unittest.TestCase):
    """През истинските задачи: пълният се таксува (1,60 €), частичният не; отказаният не се записва."""

    @classmethod
    def setUpClass(cls):
        cls.client = TestClient(main.app)

    def setUp(self):
        limiter.reset()

    def run_job(self, email, wrong, extra=None):
        headers = register_and_login(self.client, email)
        db = SessionLocal()
        try:
            uid = db.query(User.id).filter(User.email == email).scalar()
            now_paid, now_gift = (int(db.query(User.paid_cents).filter(User.id == uid).scalar()), int(db.query(User.gift_cents).filter(User.id == uid).scalar()))
            billing.apply_transaction(db, uid, "admin", paid=1000 - now_paid, gift=-now_gift, allow_negative=True, description="тест")
            db.commit()
        finally:
            db.close()
        body = {"date": "1990-02-15", "time": "13:00", "lat": 42.6977, "lon": 23.3219, "name": "Тест А — одит", "report_type": "money", **(extra or {})}
        seen = []

        async def fake(system_prompt, user_prompt, max_tokens, **kw):
            if "MODE: ONE-PERSON NATAL ANALYSIS. You INTERPRET" in system_prompt:
                seen.append(system_prompt)
                ids = re.findall(r"^(A\d+): ", user_prompt, re.M)
                parts = [f"[[{i}]]\n{WRONG if i in wrong else GOOD}" for i in ids]
                return "\n\n".join(parts + [f"[[SUMMARY]]\n{SUMMARY}", f"[[ADVICE]]\n{ADVICE}", f"[[CONCLUSION]]\n{CONCLUSION}"])
            if "COMPOSITION FROM VERIFIED BLOCKS" in system_prompt:
                return f"[[SUMMARY]]\n{SUMMARY}\n\n[[ADVICE]]\n{ADVICE}\n\n[[CONCLUSION]]\n{CONCLUSION}"
            if "You write ONE short interpretation" in system_prompt:
                return WRONG if re.search(r"CARD ([A-Z]\d+):", user_prompt).group(1) in wrong else GOOD
            return WRONG if "усилва близостта в 7-ми дом" in user_prompt else user_prompt
        with mock.patch.dict(os.environ, {"BALANCE_ENFORCED": "1"}), mock.patch.object(main.ai_interpreter, "_call_api", fake):
            response = self.client.post("/jobs?wait=60", json=body, headers=headers)
        self.assertEqual(response.status_code, 200, response.text)
        job = response.json()["job"]
        report = self.client.get(f"/reports/{job['report_id']}", headers=headers).json() if job["report_id"] else None
        db = SessionLocal()
        try:
            row = db.query(User.paid_cents, User.gift_cents).filter(User.id == uid).one()
        finally:
            db.close()
        return job, report, (int(row[0]), int(row[1])), seen

    def victim(self):
        return non_key(report_type="money")[0].id

    def test_full_is_charged_the_basic_price_and_saved_with_the_headings(self):
        job, report, (paid, gift), seen = self.run_job("ns-full@test.bg", set())
        self.assertEqual((job["status"], job["charged_cents"], paid), ("succeeded", 160, 1000 - 160))
        self.assertNotIn("quality", report["params"])
        self.assertIn("## Натална карта на", report["content"])
        self.assertTrue(seen)

    def test_a_partial_is_not_charged_and_a_refused_one_is_not_saved(self):
        job, report, balances, _ = self.run_job("ns-partial@test.bg", {self.victim()})
        self.assertEqual((job["status"], job["charged_cents"], balances), ("succeeded", 0, (1000, 0)))
        self.assertTrue(report["content"].startswith("**Частичен анализ.**"))
        job, report, balances, _ = self.run_job("ns-refused@test.bg", {"A1", "A2", "A3", "A4", "A5"})
        self.assertEqual((job["status"], job["report_id"], balances), ("failed", None, (1000, 0)))

    def test_a_fallback_is_charged_like_a_full_one(self):
        # затварящият блок нарушава правилото при цели карти: съставя се наново от тях и се таксува
        headers = register_and_login(self.client, "ns-fallback@test.bg")
        db = SessionLocal()
        try:
            uid = db.query(User.id).filter(User.email == "ns-fallback@test.bg").scalar()
            now_paid, now_gift = (int(db.query(User.paid_cents).filter(User.id == uid).scalar()), int(db.query(User.gift_cents).filter(User.id == uid).scalar()))
            billing.apply_transaction(db, uid, "admin", paid=1000 - now_paid, gift=-now_gift, allow_negative=True, description="тест")
            db.commit()
        finally:
            db.close()

        async def fake(system_prompt, user_prompt, max_tokens, **kw):
            if "MODE: ONE-PERSON NATAL ANALYSIS. You INTERPRET" in system_prompt:
                ids = re.findall(r"^(A\d+): ", user_prompt, re.M)
                return "\n\n".join([f"[[{i}]]\n{GOOD}" for i in ids] + [f"[[SUMMARY]]\n{NAMING}", f"[[ADVICE]]\n{ADVICE}", f"[[CONCLUSION]]\n{CONCLUSION}"])
            if "COMPOSITION FROM VERIFIED BLOCKS" in system_prompt:
                return f"[[SUMMARY]]\n{SUMMARY}\n\n[[ADVICE]]\n{ADVICE}\n\n[[CONCLUSION]]\n{CONCLUSION}"
            return NAMING if "Венера е в 7-ми дом" in user_prompt else GOOD          # поправката не оправя нарушението
        body = {"date": "1990-02-15", "time": "13:00", "lat": 42.6977, "lon": 23.3219, "name": "Тест А — одит", "report_type": "money"}
        with mock.patch.dict(os.environ, {"BALANCE_ENFORCED": "1"}), mock.patch.object(main.ai_interpreter, "_call_api", fake):
            response = self.client.post("/jobs?wait=60", json=body, headers=headers)
        job = response.json()["job"]
        report = self.client.get(f"/reports/{job['report_id']}", headers=headers).json()
        self.assertEqual((job["status"], job["charged_cents"]), ("succeeded", 160))
        self.assertEqual(report["params"].get("quality"), "fallback")
        self.assertTrue(report["content"].startswith("**Резервен анализ.**"))


if __name__ == "__main__":
    unittest.main()
