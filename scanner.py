"""
Transit Scanner - Открива Retrogrades, Eclipses и Transits в период от време
"""

import swisseph as swe  # type: ignore
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Tuple
from pathlib import Path
from engine import AstrologyEngine


class TransitScanner:
    """Сканира период от време за астрологични събития"""
    
    # Планети, които могат да бъдат ретроградни (Mercury до Pluto)
    RETROGRADE_PLANETS = {
        "Mercury": swe.MERCURY,
        "Venus": swe.VENUS,
        "Mars": swe.MARS,
        "Jupiter": swe.JUPITER,
        "Saturn": swe.SATURN,
        "Uranus": swe.URANUS,
        "Neptune": swe.NEPTUNE,
        "Pluto": swe.PLUTO
    }
    
    # Планети за транзитен анализ (външни планети + Mars)
    TRANSIT_PLANETS = {
        "Mars": swe.MARS,
        "Jupiter": swe.JUPITER,
        "Saturn": swe.SATURN,
        "Uranus": swe.URANUS,
        "Neptune": swe.NEPTUNE,
        "Pluto": swe.PLUTO,
    }

    # Планети за INGRES анализ (всички основни движещи се тела)
    INGRESS_PLANETS = {
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
    }
    
    # Аспекти и техните ъгли
    ASPECTS = {
        "Conjunction": 0.0,
        "Sextile": 60.0,
        "Square": 90.0,
        "Trine": 120.0,
        "Opposition": 180.0,
    }
    # Максимални орбиси за транзити:
    # - Приближаващи (applying): 1.5°
    # - Отделящи се (separating): 1.0°
    MAX_ORB_APPLYING = 1.5
    MAX_ORB_SEPARATING = 1.0
    
    def __init__(self, base_dir: Path = None):
        """Инициализация на scanner"""
        self.engine = AstrologyEngine(base_dir)
        # Съхраняваме предишните скорости за откриване на ретрогради
        self.prev_speeds: Dict[str, float] = {}
    
    def _datetime_to_jd(self, dt: datetime) -> float:
        """Конвертира datetime в Julian Day"""
        year = int(dt.year)
        month = int(dt.month)
        day = int(dt.day)
        hour = dt.hour
        minute = dt.minute
        second = dt.second
        
        # Конвертиране в десетичен час (0.0 до 23.999...)
        decimal_hour = hour + minute / 60.0 + second / 3600.0
        
        # swe.julday(year, month, day, decimal_hour, flag)
        # day трябва да е INT, decimal_hour е float
        jd = swe.julday(year, month, day, decimal_hour, swe.GREG_CAL)
        return jd
    
    def _get_planet_position(self, jd: float, planet_id: int) -> Tuple[float, float]:
        """Връща позиция и скорост на планета с валидация"""
        try:
            result = swe.calc_ut(jd, planet_id, swe.FLG_SWIEPH | swe.FLG_SPEED)
            xx = result[0]
            longitude = xx[0]
            speed = xx[3]
            
            # Validation for Jupiter position in 2026 (VERIFIED CORRECT)
            # Jupiter in Cancer during 2026 (retrograde Feb-Jun)
            # This is CORRECT per Swiss Ephemeris calculations
            if planet_id == swe.JUPITER:
                # Convert JD back to date for validation
                cal = swe.revjul(jd, swe.GREG_CAL)
                year = cal[0]
                month = cal[1]
                
                if year == 2026 and (month >= 1 and month <= 6):
                    # Jupiter in Cancer: Jan-Jun 2026 (90° to 120° longitude)
                    expected_sign = "Cancer"
                    expected_range = (90, 120)
                    actual_sign = self._get_sign_name(longitude)
                    
                    if actual_sign != expected_sign:
                        print(f"⚠️ WARNING: Jupiter position unexpected!")
                        print(f"   Date: {year}-{month:02d}, Expected: {expected_sign}")
                        print(f"   Actual: {actual_sign} ({longitude:.2f}°)")
            
            return longitude, speed
        except Exception as e:
            raise RuntimeError(f"Грешка при изчисляване на планета {planet_id}: {e}")
    
    def _get_sign_name(self, longitude: float) -> str:
        """Връща име на знак по longitude"""
        signs = ["Aries", "Taurus", "Gemini", "Cancer", "Leo", "Virgo",
                 "Libra", "Scorpio", "Sagittarius", "Capricorn", "Aquarius", "Pisces"]
        sign_index = int(longitude / 30) % 12
        return signs[sign_index]
    
    def _normalize_angle(self, angle: float) -> float:
        """Нормализира ъгъл в диапазона 0-360"""
        while angle < 0:
            angle += 360
        while angle >= 360:
            angle -= 360
        return angle
    
    def _calculate_aspect_exact(
        self,
        transit_long: float,
        natal_long: float,
        prev_transit_long: Optional[float] = None,
    ) -> Optional[Dict]:
        """
        Строго изчислява аспект между транзитна и натална позиция.

        Връща речник:
            {
                "aspect": "Trine",
                "angle_deg": 120.0,
                "orb": 0.42,
                "is_applying": True/False
            }
        или None, ако няма аспект в позволените орбиси.
        """
        # Нормализираме ъглите
        t = self._normalize_angle(transit_long)
        n = self._normalize_angle(natal_long)

        # Най-късата дистанция между двете точки по кръга
        raw_diff = abs(t - n)
        diff = min(raw_diff, 360.0 - raw_diff)

        best_match: Optional[Dict] = None

        for aspect_name, aspect_angle in self.ASPECTS.items():
            orb = abs(diff - aspect_angle)

            # За да избегнем грешни "почти аспекти" извън тесните орбиси,
            # първо определяме дали аспектът е applying или separating (ако имаме предишна позиция).
            is_applying: Optional[bool] = None
            if prev_transit_long is not None:
                prev_t = self._normalize_angle(prev_transit_long)
                prev_raw_diff = abs(prev_t - n)
                prev_diff = min(prev_raw_diff, 360.0 - prev_raw_diff)
                # Ако разстоянието намалява към точния ъгъл – applying,
                # ако се увеличава – separating.
                prev_orb = abs(prev_diff - aspect_angle)
                is_applying = prev_orb > orb

            # Определяме допустимия орбис
            if is_applying is None:
                # Ако нямаме предишна точка, използваме по-консервативния лимит (separating)
                max_orb = self.MAX_ORB_SEPARATING
            else:
                max_orb = (
                    self.MAX_ORB_APPLYING if is_applying else self.MAX_ORB_SEPARATING
                )

            if orb <= max_orb:
                # Ако има повече от един аспект в обхват (рядко, но възможно при граници),
                # избираме този с най-малка орбис.
                if best_match is None or orb < best_match["orb"]:
                    best_match = {
                        "aspect": aspect_name,
                        "angle_deg": aspect_angle,
                        "orb": round(orb, 4),
                        "is_applying": bool(is_applying) if is_applying is not None else False,
                    }

        return best_match
    
    def _detect_retrograde_stations(self, date: datetime, events: List[Dict]) -> None:
        """Открива ретроградни станции за датата"""
        jd = self._datetime_to_jd(date)
        
        for planet_name, planet_id in self.RETROGRADE_PLANETS.items():
            try:
                _, speed = self._get_planet_position(jd, planet_id)
                
                # Проверка за промяна в посоката
                if planet_name in self.prev_speeds:
                    prev_speed = self.prev_speeds[planet_name]
                    
                    # Stationary Retrograde: от положителна към отрицателна скорост
                    if prev_speed > 0 and speed < 0:
                        longitude, _ = self._get_planet_position(jd, planet_id)
                        pos_data = self.engine._decimal_to_dms(longitude)
                        events.append(
                            {
                                "date": date.strftime("%Y-%m-%d"),
                                "type": "RETROGRADE",
                                "planet": planet_name,
                                "direction": "retrograde",
                                "event": f"{planet_name} turns Retrograde in {pos_data['sign']}",
                                "description": f"{planet_name} turns Retrograde in {pos_data['sign']}",
                                "position": pos_data["str"],
                            }
                        )
                    
                    # Stationary Direct: от отрицателна към положителна скорост
                    elif prev_speed < 0 and speed > 0:
                        longitude, _ = self._get_planet_position(jd, planet_id)
                        pos_data = self.engine._decimal_to_dms(longitude)
                        events.append(
                            {
                                "date": date.strftime("%Y-%m-%d"),
                                "type": "RETROGRADE",
                                "planet": planet_name,
                                "direction": "direct",
                                "event": f"{planet_name} turns Direct in {pos_data['sign']}",
                                "description": f"{planet_name} turns Direct in {pos_data['sign']}",
                                "position": pos_data["str"],
                            }
                        )
                
                # Запазване на текущата скорост за следващия ден
                self.prev_speeds[planet_name] = speed
                
            except Exception as e:
                print(f"Грешка при откриване на ретрограда за {planet_name}: {e}")
                continue
    
    def _detect_lunations_and_eclipses(self, date: datetime, lat: float, lon: float, events: List[Dict]) -> None:
        """Открива New Moon, Full Moon и Eclipses"""
        jd = self._datetime_to_jd(date)
        
        try:
            sun_long, _ = self._get_planet_position(jd, swe.SUN)
            moon_long, _ = self._get_planet_position(jd, swe.MOON)
            
            # Изчисляване на разстоянието между Слънцето и Луната
            separation = abs(sun_long - moon_long)
            separation = min(separation, 360 - separation)  # Най-малкото разстояние
            
            # New Moon (0° ± 13°)
            if separation < 13.0:
                # Проверка за Solar Eclipse
                try:
                    # swe.sol_eclipse_when_loc намира следващото слънчево затъмнение
                    # Използваме го за проверка дали има затъмнение близо до тази дата
                    ret = swe.sol_eclipse_when_loc(
                        jd - 1,  # Търсим от предишния ден
                        swe.FLG_SWIEPH,
                        lat, lon, 0,  # altitude = 0
                        0  # backward = 0 (търси напред)
                    )
                    
                    if ret[0] >= 0:  # Успешно намерено затъмнение
                        eclipse_jd = ret[1][0]
                        # Конвертиране на Julian Day към datetime
                        # JD 2440587.5 = 1970-01-01 00:00:00 UTC
                        days_since_epoch = eclipse_jd - 2440587.5
                        eclipse_timestamp = days_since_epoch * 86400.0
                        eclipse_date = datetime.fromtimestamp(eclipse_timestamp, tz=None)
                        
                        # Проверка дали затъмнението е в рамките на 1 ден от текущата дата
                        date_diff = abs((eclipse_date.date() - date.date()).days)
                        if date_diff <= 1:
                            sun_pos = self.engine._decimal_to_dms(sun_long)
                            events.append({
                                "date": date.strftime("%Y-%m-%d"),
                                "type": "ECLIPSE",
                                "planet": "Sun/Moon",
                                "event": f"Solar Eclipse in {sun_pos['sign']}",
                                "position": sun_pos["str"]
                            })
                            return  # Не добавяме New Moon, ако е затъмнение
                except:
                    pass  # Ако няма затъмнение, продължаваме
                
                # Обикновен New Moon
                sun_pos = self.engine._decimal_to_dms(sun_long)
                events.append({
                    "date": date.strftime("%Y-%m-%d"),
                    "type": "LUNATION",
                    "planet": "Sun/Moon",
                    "event": f"New Moon in {sun_pos['sign']}",
                    "position": sun_pos["str"]
                })
            
            # Full Moon (180° ± 13°)
            elif separation > 167.0:
                # Проверка за Lunar Eclipse
                try:
                    ret = swe.lun_eclipse_when(
                        jd - 1,
                        swe.FLG_SWIEPH,
                        0  # backward = 0
                    )
                    
                    if ret[0] >= 0:
                        eclipse_jd = ret[1][0]
                        # Конвертиране на Julian Day към datetime
                        days_since_epoch = eclipse_jd - 2440587.5
                        eclipse_timestamp = days_since_epoch * 86400.0
                        eclipse_date = datetime.fromtimestamp(eclipse_timestamp, tz=None)
                        
                        date_diff = abs((eclipse_date.date() - date.date()).days)
                        if date_diff <= 1:
                            moon_pos = self.engine._decimal_to_dms(moon_long)
                            events.append({
                                "date": date.strftime("%Y-%m-%d"),
                                "type": "ECLIPSE",
                                "planet": "Sun/Moon",
                                "event": f"Lunar Eclipse in {moon_pos['sign']}",
                                "position": moon_pos["str"]
                            })
                            return
                except:
                    pass
                
                # Обикновен Full Moon
                moon_pos = self.engine._decimal_to_dms(moon_long)
                events.append({
                    "date": date.strftime("%Y-%m-%d"),
                    "type": "LUNATION",
                    "planet": "Sun/Moon",
                    "event": f"Full Moon in {moon_pos['sign']}",
                    "position": moon_pos["str"]
                })
        
        except Exception as e:
            print(f"Грешка при откриване на лунации/затъмнения: {e}")
    
    def _detect_transits_to_natal(
        self,
        date: datetime,
        natal_chart: Dict,
        events: List[Dict],
        target: str = "User",
    ) -> None:
        """
        Открива транзити към наталната карта по СТРОГИ математически правила.

        - Аспекти: Conjunction (0°), Sextile (60°), Square (90°), Trine (120°), Opposition (180°)
        - Орбис:
            * Приближаващи (applying): до 1.5°
            * Отделящи се (separating): до 1.0°

        Събитието съдържа:
            - aspect: име на аспекта (напр. "Trine")
            - angle_deg: точният аспектен ъгъл (0/60/90/120/180)
            - orb: разлика в градуси от точен аспект
        """
        jd = self._datetime_to_jd(date)
        jd_prev = jd - 1.0  # един ден назад, за да различим applying/separating

        # Натални планети за сравнение
        natal_planets = natal_chart.get("planets", {})

        # Проверяваме само важните натални планети
        natal_targets = [
            "Sun",
            "Moon",
            "Mercury",
            "Venus",
            "Mars",
            "Jupiter",
            "Saturn",
            "Uranus",
            "Neptune",
            "Pluto",
        ]

        for transit_planet_name, transit_planet_id in self.TRANSIT_PLANETS.items():
            try:
                transit_long, _ = self._get_planet_position(jd, transit_planet_id)
                prev_transit_long, _ = self._get_planet_position(jd_prev, transit_planet_id)

                # Сравняваме с наталните планети
                for natal_planet_name in natal_targets:
                    if natal_planet_name not in natal_planets:
                        continue

                    natal_long = natal_planets[natal_planet_name]["longitude"]

                    # Строго изчисляване на аспект
                    aspect_info = self._calculate_aspect_exact(
                        transit_long=transit_long,
                        natal_long=natal_long,
                        prev_transit_long=prev_transit_long,
                    )

                    if not aspect_info:
                        continue

                    transit_pos = self.engine._decimal_to_dms(transit_long)
                    natal_pos = self.engine._decimal_to_dms(natal_long)

                    house_impact = self._find_house_for_position(transit_long, natal_chart)

                    events.append(
                        {
                            "date": date.strftime("%Y-%m-%d"),
                            "type": "TRANSIT",
                            "target": target,  # "User" или "Partner"
                            "planet": transit_planet_name,
                            "natal_planet": natal_planet_name,
                            "aspect": aspect_info["aspect"],
                            "angle_deg": aspect_info["angle_deg"],
                            "orb": round(aspect_info["orb"], 2),
                            "is_applying": aspect_info["is_applying"],
                            "transit_position": transit_pos["str"],
                            "natal_position": natal_pos["str"],
                            "house_impact": house_impact,
                            "description": f"Transit {transit_planet_name} {aspect_info['aspect']} NATAL {natal_planet_name} (House {house_impact})",
                        }
                    )

            except Exception as e:
                print(f"Грешка при откриване на транзит за {transit_planet_name}: {e}")
                continue
    
    def _find_house_for_position(self, longitude: float, natal_chart: Dict) -> str:
        """Намира в кой натален дом попада позицията"""
        houses = natal_chart.get("angles", {}).get("houses", {})
        
        if not houses:
            return "Unknown"
        
        # Сортиране на домовете по дължина
        house_list = []
        for house_name, house_cusp in houses.items():
            house_list.append((house_name, house_cusp))
        
        house_list.sort(key=lambda x: x[1])
        
        # Намиране на дома
        for i, (house_name, house_cusp) in enumerate(house_list):
            next_house_cusp = house_list[(i + 1) % len(house_list)][1]
            
            # Проверка дали позицията е между тези два дома
            if house_cusp <= longitude < next_house_cusp or (next_house_cusp < house_cusp and (longitude >= house_cusp or longitude < next_house_cusp)):
                return house_name.replace("House", "")
        
        return "1"  # Default
    
    def scan_period(
        self,
        natal_chart: Dict,
        start_date: str,
        end_date: str,
        lat: float,
        lon: float,
        partner_chart: Optional[Dict] = None,
    ) -> List[Dict]:
        """
        Сканира период от време за астрологични събития.

        - Транзити към наталната карта (СТРОГИ аспекти).
        - Ретроградни станции (Retrograde / Direct).
        - Лунации и затъмнения.
        - INGRES събития: планета влиза в нов знак.
        """
        events: List[Dict] = []

        # Парсиране на датите
        start_dt = datetime.strptime(start_date, "%Y-%m-%d")
        end_dt = datetime.strptime(end_date, "%Y-%m-%d")

        # Инициализация на предишните скорости (използваме ден преди началната дата)
        init_date = start_dt - timedelta(days=1)
        init_jd = self._datetime_to_jd(init_date)

        for planet_name, planet_id in self.RETROGRADE_PLANETS.items():
            try:
                _, speed = self._get_planet_position(init_jd, planet_id)
                self.prev_speeds[planet_name] = speed
            except Exception:
                self.prev_speeds[planet_name] = 0.0

        # Инициализация на предишните знаци за INGRES детекция
        prev_signs: Dict[str, int] = {}
        for planet_name, planet_id in self.INGRESS_PLANETS.items():
            try:
                long_init, _ = self._get_planet_position(init_jd, planet_id)
                prev_signs[planet_name] = int(self._normalize_angle(long_init) // 30)
            except Exception:
                prev_signs[planet_name] = -1

        # Итерация през всеки ден
        current_date = start_dt
        while current_date <= end_dt:
            # Откриване на ретрогради (общи за всички)
            self._detect_retrograde_stations(current_date, events)

            # Откриване на лунации и затъмнения (общи за всички)
            self._detect_lunations_and_eclipses(current_date, lat, lon, events)

            # Откриване на INGRES събития (планета влиза в нов знак)
            jd_current = self._datetime_to_jd(current_date)
            for planet_name, planet_id in self.INGRESS_PLANETS.items():
                try:
                    long_now, _ = self._get_planet_position(jd_current, planet_id)
                    normalized = self._normalize_angle(long_now)
                    current_sign_index = int(normalized // 30)

                    prev_sign_index = prev_signs.get(planet_name, current_sign_index)
                    if current_sign_index != prev_sign_index:
                        # Планетата е влязла в нов знак между предишния и текущия ден
                        pos_data = self.engine._decimal_to_dms(normalized)
                        events.append(
                            {
                                "date": current_date.strftime("%Y-%m-%d"),
                                "type": "INGRESS",
                                "planet": planet_name,
                                "sign": pos_data["sign"],
                                "event": f"{planet_name} enters {pos_data['sign']}",
                                "description": f"{planet_name} enters {pos_data['sign']}",
                                "position": pos_data["str"],
                            }
                        )

                    prev_signs[planet_name] = current_sign_index
                except Exception as e:
                    print(f"Грешка при откриване на ingress за {planet_name}: {e}")

            # Откриване на транзити за User
            self._detect_transits_to_natal(
                current_date, natal_chart, events, target="User"
            )

            # Откриване на транзити за Partner (ако е предоставена карта)
            if partner_chart:
                self._detect_transits_to_natal(
                    current_date, partner_chart, events, target="Partner"
                )

            current_date += timedelta(days=1)

        # Сортиране по дата (и вторично по тип за стабилност)
        events.sort(key=lambda x: (x.get("date", ""), x.get("type", "")))

        return events

