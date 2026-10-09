"""
Модул за изчисляване на астрологични аспекти
Използва се за натални аспекти, синастрични аспекти и транзитни аспекти
"""

from typing import Dict, List, Tuple, Optional

# Единна версионирана политика на орбисите. Какво важи къде:
#  - натални и синастрични аспекти: _get_orb_for_aspect (8° / 5°, при външна планета 5° / 4°);
#  - аспекти между небето в избран момент и наталната карта (анализ за дата): TRANSIT_SNAPSHOT_MAX_ORB;
#  - календар на периода (scanner.py): 1,5° приближаващ и 1,0° отделящ се аспект.
# Аспект извън тези граници не се подава към AI като активен фактор.
ORB_POLICY_VERSION = "orbs-v1"
TRANSIT_SNAPSHOT_MAX_ORB = 3.0

ASPECT_ANGLES = [("conjunction", 0), ("opposition", 180), ("square", 90), ("trine", 120), ("sextile", 60)]


def _get_orb_for_aspect(
    planet1: str,
    planet2: str,
    aspect_type: str,
    use_wider_orbs: bool = False
) -> float:
    """
    Връща максималния орб (в градуси) за дадена двойка планети и аспект.
    """
    personal_points = {"Sun", "Moon", "Mercury", "Venus", "Mars", "ASC", "MC"}
    social_planets = {"Jupiter", "Saturn"}
    outer_planets = {"Uranus", "Neptune", "Pluto"}

    # Основни орбове
    if use_wider_orbs:
        major_orb = 10.0
        minor_orb = 6.0
        outer_major = 6.0
        outer_minor = 5.0
    else:
        major_orb = 8.0
        minor_orb = 5.0
        outer_major = 5.0
        outer_minor = 4.0

    # Проверка дали някоя от планетите е външна
    is_outer = (planet1 in outer_planets) or (planet2 in outer_planets)

    if aspect_type in ("conjunction", "opposition", "square"):
        return outer_major if is_outer else major_orb
    else:  # sextile, trine
        return outer_minor if is_outer else minor_orb


def _calculate_angle(longitude1: float, longitude2: float) -> float:
    """Изчислява най-малкия ъгъл между две точки (0–180°)."""
    diff = abs(longitude1 - longitude2) % 360
    return min(diff, 360 - diff)


def calculate_natal_aspects(
    chart_data: Dict,
    use_wider_orbs: bool = False
) -> List[Dict]:
    """
    Изчислява аспекти в една натална карта.
    
    Args:
        chart_data: Речник с данни от наталната карта (съдържа "planets" и "angles")
        use_wider_orbs: Дали да се използват по-широки орбове
    
    Returns:
        Списък с речници, всеки съдържа:
        - "planet1": име на първата планета
        - "planet2": име на втората планета
        - "aspect": тип аспект ("conjunction", "opposition", "square", "trine", "sextile")
        - "angle": изчисленият ъгъл в градуси
        - "orb": орбът в градуси
    """
    points = {}

    # Добавяне на планети
    for name, data in chart_data.get("planets", {}).items():
        if data.get("longitude") is not None:
            points[name] = data["longitude"]

    # Добавяне на ъгли
    angles = chart_data.get("angles", {})
    if angles.get("Ascendant") is not None:
        points["ASC"] = angles["Ascendant"]
    if angles.get("MC") is not None:
        points["MC"] = angles["MC"]

    return _calculate_aspects_between_points(points, use_wider_orbs)


def calculate_synastry_aspects(
    user_chart: Dict,
    partner_chart: Dict,
    use_wider_orbs: bool = False
) -> List[Dict]:
    """
    Изчислява аспекти между две карти (Синастрия).
    Всички аспекти са: User Planet ↔ Partner Planet.
    
    Args:
        user_chart: Натална карта на потребителя
        partner_chart: Натална карта на партньора
        use_wider_orbs: Дали да се използват по-широки орбове
    
    Returns:
        Списък с речници, всеки съдържа:
        - "planet1": планета от user chart
        - "planet2": планета от partner chart
        - "aspect": тип аспект
        - "angle": изчисленият ъгъл
        - "orb": орбът
    """
    user_points = {}
    partner_points = {}

    for name, data in user_chart.get("planets", {}).items():
        if data.get("longitude") is not None:
            user_points[name] = data["longitude"]
    
    for name, data in partner_chart.get("planets", {}).items():
        if data.get("longitude") is not None:
            partner_points[name] = data["longitude"]

    # Добавяне на ASC/MC само от user (за релационна динамика)
    angles = user_chart.get("angles", {})
    if angles.get("Ascendant") is not None:
        user_points["ASC"] = angles["Ascendant"]
    if angles.get("MC") is not None:
        user_points["MC"] = angles["MC"]

    aspects = []
    for p1_name, p1_lon in user_points.items():
        for p2_name, p2_lon in partner_points.items():
            angle = _calculate_angle(p1_lon, p2_lon)
            for aspect_name, ideal in [("conjunction", 0), ("opposition", 180), ("square", 90), ("trine", 120), ("sextile", 60)]:
                orb = abs(angle - ideal)
                if orb <= 180:  # винаги вярно, но за сигурност
                    max_orb = _get_orb_for_aspect(p1_name, p2_name, aspect_name, use_wider_orbs)
                    if orb <= max_orb:
                        aspects.append({
                            "planet1": p1_name,       # от user
                            "planet2": p2_name,       # от partner
                            "aspect": aspect_name,
                            "angle": round(angle, 2),
                            "orb": round(orb, 2)
                        })
    aspects.sort(key=lambda x: x["orb"])
    return aspects


