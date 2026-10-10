"""
Износ на запазен отчет (Фаза 13): DOCX и Markdown. Работи само върху запазения отчет на собственика: същият текст и същата
дата като в Историята, картите се смятат наново от запазените входни данни (същите входни данни дават същата карта).
Износът не вика AI и не струва нищо.
"""
import re
from datetime import datetime
from typing import Dict, List, Optional

import bg_text
import engine
import factpack
import relationship
import report_text
from aspects_engine import calculate_natal_aspects
from database import Report
from docx_generator import DOCXGenerator, PLANET_NAMES

KIND_LABELS = {
    "general": "Общ анализ", "health": "Здраве", "career": "Кариера", "money": "Пари и успех", "love": "Любов",
    "karmic": "Карма и род",
}
MARKDOWN_TYPE = "text/markdown; charset=utf-8"
DOCX_TYPE = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"


def _chart(params: Dict, partner: bool = False) -> Optional[Dict]:
    """Картата от запазените данни; None, ако данните не стигат (стар или внесен отчет)."""
    p = "partner_" if partner else ""
    date, time, lat, lon = (params.get(p + key) for key in ("date", "time", "lat", "lon"))
    known = params.get("partner_time_known" if partner else "birth_time_known", True) is not False
    if not date or lat is None or lon is None or (known and not time):
        return None
    try:
        return engine.get_engine().calculate_chart(date, time or "12:00", float(lat), float(lon),
                                                   fold=params.get("partner_fold" if partner else "birth_fold"),
                                                   time_known=known)
    except Exception:
        return None


def _transit_entry(params: Dict, natal: Optional[Dict]) -> Optional[Dict]:
    """Небето на избрания момент (анализ за дата), както е ползвано при генерирането: същите дата, час и координати.
    Без запазен час (стар отчет) не се измисля карта."""
    if params.get("is_dynamic") or not params.get("target_date") or not params.get("target_time"):
        return None
    lat = params.get("target_lat") if params.get("target_lat") is not None else params.get("lat")
    lon = params.get("target_lon") if params.get("target_lon") is not None else params.get("lon")
    if lat is None or lon is None:
        return None
    try:
        sky = engine.get_engine().calculate_chart(params["target_date"], params["target_time"], float(lat), float(lon),
                                                  fold=params.get("target_fold"), zone=params.get("forecast_timezone"))
    except Exception:
        return None
    rows = []
    if natal:
        names = PLANET_NAMES
        for a in factpack.transit_aspects(natal, sky):
            rows.append({"planet1": f"транзитен {names.get(a['transit_planet'], a['transit_planet'])}",
                         "planet2": f"натален {names.get(a['natal_planet'], a['natal_planet'])}",
                         "aspect": a["aspect"], "orb": a["orb"]})
    when = f"{'.'.join(reversed(str(params['target_date']).split('-')))} {str(params['target_time'])[:5]}"
    offset = engine.utc_offset_label(sky.get("utc_offset_minutes") or 0)
    return {"title": f"Небе на {when} ({offset}, транзити)", "chart": sky, "aspects": rows}


def _person(params: Dict, prefix: str, fallback_name: str) -> Optional[Dict]:
    name = (params.get(prefix + "name") or "").strip() or fallback_name
    date = params.get(prefix + "date")
    if prefix and not date:
        return None
    known = params.get("partner_time_known" if prefix else "birth_time_known", True) is not False
    time = params.get(prefix + "time")
    birth = ""
    if date:
        birth = f"Дата на раждане: {'.'.join(reversed(str(date).split('-')))}"
        birth += f", {time}" if known and time else ", часът е неизвестен"
    lat, lon = params.get(prefix + "lat"), params.get(prefix + "lon")
    place = f"Координати: {lat}, {lon}" if lat is not None and lon is not None else ""
    return {"name": name, "birth": birth, "place": place}


def context(report: Report) -> Dict:
    params = report.params or {}
    people: List[Dict] = []
    first = _person(params, "", report.profile_name or "Първи човек")
    if first:
        people.append(first)
    second = _person(params, "partner_", "Втори човек")
    if second:
        people.append(second)
    charts = []
    natal = _chart(params)
    if natal:
        charts.append({"title": f"Карта: {people[0]['name']}" if people else "Карта", "chart": natal,
                       "aspects": calculate_natal_aspects(natal, use_wider_orbs=False)})
    partner = _chart(params, partner=True) if second else None
    if partner:
        charts.append({"title": f"Карта: {second['name']}", "chart": partner,
                       "aspects": calculate_natal_aspects(partner, use_wider_orbs=False)})
    transit = _transit_entry(params, natal)
    if transit:
        charts.append(transit)
    period = ""
    if params.get("is_dynamic") and params.get("target_date") and params.get("end_date"):
        period = f"{'.'.join(reversed(params['target_date'].split('-')))} – {'.'.join(reversed(params['end_date'].split('-')))}"
    zone = (natal or {}).get("timezone")
    return {
        "title": report.label, "kind": KIND_LABELS.get(report.report_type, "Анализ"),
        "created": report.created_at.strftime("%d.%m.%Y") if report.created_at else "",
        "people": people, "charts": charts, "period": period,
        "relationship": relationship.LABELS.get(params.get("relationship", ""), "") if second else "",
        "zone": zone,
        "sections": [{"title": title, "text": text} for title, text in report_text.split_sections(
            bg_text.for_report(report.content, params, report.profile_name or ""))],
    }


def docx_bytes(report: Report) -> bytes:
    return DOCXGenerator().generate_docx(context(report))


def markdown_text(report: Report) -> str:
    ctx = context(report)
    lines = [f"# {ctx['title']}", ""]
    meta = [("Вид анализ", ctx["kind"]), ("Отношения", ctx["relationship"]), ("Период", ctx["period"]),
            ("Часова зона", ctx["zone"]), ("Създаден на", ctx["created"])]
    for person in ctx["people"]:
        lines.append(" · ".join([f"**{person['name']}**"] + [x for x in (person["birth"], person["place"]) if x]))
    lines.append("")
    lines += [f"- {name}: {value}" for name, value in meta if value]
    lines += ["", "---", ""]
    for section in ctx["sections"]:
        if section["title"]:
            lines += [f"## {section['title']}", ""]
        lines += [report_text.to_markdown(section["text"]).rstrip(), ""]
    lines += ["---", "", "*AstroMind · само за занимателна цел.*", ""]
    return "\n".join(lines)


def filename(report: Report, extension: str) -> str:
    """Безопасно име: само латиница, цифри и тирета; без имена, имейл и други лични данни."""
    created = report.created_at.strftime("%Y-%m-%d") if report.created_at else datetime.utcnow().strftime("%Y-%m-%d")
    kind = re.sub(r"[^a-z]", "", (report.report_type or "report").lower()) or "report"
    return f"AstroMind-{kind}-{created}-{int(report.id)}.{extension}"
