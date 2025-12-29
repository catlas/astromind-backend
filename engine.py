"""
Астрологичен двигател с използване на Swiss Ephemeris
Извършва изчисления на планетарни позиции и домове
"""

import os
import swisseph as swe  # type: ignore
from datetime import datetime, timezone, timedelta
from typing import Dict, Tuple, Optional, List
from pathlib import Path
from timezonefinder import TimezoneFinder  # type: ignore
import pytz


class AstrologyEngine:
    """Основен клас за астрологични изчисления"""
    
    # Константи за планетите
    PLANETS = {
        "Sun": swe.SUN,
        "Moon": swe.MOON,
        "Mercury": swe.MERCURY,
        "Venus": swe.VENUS,
        "Mars": swe.MARS,
        "Jupiter": swe.JUPITER,
        "Saturn": swe.SATURN,
        "Uranus": swe.URANUS,
        "Neptune": swe.NEPTUNE,
        "Pluto": swe.PLUTO,
        "Node": swe.TRUE_NODE,  # True Node (Mean Node = swe.MEAN_NODE)
        "Chiron": swe.CHIRON
    }
    
    # Флагове за изчисления
    CALC_FLAGS = swe.FLG_SWIEPH | swe.FLG_SPEED
    
    def __init__(self, base_dir: Path = None):
        """
        Инициализация на астрологичния двигател.
        
        Args:
            base_dir: Базова директория на проекта (по подразбиране: родител на engine.py)
        """
        if base_dir is None:
            base_dir = Path(__file__).parent
        
        ephe_path = str(base_dir / "ephe")
        
        # Задаване на пътя към ефемеридите
        swe.set_ephe_path(ephe_path)
        
        # Проверка дали директорията съществува
        if not os.path.exists(ephe_path):
            raise FileNotFoundError(
                f"Директорията с ефемериди не е намерена: {ephe_path}\n"
                f"Моля изпълнете scripts/download_ephe.py първо."
            )
        
        self.base_dir = base_dir
        self.ephe_path = ephe_path
        
        # Инициализация на TimezoneFinder (тежък обект, зарежда се веднъж)
        self.tf = TimezoneFinder()
    
    def _datetime_to_utc(self, date: str, time: str, lat: float, lon: float) -> Tuple[datetime, str]:
        """
        Конвертира локална дата и час в UTC базирано на географски координати.
        
        Args:
            date: Дата във формат "YYYY-MM-DD" или "YYYY/MM/DD"
            time: Час във формат "HH:MM:SS" или "HH:MM" (локално време)
            lat: Географска ширина в градуси
            lon: Географска дължина в градуси
            
        Returns:
            Tuple от (datetime обект в UTC, timezone string)
        """
        # Нормализиране на формата на датата
        date_clean = date.replace("/", "-")
        
        # Парсиране на датата
        date_parts = date_clean.split("-")
        year, month, day = int(date_parts[0]), int(date_parts[1]), int(date_parts[2])
        
        # Парсиране на времето
        time_parts = time.split(":")
        hour = int(time_parts[0])
        minute = int(time_parts[1]) if len(time_parts) > 1 else 0
        second = int(time_parts[2]) if len(time_parts) > 2 else 0
        
        # Намиране на timezone от координатите
        timezone_str = self.tf.timezone_at(lat=lat, lng=lon)
        
        if timezone_str is None:
            # Fallback: използваме UTC ако не можем да намерим timezone
            timezone_str = "UTC"
            tz = pytz.UTC
        else:
            # Получаване на pytz timezone обект
            tz = pytz.timezone(timezone_str)
        
        # Създаване на локален datetime (naive)
        local_dt_naive = datetime(year, month, day, hour, minute, second)
        
        # 🔥 СПЕЦИАЛНО ПРАВИЛО ЗА БЪЛГАРИЯ ПРЕДИ 1979
        # В България смяната на времето (лятно/зимно) е въведена за първи път на 1 април 1979 г.
        # Преди това няма лятно часово време, затова използваме фиксиран UTC+2 (EET)
        if timezone_str == "Europe/Sofia" and year < 1979:
            # През този период НЯМА лятно часово време
            # Използваме фиксиран UTC+2 (EET)
            tz = pytz.FixedOffset(120)  # 120 минути = +2 часа
            local_dt = tz.localize(local_dt_naive)
        else:
            # За останалите случаи — стандартно локализиране (правилно обработва DST и исторически промени)
            local_dt = tz.localize(local_dt_naive)
        
        # Конвертиране в UTC
        utc_dt = local_dt.astimezone(pytz.UTC)
        
        return utc_dt, timezone_str
    
    @staticmethod
    def get_house_ruler(sign: str) -> Optional[str]:
        """
        Връща управителя на даден зодиакален знак.
        
        Args:
            sign: Име на зодиакалния знак (Aries, Taurus, etc.)
            
        Returns:
            Име на планетата-управител или None ако знакът е невалиден
        """
        sign_rulers = {
            "Aries": "Mars",
            "Taurus": "Venus",
            "Gemini": "Mercury",
            "Cancer": "Moon",
            "Leo": "Sun",
            "Virgo": "Mercury",
            "Libra": "Venus",
            "Scorpio": "Pluto",  # Modern ruler (traditional: Mars)
            "Sagittarius": "Jupiter",
            "Capricorn": "Saturn",
            "Aquarius": "Uranus",  # Modern ruler (traditional: Saturn)
            "Pisces": "Neptune"  # Modern ruler (traditional: Jupiter)
        }
        return sign_rulers.get(sign)
    
    def get_house_rulers(self, houses_dict: Dict[str, float]) -> Dict[str, str]:
        """
        Изчислява управителите на всички домове базирано на знака на cusp-а на всеки дом.
        
        Args:
            houses_dict: Речник с домове (напр. {"House1": 315.5, "House2": 345.2, ...})
            
        Returns:
            Речник с управители на домове (напр. {"house_1_ruler": "Mars", "house_2_ruler": "Venus", ...})
        """
        house_rulers = {}
        
        for house_name, cusp_longitude in houses_dict.items():
            # Извличане на номера на дома (House1 -> 1, House2 -> 2, etc.)
            house_number = int(house_name.replace("House", ""))
            
            # Определяне на знака на cusp-а
            dms_data = self._decimal_to_dms(cusp_longitude)
            sign = dms_data.get("sign")
            
            # Намиране на управителя
            if sign:
                ruler = self.get_house_ruler(sign)
                if ruler:
                    house_rulers[f"house_{house_number}_ruler"] = ruler
        
        return house_rulers
    
    def get_house_ruler_from_cusp(self, house_cusp_longitude: float) -> Tuple[Optional[str], Optional[str]]:
        """
        Определя знака на cusp на дома и връща управителя на този дом.
        
        Args:
            house_cusp_longitude: Дължината на cusp-а в градуси (0-360)
            
        Returns:
            Tuple от (sign, ruler) или (None, None) ако не е намерен
        """
        dms_data = self._decimal_to_dms(house_cusp_longitude)
        sign = dms_data.get("sign")
        ruler = self.get_house_ruler(sign) if sign else None
        return (sign, ruler)
    
    def map_planet_to_natal_house(
        self,
        planet_longitude: float,
        natal_house_cusps: Dict[str, float]
    ) -> int:
        """
        Maps a planet (given by longitude in degrees [0, 360)) to the correct natal house
        using Placidus house cusps.
        
        This method correctly handles:
        - Uneven house sizes in Placidus system
        - Wrap-around cases (e.g., ASC = 355°, planet = 5° → House 1)
        - Cases where houses span multiple signs
        
        Args:
            planet_longitude: Planet's ecliptic longitude in degrees (0–360)
            natal_house_cusps: Dict like {"House1": 82.5, "House2": 112.3, ..., "House12": 350.1}
        
        Returns:
            House number (1–12)
        """
        # Use existing _get_planet_house method which already implements correct logic
        house_number = self._get_planet_house(planet_longitude, natal_house_cusps)
        if house_number is None:
            # Fallback: if for some reason None is returned, default to House 1
            return 1
        return house_number
    
    def calculate_synastry_house_overlays(
        self,
        user_natal_chart: Dict,
        partner_planets: Dict[str, Dict]
    ) -> Dict[str, int]:
        """
        Calculates which of USER'S natal houses each of PARTNER'S planets falls into.
        
        This is used for synastry analysis - placing partner's planets into user's house system.
        The calculation is done using the exact Placidus house cusps from user's natal chart.
        
        Args:
            user_natal_chart: Full natal chart dict of the user (must contain "houses")
            partner_planets: Dict like {"Sun": {"longitude": 123.45, ...}, "Moon": {...}, ...}
        
        Returns:
            Dict like {"Sun": 7, "Moon": 4, "Venus": 1, ...} mapping partner planets to user's houses
        """
        user_houses = user_natal_chart.get("houses", {})
        if not user_houses:
            raise ValueError("User natal chart missing 'houses' data")
        
        overlays = {}
        for planet_name, planet_data in partner_planets.items():
            longitude = planet_data.get("longitude")
            if longitude is None:
                continue  # Skip if no position
            house_num = self.map_planet_to_natal_house(longitude, user_houses)
            overlays[planet_name] = house_num
        return overlays
    
    def map_transit_planets_to_natal_houses(
        self,
        user_natal_chart: Dict,
        transit_planets: Dict[str, Dict]
    ) -> Dict[str, int]:
        """
        Maps transit planets into USER'S natal houses.
        
        This is used for transit analysis - placing current/future planetary positions
        into the user's natal house system for interpretation.
        
        Args:
            user_natal_chart: User's natal chart (must contain "houses")
            transit_planets: Transit chart planetary data (must contain "longitude" for each planet)
        
        Returns:
            Dict like {"Jupiter": 12, "Saturn": 4, "Mars": 9, ...} mapping transit planets to natal houses
        """
        return self.calculate_synastry_house_overlays(user_natal_chart, transit_planets)
    
    def _decimal_to_dms(self, longitude: float) -> Dict[str, any]:
        """
        Конвертира десетична дължина (0-360) в Zodiac Sign, Degrees и Minutes.
        
        Args:
            longitude: Дължина в градуси (0-360)
            
        Returns:
            Речник с:
            - sign: Име на зодиакалния знак
            - deg: Градуси в знака (0-29)
            - min: Минути (0-59)
            - str: Форматиран string "Sign deg°min'"
        """
        # Нормализиране на дължината в диапазона 0-360
        while longitude < 0:
            longitude += 360
        while longitude >= 360:
            longitude -= 360
        
        # Определяне на зодиакалния знак (всеки знак е 30 градуса)
        sign_index = int(longitude / 30)
        degrees_in_sign = longitude % 30
        
        # Списък с зодиакални знаци
        signs = [
            "Aries", "Taurus", "Gemini", "Cancer",
            "Leo", "Virgo", "Libra", "Scorpio",
            "Sagittarius", "Capricorn", "Aquarius", "Pisces"
        ]
        
        sign = signs[sign_index]
        
        # Извличане на градуси и минути
        deg = int(degrees_in_sign)
        minutes_decimal = (degrees_in_sign - deg) * 60
        min = int(round(minutes_decimal))
        
        # Корекция ако минутите са 60
        if min >= 60:
            min = 0
            deg += 1
            if deg >= 30:
                deg = 0
                sign_index = (sign_index + 1) % 12
                sign = signs[sign_index]
        
        # Форматиране на string
        formatted = f"{sign} {deg}°{min:02d}'"
        
        return {
            "sign": sign,
            "deg": deg,
            "min": min,
            "str": formatted
        }
    
    def _datetime_to_julian_day(self, dt: datetime) -> float:
        """
        Конвертира datetime в Julian Day.
        FIX: Подаваме часа като отделен аргумент (decimal_hour), 
        защото swe.julday изисква денят да е INT.
        """
        # Извличане на компонентите
        year = dt.year
        month = dt.month
        day = dt.day
        hour = dt.hour
        minute = dt.minute
        second = dt.second
        
        # Конвертиране на часа в десетично число (напр. 12:30 става 12.5)
        decimal_hour = hour + (minute / 60.0) + (second / 3600.0)
        
        # Изчисляване на Julian Day
        # Синтаксис: swe.julday(year, month, day, hour_float, flag)
        jd = swe.julday(year, month, day, decimal_hour, swe.GREG_CAL)
        
        return jd

    def _calculate_planet_position(self, jd: float, planet_id: int) -> Tuple[float, float, float]:
        """
        Изчислява позицията на планета.
        FIX: Pyswisseph връща (списък_данни, флаг), а не обратното.
        """
        try:
            # result е tuple: (списък_с_координати, статус_флаг)
            result = swe.calc_ut(jd, planet_id, self.CALC_FLAGS)
            
            # Данните са на позиция 0 (xx), Флагът е на позиция 1
            xx = result[0]
            ret_flag = result[1]
            
            if ret_flag < 0:
                raise RuntimeError(f"Грешка от swisseph (flag {ret_flag})")
            
            # xx структура: [longitude, latitude, distance, speed_long, speed_lat, speed_dist]
            longitude = xx[0]
            speed = xx[3]
            distance = xx[2]
            
            return longitude, speed, distance
            
        except Exception as e:
            raise RuntimeError(f"Грешка при изчисляване на планета {planet_id}: {e}")

    
    def _get_planet_house(self, planet_longitude: float, house_cusps: Dict[str, float]) -> Optional[int]:
        """
        Определя в кой дом е планетата базирано на нейната дължина и cusp-овете на домовете.
        
        Алгоритъм за Placidus система:
        - Планетата е в дом X ако нейната дължина е >= cusp(X) и < cusp(X+1)
        - Обработваме wrap-around когато домовете преминават през 360°->0°
        - Използваме оригиналния ред на домовете (House1 до House12)
        
        Args:
            planet_longitude: Дължината на планетата в градуси (0-360)
            house_cusps: Речник с домове {"House1": 104.5, "House2": 135.2, ...}
        
        Returns:
            Номер на дома (1-12) или None ако не може да се определи
        """
        if not house_cusps or planet_longitude is None:
            return None
        
        # Нормализиране на planet_longitude (0-360)
        planet_lon = planet_longitude % 360.0
        
        # Създаване на списък от домове в правилния ред (House1 до House12)
        house_cusp_list = []
        for i in range(1, 13):
            house_name = f"House{i}"
            if house_name in house_cusps:
                cusp = house_cusps[house_name] % 360.0
                house_cusp_list.append((i, cusp))
        
        if not house_cusp_list:
            return None
        
        # Проверка за всеки дом в последователен ред
        for i in range(len(house_cusp_list)):
            current_house_num, current_cusp = house_cusp_list[i]
            next_house_num, next_cusp = house_cusp_list[(i + 1) % len(house_cusp_list)]
            
            # Обработка на wrap-around (напр. House12=352°, House1=14°)
            if next_cusp < current_cusp:
                # Wrap-around case: домът обхваща диапазона от current_cusp до 360° и от 0° до next_cusp
                if planet_lon >= current_cusp or planet_lon < next_cusp:
                    return current_house_num
            else:
                # Normal case: домът обхваща диапазона от current_cusp до next_cusp
                if current_cusp <= planet_lon < next_cusp:
                    return current_house_num
        
        # Fallback: ако не сме намерили (не би трябвало да се случи), връщаме дома с най-близкия cusp
        closest_house = min(house_cusp_list, key=lambda x: min(
            abs(x[1] - planet_lon),
            abs((x[1] + 360) - planet_lon),
            abs(x[1] - (planet_lon + 360))
        ))
        return closest_house[0]
    
    def _calculate_houses(self, jd: float, lat: float, lon: float) -> Dict:
        """
        Изчислява домовете по системата Placidus.
        FIX: Автоматично засичане дали масивът е с 12 или 13 елемента.
        """
        try:
            # swe.houses връща tuple (cusps, ascmc)
            result = swe.houses(jd, lat, lon, b'P')
            
            cusps = result[0]
            ascmc = result[1]
            
            houses = {}
            
            # ВАЖНО: Проверка на дължината, за да избегнем IndexError
            if len(cusps) == 12:
                # Ако са 12, индексите са 0..11
                for i in range(12):
                    houses[f"House{i+1}"] = cusps[i]
            elif len(cusps) >= 13:
                # Ако са 13, индекс 0 се пропуска, ползваме 1..12
                for i in range(1, 13):
                    houses[f"House{i}"] = cusps[i]
            else:
                # Fallback за всеки случай
                for i, cusp in enumerate(cusps):
                    houses[f"House{i+1}"] = cusp

            # Форматиране на ASC и MC
            mc_raw = ascmc[1]
            asc_raw = ascmc[0]
            asc_dms = self._decimal_to_dms(asc_raw)
            mc_dms = self._decimal_to_dms(mc_raw)
            
            return {
                "houses": houses,
                "Ascendant": asc_raw,  # ASC raw value
                "MC": mc_raw,          # MC raw value
                "Ascendant_formatted": asc_dms["str"],
                "MC_formatted": mc_dms["str"],
                "Ascendant_sign": asc_dms["sign"],
                "MC_sign": mc_dms["sign"]
            }
            
        except Exception as e:
            # Принтираме детайли за дебъг, ако пак гръмне
            print(f"DEBUG info: cusps type: {type(result[0])}, len: {len(result[0])}")
            raise RuntimeError(f"Грешка при изчисляване на домове: {e}")
    
    def calculate_chart(
        self,
        date: str,
        time: str,
        lat: float,
        lon: float
    ) -> Dict:
        """
        Изчислява пълна натална карта.
        
        Args:
            date: Дата във формат "YYYY-MM-DD" или "YYYY/MM/DD" (локална дата)
            time: Час във формат "HH:MM:SS" или "HH:MM" (локално време)
            lat: Географска ширина в градуси (-90 до 90)
            lon: Географска дължина в градуси (-180 до 180, източна положителна)
        
        Returns:
            Речник със структурирани данни за картата:
            {
                "planets": {...},
                "houses": {...},
                "angles": {...},
                "julian_day": ...,
                "datetime_utc": "...",
                "timezone": "Europe/Sofia",
                "datetime_local": "..."
            }
        """
        # Конвертиране на локалното време в UTC базирано на координатите
        dt_utc, timezone_str = self._datetime_to_utc(date, time, lat, lon)
        
        # Конвертиране в Julian Day
        jd = self._datetime_to_julian_day(dt_utc)
        
        # Изчисляване на позициите на планетите
        planets = {}
        for name, planet_id in self.PLANETS.items():
            try:
                longitude, speed, distance = self._calculate_planet_position(jd, planet_id)
                
                # Форматиране на позицията (Zodiac Sign + Degrees/Minutes)
                if longitude is not None:
                    dms_data = self._decimal_to_dms(longitude)
                    planets[name] = {
                        "longitude": longitude,
                        "speed": speed,
                        "distance": distance,
                        "zodiac_sign": dms_data["sign"],
                        "formatted_pos": dms_data["str"]
                    }
                else:
                    planets[name] = {
                        "longitude": None,
                        "speed": None,
                        "distance": None,
                        "zodiac_sign": None,
                        "formatted_pos": None
                    }
            except Exception as e:
                print(f"Предупреждение: {e}")
                planets[name] = {
                    "longitude": None,
                    "speed": None,
                    "distance": None,
                    "zodiac_sign": None,
                    "formatted_pos": None
                }
        
        # Изчисляване на домовете
        house_data = self._calculate_houses(jd, lat, lon)
        house_cusps = house_data["houses"]
        
        # Изчисляване на дома за всяка планета
        for name in planets:
            if planets[name].get("longitude") is not None:
                planet_longitude = planets[name]["longitude"]
                house_number = self._get_planet_house(planet_longitude, house_cusps)
                planets[name]["house"] = house_number
        
        # Съставяне на резултата
        result = {
            "planets": planets,
            "houses": house_cusps,
            "angles": {
                "Ascendant": house_data["Ascendant"],
                "MC": house_data["MC"],
                "Ascendant_formatted": house_data.get("Ascendant_formatted", ""),
                "MC_formatted": house_data.get("MC_formatted", ""),
                "Ascendant_sign": house_data.get("Ascendant_sign", ""),
                "MC_sign": house_data.get("MC_sign", "")
            },
            "julian_day": jd,
            "datetime_utc": dt_utc.isoformat(),
            "timezone": timezone_str,
            "datetime_local": f"{date} {time}",
            "location": {
                "latitude": lat,
                "longitude": lon
            }
        }
        
        return result


