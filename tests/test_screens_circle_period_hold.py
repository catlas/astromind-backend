"""
Екрани (Табло, генератор, История): поведенията от искането на 17.10.2026.

- „Твоят кръг“: „Всички“ показва ВСИЧКИ профили (не първите четири), „Семейство“ събира съпруг/а, партньор, дете и роднина (и двата кода
  за роднина), „Аз“ няма свой филтър, а непознат или празен вид отношения е „Други“;
- периодите: краят на периода е като правилото на сървъра (календарни месеци: 3 за един човек, 2 за двама), а „Следващите N месеца“ започва
  на следващия ден без дупка и без припокриване; броят продължения не е ограничен;
- генераторът: след успех четирите стъпки остават отметнати поне 2 секунди, бутонът е заключен дотогава, а продължението само попълва формата;
- История: „Скрий“ и „Скрий всички неуспешни“ важат само за неуспешни и отказани записи.
"""
import json
import os
import re
import unittest
from datetime import date, timedelta

import testenv  # noqa: F401  (пътища и среда)
import limits
from test_phase27_screens import HAVE_NODE, SRC, run_node


def source(relative):
    return open(os.path.join(SRC, relative), encoding="utf-8").read()


def body(text, start, end):
    """Частта от изходния текст между два маркера: проверката е на конкретната функция, не на целия файл."""
    return text[text.index(start):text.index(end, text.index(start))]


@unittest.skipUnless(HAVE_NODE, "Node.js или файловете на екраните не са налични")
class CircleTest(unittest.TestCase):
    RELATIONS = ["self", "spouse", "partner", "child", "relative", "family", "friend", "other"]

    def run_circle(self, profiles, filters):
        script = f"""
const profiles = {json.dumps(profiles)};
console.log(JSON.stringify({{
  chips: circle.circleChips(profiles).map((g) => g.id),
  views: Object.fromEntries({json.dumps(filters)}.map((f) => [f, circle.circleVisible(profiles, f).map((p) => p.id)])),
}}));
"""
        return run_node(["circle"], script)

    def test_all_shows_every_profile_and_not_only_the_first_four(self):
        profiles = [{"id": i, "relation": ("friend" if i % 2 else "child")} for i in range(1, 51)]
        out = self.run_circle(profiles, ["all", "family", "friends"])
        self.assertEqual(out["views"]["all"], list(range(1, 51)))
        self.assertEqual(len(out["views"]["family"]) + len(out["views"]["friends"]), 50)

    def test_family_collects_spouse_partner_child_and_both_codes_of_a_relative(self):
        profiles = [{"id": i, "relation": r} for i, r in enumerate(self.RELATIONS, 1)]
        out = self.run_circle(profiles, ["all", "family", "friends", "other"])
        self.assertEqual(out["views"]["all"], list(range(1, 9)))                      # и осемте
        self.assertEqual(out["views"]["family"], [2, 3, 4, 5, 6])                     # съпруг/а, партньор, дете, роднина (relative и family)
        self.assertEqual(out["views"]["friends"], [7])
        self.assertEqual(out["views"]["other"], [8])

    def test_self_has_no_filter_but_is_in_all_and_only_groups_with_profiles_get_a_filter(self):
        out = self.run_circle([{"id": 1, "relation": "self"}, {"id": 2, "relation": "friend"}], ["all"])
        self.assertEqual(out["chips"], ["friends"])
        self.assertEqual(out["views"]["all"], [1, 2])
        out = self.run_circle([{"id": 1, "relation": "self"}], ["all"])
        self.assertEqual(out["chips"], [])

    def test_an_unknown_or_missing_relation_is_other_and_never_disappears(self):
        profiles = [{"id": 1, "relation": "colleague"}, {"id": 2, "relation": None}, {"id": 3, "relation": ""}, {"id": 4}]
        out = self.run_circle(profiles, ["all", "other", "family"])
        self.assertEqual(out["views"]["other"], [1, 2, 3, 4])
        self.assertEqual(out["views"]["family"], [])
        self.assertEqual(out["chips"], ["other"])


