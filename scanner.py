"""
Точен календар на периода (Фаза 9).

Всичко тук е изчисление със Swiss Ephemeris: без AI и без мрежа, затова се тества офлайн срещу независим контрол.

Старият сканер гледаше небето веднъж на ден в 00:00 UTC и записваше събитието на първата проверка след промяната,
тоест с ден по-късно, а всеки аспект беше десетки дневни реда без пик. Сега календарът намира точния момент на
всяко събитие с разделяне на интервала наполовина (грешка под секунда):

- станция (планетата сменя посоката): коренът на скоростта;
- влизане в знак, и при обратен ход: пресичането на границата на знака;
- новолуние и пълнолуние: пресичане на 0° и 180° между Слънцето и Луната; затъмнение е лунация със затъмнение;
- аспект на транзитна планета към натална: всяко точно преминаване (при ретрограден ход са до три) и прозорец
  около него (до 1,5° преди пика и до 1,0° след него); преминаванията с застъпващи се прозорци са една серия.

Часовете се показват в часовата зона на анализа (с лятно/зимно време) и са закръглени до минута. UTC се пази в
полетата с префикс "_" (те не стигат до AI, виж PeriodCalendar.public).
"""
import math
from calendar import monthrange
from datetime import date, datetime, timedelta, timezone
from typing import Callable, Dict, List, Optional, Tuple

import pytz
import swisseph as swe  # type: ignore

from engine import AstrologyEngine, decimal_to_dms, house_for_longitude

CALENDAR_VERSION = "9.0"
CALC_FLAGS = swe.FLG_SWIEPH | swe.FLG_SPEED

# Аспекти и техните ъгли
ASPECTS: Tuple[Tuple[str, float], ...] = (
    ("Conjunction", 0.0), ("Sextile", 60.0), ("Square", 90.0), ("Trine", 120.0), ("Opposition", 180.0),
)
# Прозорец на аспекта: до 1,5° докато се приближава и до 1,0° след пика (политика orbs-v1)
APPLYING_ORB = 1.5
SEPARATING_ORB = 1.0

# Планети, които правят аспекти към натала (както досега: Марс до Плутон) и натални точки, към които ги правят
TRANSIT_PLANETS = {
    "Mars": swe.MARS, "Jupiter": swe.JUPITER, "Saturn": swe.SATURN,
    "Uranus": swe.URANUS, "Neptune": swe.NEPTUNE, "Pluto": swe.PLUTO,
}
NATAL_TARGETS = ("Sun", "Moon", "Mercury", "Venus", "Mars", "Jupiter", "Saturn", "Uranus", "Neptune", "Pluto")
# Станции: Меркурий до Плутон. Влизане в знак: Слънцето и планетите. Луната не влиза в списъка: сменя знака на
# всеки 2-3 дни и 27 от 32 влизания в знак бяха нейни (шум за AI).
STATION_PLANETS = {
    "Mercury": swe.MERCURY, "Venus": swe.VENUS, "Mars": swe.MARS, "Jupiter": swe.JUPITER,
    "Saturn": swe.SATURN, "Uranus": swe.URANUS, "Neptune": swe.NEPTUNE, "Pluto": swe.PLUTO,
}
INGRESS_PLANETS = {"Sun": swe.SUN, **STATION_PLANETS}

# Аспектът може да е активен в периода, а точен извън него. Затова преминаванията се търсят и извън периода:
# за толкова дни преди началото и след края (повече от най-дългия прозорец с ретрограден ход на планетата).
PASS_MARGIN_DAYS = {"Mars": 150, "Jupiter": 250, "Saturn": 420, "Uranus": 600, "Neptune": 800, "Pluto": 1000}
# Стъпка на грубата мрежа (дни); станциите са отделни точки, затова между две съседни точки посоката е една
GRID_STEP_DAYS = {"Mars": 1.0, "Jupiter": 2.0, "Saturn": 2.0, "Uranus": 3.0, "Neptune": 4.0, "Pluto": 4.0}
POINT_EVENT_STEP_DAYS = 1.0
LUNATION_STEP_DAYS = 0.25

_EPOCH = datetime(1970, 1, 1, tzinfo=timezone.utc)
_UNIX_EPOCH_JD = 2440587.5
SIGNS = ["Aries", "Taurus", "Gemini", "Cancer", "Leo", "Virgo", "Libra", "Scorpio",
         "Sagittarius", "Capricorn", "Aquarius", "Pisces"]