# Функция за удобство (не изисква инстанция)
def calculate_chart(
    date: str,
    time: str,
    lat: float,
    lon: float,
    base_dir: Path = None
) -> Dict:
    """
    Удобна функция за изчисляване на натална карта.
    
    Args:
        date: Дата във формат "YYYY-MM-DD" (локална дата)
        time: Час във формат "HH:MM:SS" (локално време)
        lat: Географска ширина в градуси
        lon: Географска дължина в градуси
        base_dir: Базова директория (опционално)
        
    Returns:
        Речник с данни за картата
    """
    engine = AstrologyEngine(base_dir)
    return engine.calculate_chart(date, time, lat, lon)


if __name__ == "__main__":
    # Тест пример
    print("Тест на астрологичния двигател...")
    
    try:
        engine = AstrologyEngine()
        
        # Пример: Натална карта
        result = engine.calculate_chart(
            date="1990-01-01",
            time="12:00:00",
            lat=42.6977,  # София
            lon=23.3219
        )
        
        print("\n" + "=" * 60)
        print("Резултати от изчислението:")
        print("=" * 60)
        print(f"Julian Day: {result['julian_day']:.6f}")
        print(f"Local DateTime: {result['datetime_local']}")
        print(f"Timezone: {result['timezone']}")
        print(f"UTC DateTime: {result['datetime_utc']}")
        print("\nПланети:")
        for planet, data in result['planets'].items():
            if data['longitude'] is not None:
                print(f"  {planet}: {data['longitude']:.6f}° (скорост: {data['speed']:.6f}°/ден)")
        
        print("\nДомове:")
        for house, cusp in result['houses'].items():
            print(f"  {house}: {cusp:.6f}°")
        
        print("\nЪгли:")
        print(f"  Ascendant: {result['angles']['Ascendant']:.6f}°")
        print(f"  MC: {result['angles']['MC']:.6f}°")
        
    except FileNotFoundError as e:
        print(f"Грешка: {e}")
        print("\nМоля изпълнете първо:")
        print("  python scripts/download_ephe.py")
    except Exception as e:
        print(f"Грешка: {e}")
        import traceback
        traceback.print_exc()

