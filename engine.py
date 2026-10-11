"""
Астрологичен двигател с използване на Swiss Ephemeris
Извършва изчисления на планетарни позиции и домове
"""

import os
import re
import swisseph as swe  # type: ignore
import threading
from datetime import datetime, timezone, timedelta
from typing import Dict, Tuple, Optional, List
from pathlib import Path
from timezonefinder import TimezoneFinder  # type: ignore
import pytz


def house_for_longitude(planet_longitude: float, house_cusps: Dict[str, float]) -> Optional[int]:
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


def decimal_to_dms(longitude: float) -> Dict[str, any]:
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
        if deg + 1 >= 30:
            min = 59                  # 29°59'30" и нагоре остава в същия знак: знакът (и управителят) са по суровата дължина
        else:
            min = 0
            deg += 1

    # Форматиране на string
    formatted = f"{sign} {deg}°{min:02d}'"

    return {
        "sign": sign,
        "deg": deg,
        "min": min,
        "str": formatted
    }


class TimeResolutionError(ValueError):
    """
    Местният час не се превръща в един момент: не съществува (часовникът е преместен напред при лятно време) или се
    повтаря (часовникът е върнат назад). code е time_nonexistent или time_ambiguous; options са възможните варианти.
    Подклас на ValueError, затова старите обработки на невалиден вход я хващат като 400.
    """

    def __init__(self, code: str, message: str, options: Optional[list] = None):
        super().__init__(message)
        self.code = code
        self.message = message
        self.options = options or []


# Часът, за който се смятат планетите, когато часът на раждане е неизвестен (местен пладне). Не е твърдение за часа.
UNKNOWN_TIME_ANCHOR = "12:00"


