"""Кратък и пълен изглед на отчета (02.11.2026): краткият се изрязва от пълния при показване; записът, DOCX и Markdown са пълни."""
import json
import os
import shutil
import subprocess
import tempfile
import unittest

import testenv  # noqa: F401  (трябва да е преди main)
from testenv import register_and_login  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

import main  # noqa: E402

TESTS = os.path.dirname(os.path.abspath(__file__))
SRC = os.path.join(os.path.dirname(os.path.dirname(TESTS)), "src")
FIX = os.path.join(TESTS, "fixtures")


def short_of(text):
    work = tempfile.mkdtemp()
    try:
        for name in ("reportText", "reportView"):
            source = open(os.path.join(SRC, "utils", f"{name}.js"), encoding="utf-8").read().replace("'./reportText'", "'./reportText.mjs'")
            open(os.path.join(work, f"{name}.mjs"), "w", encoding="utf-8").write(source)
        script = (f"import {{ shortReport }} from '{work}/reportView.mjs'; import fs from 'fs';\n"
                  "const t = fs.readFileSync(0, 'utf8'); console.log(JSON.stringify(shortReport(t)));")
        out = subprocess.run(["node", "--input-type=module", "-e", script], input=text, capture_output=True, text=True, timeout=60)
        if out.returncode != 0:
            raise AssertionError(out.stderr)
        return json.loads(out.stdout)
    finally:
        shutil.rmtree(work, ignore_errors=True)


def fixture(name):
    with open(os.path.join(FIX, name), encoding="utf-8") as f:
        return f.read()


NATAL = ("live_2026_10_18_final_natal_single_A.txt", "live_2026_10_18_final_natal_pair_A_IVAN_love.txt", "live_2026_10_20_phase32_career_natal_A.txt",
         "live_2026_10_20_phase32_karmic_pair_A_IVAN.txt", "live_2026_10_20_phase32_love_pair_A_IVAN.txt", "live_2026_10_19_phase31_health_natal_A.txt")
PERIOD = ("live_2026_10_18_final_period_single_A.txt", "live_2026_10_18_final_period_pair_A_IVAN.txt", "live_2026_10_20_phase32_health_period_A.txt",
          "live_2026_10_19_phase31_karmic_period_A.txt")


@unittest.skipUnless(shutil.which("node"), "Node.js не е наличен")
class ShortReportTest(unittest.TestCase):
    def test_a_natal_report_keeps_only_the_closing_blocks(self):
        for name in NATAL:
            text = fixture(name)
            result = short_of(text)
            self.assertTrue(result["shortened"], name)
            self.assertNotIn("\n### ", "\n" + result["text"], name)                      # без карти
            for heading in ("## Обща картина", "## Заключение"):
                self.assertIn(heading, result["text"], (name, heading))
            self.assertLess(len(result["text"]), len(text) * 0.7, name)
            closing = text[text.index("## Обща картина"):]
            for line in [l for l in closing.splitlines() if l.strip() and not l.startswith("<p")][:40]:
                self.assertIn(line.strip().lstrip("-• ").strip()[:40], result["text"].replace("**", ""), (name, line[:60]))

    def test_a_snapshot_keeps_the_closing_blocks_and_drops_the_transit_cards(self):
        for name in ("live_2026_10_18_final_single_snapshot_A.txt", ):
            result = short_of(fixture(name))
            self.assertTrue(result["shortened"])
            self.assertNotIn("Транзитното", result["text"].split("## Обща картина")[0])
            self.assertIn("## Заключение", result["text"])

    def test_a_period_keeps_the_overview_and_each_monthly_advice_without_the_cards(self):
        for name in PERIOD:
            text = fixture(name)
            result = short_of(text)
            self.assertTrue(result["shortened"], name)
            self.assertIn("Общ преглед на периода", result["text"], name)
            self.assertIn("### Заключение", result["text"], name)
            months = result["text"].count("## 📅")
            self.assertGreaterEqual(months, 1, name)
            self.assertEqual(result["text"].count("### Съвет за месеца"), months, name)
            self.assertNotIn("Интервал на действие", result["text"], name)
            self.assertLess(len(result["text"]), len(text) * 0.75, name)

    def test_the_health_notes_stay_at_the_very_end(self):
        for name in ("live_2026_10_20_phase32_health_period_A.txt", "live_2026_10_19_phase31_health_natal_A.txt"):
            result = short_of(fixture(name))
            self.assertTrue(result["text"].rstrip().endswith("лекар.") or "астрологията описват склонности" in result["text"].splitlines()[-2] + result["text"].splitlines()[-1] or "склонности" in result["text"][-600:], name)
            self.assertIn("Този анализ е за себепознание", result["text"], name)

    def test_an_unknown_format_is_returned_unchanged(self):
        for text in ("", "просто текст без заглавия", "## Нещо друго\nтекст\n\n### карта\nтекст"):
            result = short_of(text)
            self.assertFalse(result["shortened"])
            self.assertEqual(result["text"], text)

    def test_html_saved_content_is_understood_too(self):
        html = "<h2>Карта</h2><h3>Карта 1</h3><p>Текст на картата, дълъг достатъчно, за да е разликата по-голяма от границата за скъсяване на текста.</p>" \
               "<h2>Обща картина</h2><p>Обобщение.</p><h2>Заключение</h2><p>Край.</p>"
        result = short_of(html)
        self.assertTrue(result["shortened"])
        self.assertIn("Обобщение", result["text"])
        self.assertNotIn("Карта 1", result["text"])


class ReportViewSettingTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.client = TestClient(main.app)
        cls.h = register_and_login(cls.client, "view-set@test.bg")

    def test_the_default_is_short_and_the_choice_is_saved_and_validated(self):
        self.assertEqual(self.client.get("/me", headers=self.h).json()["report_view"], "short")
        r = self.client.patch("/me", json={"report_view": "full"}, headers=self.h)
        self.assertEqual((r.status_code, r.json()["report_view"]), (200, "full"))
        self.assertEqual(self.client.get("/me", headers=self.h).json()["report_view"], "full")
        self.assertEqual(self.client.patch("/me", json={"report_view": "huge"}, headers=self.h).status_code, 400)
        self.assertEqual(self.client.get("/me", headers=self.h).json()["report_view"], "full")
        self.assertEqual(self.client.patch("/me", json={"report_view": "short"}, headers=self.h).json()["report_view"], "short")


class ScreensTextTest(unittest.TestCase):
    def read(self, *parts):
        with open(os.path.join(SRC, *parts), encoding="utf-8") as f:
            return f.read()

    def test_history_and_the_result_use_the_viewer_and_the_export_says_full(self):
        for page in ("History.jsx", "GenerateReport.jsx"):
            text = self.read("pages", page)
            self.assertIn("ReportViewer", text, page)
            self.assertNotIn("<ReportText", text, page)
        self.assertIn("Изтегли пълния (DOCX)", self.read("components", "ReportExport.jsx"))
        self.assertIn("Покажи пълния анализ", self.read("components", "ReportViewer.jsx"))
        self.assertIn("report_view", self.read("pages", "Settings.jsx"))


if __name__ == "__main__":
    unittest.main()
