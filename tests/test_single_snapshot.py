"""
Анализ „за дата“ за един човек (single_snapshot.py): същата машина като при двама (card_engine.py).
Карти от кода, проверка на всеки блок, възстановяване, резервен и частичен анализ, ключови карти, покритие на въпроса и защита на
таксуването. AI е подменен със сценарий; картите, проверката, поправките и сглобяването са истинският код.
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
import engine  # noqa: E402
import factpack  # noqa: E402
import main  # noqa: E402
import regression_support as rs  # noqa: E402
import single_snapshot as ss  # noqa: E402
import text_check as tc  # noqa: E402
import text_guard  # noqa: E402
from database import SessionLocal, User  # noqa: E402
from rate_limit import limiter  # noqa: E402
from testenv import register_and_login  # noqa: E402

GOOD = "Това напрежение изисква търпение и яснота в мислите в този ден."
WRONG = "Транзитното Слънце е в тригон с наталния Марс на Иван, затова в този ден всичко върви леко."
SUMMARY = "В този ден човекът е по-скоро напрегнат и разсеян, затова е разумно да се действа бавно и да се изчаква, ако нещо тежи."
ADVICE = "- Планирайте деня с повече пауза.\n- Не бързайте с големи решения през този ден."
ANSWER = ("Най-добре е да планирате деня спокойно: направете първо най-важното, оставете място за почивка и не се претоварвайте. "
          "Изберете една основна задача вместо много и я довършете, така денят ще мине по-леко и без излишно напрежение.")
CONCLUSION = "Денят е подходящ за внимателна работа и неподходящ за прибързани решения."
QUESTION = "Как да планирам деня си?"
SKY = {}
FIRST = "Иван"


def sky():
    if not SKY:
        SKY["c"] = engine.get_engine().calculate_chart("2026-10-14", "18:00", 48.2082, 16.3738, zone="Europe/Vienna")
    return SKY["c"]


def cards(natal=None):
    ctx = ss.Context(natal_chart=natal or rs.chart("Ivan"), partner_chart=None, transit_chart=sky(), user_name=FIRST, partner_name=None,
                     first=FIRST, second="", question="", target_date="", theme="", utc_today="")
    built, _ = ss.MODE.build(ctx)
    return built


class Script:
    def __init__(self, card_text=None, composition=None, repair=None, compose=None, restore=None, natal=None):
        self.card_text, self.repair, self.restore, self.natal = card_text or {}, repair or {}, restore or {}, natal
        self.composition = composition or {"SUMMARY": SUMMARY, "ADVICE": ADVICE, "ANSWER": ANSWER, "CONCLUSION": CONCLUSION}
        self.compose_blocks = compose
        self.calls = {"main": 0, "compose": 0, "repair": 0, "restore": 0}
        self.prompts = []

    def tagged(self, with_cards, blocks):
        parts = [f"[[{c.id}]]\n{self.card_text.get(c.id, GOOD)}" for c in (cards(self.natal) if with_cards else [])]
        return "\n\n".join(parts + [f"[[{k}]]\n{v}" for k, v in blocks.items() if v])

    async def __call__(self, system_prompt, user_prompt, max_tokens, **kwargs):
        if "COMPOSITION FROM VERIFIED BLOCKS" in system_prompt:
            self.calls["compose"] += 1
            self.prompts.append(user_prompt)
            return self.tagged(False, self.compose_blocks or self.composition)
        if "ONE-PERSON SNAPSHOT for ONE chosen" in system_prompt:
            self.calls["main"] += 1
            return self.tagged(True, self.composition)
        if "You write ONE short interpretation" in system_prompt:
            self.calls["restore"] += 1
            return self.restore.get(re.search(r"CARD ([A-Z]\d):", user_prompt).group(1), GOOD)
        self.calls["repair"] += 1
        for key, fixed in self.repair.items():
            if key in user_prompt:
                return fixed
        return re.search(r"```(.*?)```", user_prompt, re.S).group(1) if "```" in user_prompt else user_prompt


def run(script, question="", natal=None):
    interp = ai_interpreter.AIInterpreter(api_key="test-dummy")
    interp._call_api = script
    checks = []
    text = asyncio.run(ss.interpret(interp, natal_chart=natal or rs.chart("Ivan"), transit_chart=sky(), user_name=FIRST, question=question,
                                    target_date="2026-10-14 18:00 (UTC+2)", checks=checks, theme="General life overview.",
                                    utc_today="2026-10-12"))
    return text, checks


def quality(checks):
    return next((c["quality"] for c in checks if c.get("quality")), None)


class SingleCardsTest(unittest.TestCase):
    def facts(self, natal=None):
        return tc.build_facts(mode="snapshot", user_name=FIRST, natal_chart=natal or rs.chart("Ivan"), transit_chart=sky(),
                              target_date="2026-10-14 18:00", report_date="2026-10-12")

    def test_the_cards_and_the_house_line_are_code_written_and_pass_the_checker(self):
        built = cards()
        self.assertTrue(0 < len(built) <= ss.CARD_LIMIT)
        self.assertTrue(all("на Иван (орб" in c.heading for c in built))
        text = "\n".join([ss.houses_line(rs.chart("Ivan"), sky(), FIRST)] + [c.heading for c in built])
        self.assertEqual([(v.code, v.detail) for v in tc.check_text(text, self.facts()) if v.severity == "hard"], [])

    def test_a_person_without_a_time_has_no_house_line_and_no_angle_cards(self):
        unknown = rs.unknown_ivan()
        self.assertEqual(ss.houses_line(unknown, sky(), FIRST), "")
        built = cards(unknown)
        self.assertEqual(len(built), min(ss.CARD_LIMIT, len(factpack.transit_aspects(unknown, sky()))))
        self.assertTrue(all(c.row["p2"] not in ("ASC", "MC") for c in built))


class SingleFlowTest(unittest.TestCase):
    def refused(self, script, question=""):
        with self.assertRaises(text_guard.TextCheckError) as caught:
            run(script, question=question)
        return caught.exception

    def test_a_clean_answer_is_full_with_headings_written_by_the_code(self):
        script = Script()
        text, checks = run(script, question=QUESTION)
        self.assertEqual((quality(checks), script.calls), ("full", {"main": 1, "compose": 0, "repair": 0, "restore": 0}))
        for card in cards():
            self.assertIn(card.heading, text)
        for heading in ("## Транзитите към Иван", "## Обща картина", "## Отговор на въпроса", "## Заключение"):
            self.assertIn(heading, text)

    def test_a_wrong_card_is_repaired_or_restored_before_it_is_left_out(self):
        script = Script(card_text={"A4": WRONG}, repair={"наталния Марс на Иван": GOOD})
        text, checks = run(script, question=QUESTION)
        self.assertEqual((quality(checks), script.calls["repair"], script.calls["restore"]), ("full", 1, 0))
        script = Script(card_text={"A4": WRONG}, repair={"наталния Марс на Иван": WRONG})                # поправката не помага, възстановяването помага
        text, checks = run(script, question=QUESTION)
        self.assertEqual((quality(checks), script.calls["restore"]), ("full", 1))
        summary = next(c for c in checks if c.get("stage") == "single_snapshot")
        self.assertEqual((summary["restored"], summary["removed"]), ("A4", ""))

    def test_a_card_that_cannot_be_checked_is_left_out_and_the_closing_blocks_use_only_the_included_cards(self):
        script = Script(card_text={"A4": WRONG}, repair={"наталния Марс на Иван": WRONG}, restore={"A4": WRONG})
        text, checks = run(script, question=QUESTION)
        omitted = next(c for c in cards() if c.id == "A4")
        self.assertEqual(quality(checks), "partial")
        self.assertNotIn(omitted.heading, text)
        self.assertNotIn(omitted.heading, script.prompts[0])
        self.assertEqual(sum(1 for c in cards() if c.heading in script.prompts[0]), len(cards()) - 1)

    def test_a_true_aspect_of_an_omitted_card_cannot_come_back_in_the_conclusion(self):
        omitted = next(c for c in cards() if c.id == "A4")
        back = {"SUMMARY": SUMMARY, "ADVICE": ADVICE, "ANSWER": ANSWER,
                "CONCLUSION": f"{omitted.heading.split(' (орб')[0]}, затова в този ден е добре да се действа внимателно."}
        script = Script(card_text={"A4": WRONG}, repair={"наталния Марс на Иван": WRONG}, restore={"A4": WRONG}, composition=back, compose=back)
        self.assertEqual(self.refused(script, QUESTION).stage, "single:compose:CONCLUSION")

    def test_the_summary_of_a_full_analysis_cannot_use_an_aspect_outside_the_cards(self):
        from aspects_engine import calculate_natal_aspects
        row = next(r for r in calculate_natal_aspects(rs.chart("Ivan"), use_wider_orbs=False) if r["planet1"] == "Sun" and r["planet2"] == "Moon")
        word = tc.ASPECT_BG[row["aspect"]]
        outside = f"Слънцето е в {word} с Луната на Иван още от раждането, затова в този ден е добре да се действа бавно."
        script = Script(composition={"SUMMARY": outside, "ADVICE": ADVICE, "ANSWER": ANSWER, "CONCLUSION": CONCLUSION},
                        compose={"SUMMARY": SUMMARY, "ADVICE": ADVICE, "ANSWER": ANSWER, "CONCLUSION": CONCLUSION},
                        repair={"още от раждането": outside})
        text, checks = run(script, question=QUESTION)
        self.assertEqual(quality(checks), "fallback")
        self.assertNotIn("още от раждането", text)

    def test_a_key_card_that_cannot_be_checked_is_a_refusal(self):
        script = Script(card_text={"A1": WRONG}, repair={"наталния Марс на Иван": WRONG}, restore={"A1": WRONG})
        self.assertEqual(self.refused(script).stage, "single:key_card")

    def test_an_answer_that_is_short_or_unrelated_is_refused_unless_repaired(self):
        short = {"SUMMARY": SUMMARY, "ADVICE": ADVICE, "ANSWER": "Да.", "CONCLUSION": CONCLUSION}
        self.assertEqual(self.refused(Script(composition=short, compose=short, repair={"Да.": "Да."}), QUESTION).stage, "single:compose:ANSWER")

    def test_a_fallback_keeps_every_card_and_has_the_answer_and_the_conclusion(self):
        bad = {"SUMMARY": WRONG, "ADVICE": ADVICE, "ANSWER": ANSWER, "CONCLUSION": CONCLUSION}
        script = Script(composition=bad, compose={"SUMMARY": SUMMARY, "ADVICE": ADVICE, "ANSWER": ANSWER, "CONCLUSION": CONCLUSION},
                        repair={"наталния Марс на Иван": WRONG})
        text, checks = run(script, question=QUESTION)
        summary = next(c for c in checks if c.get("stage") == "single_snapshot")
        self.assertEqual((quality(checks), summary["removed"]), ("fallback", ""))
        for card in cards():
            self.assertIn(card.heading, text)
        self.assertIn("## Отговор на въпроса", text)
        self.assertIn("## Заключение", text)

    def test_too_many_failed_cards_stop_the_analysis(self):
        bad = {c.id: WRONG for c in cards()}
        script = Script(card_text=bad, repair={"наталния Марс на Иван": WRONG}, restore={c.id: WRONG for c in cards()})
        self.assertEqual(self.refused(script).stage, "single:too_many")

    def test_an_unknown_time_person_gets_a_full_analysis_without_houses(self):
        unknown = rs.unknown_ivan()
        text, checks = run(Script(natal=unknown), question="", natal=unknown)
        self.assertEqual(quality(checks), "full")
        self.assertNotIn("Транзитните планети в домовете на", text)


class SingleJobsTest(unittest.TestCase):
    """През истинските задачи: пълният анализ се таксува, частичният не, повторно довършване не променя баланса."""

    @classmethod
    def setUpClass(cls):
        cls.client = TestClient(main.app)

    def setUp(self):
        limiter.reset()

    def run_job(self, email, scenario):
        headers = register_and_login(self.client, email)
        db = SessionLocal()
        try:
            uid = db.query(User.id).filter(User.email == email).scalar()
            now_paid, now_gift = (int(db.query(User.paid_cents).filter(User.id == uid).scalar()), int(db.query(User.gift_cents).filter(User.id == uid).scalar()))
            billing.apply_transaction(db, uid, "admin", paid=1000 - now_paid, gift=-now_gift, allow_negative=True, description="тест")
            db.commit()
        finally:
            db.close()
        body = {"date": "1985-07-12", "time": "18:45", "lat": 48.2082, "lon": 16.3738, "name": "Иван", "target_date": "2026-10-14",
                "target_time": "18:00", "report_type": "general"}

        async def fake(system_prompt, user_prompt, max_tokens, **kw):
            if "ONE-PERSON SNAPSHOT for ONE chosen" in system_prompt:
                ids = re.findall(r"^(A\d): ", user_prompt, re.M)
                parts = [f"[[{i}]]\n{WRONG if i in scenario else GOOD}" for i in ids]
                parts += [f"[[SUMMARY]]\n{SUMMARY}", f"[[ADVICE]]\n{ADVICE}", f"[[CONCLUSION]]\n{CONCLUSION}"]
                return "\n\n".join(parts)
            if "COMPOSITION FROM VERIFIED BLOCKS" in system_prompt:
                return f"[[SUMMARY]]\n{SUMMARY}\n\n[[ADVICE]]\n{ADVICE}\n\n[[CONCLUSION]]\n{CONCLUSION}"
            if "You write ONE short interpretation" in system_prompt:
                return WRONG if re.search(r"CARD ([A-Z]\d):", user_prompt).group(1) in scenario else GOOD
            return WRONG if "наталния Марс на Иван" in user_prompt else user_prompt
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
        return job, report, (int(row[0]), int(row[1]))

    def test_full_is_charged_and_a_partial_is_not(self):
        job, report, (paid, gift) = self.run_job("ss-full@test.bg", set())
        self.assertEqual((job["status"], job["charged_cents"], paid), ("succeeded", 160, 1000 - 160))
        self.assertNotIn("quality", report["params"])
        job, report, (paid, gift) = self.run_job("ss-partial@test.bg", {"A4"})
        self.assertEqual((job["status"], job["charged_cents"], (paid, gift)), ("succeeded", 0, (1000, 0)))
        self.assertTrue(report["content"].startswith("**Частичен анализ.**"))
        self.assertEqual(report["params"]["quality"], "partial")

    def test_a_refused_single_analysis_is_not_saved_and_not_charged(self):
        job, report, (paid, gift) = self.run_job("ss-refused@test.bg", {"A1"})                    # ключова карта
        self.assertEqual((job["status"], job["report_id"], (paid, gift)), ("failed", None, (1000, 0)))


if __name__ == "__main__":
    unittest.main()