@unittest.skipUnless(HAVE_NODE, "Node.js или файловете на екраните не са налични")
class PeriodTest(unittest.TestCase):
    def test_the_end_date_rule_is_the_same_as_the_servers_for_every_start_day(self):
        starts = [(date(2026, 10, 1) + timedelta(days=n)).isoformat() for n in range(0, 760)]       # през високосна 2028 и смяна на години
        out = run_node(["period"], f"""
const starts = {json.dumps(starts)};
console.log(JSON.stringify([2, 3].flatMap((months) => starts.map((s) => ({{ s, months, end: period.maxEndDate(s, months), next: period.nextDay(period.maxEndDate(s, months)) }})))));
""")
        self.assertEqual(len(out), 2 * len(starts))
        for row in out:
            pair = row["months"] == 2
            self.assertEqual(limits.period_months(row["s"], row["end"]), row["months"], msg=row)           # точно толкова месеца
            self.assertIsNone(limits.forecast_period_error(row["s"], row["end"], pair), msg=row)            # сървърът го приема
            self.assertIsNotNone(limits.forecast_period_error(row["s"], row["next"], pair), msg=row)        # а един ден повече не

    def test_the_next_period_starts_the_next_day_and_chains_without_a_gap_or_an_overlap(self):
        out = run_node(["period"], """
let end = '2026-12-31';
const chain = [];
for (let i = 0; i < 6; i += 1) { const n = period.nextPeriod(end, 3); chain.push(n); end = n.end; }
console.log(JSON.stringify({
  chain,
  single: period.nextPeriod('2026-12-31', 3), pair: period.nextPeriod('2026-12-31', 2),
  middle: period.nextPeriod('2027-03-15', 3), leap: [period.nextDay('2028-02-28'), period.nextDay('2028-02-29'), period.nextDay('2027-02-28')],
  bad: [period.nextPeriod('', 3), period.nextPeriod('нещо', 3), period.nextDay(null), period.maxEndDate('', 3)],
}));
""")
        self.assertEqual([(n["start"], n["end"]) for n in out["chain"]],
                         [("2027-01-01", "2027-03-31"), ("2027-04-01", "2027-06-30"), ("2027-07-01", "2027-09-30"),
                          ("2027-10-01", "2027-12-31"), ("2028-01-01", "2028-03-31"), ("2028-04-01", "2028-06-30")])
        previous_end = "2026-12-31"
        for n in out["chain"]:                                                         # следващият започва точно на следващия ден
            self.assertEqual(date.fromisoformat(n["start"]), date.fromisoformat(previous_end) + timedelta(days=1))
            previous_end = n["end"]
        self.assertEqual(out["single"], {"start": "2027-01-01", "end": "2027-03-31"})
        self.assertEqual(out["pair"], {"start": "2027-01-01", "end": "2027-02-28"})    # двама: най-много 2 месеца
        self.assertEqual(out["middle"], {"start": "2027-03-16", "end": "2027-05-31"})
        self.assertEqual(out["leap"], ["2028-02-29", "2028-03-01", "2027-03-01"])
        self.assertEqual(out["bad"], [None, None, "", ""])

    def test_a_restored_period_never_starts_in_the_past(self):
        out = run_node(["period"], """
console.log(JSON.stringify({
  past: period.restoredPeriodStart('2026-10-01', '2026-10-18'), today: period.restoredPeriodStart('2026-10-18', '2026-10-18'),
  future: period.restoredPeriodStart('2026-11-05', '2026-10-18'), empty: period.restoredPeriodStart('', '2026-10-18'),
  missing: period.restoredPeriodStart(undefined, '2026-10-18'), year: period.restoredPeriodStart('2026-12-31', '2027-01-02'),
}));
""")
        self.assertEqual(out, {"past": "2026-10-18", "today": "2026-10-18", "future": "2026-11-05", "empty": "2026-10-18",
                               "missing": "2026-10-18", "year": "2027-01-02"})


