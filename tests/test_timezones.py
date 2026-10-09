"""
Часови зони по координати (Фаза 8).

timezonefinder 8.0.x/8.1.x върна слети зони (София като Europe/Athens, Виена като Europe/Paris). За днешни дати
разликата не личи, но при исторически дати часът излиза с 1 час разлика и картата (Асцендент, домове, Луна) е друга.
Очакваните часове са от независимия zoneinfo, не от кода на двигателя.

Пускане: python -m unittest discover -s tests -p "test_timezones.py"
"""
import unittest
from datetime import datetime
from zoneinfo import ZoneInfo

import testenv  # noqa: F401
import engine  # noqa: E402

BULGARIAN_CITIES = {
    "София": (42.6977, 23.3219), "Пловдив": (42.1354, 24.7453), "Варна": (43.2141, 27.9147),
    "Бургас": (42.5048, 27.4626), "Русе": (43.8356, 25.9657), "Благоевград": (42.0119, 23.0897),
    "Видин": (43.9962, 22.8679), "Кърджали": (41.6338, 25.3777), "Смолян": (41.5774, 24.7120),
    "Петрич": (41.39846, 23.20702), "Добрич": (43.5726, 27.8273), "Силистра": (44.1147, 27.2672),
}


def expected_utc(local_iso, zone):
    local = datetime.fromisoformat(local_iso).replace(tzinfo=ZoneInfo(zone))
    return local.astimezone(ZoneInfo("UTC")).replace(tzinfo=None)


class TimezoneLookupTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.eng = engine.AstrologyEngine()

    def zone_of(self, lat, lon):
        return self.eng.tf.timezone_at(lat=lat, lng=lon)

    def test_every_bulgarian_city_is_europe_sofia(self):
        for name, (lat, lon) in BULGARIAN_CITIES.items():
            with self.subTest(city=name):
                self.assertEqual(self.zone_of(lat, lon), "Europe/Sofia")

    def test_neighbouring_and_world_places_keep_their_own_zone(self):
        expected = {
            "Виена": ((48.2082, 16.3738), "Europe/Vienna"), "Берлин": ((52.52, 13.405), "Europe/Berlin"),
            "Варшава": ((52.2297, 21.0122), "Europe/Warsaw"), "Букурещ": ((44.4268, 26.1025), "Europe/Bucharest"),
            "Атина": ((37.9838, 23.7275), "Europe/Athens"), "Лондон": ((51.5074, -0.1278), "Europe/London"),
            "Делхи": ((28.6139, 77.209), "Asia/Kolkata"),
        }
        for name, ((lat, lon), zone) in expected.items():
            with self.subTest(place=name):
                self.assertEqual(self.zone_of(lat, lon), zone)

    def test_historical_hours_match_independent_zoneinfo(self):
        cases = [
            # Лято 1975: в България няма лятно часово време (Атина има), затова София е UTC+2
            ("1975-07-01T12:00:00", "Europe/Sofia", BULGARIAN_CITIES["София"]),
            ("1990-02-15T13:00:00", "Europe/Sofia", BULGARIAN_CITIES["София"]),
            ("1985-07-12T18:45:00", "Europe/Vienna", (48.2082, 16.3738)),
            # Лято 1977: Австрия още няма лятно време (Франция има), затова Виена е UTC+1
            ("1977-07-01T12:00:00", "Europe/Vienna", (48.2082, 16.3738)),
        ]
        for local_iso, zone, (lat, lon) in cases:
            with self.subTest(local=local_iso, zone=zone):
                date, time = local_iso.split("T")
                utc, zone_name = self.eng._datetime_to_utc(date, time[:5], lat, lon)
                self.assertEqual(zone_name, zone)
                self.assertEqual(utc.replace(tzinfo=None), expected_utc(local_iso, zone))

    def test_chart_reports_the_specific_zone(self):
        chart = self.eng.calculate_chart(date="1975-07-01", time="12:00", lat=42.6977, lon=23.3219)
        self.assertEqual(chart["timezone"], "Europe/Sofia")
        self.assertTrue(chart["datetime_utc"].startswith("1975-07-01T10:00"))


if __name__ == "__main__":
    unittest.main()
