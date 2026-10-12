"""
Фаза 27 (R14, R17, R11, R08): екрани и История.

- една функция за позиция в зодиака: подсказката, колелото и обобщението дават същите градуси и минути като сървъра (за истински карти);
- таблицата и времевата линия филтрират с ЕДНО правило: една комбинация тема + профил + текст дава еднакво множество отчети;
  два профила с еднакво име не се смесват; броят в групите е преизчислен;
- „Нов опит“ пази входните данни на последните 10 задачи; готова задача се линква от генератора;
- резултатът на неуспешен опит не е резултат на стария: слотът е „в процес“, а предишният е в отделна карта с „Отвори“.
"""
import json
import os
import re
import shutil
import subprocess
import tempfile
import unittest

import testenv  # noqa: F401  (пътища и среда)
import engine

SRC = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), "src")
UTILS = os.path.join(SRC, "utils")
HAVE_NODE = bool(shutil.which("node")) and os.path.exists(os.path.join(UTILS, "astroMath.js"))


def run_node(files, script):
    """files: имена от src/utils, копирани като .mjs; в jobs.js импортът на ./api се заменя с празен обект."""
    work = tempfile.mkdtemp()
    try:
        imports = []
        for name in files:
            source = open(os.path.join(UTILS, f"{name}.js"), encoding="utf-8").read()
            source = source.replace("import { api } from './api';", "const api = {};")
            open(os.path.join(work, f"{name}.mjs"), "w", encoding="utf-8").write(source)
            imports.append(f"import * as {name} from '{work}/{name}.mjs';")
        out = subprocess.run(["node", "--input-type=module", "-e", "\n".join(imports) + "\n" + script], capture_output=True, text=True, timeout=60)
        if out.returncode != 0:
            raise AssertionError(out.stderr)
        return json.loads(out.stdout)
    finally:
        shutil.rmtree(work, ignore_errors=True)