Point = Tuple[float, float, float, Optional[str]]   # (jd, дължина, скорост, "retrograde"/"direct" за станция)


# --------------------------------------------------------------------------------------------------------------
# Време
# --------------------------------------------------------------------------------------------------------------
def jd_to_utc(jd: float) -> datetime:
    """Julian Day (UT) -> datetime в UTC."""
    return _EPOCH + timedelta(days=jd - _UNIX_EPOCH_JD)


def utc_to_jd(dt: datetime) -> float:
    """datetime в UTC -> Julian Day (UT)."""
    return (dt - _EPOCH).total_seconds() / 86400.0 + _UNIX_EPOCH_JD


def _local_midnight_jd(day: date, tz) -> float:
    """Полунощ на дадена дата в зоната tz, като Julian Day. При несъществуваща или двойна полунощ (смяна на
    времето точно в 00:00) се взима зимното време."""
    naive = datetime(day.year, day.month, day.day)
    try:
        local = tz.localize(naive, is_dst=None)
    except (pytz.exceptions.NonExistentTimeError, pytz.exceptions.AmbiguousTimeError):
        local = tz.localize(naive, is_dst=False)
    return utc_to_jd(local.astimezone(pytz.utc).replace(tzinfo=timezone.utc))


def _round_minute(dt: datetime) -> datetime:
    return (dt + timedelta(seconds=30)).replace(second=0, microsecond=0)


def _wrap180(x: float) -> float:
    return (x + 180.0) % 360.0 - 180.0


def _lon_speed(jd: float, planet_id: int) -> Tuple[float, float]:
    xx = swe.calc_ut(jd, planet_id, CALC_FLAGS)[0]
    return xx[0] % 360.0, xx[3]


def _lon(jd: float, planet_id: int) -> float:
    return swe.calc_ut(jd, planet_id, CALC_FLAGS)[0][0] % 360.0


def _bisect(f: Callable[[float], float], a: float, b: float, fa: Optional[float] = None,
            tol_days: float = 0.25 / 86400.0) -> float:
    """Корен на f в [a, b], където f сменя знака. Грешка под 0,25 секунда."""
    if fa is None:
        fa = f(a)
    if fa == 0.0:
        return a
    for _ in range(80):
        if b - a <= tol_days:
            break
        m = 0.5 * (a + b)
        fm = f(m)
        if fm == 0.0:
            return m
        if (fm > 0.0) == (fa > 0.0):
            a, fa = m, fm
        else:
            b = m
    return 0.5 * (a + b)


def _monotone_points(planet_id: int, a: float, b: float, step: float) -> List[Point]:
    """Мрежа от точки от a до b. Всяка станция е отделна точка със скорост 0, затова между две съседни точки
    планетата върви само в една посока: дължината е монотонна и всеки аспект се пресича най-много веднъж."""
    n = max(1, int(math.ceil((b - a) / step)))
    grid = []
    for i in range(n + 1):
        t = a + (b - a) * i / n
        lon, speed = _lon_speed(t, planet_id)
        grid.append((t, lon, speed))
    out: List[Point] = [(grid[0][0], grid[0][1], grid[0][2], None)]
    for prev, cur in zip(grid, grid[1:]):
        if prev[2] * cur[2] < 0:
            ts = _bisect(lambda t: _lon_speed(t, planet_id)[1], prev[0], cur[0], fa=prev[2])
            out.append((ts, _lon(ts, planet_id), 0.0, "retrograde" if prev[2] > 0 else "direct"))
        out.append((cur[0], cur[1], cur[2], None))
    return out


