"""
Неизменяем изчислителен снимка на отчета (R06, регресионен одит 11.10.2026).

При успешно генериране се запазват всички изчислени данни, върху които е написан текстът: картите на двамата, небето на избрания
момент или календарът на периода, моментите в UTC, зоните и координатите на всяка карта, отношенията, версиите на двигателя и
отпечатъкът на ефемеридите. Износът (DOCX, Markdown) ползва снимката, а не текущия двигател: след промяна на версия или на профил
старият износ има същите числа, карти и текст. content_sha256 е отпечатък на запазения текст.

Старите отчети (преди снимката) нямат такава и не получават измислена: report_export ги обозначава като „legacy“ и ги възстановява
от запазените входни данни с изричен знак, без гаранция за идентични числа.
"""
import hashlib
import json
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

import aspects_engine
import engine
import factpack
import relationship
import safety
import scanner
import text_check
import theme_style

SNAPSHOT_VERSION = 1


def prompt_fingerprint(theme: Optional[str] = None) -> str:
    """Отпечатък на правилата, добавяни към всяка заявка към AI (безопасност и качество): променят се само с версия на текстовете.
    „Здраве“ и „Карма и род“ имат свой отпечатък (заместено правило и стил на темата); за всяка друга тема е същият като преди."""
    rules = safety.rules_for(theme) + safety.QUALITY_RULES + theme_style.block(theme)
    return hashlib.sha256(rules.encode("utf-8")).hexdigest()[:12]


def versions(theme: Optional[str] = None) -> Dict[str, str]:
    return {"snapshot": str(SNAPSHOT_VERSION), "calendar": scanner.CALENDAR_VERSION, "factpack": factpack.FACTPACK_VERSION,
            "orbs": aspects_engine.ORB_POLICY_VERSION, "checker": text_check.CHECKER_VERSION, "prompt": prompt_fingerprint(theme)}


def _person(role: str, name: Optional[str], profile_id: Optional[int], chart: Dict, *, fold: Optional[int]) -> Dict[str, Any]:
    location = chart.get("location") or {}
    local = str(chart.get("datetime_local") or "")
    known = chart.get("time_known") is not False
    try:
        natal_aspects = [{"planet1": a["planet1"], "planet2": a["planet2"], "aspect": a["aspect"], "orb": a.get("orb")}
                         for a in aspects_engine.calculate_natal_aspects(chart, use_wider_orbs=False)]
    except Exception:
        natal_aspects = None                       # без аспекти износът показва само позициите; не се измисля
    return {
        "role": role, "name": (name or "").strip() or None, "profile_id": profile_id,
        "birth": {
            "date": local[:10] or None, "time": local[11:16] if known and len(local) >= 16 else None, "time_known": known, "fold": fold,
            "lat": location.get("latitude"), "lon": location.get("longitude"),
            "zone": chart.get("timezone"), "utc": chart.get("datetime_utc"), "utc_offset_minutes": chart.get("utc_offset_minutes"),
        },
        "natal_aspects": natal_aspects,
        "chart": chart,
    }


def _transit_aspects(natal: Dict, transit: Dict) -> Optional[List[Dict[str, Any]]]:
    try:
        return [{"transit_planet": a["transit_planet"], "natal_planet": a["natal_planet"], "aspect": a["aspect"], "orb": a.get("orb")}
                for a in factpack.transit_aspects(natal, transit)]
    except Exception:
        return None


def build(*, mode: str, request: Any, natal: Dict, partner: Optional[Dict] = None, transit: Optional[Dict] = None,
          forecast_zone: Optional[str] = None, target_lat: Optional[float] = None, target_lon: Optional[float] = None,
          calendar: Any = None) -> Dict[str, Any]:
    """mode: natal | snapshot | period. request е ChartRequest; calendar е scanner.PeriodCalendar (само за период)."""
    people: List[Dict[str, Any]] = [_person("user", request.name, request.profile_id, natal, fold=request.birth_fold)]
    if partner is not None:
        people.append(_person("partner", request.partner_name, request.partner_profile_id, partner, fold=request.partner_fold))
    snapshot: Dict[str, Any] = {
        "version": SNAPSHOT_VERSION,
        "created_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "mode": mode,
        "versions": versions(getattr(request, "report_type", None)),
        "ephemeris": {"source": (natal.get("ephemeris") or {}).get("source"), "fingerprint": engine.ephemeris_fingerprint(),
                      "years": list(engine.supported_year_range()),
                      "missing": sorted({n for c in (natal, partner, transit) for n in engine.missing_bodies(c)})},
        "house_systems": {p["role"]: p["chart"].get("house_system") for p in people},
        "people": people,
        "relationship": ({"kind": relationship.normalize(request.relationship), "parent": request.parent_role or None}
                         if partner is not None else None),
        "forecast_zone": forecast_zone,
    }
    if mode == "snapshot" and transit is not None:
        snapshot["target"] = {
            "date": request.target_date, "time": request.target_time, "zone": forecast_zone,
            "lat": target_lat if target_lat is not None else request.lat, "lon": target_lon if target_lon is not None else request.lon,
            "relocated": target_lat is not None or target_lon is not None,
            "utc": transit.get("datetime_utc"), "chart": transit,
            "aspects": {p["role"]: _transit_aspects(p["chart"], transit) for p in people},
        }
    if mode == "period" and calendar is not None:
        snapshot["period"] = {"start": calendar.start, "end": calendar.end, "zone": calendar.timezone,
                              "lat": request.lat, "lon": request.lon, "start_chart": transit}
        snapshot["calendar"] = {"version": calendar.version, "timezone": calendar.timezone, "start": calendar.start, "end": calendar.end,
                                "events": calendar.public_events(), "context_events": [calendar.public(e) for e in calendar.context_events]}
    return snapshot


def seal(snapshot: Dict[str, Any], content: str) -> Dict[str, Any]:
    """Добавя отпечатъка на запазения текст. Вика се при запис, с текста точно както е записан."""
    snapshot = dict(snapshot)
    snapshot["content_sha256"] = hashlib.sha256((content or "").encode("utf-8")).hexdigest()
    return snapshot


def is_legacy(snapshot: Optional[Dict[str, Any]]) -> bool:
    return not snapshot or snapshot.get("version") is None


def person(snapshot: Dict[str, Any], role: str) -> Optional[Dict[str, Any]]:
    return next((p for p in snapshot.get("people") or [] if p.get("role") == role), None)


def verify(snapshot: Dict[str, Any], content: str) -> bool:
    """Текстът на отчета е същият, за който е направен снимкът."""
    return bool(snapshot) and snapshot.get("content_sha256") == hashlib.sha256((content or "").encode("utf-8")).hexdigest()


def dumps(snapshot: Dict[str, Any]) -> str:
    return json.dumps(snapshot, ensure_ascii=False, sort_keys=True, default=str)