@unittest.skipUnless(HAVE_NODE, "Node.js или файловете на екраните не са налични")
class LongitudeParityTest(unittest.TestCase):
    CHARTS = [("1990-02-15", "13:00", 42.6977, 23.3219), ("1985-07-12", "18:45", 48.2082, 16.3738), ("2014-05-20", "09:15", 42.1354, 24.7453),
              ("2026-10-10", "12:00", 42.6977, 23.3219), ("1970-03-21", "00:01", 40.7128, -74.006)]

    def test_the_screen_formatter_gives_the_same_degrees_and_minutes_as_the_server(self):
        rows = []
        for date, time, lat, lon in self.CHARTS:
            chart = engine.get_engine().calculate_chart(date, time, lat, lon)
            for name, planet in chart["planets"].items():
                if planet.get("longitude") is not None:
                    m = re.search(r"(\d+)°(\d+)", planet["formatted_pos"])
                    rows.append({"name": name, "longitude": planet["longitude"], "deg": int(m.group(1)), "min": int(m.group(2)),
                                 "sign": int(planet["longitude"] // 30)})
        self.assertGreater(len(rows), 50)
        out = run_node(["astroMath"], f"console.log(JSON.stringify({json.dumps(rows)}.map((r) => ({{ ...r, got: astroMath.formatLongitude(r.longitude) }}))));")
        for row in out:
            got = row["got"]
            self.assertEqual((got["deg"], got["min"], got["signIndex"]), (row["deg"], row["min"], row["sign"]), msg=row["name"])

    def test_the_sign_boundary_is_never_crossed_by_rounding(self):
        out = run_node(["astroMath"], """
console.log(JSON.stringify([29.9999, 59.99, 359.9999, 0, 30, 20.2667, 110.2706].map((l) => astroMath.formatLongitude(l))));
""")
        self.assertEqual([(o["deg"], o["min"], o["signIndex"]) for o in out],
                         [(29, 59, 0), (29, 59, 1), (29, 59, 11), (0, 0, 0), (0, 0, 1), (20, 16, 0), (20, 16, 3)])
        self.assertEqual(out[5]["text"], "20°16′")


@unittest.skipUnless(HAVE_NODE, "Node.js или файловете на екраните не са налични")
class HistoryFilterTest(unittest.TestCase):
    ITEMS = [
        {"id": 1, "type": "career", "label": "Кариера", "profile": "Иван", "profile_id": 10},
        {"id": 2, "type": "career", "label": "Кариера с Мария", "profile": "Мария", "profile_id": 11},
        {"id": 3, "type": "love", "label": "Любов", "profile": "Иван", "profile_id": 10},
        {"id": 4, "type": "career", "label": "Кариера", "profile": "Иван", "profile_id": 12},      # друг профил със същото име
        {"id": 5, "type": "karmic", "label": "Карма и род", "profile": "", "profile_id": None},
        {"id": 6, "type": "career", "label": "Кариера", "profile": "Стар профил", "profile_id": None},
    ]
    GROUPS = [{"month": "2026-10", "label": "Октомври 2026", "reports": ITEMS[:4]}, {"month": "2026-09", "label": "Септември 2026", "reports": ITEMS[4:]}]

    def evaluate(self, filters):
        script = f"""
const items = {json.dumps(self.ITEMS)}; const groups = {json.dumps(self.GROUPS)}; const f = {json.dumps(filters)};
const table = historyFilters.filterItems(items, f).map((r) => r.id);
const line = historyFilters.filterGroups(groups, f);
console.log(JSON.stringify({{ table, timeline: line.flatMap((g) => g.reports.map((r) => r.id)), counts: line.map((g) => [g.month, g.reports.length]) }}));
"""
        return run_node(["historyFilters"], script)

    def test_table_and_timeline_return_the_same_reports_for_every_combination(self):
        combos = [{"type": t, "profile": p, "query": q} for t in ("all", "career", "love", "karmic") for p in ("all", "id:10", "id:11", "id:12", "name:Стар профил", "none")
                  for q in ("", "кариера", "иван", "мария", "няма такова")]
        for combo in combos:
            out = self.evaluate(combo)
            self.assertEqual(sorted(out["table"]), sorted(out["timeline"]), msg=combo)

    def test_a_career_filter_shows_only_career_reports_in_both_views(self):
        out = self.evaluate({"type": "career", "profile": "all", "query": ""})
        self.assertEqual(sorted(out["table"]), [1, 2, 4, 6])
        self.assertEqual(out["counts"], [["2026-10", 3], ["2026-09", 1]])        # преизчислени, празните групи изчезват

    def test_clear_returns_everything_and_empty_groups_disappear(self):
        out = self.evaluate({"type": "all", "profile": "all", "query": ""})
        self.assertEqual(sorted(out["table"]), [1, 2, 3, 4, 5, 6])
        self.assertEqual(self.evaluate({"type": "love", "profile": "all", "query": ""})["counts"], [["2026-10", 1]])

    def test_two_profiles_with_one_displayed_name_are_not_merged(self):
        self.assertEqual(self.evaluate({"type": "all", "profile": "id:10", "query": ""})["table"], [1, 3])
        self.assertEqual(self.evaluate({"type": "all", "profile": "id:12", "query": ""})["table"], [4])
        options = run_node(["historyFilters"], f"console.log(JSON.stringify(historyFilters.profileOptions({json.dumps(self.ITEMS)})));")
        labels = {o["key"]: o["label"] for o in options}
        self.assertEqual(labels["id:10"], "Иван (профил №10)")
        self.assertEqual(labels["id:12"], "Иван (профил №12)")
        self.assertEqual(labels["id:11"], "Мария")                                  # единственото име остава без номер
        self.assertEqual(labels["name:Стар профил"], "Стар профил")


@unittest.skipUnless(HAVE_NODE, "Node.js или файловете на екраните не са налични")
class JobInputsTest(unittest.TestCase):
    PRELUDE = """
const store = new Map();
globalThis.localStorage = { getItem: (k) => (store.has(k) ? store.get(k) : null), setItem: (k, v) => store.set(k, String(v)), removeItem: (k) => store.delete(k) };
"""

    def test_inputs_are_kept_for_the_last_ten_jobs_only(self):
        out = run_node(["jobs"], self.PRELUDE + """
for (let id = 1; id <= 12; id += 1) jobs.rememberInputs(7, id, { name: `Иван ${id}`, question: 'въпрос' });
console.log(JSON.stringify({ first: jobs.recallInputs(7, 1), second: jobs.recallInputs(7, 2), last: jobs.recallInputs(7, 12), other: jobs.recallInputs(8, 12), keys: store.size }));
""")
        self.assertIsNone(out["first"])
        self.assertIsNone(out["second"])
        self.assertEqual(out["last"], {"name": "Иван 12", "question": "въпрос"})
        self.assertIsNone(out["other"])                                       # данните са по потребител
        self.assertEqual(out["keys"], 11)                                     # 10 набора и индексът

    def test_a_missing_or_broken_store_gives_nothing_instead_of_an_error(self):
        out = run_node(["jobs"], "globalThis.localStorage = { getItem: () => { throw new Error('блокирано'); }, setItem: () => { throw new Error('блокирано'); } };\n"
                                 "jobs.rememberInputs(1, 1, {}); console.log(JSON.stringify({ value: jobs.recallInputs(1, 1) }));")
        self.assertIsNone(out["value"])

    def test_a_recent_finished_job_is_linked_but_an_old_or_failed_one_is_not(self):
        out = run_node(["jobs"], """
const now = Date.parse('2026-10-12T12:00:00Z');
const job = (id, status, created, report) => ({ id, status, created_at: created, report_id: report });
console.log(JSON.stringify({
  fresh: jobs.recentFinishedJob([job(1, 'failed', '2026-10-12T11:00:00', null), job(2, 'succeeded', '2026-10-12T10:00:00', 55)], now),
  old: jobs.recentFinishedJob([job(3, 'succeeded', '2026-10-11T10:00:00', 56)], now),
  none: jobs.recentFinishedJob([job(4, 'running', '2026-10-12T11:30:00', null)], now),
  noReport: jobs.recentFinishedJob([job(5, 'succeeded', '2026-10-12T11:30:00', null)], now),
}));
""")
        self.assertEqual(out["fresh"]["id"], 2)
        self.assertEqual((out["old"], out["none"], out["noReport"]), (None, None, None))


class SourceGuardTest(unittest.TestCase):
    def source(self, relative):
        return open(os.path.join(SRC, relative), encoding="utf-8").read()

    def test_a_failed_attempt_does_not_show_the_old_report_as_its_result(self):
        generate = self.source("pages/GenerateReport.jsx")
        restore = generate[generate.index("const restorePrevious"):generate.index("if (job.status === 'failed')")]
        self.assertIn("setPreviousCard(", restore)
        self.assertNotIn("setResult(previous", restore)                         # старият текст не става резултат на новия опит
        self.assertIn("setResultMeta(null)", restore)
        started = generate[generate.index("attached = Boolean(await startJob(requestData))"):]
        self.assertIn("setResult(null)", started[:1500])                        # при старт слотът е „в процес“
        self.assertIn('data-testid="previous-card"', generate)
        self.assertIn("Отвори", generate)
        self.assertIn("pendingMetaRef.current", generate)                        # параметрите на резултата са на неговата задача

    def test_the_new_attempt_restores_its_inputs_once_and_profile_switch_restores_the_whole_context(self):
        generate = self.source("pages/GenerateReport.jsx")
        self.assertIn("restoreJobId", generate)
        self.assertIn("navigate(location.pathname, { replace: true, state: null })", generate)
        self.assertIn("rememberInputs(user.id", generate)
        apply_profile = generate[generate.index("const applyProfile"):generate.index("const handleProfileChange")]
        for needle in ("changeAnalysisMode(mode)", "setEndDate(", "setReportType(", "periodValid"):
            self.assertIn(needle, apply_profile)
        self.assertIn("openReportId", self.source("pages/History.jsx"))
        self.assertIn("restoreJobId", self.source("pages/History.jsx"))

    def test_the_mode_is_one_choice_of_three_and_a_saved_profile_shows_a_summary(self):
        generate = self.source("pages/GenerateReport.jsx")
        self.assertIn('role="radiogroup"', generate)
        for value in ("natal", "snapshot", "period"):
            self.assertIn(f"['{value}'", generate)
        self.assertNotIn('id="isDynamic"', generate)                            # няма втори, независим превключвател
        self.assertNotIn('id="enableTransit"', generate)
        self.assertIn('data-testid="birth-summary"', generate)
        self.assertIn("Промени данните за този анализ", generate)

    def test_the_chart_tooltip_works_with_keyboard_and_touch(self):
        chart = self.source("components/AstroChart.jsx")
        for needle in ("tabIndex={0}", 'role="button"', "onFocus={choose}", "onClick={choose}", "e.key === 'Enter'", 'aria-live="polite"',
                       "formatLongitude"):
            self.assertIn(needle, chart)
        self.assertNotIn("Math.floor((inSign - deg) * 60)", chart)              # старата floor логика на подсказката
        self.assertNotIn("Math.floor((angle - degrees) * 60)", self.source("components/ChartSummary.jsx"))

    def test_the_history_uses_the_shared_rule_and_shows_errors(self):
        history = self.source("pages/History.jsx")
        self.assertIn("filterItems(history, filters)", history)
        self.assertIn("filterGroups(timeline.groups, filters)", history)
        self.assertNotIn("params: filterProfile", history)
        self.assertIn("setTimelineError(", history)
        self.assertIn("setCurrentPage(1)", history)
        self.assertIn("fetchReports(500)", history)


if __name__ == "__main__":
    unittest.main()