def _sign_name(longitude: float) -> str:
    return SIGNS[int(longitude % 360.0 // 30) % 12]


class PeriodCalendar:
    """Календарът на един период: хронологичен списък от събития и помощници за месеците."""

    def __init__(self, timezone_name: str, start: str, end: str, events: List[Dict], planets_scanned: List[str]):
        self.timezone = timezone_name
        self.start = start
        self.end = end
        self.events = events
        self.version = CALENDAR_VERSION
        self.planets_scanned = planets_scanned

    @staticmethod
    def public(row: Dict) -> Dict:
        """Редът, какъвто го вижда AI: без вътрешните полета (префикс "_")."""
        return {k: v for k, v in row.items() if not k.startswith("_")}

    def public_events(self) -> List[Dict]:
        return [self.public(e) for e in self.events]

    @property
    def months(self) -> List[str]:
        """Всички календарни месеци на периода, като "YYYY-MM" (първият и последният включително)."""
        first, last = self.start[:7], self.end[:7]
        y, m = int(first[:4]), int(first[5:7])
        out = []
        while f"{y:04d}-{m:02d}" <= last:
            out.append(f"{y:04d}-{m:02d}")
            m += 1
            if m == 13:
                y, m = y + 1, 1
        return out

    def month_events(self, month: str) -> List[Dict]:
        """Събитията за един месец (във вида за AI). Точкови събития: тези от месеца. Аспект: тези, които имат точен
        момент в месеца или са активни в него (state_in_month: exact_in_month, approaching, separating, active)."""
        rows: List[Dict] = []
        month_first = f"{month}-01"
        month_last = f"{month}-{monthrange(int(month[:4]), int(month[5:7]))[1]:02d}"
        for e in self.events:
            if e["type"] != "TRANSIT":
                if e["when"][:7] == month:
                    rows.append(self.public(e))
                continue
            in_month = [x["when"] for x in e["exact"] if x["when"][:7] == month]
            active = e["active_from"] <= month_last and e["active_to"] >= month_first
            if not in_month and not active:
                continue
            row = self.public(e)
            row["exact_this_month"] = in_month
            if in_month:
                state = "exact_in_month"
            elif not e["exact"]:
                state = "active"
            elif all(x["when"][:7] > month for x in e["exact"]):
                state = "approaching"
            elif all(x["when"][:7] < month for x in e["exact"]):
                state = "separating"
            else:
                state = "active"
            row["state_in_month"] = state
            rows.append(row)
        rows.sort(key=lambda r: _row_sort_key(r, month_first))
        return rows

    def months_with_events(self) -> List[str]:
        return [m for m in self.months if self.month_events(m)]


def _row_sort_key(row: Dict, month_first: str) -> str:
    if row["type"] == "TRANSIT":
        times = row.get("exact_this_month") or []
        return times[0] if times else max(row["active_from"], month_first) + " 00:00"
    return row["when"]


class TransitScanner:
    """Строи точния календар на период за един или двама души."""

    def __init__(self, base_dir=None, engine_instance: Optional[AstrologyEngine] = None):
        self.engine = engine_instance or AstrologyEngine(base_dir)

    # ------------------------------------------------------------------------------------------------------
    # Публичен вход
    # ------------------------------------------------------------------------------------------------------
    def zone_for(self, lat: float, lon: float) -> str:
        zone = None
        try:
            zone = self.engine.tf.timezone_at(lat=lat, lng=lon)
        except Exception:
            zone = None
        return zone or "UTC"

    def scan_period(self, natal_chart: Dict, start_date: str, end_date: str, lat: float, lon: float,
                    partner_chart: Optional[Dict] = None, tz_name: Optional[str] = None) -> List[Dict]:
        """Съвместим вход: хронологичният списък със събития (с вътрешните полета "_")."""
        return self.build_calendar(natal_chart, start_date, end_date, lat, lon, partner_chart, tz_name).events

    def build_calendar(self, natal_chart: Dict, start_date: str, end_date: str, lat: float, lon: float,
                       partner_chart: Optional[Dict] = None, tz_name: Optional[str] = None) -> PeriodCalendar:
        start_d = datetime.strptime(start_date, "%Y-%m-%d").date()
        end_d = datetime.strptime(end_date, "%Y-%m-%d").date()
        if end_d < start_d:
            raise ValueError("Крайната дата е преди началната")
        zone = tz_name or self.zone_for(lat, lon)
        tz = pytz.timezone(zone)
        p0 = _local_midnight_jd(start_d, tz)
        p1 = _local_midnight_jd(end_d + timedelta(days=1), tz)       # изключваща граница

        ctx = _Context(tz, zone, start_d, end_d, p0, p1)
        events: List[Dict] = []
        events += self._station_and_ingress_events(ctx)
        events += self._lunation_events(ctx)

        tracks = {name: _monotone_points(pid, p0 - PASS_MARGIN_DAYS[name], p1 + PASS_MARGIN_DAYS[name],
                                         GRID_STEP_DAYS[name])
                  for name, pid in TRANSIT_PLANETS.items()}
        events += self._aspect_series(ctx, tracks, natal_chart, "User")
        if partner_chart:
            events += self._aspect_series(ctx, tracks, partner_chart, "Partner")

        events.sort(key=_event_order_key)
        _number_series(events)
        return PeriodCalendar(zone, start_date, end_date, events, list(TRANSIT_PLANETS))

    # ------------------------------------------------------------------------------------------------------
    # Съвместимост със старите тестове
    # ------------------------------------------------------------------------------------------------------
    def _find_house_for_position(self, longitude: float, natal_chart: Dict) -> str:
        """Номер на натален дом за позиция ("Unknown" без куспиди)."""
        houses = natal_chart.get("houses") or natal_chart.get("angles", {}).get("houses", {})
        house = house_for_longitude(longitude, houses) if houses else None
        return str(house) if house else "Unknown"

    # ------------------------------------------------------------------------------------------------------
    # Станции и влизания в знак
    # ------------------------------------------------------------------------------------------------------
    def _station_and_ingress_events(self, ctx: "_Context") -> List[Dict]:
        events: List[Dict] = []
        a, b = ctx.p0 - 1.0, ctx.p1 + 1.0
        for name, pid in INGRESS_PLANETS.items():
            points = _monotone_points(pid, a, b, POINT_EVENT_STEP_DAYS)
            for jd, lon, _speed, tag in points:
                if tag and name in STATION_PLANETS:
                    ev = ctx.make_point(jd, "RETROGRADE", name, {
                        "direction": tag,
                        "event": f"{name} turns {'Retrograde' if tag == 'retrograde' else 'Direct'} in {_sign_name(lon)}",
                        "position": decimal_to_dms(lon)["str"],
                    })
                    if ev:
                        ev["id"] = f"ST:{name}:{tag}:{ev['_utc'][:10]}"
                        events.append(ev)
            for prev, cur in zip(points, points[1:]):
                idx_p, idx_c = int(prev[1] // 30), int(cur[1] // 30)
                if idx_p == idx_c or abs(_wrap180(cur[1] - prev[1])) > 20.0:
                    continue
                forward = (idx_c - idx_p) % 12 == 1
                boundary = (idx_c if forward else idx_p) * 30.0
                t = _bisect(lambda x: _wrap180(_lon(x, pid) - boundary), prev[0], cur[0],
                            fa=_wrap180(prev[1] - boundary))
                new_sign = SIGNS[idx_c % 12]
                motion = "direct" if forward else "retrograde"
                text = f"{name} enters {new_sign}" + ("" if forward else " (moving retrograde)")
                ev = ctx.make_point(t, "INGRESS", name, {"sign": new_sign, "motion": motion, "event": text})
                if ev:
                    ev["id"] = f"IN:{name}:{new_sign}:{ev['_utc'][:10]}"
                    events.append(ev)
        return events

    # ------------------------------------------------------------------------------------------------------
    # Новолуния, пълнолуния и затъмнения
    # ------------------------------------------------------------------------------------------------------
    def _lunation_events(self, ctx: "_Context") -> List[Dict]:
        a, b = ctx.p0 - 2.0, ctx.p1 + 2.0

        def elongation(jd: float) -> float:
            return _lon(jd, swe.MOON) - _lon(jd, swe.SUN)

        n = int(math.ceil((b - a) / LUNATION_STEP_DAYS))
        times = [a + (b - a) * i / n for i in range(n + 1)]
        raw = [(t, _wrap180(elongation(t) - 0.0), _wrap180(elongation(t) - 180.0)) for t in times]
        found: List[Tuple[float, str]] = []
        for (t0, n0, f0), (t1, n1, f1) in zip(raw, raw[1:]):
            if n0 < 0 <= n1 and abs(n0) < 90 and abs(n1) < 90:
                found.append((_bisect(lambda x: _wrap180(elongation(x)), t0, t1, fa=n0), "New"))
            if f0 < 0 <= f1 and abs(f0) < 90 and abs(f1) < 90:
                found.append((_bisect(lambda x: _wrap180(elongation(x) - 180.0), t0, t1, fa=f0), "Full"))
        eclipses = self._eclipses(a, b)

        events: List[Dict] = []
        for t, kind in sorted(found):
            sun, moon = _lon(t, swe.SUN), _lon(t, swe.MOON)
            lon = sun if kind == "New" else moon
            sign = _sign_name(lon)
            eclipse = next((e for e in eclipses if abs(e[0] - t) < 0.5 and e[1] == ("solar" if kind == "New" else "lunar")), None)
            if eclipse:
                label = f"{'Solar' if kind == 'New' else 'Lunar'} Eclipse in {sign}"
                ev = ctx.make_point(t, "ECLIPSE", "Sun/Moon", {"event": label, "position": decimal_to_dms(lon)["str"],
                                                                 "eclipse_kind": eclipse[2]})
                prefix = "EC"
            else:
                label = f"{kind} Moon in {sign}"
                ev = ctx.make_point(t, "LUNATION", "Sun/Moon", {"event": label, "position": decimal_to_dms(lon)["str"]})
                prefix = "LU"
            if ev:
                ev["id"] = f"{prefix}:{kind.lower()}:{ev['_utc'][:10]}"
                events.append(ev)
        return events

    @staticmethod
    def _eclipses(a: float, b: float) -> List[Tuple[float, str, str]]:
        """Затъмненията в [a, b] като (момент на максимума, solar/lunar, вид)."""
        out: List[Tuple[float, str, str]] = []
        for which in ("solar", "lunar"):
            t = a
            for _ in range(8):
                try:
                    ret = (swe.sol_eclipse_when_glob(t, swe.FLG_SWIEPH, 0, False) if which == "solar"
                           else swe.lun_eclipse_when(t, swe.FLG_SWIEPH, 0, False))
                except Exception:
                    break
                flag, tret = ret[0], ret[1]
                if flag < 0 or tret[0] > b:
                    break
                if tret[0] >= a:
                    out.append((tret[0], which, _eclipse_kind(flag)))
                t = tret[0] + 20.0
        return out

    # ------------------------------------------------------------------------------------------------------
    # Аспекти на транзитни планети към наталната карта
    # ------------------------------------------------------------------------------------------------------
    def _aspect_series(self, ctx: "_Context", tracks: Dict[str, List[Point]], natal_chart: Dict,
                       target: str) -> List[Dict]:
        cusps = natal_chart.get("houses") or {}
        natal_planets = natal_chart.get("planets", {})
        rows: List[Dict] = []
        for transit_name, pid in TRANSIT_PLANETS.items():
            pts = tracks[transit_name]
            for natal_name in NATAL_TARGETS:
                natal = natal_planets.get(natal_name) or {}
                if natal.get("longitude") is None:
                    continue
                n_lon = natal["longitude"] % 360.0
                for aspect_name, angle in ASPECTS:
                    if angle == 0.0:
                        points_of_aspect = [n_lon]
                    elif angle == 180.0:
                        points_of_aspect = [(n_lon + 180.0) % 360.0]
                    else:
                        points_of_aspect = [(n_lon + angle) % 360.0, (n_lon - angle) % 360.0]
                    for aspect_lon in points_of_aspect:
                        peaks = self._peaks(pts, pid, aspect_lon)
                        for series in _merge_peaks(peaks):
                            if series["exit"] < ctx.p0 or series["entry"] > ctx.p1:
                                continue
                            rows.append(self._series_row(
                                ctx, pts, pid, target, transit_name, natal_name, natal, aspect_name, angle,
                                aspect_lon, series, cusps))
        return rows

    @staticmethod
    def _peaks(pts: List[Point], pid: int, aspect_lon: float) -> List[Dict]:
        """Върховете на един аспект: точните преминавания през aspect_lon ("pass") и „касанията“ ("touch"): станция
        в орбис, при която планетата се връща, без да стане точен аспект. Около всеки връх има прозорец."""
        fvals = [_wrap180(p[1] - aspect_lon) for p in pts]
        peaks: List[Dict] = []
        for i in range(len(pts) - 1):
            fp, fq = fvals[i], fvals[i + 1]
            if abs(fp) >= 90.0 or abs(fq) >= 90.0:
                continue
            if not ((fp < 0.0 <= fq) or (fp > 0.0 >= fq)) or fp == 0.0:
                continue
            t_pass = _bisect(lambda t: _wrap180(_lon(t, pid) - aspect_lon), pts[i][0], pts[i + 1][0], fa=fp)
            entry = _boundary_time(pts, fvals, pid, aspect_lon, i, i + 1, t_pass, APPLYING_ORB, backward=True)
            exit_ = _boundary_time(pts, fvals, pid, aspect_lon, i, i + 1, t_pass, SEPARATING_ORB, backward=False)
            _, speed = _lon_speed(t_pass, pid)
            peaks.append({"kind": "pass", "t": t_pass, "entry": entry, "exit": exit_,
                          "clipped": entry <= pts[0][0] + 1e-9 or exit_ >= pts[-1][0] - 1e-9,
                          "motion": "retrograde" if speed < 0 else "direct"})
        for k, p in enumerate(pts):
            if not p[3] or fvals[k] == 0.0 or abs(fvals[k]) > APPLYING_ORB:
                continue
            entry = _boundary_time(pts, fvals, pid, aspect_lon, k - 1, k + 1, p[0], APPLYING_ORB, backward=True)
            exit_ = _boundary_time(pts, fvals, pid, aspect_lon, k - 1, k + 1, p[0], SEPARATING_ORB, backward=False)
            peaks.append({"kind": "touch", "t": p[0], "entry": entry, "exit": exit_,
                          "clipped": entry <= pts[0][0] + 1e-9 or exit_ >= pts[-1][0] - 1e-9, "motion": p[3]})
        return peaks

    def _series_row(self, ctx: "_Context", pts: List[Point], pid: int, target: str, transit_name: str,
                    natal_name: str, natal: Dict, aspect_name: str, angle: float, aspect_lon: float, series: Dict,
                    cusps: Dict) -> Dict:
        in_period, outside = [], []
        passes = [p for p in series["peaks"] if p["kind"] == "pass"]
        for p in passes:
            local = ctx.local(p["t"])
            item = {"when": local.strftime("%Y-%m-%d %H:%M"), "motion": p["motion"], "_utc": _utc_text(p["t"])}
            (in_period if ctx.start <= local.date() <= ctx.end else outside).append(item)
        window_from = ctx.local(series["entry"])
        window_to = ctx.local(series["exit"])
        house = house_for_longitude(aspect_lon, cusps) if cusps else None
        row: Dict = {
            "id": "", "type": "TRANSIT", "target": target,
            "planet": transit_name, "natal_planet": natal_name,
            "aspect": aspect_name, "angle_deg": angle,
            "exact": [{"when": x["when"], "motion": x["motion"]} for x in in_period],
            "active_from": window_from.strftime("%Y-%m-%d"), "active_to": window_to.strftime("%Y-%m-%d"),
            "natal_position": decimal_to_dms(natal["longitude"])["str"],
            "_exact_utc": [x["_utc"] for x in in_period],
            "_pass_count": len(passes),
        }
        # Без час на раждане няма домове: полетата за дом не се пращат (null би подканил към измислен дом)
        if house is not None:
            row["transit_planet_natal_house"] = house
        if natal.get("house") is not None:
            row["natal_planet_natal_house"] = natal["house"]
        if not passes:
            row["turns_back"] = True
        if outside:
            row["exact_outside_period"] = [x["when"] for x in outside]
        if series["clipped"]:
            row["window_clipped"] = True
        if not in_period:
            closest = _closest_in_period(ctx, pts, pid, aspect_lon)
            if closest:
                row["closest_in_period"] = closest
        first = in_period[0]["when"] if in_period else max(row["active_from"], ctx.start.isoformat()) + " 00:00"
        row["_sort"] = first
        return row


class _Context:
    """Периодът и зоната, в която се показват часовете."""

    def __init__(self, tz, zone: str, start: date, end: date, p0: float, p1: float):
        self.tz, self.zone, self.start, self.end, self.p0, self.p1 = tz, zone, start, end, p0, p1

    def local(self, jd: float) -> datetime:
        return _round_minute(jd_to_utc(jd)).astimezone(self.tz)

    def make_point(self, jd: float, type_: str, planet: str, extra: Dict) -> Optional[Dict]:
        """Точково събитие или None, ако е извън периода (по местна дата)."""
        local = self.local(jd)
        if not (self.start <= local.date() <= self.end):
            return None
        row = {"id": "", "type": type_, "when": local.strftime("%Y-%m-%d %H:%M"), "planet": planet}
        row.update(extra)
        row["_utc"] = _utc_text(jd)
        return row


def _utc_text(jd: float) -> str:
    return jd_to_utc(jd).strftime("%Y-%m-%dT%H:%M:%SZ")


def _eclipse_kind(flag: int) -> str:
    if flag & 4:
        return "total"
    if flag & 32:
        return "hybrid"
    if flag & 8:
        return "annular"
    if flag & 16:
        return "partial"
    if flag & 64:
        return "penumbral"
    return "unknown"


def _boundary_time(pts: List[Point], fvals: List[float], pid: int, aspect_lon: float, last_before: int,
                   first_after: int, t_peak: float, limit: float, backward: bool) -> float:
    """Кога планетата влиза в (backward) или излиза от орбиса limit около върха. last_before е последната точка
    от мрежата преди върха, first_after е първата след него. Ако мрежата свърши, нейната граница е границата
    на прозореца."""
    def h(t: float) -> float:
        return abs(_wrap180(_lon(t, pid) - aspect_lon)) - limit

    if backward:
        for j in range(last_before, -1, -1):
            if abs(fvals[j]) >= limit:
                hi = t_peak if j == last_before else pts[j + 1][0]
                return _bisect(h, pts[j][0], hi, fa=h(pts[j][0]))
        return pts[0][0]
    if abs(_wrap180(_lon(t_peak, pid) - aspect_lon)) > limit:
        return t_peak                      # връх, който вече е извън орбиса за отделяне (само при „касане“)
    for j in range(first_after, len(pts)):
        if abs(fvals[j]) > limit:
            lo = t_peak if j == first_after else pts[j - 1][0]
            return _bisect(h, lo, pts[j][0], fa=h(lo))
    return pts[-1][0]


def _merge_peaks(peaks: List[Dict]) -> List[Dict]:
    """Върхове със застъпващи се прозорци са една серия (ретроградна примка)."""
    series: List[Dict] = []
    for p in sorted(peaks, key=lambda x: x["t"]):
        if series and p["entry"] <= series[-1]["exit"]:
            cur = series[-1]
            cur["peaks"].append(p)
            cur["exit"] = max(cur["exit"], p["exit"])
            cur["entry"] = min(cur["entry"], p["entry"])
            cur["clipped"] = cur["clipped"] or p["clipped"]
        else:
            series.append({"peaks": [p], "entry": p["entry"], "exit": p["exit"], "clipped": p["clipped"]})
    return series


def _closest_in_period(ctx: "_Context", pts: List[Point], pid: int, aspect_lon: float) -> Optional[Dict]:
    """Най-малкият орбис в периода, когато аспектът е активен, но няма точен момент в него. Между станциите
    орбисът е монотонен, затова минимумът е в началото, в края или в станция вътре в периода. Час се дава само за
    станция (обръщане на планетата); за началото и края на периода се казва само „at“, защото това не е събитие."""
    candidates = [(ctx.p0, "start_of_period"), (ctx.p1 - 1.0 / 1440.0, "end_of_period")]
    candidates += [(p[0], None) for p in pts if p[3] and ctx.p0 <= p[0] < ctx.p1]
    orb, t, at = min(((abs(_wrap180(_lon(t, pid) - aspect_lon)), t, at) for t, at in candidates), key=lambda x: x[0])
    if at:
        return {"orb": round(orb, 2), "at": at}
    return {"when": ctx.local(t).strftime("%Y-%m-%d %H:%M"), "orb": round(orb, 2)}


def _event_order_key(row: Dict) -> Tuple[str, int]:
    if row["type"] == "TRANSIT":
        return (row["_sort"], 2)
    return (row["when"], 1)


def _number_series(events: List[Dict]) -> None:
    """Стабилни номера: AS:<човек>:<планета>:<аспект>:<натална точка>, а при повторение в периода #2, #3..."""
    seen: Dict[str, int] = {}
    for e in events:
        if e["type"] != "TRANSIT":
            continue
        base = f"AS:{e['target']}:{e['planet']}:{e['aspect']}:{e['natal_planet']}"
        seen[base] = seen.get(base, 0) + 1
        e["id"] = base if seen[base] == 1 else f"{base}#{seen[base]}"