def utc_offset_label(minutes: int) -> str:
    sign = "+" if minutes >= 0 else "-"
    hours, rest = divmod(abs(int(minutes)), 60)
    return f"UTC{sign}{hours}" + (f":{rest:02d}" if rest else "")


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
        
        # Задаване на пътя към ефемеридите (за тази и, при нужда, за всяка следваща нишка: виж ensure_ephemeris)
        global _EPHE_PATH
        _EPHE_PATH = ephe_path
        _ephe_thread.path = None
        ensure_ephemeris()
        
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
    
    def resolve_local_time(self, date: str, time: str, lat: float, lon: float, fold: Optional[int] = None,
                           zone: Optional[str] = None) -> Dict:
        """
        Превръща местния час в UTC момент и описва зоната. Не хвърля грешка при несъществуващ или повтарящ се час, а ги
        връща като статус, за да ги покаже екранът:
        - status "ok": utc, timezone, utc_offset_minutes;
        - status "nonexistent": часът е прескочен при лятно време; next_valid_time е най-близкият валиден час след него;
        - status "ambiguous": часът се повтаря; options са двата момента (fold 0 е първото преминаване, fold 1 е второто);
          с избран fold (0 или 1) статусът е "ok" за избрания момент.
        zone_found е False, когато координатите нямат часова зона: тогава се ползва UTC. at_sea е True за точки в открито
        море (зона Etc/GMT±N), най-често грешка в координатите.
        zone (IANA, например "Europe/Vienna") замества зоната по координати: за прогнозния момент, който се задава в избрана
        зона, а не в зоната на мястото на раждане. Непозната зона е ValueError.
        """
        date_clean = date.replace("/", "-")
        year, month, day = (int(part) for part in date_clean.split("-")[:3])
        time_parts = time.split(":")
        hour = int(time_parts[0])
        minute = int(time_parts[1]) if len(time_parts) > 1 else 0
        second = int(time_parts[2]) if len(time_parts) > 2 else 0
        local_naive = datetime(year, month, day, hour, minute, second)

        if zone:
            if zone not in pytz.all_timezones_set:
                raise ValueError(f"Непозната часова зона: {zone}")
            timezone_str = zone
            zone_found = True
        else:
            timezone_str = self.tf.timezone_at(lat=lat, lng=lon)
            zone_found = timezone_str is not None
            if not zone_found:
                timezone_str = "UTC"       # няма зона за тези координати
        # timezonefinder връща Etc/GMT±N (фиксирано отместване) за точки в открито море: най-често грешка в координатите
        base = {"timezone": timezone_str, "zone_found": zone_found, "at_sea": timezone_str.startswith("Etc/GMT")}

        # ПРАВИЛО ЗА БЪЛГАРИЯ 1945-1979: от 2 април 1945 до въвеждането на лятното часово време през 1979 г. се ползва фиксиран
        # UTC+2 (EET). Само за този доказан период; преди него важи историческата зона от IANA (1942-1945 CET, преди 1894 местно
        # слънчево време), а не заменяне на всички ранни дати
        if timezone_str == "Europe/Sofia" and datetime(1945, 4, 2) <= local_naive < datetime(1979, 1, 1):
            fixed = pytz.FixedOffset(120).localize(local_naive)
            return {**base, "status": "ok", "utc": fixed.astimezone(pytz.UTC), "utc_offset_minutes": 120}

        tz = pytz.UTC if timezone_str == "UTC" else pytz.timezone(timezone_str)

        def described(local_dt) -> Dict:
            offset = int(local_dt.utcoffset().total_seconds() // 60)
            return {"utc": local_dt.astimezone(pytz.UTC), "utc_offset_minutes": offset}

        try:
            return {**base, "status": "ok", **described(tz.localize(local_naive, is_dst=None))}
        except pytz.exceptions.AmbiguousTimeError:
            first, second = sorted((tz.localize(local_naive, is_dst=True), tz.localize(local_naive, is_dst=False)),
                                   key=lambda dt: dt.astimezone(pytz.UTC))
            options = []
            for index, candidate in enumerate((first, second)):
                info = described(candidate)
                options.append({"fold": index, "utc_offset_minutes": info["utc_offset_minutes"],
                                "utc_offset": utc_offset_label(info["utc_offset_minutes"]),
                                "label": ("първото преминаване" if index == 0 else "второто преминаване")
                                         + f" ({utc_offset_label(info['utc_offset_minutes'])})"})
            if fold in (0, 1):
                return {**base, "status": "ok", **described((first, second)[fold]), "options": options, "fold": fold}
            return {**base, "status": "ambiguous", "options": options}
        except pytz.exceptions.NonExistentTimeError:
            next_valid = None
            for extra in range(1, 241):
                probe = local_naive + timedelta(minutes=extra)
                try:
                    tz.localize(probe, is_dst=None)
                except pytz.exceptions.NonExistentTimeError:
                    continue
                except pytz.exceptions.AmbiguousTimeError:
                    pass
                next_valid = probe.strftime("%H:%M")
                break
            return {**base, "status": "nonexistent", "next_valid_time": next_valid}

    def _datetime_to_utc(self, date: str, time: str, lat: float, lon: float, fold: Optional[int] = None,
                         strict: bool = False, zone: Optional[str] = None) -> Tuple[datetime, str]:
        """
        Конвертира локална дата и час в UTC базирано на географски координати.

        Args:
            date: Дата във формат "YYYY-MM-DD" или "YYYY/MM/DD"
            time: Час във формат "HH:MM:SS" или "HH:MM" (локално време)
            lat: Географска ширина в градуси
            lon: Географска дължина в градуси
            fold: 0 или 1 при повтарящ се час (първото или второто преминаване)
            strict: True за рождени данни. Несъществуващ или повтарящ се час без избран fold е грешка
                (TimeResolutionError), а не тихо поправяне. При False (транзити, "сега") се ползва зимното време.

        Returns:
            Tuple от (datetime обект в UTC, timezone string)
        """
        resolved = self.resolve_local_time(date, time, lat, lon, fold, zone=zone)
        if resolved["status"] == "ok":
            return resolved["utc"], resolved["timezone"]
        if strict:
            zone = resolved["timezone"]
            if resolved["status"] == "nonexistent":
                hint = f" Най-близкият валиден час е {resolved['next_valid_time']}." if resolved.get("next_valid_time") else ""
                raise TimeResolutionError(
                    "time_nonexistent",
                    f"Часът {time} на {date} не съществува в {zone}: тогава часовникът е преместен напред при смяната към "
                    f"лятно време.{hint}")
            raise TimeResolutionError(
                "time_ambiguous",
                f"Часът {time} на {date} се повтаря в {zone}: часовникът е върнат назад. Изберете първото или второто "
                f"преминаване.", resolved["options"])
        # Нестрого: старото поведение (зимно време за повтарящ се час; за несъществуващ час същият стенен час като зимно време)
        tz = pytz.UTC if resolved["timezone"] == "UTC" else pytz.timezone(resolved["timezone"])
        date_clean = date.replace("/", "-")
        year, month, day = (int(part) for part in date_clean.split("-")[:3])
        parts = time.split(":")
        naive = datetime(year, month, day, int(parts[0]), int(parts[1]) if len(parts) > 1 else 0,
                         int(parts[2]) if len(parts) > 2 else 0)
        return tz.localize(naive).astimezone(pytz.UTC), resolved["timezone"]

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
        """Знак, градуси и минути за дължина; логиката е в decimal_to_dms."""
        return decimal_to_dms(longitude)
    
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
            ensure_ephemeris()
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
        """Номер на дома (1-12) за дължина; логиката е в house_for_longitude."""
        return house_for_longitude(planet_longitude, house_cusps)
    
    def _calculate_houses(self, jd: float, lat: float, lon: float) -> Dict:
        """
        Изчислява домовете по системата Placidus.
        FIX: Автоматично засичане дали масивът е с 12 или 13 елемента.
        """
        # Над полярния кръг Placidus не е определена и Swiss Ephemeris хвърля грешка: тогава се ползва Porphyry и това се заявява в
        # картата (house_system), не се крие (R06, регресионен одит 11.10.2026)
        fallback = False
        try:
            try:
                result = swe.houses(jd, lat, lon, b'P')
            except swe.Error:
                result = swe.houses(jd, lat, lon, b'O')
                fallback = True
            
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
                "house_system": "porphyry_fallback" if fallback else "placidus",
                "houses": houses,
                "Ascendant": asc_raw,  # ASC raw value
                "MC": mc_raw,          # MC raw value
                "Ascendant_formatted": asc_dms["str"],
                "MC_formatted": mc_dms["str"],
                "Ascendant_sign": asc_dms["sign"],
                "MC_sign": mc_dms["sign"]
            }
            
        except Exception as e:
            raise RuntimeError(f"Грешка при изчисляване на домове: {e}")
    
    def _ephemeris_info(self, jd: float, planets: Dict[str, Dict], intentional=()) -> Dict:
        """Откъде са данните: swiss (файловете .se1) или moshier (резервен режим), и кои тела липсват (тих частичен успех е грешка)."""
        try:
            ensure_ephemeris()
            flag = swe.calc_ut(jd, swe.SUN, self.CALC_FLAGS)[1]
            source = "moshier" if flag & swe.FLG_MOSEPH else "swiss"
        except Exception:
            source = "unknown"
        return {"source": source, "missing": sorted(n for n, d in planets.items() if d.get("longitude") is None and n not in intentional)}

    def _planet_positions(self, jd: float) -> Dict[str, Dict]:
        """Позиции на всички тела за един момент: дължина, скорост, разстояние, знак и форматирана позиция."""
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
        return planets

    def _calculate_chart_without_time(self, date: str, lat: float, lon: float) -> Dict:
        """
        Карта без час на раждане. Часът не се измисля: планетите са за местния пладне на датата, а Асцендент, МС, домове и
        домът на всяка планета липсват. Луната се мести около 13° на ден, затова няма позиция (longitude е None и
        аспектите я пропускат), а sign_ranges казва в кои знаци е била през деня. Същото важи за всяка друга планета,
        която сменя знак през този ден. Дължините на останалите тела са за пладне (грешка най-много около 1°).
        """
        date_clean = date.replace("/", "-")
        noon_utc, timezone_str = self._datetime_to_utc(date_clean, UNKNOWN_TIME_ANCHOR, lat, lon)
        jd = self._datetime_to_julian_day(noon_utc)
        planets = self._planet_positions(jd)

        year, month, day = (int(part) for part in date_clean.split("-")[:3])
        next_day = (datetime(year, month, day) + timedelta(days=1)).strftime("%Y-%m-%d")
        start_utc, _ = self._datetime_to_utc(date_clean, "00:00", lat, lon)
        end_utc, _ = self._datetime_to_utc(next_day, "00:00", lat, lon)
        morning = self._planet_positions(self._datetime_to_julian_day(start_utc))
        evening = self._planet_positions(self._datetime_to_julian_day(end_utc))

        sign_ranges: Dict[str, Dict] = {}
        for name, noon_data in planets.items():
            first, last = morning[name].get("longitude"), evening[name].get("longitude")
            if first is None or last is None:
                continue
            first_dms, last_dms = decimal_to_dms(first), decimal_to_dms(last)
            signs = [first_dms["sign"]] + ([last_dms["sign"]] if last_dms["sign"] != first_dms["sign"] else [])
            if name == "Moon" or len(signs) > 1:
                sign_ranges[name] = {"signs": signs, "from": first_dms["str"], "to": last_dms["str"]}
        moon = planets.get("Moon")
        if moon:
            moon.update({"longitude": None, "speed": None, "distance": None, "zodiac_sign": None, "formatted_pos": None})
        for data in planets.values():
            data["house"] = None

        return {
            "planets": planets,
            "houses": {},
            "angles": {},
            "time_known": False,
            "ephemeris": self._ephemeris_info(jd, planets, intentional=set(sign_ranges)),   # Луната без час няма дължина нарочно
            "sign_ranges": sign_ranges,
            "julian_day": jd,
            "datetime_utc": noon_utc.isoformat(),
            "timezone": timezone_str,
            "datetime_local": f"{date} (час неизвестен)",
            "location": {"latitude": lat, "longitude": lon},
        }

    def calculate_chart(
        self,
        date: str,
        time: str,
        lat: float,
        lon: float,
        fold: Optional[int] = None,
        strict: bool = False,
        time_known: bool = True,
        zone: Optional[str] = None
    ) -> Dict:
        """
        Изчислява пълна натална карта.
        
        Args:
            date: Дата във формат "YYYY-MM-DD" или "YYYY/MM/DD" (локална дата)
            time: Час във формат "HH:MM:SS" или "HH:MM" (локално време)
            lat: Географска ширина в градуси (-90 до 90)
            lon: Географска дължина в градуси (-180 до 180, източна положителна)
            fold, strict: виж _datetime_to_utc (несъществуващ и повтарящ се местен час)
            time_known: False, когато часът на раждане е неизвестен. Тогава time се игнорира и картата няма домове,
                Асцендент и МС (виж _calculate_chart_without_time)
        
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
        if not time_known:
            return self._calculate_chart_without_time(date, lat, lon)

        # Конвертиране на локалното време в UTC базирано на координатите
        dt_utc, timezone_str = self._datetime_to_utc(date, time, lat, lon, fold=fold, strict=strict, zone=zone)
        
        # Конвертиране в Julian Day
        jd = self._datetime_to_julian_day(dt_utc)
        
        # Изчисляване на позициите на планетите
        planets = self._planet_positions(jd)
        
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
            "time_known": True,
            "house_system": house_data.get("house_system", "placidus"),
            "ephemeris": self._ephemeris_info(jd, planets),
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
            "utc_offset_minutes": self._offset_minutes(date, time, dt_utc),
            "location": {
                "latitude": lat,
                "longitude": lon
            }
        }
        
        return result

    @staticmethod
    def _offset_minutes(date: str, time: str, dt_utc: datetime) -> int:
        """Отместването на местния час спрямо UTC в минути (местният час минус UTC момента)."""
        year, month, day = (int(part) for part in date.replace("/", "-").split("-")[:3])
        parts = time.split(":")
        local = datetime(year, month, day, int(parts[0]), int(parts[1]) if len(parts) > 1 else 0,
                         int(parts[2]) if len(parts) > 2 else 0)
        return int(round((local - dt_utc.replace(tzinfo=None)).total_seconds() / 60.0))


_shared_engine: Optional["AstrologyEngine"] = None


_EPHE_PATH: Optional[str] = None
_ephe_thread = threading.local()


def ensure_ephemeris() -> None:
    """
    Swiss Ephemeris помни пътя до файловете за всяка нишка поотделно (на Linux). В нова работна нишка без него Хирон липсва, а
    изчисленията тихо минават към Moshier. Затова всяка входна точка (двигателят, скенерът, износът) го задава за текущата
    нишка; проверката е една стойност в thread-local, а не извикване при всяко изчисление.
    """
    if _EPHE_PATH and getattr(_ephe_thread, "path", None) != _EPHE_PATH:
        swe.set_ephe_path(_EPHE_PATH)
        _ephe_thread.path = _EPHE_PATH


EPHEMERIS_BODY_FILE = "sepl"          # основните планети; seas_* е за Хирон (виж ephemeris_files)


def ephemeris_files() -> Dict[str, int]:
    """Файловете .se1 в директорията с ефемериди и размерите им (за отпечатъка на източника на изчисленията)."""
    path = Path(_EPHE_PATH) if _EPHE_PATH else Path(__file__).resolve().parent / "ephe"
    return {f.name: f.stat().st_size for f in sorted(path.glob("*.se1"))} if path.exists() else {}


def ephemeris_fingerprint() -> str:
    """Кратък отпечатък на файловете с ефемериди (имена и размери): запазва се в снимката на отчета."""
    import hashlib
    files = ephemeris_files()
    return hashlib.sha256("|".join(f"{n}:{sz}" for n, sz in files.items()).encode("utf-8")).hexdigest()[:16]


def supported_year_range() -> Tuple[int, int]:
    """Поддържаният диапазон от години според файловете, които наистина са налични: sepl_18.se1 е 1800-2399, sepl_12 е 1200-1799.
    Файл за Хирон (seas_NN) трябва да покрива същите години; диапазонът е сечението на планетите и Хирон. (0, -1) без файлове."""
    def covered(prefix: str) -> set:
        years = set()
        for name in ephemeris_files():
            m = re.fullmatch(rf"{prefix}_(\d{{2}})\.se1", name)
            if m:
                start = int(m.group(1)) * 100
                years.update(range(start, start + 600))
        return years
    both = covered("sepl") & covered("seas")
    return (min(both), max(both)) if both else (0, -1)


def missing_bodies(chart: Optional[Dict]) -> List[str]:
    """Телата, които не са изчислени в картата (ephemeris.missing). Празно = пълна карта."""
    return list(((chart or {}).get("ephemeris") or {}).get("missing") or [])


def get_engine() -> "AstrologyEngine":
    """
    Общата инстанция на двигателя. Зареждането на двигателя взема над 0,6 секунди (TimezoneFinder), затова се прави веднъж, а не при
    всяко изчисление: картата с готова инстанция се смята за около милисекунда.
    """
    global _shared_engine
    if _shared_engine is None:
        _shared_engine = AstrologyEngine()
    ensure_ephemeris()
    return _shared_engine


# Функция за удобство (не изисква инстанция)
def calculate_chart(
    date: str,
    time: str,
    lat: float,
    lon: float,
    base_dir: Path = None,
    fold: Optional[int] = None,
    strict: bool = False,
    time_known: bool = True,
    zone: Optional[str] = None
) -> Dict:
    """
    Удобна функция за изчисляване на натална карта.
    
    Args:
        date: Дата във формат "YYYY-MM-DD" (локална дата)
        time: Час във формат "HH:MM:SS" (локално време)
        lat: Географска ширина в градуси
        lon: Географска дължина в градуси
        base_dir: Базова директория (опционално)
        fold, strict, time_known: виж AstrologyEngine.calculate_chart
        
    Returns:
        Речник с данни за картата
    """
    engine = AstrologyEngine(base_dir) if base_dir is not None else get_engine()
    return engine.calculate_chart(date, time, lat, lon, fold=fold, strict=strict, time_known=time_known, zone=zone)


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

