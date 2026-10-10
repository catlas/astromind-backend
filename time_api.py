"""
Проверка на местния час и показване на часовата зона (Фаза 12).

GET /time-check казва в коя зона е мястото, какво е отместването спрямо UTC на тази дата и дали въведеният час
съществува: часът може да е прескочен при преминаване към лятно време или да се повтаря при връщане назад. Екранът го
вика, докато потребителят въвежда, за да покаже зоната и да поиска избор преди анализа, вместо да поправя часа тихо.
"""
from typing import Optional

from fastapi import APIRouter, HTTPException, Query, Request

import engine
from engine import utc_offset_label as offset_label
from rate_limit import client_ip, enforce

router = APIRouter()


@router.get("/time-check")
def time_check(
    http_request: Request,
    date: str,
    time: str,
    lat: float = Query(..., ge=-90, le=90),
    lon: float = Query(..., ge=-180, le=180),
    fold: Optional[int] = Query(None, ge=0, le=1),
):
    enforce(f"timecheck:{client_ip(http_request)}", 120, 60, "Твърде много проверки на часа.")
    try:
        resolved = engine.get_engine().resolve_local_time(date, time, lat, lon, fold)
    except (ValueError, IndexError, OverflowError):
        raise HTTPException(status_code=400, detail="Невалидна дата или час.")

    zone = resolved["timezone"]
    out = {"timezone": zone, "zone_found": resolved["zone_found"], "at_sea": resolved["at_sea"],
           "status": resolved["status"], "options": resolved.get("options", []), "message": "",
           "next_valid_time": resolved.get("next_valid_time")}
    if not resolved["zone_found"]:
        out["message"] = "За тези координати няма часова зона. Ползва се UTC; проверете мястото."
    elif resolved["status"] == "ok":
        minutes = resolved["utc_offset_minutes"]
        out["utc_offset_minutes"] = minutes
        out["utc_offset"] = offset_label(minutes)
        out["message"] = f"Часова зона: {zone} ({offset_label(minutes)} на тази дата)."
        if resolved["at_sea"]:
            out["message"] += " Координатите са в открито море: проверете мястото (често е объркана ширина с дължина)."
    elif resolved["status"] == "nonexistent":
        hint = f" Най-близкият валиден час е {resolved['next_valid_time']}." if resolved.get("next_valid_time") else ""
        out["message"] = (f"Часът {time} на {date} не съществува в {zone}: тогава часовникът е преместен напред при "
                          f"смяната към лятно време.{hint}")
    else:
        out["message"] = (f"Часът {time} на {date} се повтаря в {zone}: часовникът е върнат назад. "
                          f"Изберете първото или второто преминаване.")
    return out
