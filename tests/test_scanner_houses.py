"""
Регресионен тест: TransitScanner трябва да намира натален дом за позиция.

По-рано _find_house_for_position търсеше куспидите в chart["angles"]["houses"],
докато engine ги връща в chart["houses"], и всеки дом излизаше "Unknown".

Пускане: python -m unittest discover -s tests
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import engine  # noqa: E402
from scanner import TransitScanner  # noqa: E402


class FindHouseForPositionTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.chart = engine.calculate_chart(date="1990-05-15", time="14:30", lat=42.6977, lon=23.3219)
        cls.scanner = TransitScanner()

    def test_never_unknown_for_real_chart(self):
        for name, planet in self.chart["planets"].items():
            lon = planet.get("longitude")
            if lon is None:
                continue
            with self.subTest(planet=name):
                self.assertNotEqual(self.scanner._find_house_for_position(lon, self.chart), "Unknown")

    def test_matches_engine_house_placement(self):
        for name, planet in self.chart["planets"].items():
            lon, house = planet.get("longitude"), planet.get("house")
            if lon is None or house is None:
                continue
            with self.subTest(planet=name):
                self.assertEqual(self.scanner._find_house_for_position(lon, self.chart), str(house))


if __name__ == "__main__":
    unittest.main()