@unittest.skipUnless(HAVE_NODE, "Node.js или файловете на екраните не са налични")
class ScreenSourceTest(unittest.TestCase):
    def test_after_success_the_four_steps_stay_ticked_for_two_seconds_and_the_button_is_locked(self):
        generate = source("pages/GenerateReport.jsx")
        self.assertGreaterEqual(int(re.search(r"const DONE_HOLD_MS = (\d+);", generate).group(1)), 2000)
        finish = body(generate, "const finishJob", "const pollJob")
        self.assertIn("if (job.status === 'succeeded')", finish)
        self.assertIn("setHoldDone(true)", finish)
        self.assertIn("setTimeout(() => setHoldDone(false), DONE_HOLD_MS)", finish)
        self.assertNotIn("setHoldDone(true)", body(finish, "if (job.status === 'failed')", "snapshotRef.current = null"))   # неуспех и отказ не се задържат
        self.assertIn("finishJob(job, { hold: false })", generate)                      # връщане след затворена страница: без задържане
        self.assertIn("<JobStages complete />", generate)
        self.assertIn("{!loading && holdDone && (", generate)
        self.assertIn("disabled={loading || holdDone || formBlocked", generate)         # бутонът е заключен през задържането
        self.assertIn("clearTimeout(holdTimer.current)", body(generate, "return () => {\n      alive = false;", "}, [user?.id]);"))
        complete = body(generate, "const JobStages", "const formatBgDate")
        self.assertIn("complete ? STAGE_RANK.done + 1", complete)                       # всички стъпки са над „Готово“: четири отметки

    def test_a_new_attempt_starts_the_step_row_from_the_beginning(self):
        generate = source("pages/GenerateReport.jsx")
        submit = body(generate, "const handleSubmit", "// Валидация")
        self.assertIn("setActiveJob(null)", submit)                                     # докато задачата се създава, не се вижда краят на предишната

    def test_the_continuation_only_fills_the_form_with_the_same_data_and_never_submits(self):
        generate = source("pages/GenerateReport.jsx")
        meta = body(generate, "pendingMetaRef.current = {", "attached = Boolean(await startJob(requestData))")
        self.assertIn("periodEnd: requestData.is_dynamic ? requestData.end_date : null", meta)
        self.assertIn("inputs: requestData.is_dynamic ? collectInputs() : null", meta)  # формата както е изпратена: същите хора, тема и въпрос
        handler = body(generate, "const handleContinue", "if (!user) return null;")
        self.assertIn("restoreInputs(", handler)
        self.assertIn("endDate: continuation.end", handler)
        for forbidden in ("startJob(", "handleSubmit(", "createJob(", "requestSubmit"):
            self.assertNotIn(forbidden, handler)                                         # нов анализ само с бутона, където се виждат цената и балансът
        derived = body(generate, "const continuation = (() => {", "const handleContinue")
        self.assertIn("nextPeriod(resultMeta.periodEnd, months)", derived)
        self.assertIn("forecastLimits.forecast_max_months_pair", derived)               # двама: по-късият лимит
        self.assertIn('data-testid="next-period"', generate)
        self.assertIn("Следващите {continuation.months} месеца", generate)
        self.assertIn("import { maxEndDate, nextPeriod, restoredPeriodStart } from '../utils/period';", generate)
        self.assertNotIn("const maxEndDate", generate)                                  # една и съща логика на едно място

    def test_the_dashboard_circle_has_no_profile_limit_and_uses_the_shared_groups(self):
        dashboard = source("pages/Dashboard.jsx")
        self.assertNotIn("CIRCLE_LIMIT", dashboard)
        self.assertNotIn("slice(0, CIRCLE", dashboard)
        self.assertIn("import { circleChips, circleVisible } from '../utils/circle';", dashboard)
        self.assertIn("const visible = circleVisible(circle.all, circleFilter);", dashboard)          # без .slice и друго ограничение отгоре
        self.assertIn("const present = circleChips(circle.all);", dashboard)
        self.assertNotRegex(dashboard, r"circle\.all[^\n]*\.slice\(")
        self.assertNotIn("const GROUPS", dashboard)

    def test_history_hides_only_failed_and_cancelled_records(self):
        history = source("pages/History.jsx")
        self.assertIn("import { fetchJobs, hideFailedJobs, hideJob, isActive, rememberJob } from '../utils/jobs';", history)
        self.assertIn('data-testid="hide-job"', history)
        self.assertIn('data-testid="hide-all-failed"', history)
        self.assertIn("Скрий всички неуспешни", history)
        row_button = body(history, "{!isActive(j) && (", 'data-testid="hide-job"')
        self.assertIn("hideOne(j)", row_button)                                          # „Скрий“ няма за задача в процес
        jobs = source("utils/jobs.js")
        self.assertIn("api.post(`/jobs/${id}/hide`)", jobs)
        self.assertIn("api.post('/jobs/hide-failed')", jobs)


    def test_a_profile_restores_a_period_only_from_today_on(self):
        generate = source("pages/GenerateReport.jsx")
        apply_profile = body(generate, "const applyProfile", "const handleProfileChange")
        self.assertIn("restoredPeriodStart(settings.transitData.target_date, today)", apply_profile)
        self.assertIn("const restored = mode === 'period'", apply_profile)                         # само периодът; „за дата“ си остава както е запазена
        self.assertIn(": settings.transitData;", apply_profile)                                  # други режими: както е запазено
        self.assertIn("import { maxEndDate, nextPeriod, restoredPeriodStart } from '../utils/period';", generate)


if __name__ == "__main__":
    unittest.main()
