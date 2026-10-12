"""
Натален анализ за двама (natal_pair.py): същата машина като при „за дата“ (card_engine.py), с карти за собствената карта на всеки човек,
синастрията и домовете на другия (с посока). Подборът на картите зависи от темата, отношенията и въпроса, а не само от орбиса.
AI е подменен със сценарий; картите, проверката, поправките и сглобяването са истинският код.
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
import natal_pair as npair  # noqa: E402
import regression_support as rs  # noqa: E402
import relationship  # noqa: E402
import text_check as tc  # noqa: E402
import text_guard  # noqa: E402
from database import SessionLocal, User  # noqa: E402
from rate_limit import limiter  # noqa: E402
from testenv import register_and_login  # noqa: E402

GOOD = "Така двамата се допълват и се разбират по-леко, когато говорят открито и не бързат с изводите."
WRONG = "Това усилва близостта в 7-ми дом и носи топлина в тяхната връзка."                   # дом в блок на карта: нарушение
NAMING = "Венера на Иван е в 7-ми дом на Тест А одит, затова връзката е топла и спокойна."          # планета и дом в затварящ блок
SUMMARY = "Двамата се допълват: единият носи спокойствие, другият търси близост, и когато говорят открито, им е лесно да се разберат."
ADVICE = "- Говорете открито и спокойно.\n- Не бързайте с изводите един за друг."
ANSWER = ("Най-добре е да говорите открито и спокойно: първо изслушайте другия докрай, после кажете какво очаквате, без да търсите виновен. "
          "Изберете един важен въпрос вместо много теми и оставете място за паузи. Така разговорът за очакванията няма да се превърне в спор.")
CONCLUSION = "Връзката има добра основа за разбирателство и изисква търпение и откритост от двамата."
QUESTION = "Как да говорим за очакванията си?"
FIRST = factpack.display_name(rs.A_NAME, factpack.FIRST_PERSON_DEFAULT)


def context(report_type="love", relation="romantic", question="", partner=None, natal=None, second=rs.IVAN):
    return card_engine.Context(
        natal_chart=natal or rs.chart("A"), partner_chart=partner or rs.chart("Ivan"), transit_chart={}, user_name=rs.A_NAME, partner_name=second,
        first=FIRST, second=second, question=question, target_date="", theme="x", utc_today="2026-10-12", report_type=report_type, relation=relation)


def cards(**kwargs):
    return npair.build_cards(context(**kwargs))


class Script:
    def __init__(self, card_text=None, composition=None, repair=None, compose=None, restore=None, ids=None):
        self.card_text, self.repair, self.restore = card_text or {}, repair or {}, restore or {}
        self.composition = composition or {"SUMMARY": SUMMARY, "ADVICE": ADVICE, "ANSWER": ANSWER, "CONCLUSION": CONCLUSION}
        self.compose_blocks, self.ids = compose, ids
        self.args = {}                                  # question, report_type, relation: както ги получава анализът
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
        if "TWO-PERSON NATAL ANALYSIS (SYNASTRY)" in system_prompt:
            self.calls["main"] += 1
            self.systems.append(system_prompt)
            return self.tagged(True, self.composition)
        if "You write ONE short interpretation" in system_prompt:
            self.calls["restore"] += 1
            return self.restore.get(re.search(r"CARD ([A-Z]\d+):", user_prompt).group(1), GOOD)
        self.calls["repair"] += 1
        for key, fixed in self.repair.items():
            if key in user_prompt:
                return fixed
        return re.search(r"```(.*?)```", user_prompt, re.S).group(1) if "```" in user_prompt else user_prompt


def run(script, question="", report_type="love", relation="romantic", partner=None):
    interp = ai_interpreter.AIInterpreter(api_key="test-dummy")
    interp._call_api = script
    script.args = {"question": question, "report_type": report_type, "relation": relation}
    checks = []
    text = asyncio.run(npair.interpret(interp, natal_chart=rs.chart("A"), partner_chart=partner or rs.chart("Ivan"), user_name=rs.A_NAME,
                                       partner_name=rs.IVAN, question=question, report_type=report_type, relation=relation, checks=checks,
                                       theme="General.", utc_today="2026-10-12"))
    return text, checks


def quality(checks):
    return next((c["quality"] for c in checks if c.get("quality")), None)


def key_ids(**kwargs):
    built = cards(**kwargs)
    card_engine.mark_key_cards(built, npair.MODE)
    return {c.id for c in built if c.key}


class SelectionTest(unittest.TestCase):
    def headings(self, **kwargs):
        return {c.heading for c in cards(**kwargs)}

    def test_four_groups_with_their_own_chart_the_synastry_and_the_houses_of_the_other(self):
        built = cards()
        self.assertEqual({c.group for c in built}, {"A", "B", "S", "H"})
        for card in (c for c in built if c.group == "A"):
            self.assertIn(f"на {FIRST}", card.heading)
            self.assertNotIn("Иван", card.heading)
        for card in (c for c in built if c.group == "B"):
            self.assertIn("на Иван", card.heading)
            self.assertNotIn(FIRST, card.heading)
        for card in (c for c in built if c.group == "S"):
            self.assertIn(FIRST, card.heading)
            self.assertIn("Иван", card.heading)

    def test_a_house_card_always_names_whose_planet_and_whose_house_and_matches_the_overlay(self):
        for card in (c for c in cards() if c.group == "H"):
            row = card.row
            owners = {"user": FIRST, "partner": "Иван"}
            self.assertIn(f"на {owners[row['po']]} попада в", card.heading)
            self.assertTrue(card.heading.endswith(f"дом на {owners[row['ho']]}."))
            houses_chart, planets_chart = (rs.chart("A"), rs.chart("Ivan")) if row["ho"] == "user" else (rs.chart("Ivan"), rs.chart("A"))
            self.assertEqual(factpack.overlay(houses_chart, planets_chart)[row["planet"]], row["house"])
        directions = {(c.row["po"], c.row["ho"]) for c in cards() if c.group == "H"}
        self.assertEqual(directions, {("user", "partner"), ("partner", "user")})

    def test_the_selection_follows_the_theme_the_relationship_and_the_question_not_only_the_orb(self):
        love = self.headings(report_type="love", relation="romantic")
        career = self.headings(report_type="career", relation="work")
        self.assertNotEqual(love, career)
        self.assertTrue(any("Венера" in h for h in love))
        self.assertTrue(any("Сатурн" in h or "Меркурий" in h or "Юпитер" in h for h in career))
        plain = self.headings(report_type="general", relation="friend", question="")
        money = self.headings(report_type="general", relation="friend", question="Как да планираме общ разход?")
        self.assertNotEqual(plain, money)                                    # въпросът за пари сменя подбора
        by_orb = sorted(cards(report_type="love", relation="romantic"), key=lambda c: c.score)
        self.assertTrue(all(c.score is not None for c in by_orb))

    def test_the_relevance_only_orders_and_never_changes_a_fact(self):
        rel = npair.Relevance("love", "romantic", "")
        self.assertGreater(rel.planet("Venus"), rel.planet("Uranus"))
        self.assertEqual(npair.question_planets("Как да поставям граници?"), {"Saturn", "Mars"})
        self.assertEqual(npair.question_planets("нещо без ключови думи"), set())

    def test_an_unknown_time_person_has_no_house_cards_and_no_uncertain_moon(self):
        unknown = rs.unknown_ivan()
        built = cards(partner=unknown)
        for card in built:
            if card.row.get("kind") == "overlay":
                self.assertEqual(card.row["ho"], "user")                       # домовете на Иван не съществуват
            if card.row.get("kind") == "placement" and card.row["owner"] == "partner":
                self.assertIsNone(card.row["house"])
                self.assertNotIn("дом", card.heading)
        uncertain = set((unknown.get("sign_ranges") or {}).keys())
        self.assertFalse([c for c in built if c.row.get("kind") == "placement" and c.row["owner"] == "partner" and c.row["planet"] in uncertain])

    def test_every_heading_passes_the_checker_for_several_pairs_themes_and_relations(self):
        pairs = {"A+Ivan": (rs.chart("A"), rs.A_NAME, rs.chart("Ivan"), rs.IVAN), "Ivan+A": (rs.chart("Ivan"), rs.IVAN, rs.chart("A"), rs.A_NAME),
                 "A+unknown": (rs.chart("A"), rs.A_NAME, rs.unknown_ivan(), rs.IVAN), "A+child": (rs.chart("A"), rs.A_NAME, rs.child(), "Мария")}
        for label, (ca, na, cb, nb) in pairs.items():
            facts = tc.build_facts(mode="natal", user_name=na, natal_chart=ca, partner_name=nb, partner_chart=cb, report_date="2026-10-12")
            for theme in ("general", "love", "career", "money", "health", "karmic"):
                for relation in ("romantic", "friend", "family", "parent_child", "work", ""):
                    with self.subTest(pair=label, theme=theme, relation=relation):
                        ctx = card_engine.Context(natal_chart=ca, partner_chart=cb, transit_chart={}, user_name=na, partner_name=nb,
                                                  first=factpack.display_name(na, "A"), second=factpack.display_name(nb, "B"), question="",
                                                  target_date="", theme="x", utc_today="2026-10-12", report_type=theme, relation=relation)
                        text = "\n".join(c.heading for c in npair.build_cards(ctx))
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
        for heading in (f"## Собствената карта на {FIRST}", "## Собствената карта на Иван", "## Взаимодействие между двете карти",
                        "## Планетите в домовете на другия", "## Обща картина", "## Отговор на въпроса", "## Заключение"):
            self.assertIn(heading, text)
        self.assertIn("Relationship: romantic", script.systems[0])

    def test_the_relationship_reaches_the_prompt_and_forbids_romance_unless_it_is_romantic(self):
        script = Script()
        run(script, relation="friend", report_type="general")
        self.assertIn("Relationship: friend", script.systems[0])
        self.assertIn("never use a romantic or sexual frame unless the relationship is romantic", script.systems[0])

    def test_a_card_text_that_names_a_house_or_a_planet_is_repaired(self):
        victim = next(c.id for c in cards(question=QUESTION) if c.id not in key_ids(question=QUESTION))
        script = Script(card_text={victim: WRONG}, repair={"усилва близостта в 7-ми дом": GOOD})
        text, checks = run(script, question=QUESTION)
        self.assertEqual((quality(checks), script.calls["repair"]), ("full", 1))
        self.assertNotIn("7-ми дом и носи", text)

    def test_a_card_is_restored_before_it_is_left_out_and_a_failed_one_makes_a_partial_analysis(self):
        victim = next(c.id for c in cards(question=QUESTION) if c.id not in key_ids(question=QUESTION))
        script = Script(card_text={victim: WRONG}, repair={"усилва близостта в 7-ми дом": WRONG})
        text, checks = run(script, question=QUESTION)
        self.assertEqual((quality(checks), script.calls["restore"]), ("full", 1))
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
        self.assertEqual(self.refused(script).stage, "natalpair:key_card")

    def test_the_closing_blocks_may_not_name_planets_houses_signs_or_aspects(self):
        named = {"SUMMARY": NAMING, "ADVICE": ADVICE, "ANSWER": ANSWER, "CONCLUSION": CONCLUSION}
        script = Script(composition=named, compose=named, repair={"Венера на Иван е в 7-ми дом": NAMING})
        self.assertEqual(self.refused(script, QUESTION).stage, "natalpair:compose:SUMMARY")
        fixed = Script(composition=named, repair={"Венера на Иван е в 7-ми дом": SUMMARY})
        text, checks = run(fixed, question=QUESTION)
        self.assertEqual(quality(checks), "full")                         # поправката махва имената; текстът няма планети в затварящите блокове
        self.assertNotIn("е в 7-ми дом на Тест А", text)

    def test_a_fallback_keeps_every_card_and_has_the_answer_and_the_conclusion(self):
        named = {"SUMMARY": NAMING, "ADVICE": ADVICE, "ANSWER": ANSWER, "CONCLUSION": CONCLUSION}
        script = Script(composition=named, compose={"SUMMARY": SUMMARY, "ADVICE": ADVICE, "ANSWER": ANSWER, "CONCLUSION": CONCLUSION},
                        repair={"Венера на Иван е в 7-ми дом": NAMING})
        text, checks = run(script, question=QUESTION)
        summary = next(c for c in checks if c.get("stage") == "natal_pair")
        total = len(cards(question=QUESTION))
        self.assertEqual((quality(checks), summary["removed"], summary["kept"], summary["cards"]), ("fallback", "", total, total))
        self.assertIn("## Отговор на въпроса", text)
        self.assertIn("## Заключение", text)

    def test_an_answer_that_is_short_is_refused(self):
        short = {"SUMMARY": SUMMARY, "ADVICE": ADVICE, "ANSWER": "Да.", "CONCLUSION": CONCLUSION}
        self.assertEqual(self.refused(Script(composition=short, compose=short, repair={"Да.": "Да."}), QUESTION).stage, "natalpair:compose:ANSWER")

    def test_too_many_failed_cards_stop_the_analysis(self):
        bad = {c.id: WRONG for c in cards()}
        script = Script(card_text=bad, repair={"усилва близостта в 7-ми дом": WRONG}, restore=bad)
        self.assertEqual(self.refused(script).stage, "natalpair:too_many")

    def test_an_unknown_time_pair_gets_a_full_analysis_without_invented_houses(self):
        text, checks = run(Script(), partner=rs.unknown_ivan())
        self.assertEqual(quality(checks), "full")
        self.assertNotIn("дом на Иван", text)


class NatalJobsTest(unittest.TestCase):
    """През истинските задачи: пълният се таксува (1,80 €), частичният не; отказаният не се записва."""

    @classmethod
    def setUpClass(cls):
        cls.client = TestClient(main.app)

    def setUp(self):
        limiter.reset()

    def run_job(self, email, wrong, relationship_code="friend"):
        headers = register_and_login(self.client, email)
        db = SessionLocal()
        try:
            uid = db.query(User.id).filter(User.email == email).scalar()
            now_paid, now_gift = (int(db.query(User.paid_cents).filter(User.id == uid).scalar()), int(db.query(User.gift_cents).filter(User.id == uid).scalar()))
            billing.apply_transaction(db, uid, "admin", paid=1000 - now_paid, gift=-now_gift, allow_negative=True, description="тест")
            db.commit()
        finally:
            db.close()
        body = {"date": "1990-02-15", "time": "13:00", "lat": 42.6977, "lon": 23.3219, "name": "Мария", "partner_name": "Иван",
                "partner_date": "1985-07-12", "partner_time": "18:45", "partner_lat": 48.2082, "partner_lon": 16.3738, "report_type": "general",
                "relationship": relationship_code}
        seen = []

        async def fake(system_prompt, user_prompt, max_tokens, **kw):
            if "TWO-PERSON NATAL ANALYSIS (SYNASTRY)" in system_prompt:
                seen.append(system_prompt)
                ids = re.findall(r"^([ABSH]\d+): ", user_prompt, re.M)
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
        ctx = context(report_type="general", relation="friend")
        built = npair.build_cards(ctx)
        card_engine.mark_key_cards(built, npair.MODE)
        return next(c.id for c in built if not c.key)

    def test_full_is_charged_and_the_relationship_reaches_the_ai(self):
        job, report, (paid, gift), seen = self.run_job("np-full@test.bg", set())
        self.assertEqual((job["status"], job["charged_cents"], paid), ("succeeded", 180, 1000 - 180))
        self.assertNotIn("quality", report["params"])
        self.assertIn("## Планетите в домовете на другия", report["content"])
        self.assertTrue(seen and "Relationship: friend" in seen[0])

    def test_a_partial_is_not_charged_and_a_refused_one_is_not_saved(self):
        job, report, balances, _ = self.run_job("np-partial@test.bg", {self.victim()})
        self.assertEqual((job["status"], job["charged_cents"], balances), ("succeeded", 0, (1000, 0)))
        self.assertTrue(report["content"].startswith("**Частичен анализ.**"))
        job, report, balances, _ = self.run_job("np-refused@test.bg", {"A1", "A2", "A3", "A4", "A5"})
        self.assertEqual((job["status"], job["report_id"], balances), ("failed", None, (1000, 0)))


if __name__ == "__main__":
    unittest.main()