def _wrap180(value: float) -> float:
    """Нормализира разлика на дължини в интервала [-180, 180)."""
    return ((value + 180.0) % 360.0) - 180.0


def _is_applying(transit_lon: float, speed: Optional[float], natal_lon: float, aspect_angle: float) -> Optional[bool]:
    """
    Дали транзитната планета се приближава към точния аспект (True) или се отдалечава (False).
    None, когато скоростта липсва или планетата е практически неподвижна (станция).
    """
    if speed is None or abs(speed) < 0.001:
        return None
    nearest = None
    for exact in {(natal_lon + aspect_angle) % 360.0, (natal_lon - aspect_angle) % 360.0}:
        distance = _wrap180(transit_lon - exact)
        if nearest is None or abs(distance) < abs(nearest):
            nearest = distance
    return nearest * speed < 0


def calculate_transit_aspects_to_natal(
    natal_chart: Dict,
    transit_chart: Dict,
    use_wider_orbs: bool = False,
    max_orb: Optional[float] = None,
    include_angles: bool = False,
) -> List[Dict]:
    """
    Изчислява аспекти между транзитни планети и натална карта.
    Transit Planet → Natal Planet.

    Args:
        natal_chart: Натална карта
        transit_chart: Транзитна карта (небето в избрания момент)
        use_wider_orbs: Дали да се използват по-широки орбове (само без max_orb)
        max_orb: Ако е зададен, е най-големият орбис за всички аспекти (политика за дата)
        include_angles: Добавя натални ASC и MC като цели на аспекта

    Returns:
        Списък с речници, всеки съдържа:
        - "transit_planet": транзитна планета
        - "natal_planet": натална планета или ъгъл (ASC, MC)
        - "aspect": тип аспект
        - "angle": изчисленият ъгъл
        - "orb": орбът
        - "applying": True/False според скоростта на транзитната планета (липсва при станция)
    """
    natal_points = {}
    for name, data in natal_chart.get("planets", {}).items():
        if data.get("longitude") is not None:
            natal_points[name] = data["longitude"]
    if include_angles:
        angles = natal_chart.get("angles", {})
        if angles.get("Ascendant") is not None:
            natal_points["ASC"] = angles["Ascendant"]
        if angles.get("MC") is not None:
            natal_points["MC"] = angles["MC"]

    transit_points = {}
    for name, data in transit_chart.get("planets", {}).items():
        if data.get("longitude") is not None:
            transit_points[name] = (data["longitude"], data.get("speed"))

    aspects = []
    for t_name, (t_lon, t_speed) in transit_points.items():
        for n_name, n_lon in natal_points.items():
            angle = _calculate_angle(t_lon, n_lon)
            for aspect_name, ideal in ASPECT_ANGLES:
                orb = abs(angle - ideal)
                limit = max_orb if max_orb is not None else _get_orb_for_aspect(t_name, n_name, aspect_name, use_wider_orbs)
                if orb <= limit:
                    entry = {
                        "transit_planet": t_name,
                        "natal_planet": n_name,
                        "aspect": aspect_name,
                        "angle": round(angle, 2),
                        "orb": round(orb, 2),
                    }
                    applying = _is_applying(t_lon, t_speed, n_lon, ideal)
                    if applying is not None:
                        entry["applying"] = applying
                    aspects.append(entry)
    aspects.sort(key=lambda x: x["orb"])
    return aspects


def _calculate_aspects_between_points(
    points: Dict[str, float],
    use_wider_orbs: bool = False
) -> List[Dict]:
    """Помощна функция за изчисление между точки в една карта."""
    point_names = list(points.keys())
    aspects = []

    for i in range(len(point_names)):
        for j in range(i + 1, len(point_names)):
            p1 = point_names[i]
            p2 = point_names[j]
            lon1 = points[p1]
            lon2 = points[p2]
            angle = _calculate_angle(lon1, lon2)

            for aspect_name, ideal in [("conjunction", 0), ("opposition", 180), ("square", 90), ("trine", 120), ("sextile", 60)]:
                orb = abs(angle - ideal)
                max_orb = _get_orb_for_aspect(p1, p2, aspect_name, use_wider_orbs)
                if orb <= max_orb:
                    aspects.append({
                        "planet1": p1,
                        "planet2": p2,
                        "aspect": aspect_name,
                        "angle": round(angle, 2),
                        "orb": round(orb, 2)
                    })
    aspects.sort(key=lambda x: x["orb"])
    return aspects
