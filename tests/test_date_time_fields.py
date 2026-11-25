"""Дата и час с цифри (25.11.2026): навсякъде вместо календара на телефона; навън същият формат YYYY-MM-DD и HH:MM."""
import json
import os
import shutil
import subprocess
import tempfile
import unittest

SRC = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), "src")


def run(script):
    work = tempfile.mkdtemp()
    try:
        source = open(os.path.join(SRC, "components", "DateTimeFields.jsx"), encoding="utf-8").read()
        body = source[source.index("const pad"):source.index("const pickerSupported")]           # само чистите функции, без React
        open(os.path.join(work, "f.mjs"), "w", encoding="utf-8").write(body.replace("export const", "const") +
              "\nexport { formatDateDigits, formatTimeDigits, parseBgDate, parseTime, isoToBg };\n")
        out = subprocess.run(["node", "--input-type=module", "-e", f"import * as F from '{work}/f.mjs';\n{script}"], capture_output=True, text=True, timeout=60)
        if out.returncode:
            raise AssertionError(out.stderr)
        return json.loads(out.stdout)
    finally:
        shutil.rmtree(work, ignore_errors=True)


@unittest.skipUnless(shutil.which("node"), "Node.js не е наличен")
class DigitsTest(unittest.TestCase):
    def test_digits_become_a_date_and_a_time(self):
        r = run("console.log(JSON.stringify([F.formatDateDigits('15021990'), F.formatDateDigits('150'), F.formatDateDigits('15.02.1990x'),"
                " F.formatTimeDigits('2015'), F.formatTimeDigits('7'), F.parseBgDate('15.02.1990').iso, F.parseTime('20:15').value]))")
        self.assertEqual(r, ["15.02.1990", "15.0", "15.02.1990", "20:15", "7", "1990-02-15", "20:15"])

    def test_impossible_or_out_of_range_values_are_refused(self):
        r = run("console.log(JSON.stringify([F.parseBgDate('30.02.2023'), F.parseBgDate('31.13.2000'), F.parseBgDate('15.02.17'),"
                " F.parseBgDate('01.01.2030', {max: '2026-11-25'}), F.parseBgDate('01.01.2020', {min: '2026-11-25'}), F.parseTime('24:00'), F.parseTime('12:60'),"
                " F.parseBgDate('29.02.2024').iso, F.isoToBg('2026-11-25')]))")
        self.assertEqual([x.get("error") for x in r[:5]], ["Няма такава дата.", "Няма такава дата.", None, "Датата трябва да е до 25.11.2026.",
                                                           "Датата трябва да е от 25.11.2026 нататък."])
        self.assertEqual(r[2]["iso"], "")                                         # непълна година: още няма дата
        self.assertTrue(r[5]["error"] and r[6]["error"])
        self.assertEqual(r[7:], ["2024-02-29", "25.11.2026"])


class EveryFieldTest(unittest.TestCase):
    def test_no_native_date_or_time_input_is_left(self):
        for root, _, files in os.walk(SRC):
            for name in files:
                if name.endswith(".jsx") and name != "DateTimeFields.jsx":
                    text = open(os.path.join(root, name), encoding="utf-8").read()
                    self.assertNotIn('type="date"', text, name)
                    self.assertNotIn('type="time"', text, name)
        counts = {}
        for rel in (("components", "BirthDataForm.jsx"), ("pages", "Profiles.jsx"), ("pages", "GenerateReport.jsx")):
            text = open(os.path.join(SRC, *rel), encoding="utf-8").read()
            counts[rel[-1]] = (text.count("<DateField"), text.count("<TimeField"))
        self.assertEqual(counts, {"BirthDataForm.jsx": (1, 1), "Profiles.jsx": (1, 1), "GenerateReport.jsx": (5, 3)})

    def test_the_fields_open_the_numeric_keyboard(self):
        text = open(os.path.join(SRC, "components", "DateTimeFields.jsx"), encoding="utf-8").read()
        self.assertEqual(text.count('inputMode="numeric"'), 2)
        self.assertIn("ДД.ММ.ГГГГ", text)
        self.assertIn("ЧЧ:ММ", text)


if __name__ == "__main__":
    unittest.main()
