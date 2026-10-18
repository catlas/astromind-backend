"""
Износ на запазен отчет (Фаза 13, R06 във Фаза 24): DOCX и Markdown. Работи само върху запазения отчет на собственика и не вика AI.

От Фаза 24 износът е от неизменяемия изчислителен снимка, записан при генерирането (report_snapshot.py): картите, небето, календарът,
моментите в UTC, зоните и координатите са точно тези, върху които е написан текстът. Промяна на версия на двигателя или редакция на
профил не променя стар износ.

Отчет без снимка (стар, преди Фаза 24) е „legacy“: картите се възстановяват от запазените входни данни и износът го казва изрично;
не се измисля снимка и няма гаранция, че числата са същите като при генерирането.
"""
import re
from datetime import datetime
from typing import Any, Dict, List, Optional

import bg_text
import engine
import factpack
import relationship
import report_snapshot
import report_text
from aspects_engine import calculate_natal_aspects
from database import Report
from docx_generator import ASPECT_NAMES, DOCXGenerator, HOUSE_SUFFIX, PLANET_NAMES, SIGN_NAMES, _sign_bg

KIND_LABELS = {
    "general": "Общ анализ", "health": "Здраве", "career": "Кариера", "money": "Пари и успех", "love": "Любов",
    "karmic": "Карма и род",
}
MARKDOWN_TYPE = "text/markdown; charset=utf-8"
DOCX_TYPE = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
LEGACY_NOTE = ("Стар отчет (legacy): записан е преди запазването на изчисленията. Картите са възстановени от запазените входни данни; "
               "няма гаранция, че числата са същите като при генерирането.")


def _bg_date(value: Optional[str]) -> str:
    return ".".join(reversed(str(value).split("-"))) if value else ""


# ---------------------------------------------------------------------------------------------------------------------
# Стар отчет (legacy): картите се смятат наново от запазените входни данни
# ---------------------------------------------------------------------------------------------------------------------
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


def _transit_rows(natal: Optional[Dict], sky: Dict) -> List[Dict]:
    rows = []
    if natal:
        for a in factpack.transit_aspects(natal, sky):
            rows.append({"planet1": f"транзитен {PLANET_NAMES.get(a['transit_planet'], a['transit_planet'])}",
                         "planet2": f"натален {PLANET_NAMES.get(a['natal_planet'], a['natal_planet'])}",
                         "aspect": a["aspect"], "orb": a["orb"]})
    return rows


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
    when = f"{_bg_date(params['target_date'])} {str(params['target_time'])[:5]}"
    offset = engine.utc_offset_label(sky.get("utc_offset_minutes") or 0)
    return {"title": f"Небе на {when} ({offset}, транзити)", "chart": sky, "aspects": _transit_rows(natal, sky)}


def _person_legacy(params: Dict, prefix: str, fallback_name: str) -> Optional[Dict]:
    name = (params.get(prefix + "name") or "").strip() or fallback_name
    date = params.get(prefix + "date")
    if prefix and not date:
        return None
    known = params.get("partner_time_known" if prefix else "birth_time_known", True) is not False
    time = params.get(prefix + "time")
    birth = ""
    if date:
        birth = f"Дата на раждане: {_bg_date(date)}"
        birth += f", {time}" if known and time else ", часът е неизвестен"
    lat, lon = params.get(prefix + "lat"), params.get(prefix + "lon")
    place = f"Координати: {lat}, {lon}" if lat is not None and lon is not None else ""
    return {"name": name, "birth": birth, "place": place, "zone": ""}


def _legacy_context(report: Report) -> Dict[str, Any]:
    params = report.params or {}
    people: List[Dict] = []
    first = _person_legacy(params, "", report.profile_name or "Първи човек")
    if first:
        people.append(first)
    second = _person_legacy(params, "partner_", "Втори човек")
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
        period = f"{_bg_date(params['target_date'])} – {_bg_date(params['end_date'])}"
    return {
        "legacy": True, "legacy_note": LEGACY_NOTE, "people": people, "charts": charts, "period": period,
        "relationship": relationship.LABELS.get(params.get("relationship", ""), "") if second else "",
        "zone": (natal or {}).get("timezone") or "", "forecast_zone": params.get("forecast_timezone") or "",
        "forecast_moment": "", "calendar_rows": [], "provenance": [], "notes": [],
    }


# ---------------------------------------------------------------------------------------------------------------------
# Отчет със снимка: всичко е от запазените изчисления
# ---------------------------------------------------------------------------------------------------------------------
def _offset_text(minutes: Optional[int]) -> str:
    return engine.utc_offset_label(minutes or 0)


def _person_snapshot(entry: Dict[str, Any], fallback: str) -> Dict[str, Any]:
    birth = entry.get("birth") or {}
    text = f"Дата на раждане: {_bg_date(birth.get('date'))}"
    text += f", {birth['time']}" if birth.get("time_known") and birth.get("time") else ", часът е неизвестен"
    place = f"Координати: {birth.get('lat')}, {birth.get('lon')}" if birth.get("lat") is not None and birth.get("lon") is not None else ""
    zone = birth.get("zone") or ""
    if zone:
        zone += f" ({_offset_text(birth.get('utc_offset_minutes'))})"
    return {"name": entry.get("name") or fallback, "birth": text, "place": place, "zone": zone, "utc": birth.get("utc") or ""}


def _calendar_rows(snapshot: Dict[str, Any]) -> List[Dict[str, str]]:
    """Редове за таблицата на периода: дата и час, вид, събитие (от запазения календар, без нови изчисления)."""
    calendar = snapshot.get("calendar") or {}
    rows: List[Dict[str, str]] = []
    for e in calendar.get("events") or []:
        kind = e.get("type")
        if kind == "TRANSIT":
            who = {"User": "първи човек", "Partner": "втори човек"}.get(e.get("target"), "")
            exact = ", ".join(_when(x["when"]) for x in e.get("exact") or []) or "няма точен момент в периода"
            what = (f"транзитен {PLANET_NAMES.get(e.get('planet'), e.get('planet'))} {ASPECT_NAMES.get(str(e.get('aspect')).lower(), e.get('aspect'))} "
                    f"натален {PLANET_NAMES.get(e.get('natal_planet'), e.get('natal_planet'))} ({who}); активен {_bg_date(e.get('active_from'))}–{_bg_date(e.get('active_to'))}")
            rows.append({"when": exact, "kind": "Аспект", "what": what})
        elif e.get("when"):
            labels = {"RETROGRADE": "Станция", "INGRESS": "Влизане в знак", "LUNATION": "Лунация", "ECLIPSE": "Затъмнение"}
            rows.append({"when": _when(e["when"]), "kind": labels.get(kind, str(kind)), "what": _event_text(e)})
    return rows


def _when(value: str) -> str:
    return f"{_bg_date(value[:10])} {value[11:16]}".strip() if value else ""


_FEMININE_BODIES = {"Venus", "Moon"}                  # Венера и Луна: „става директна“, „ретроградна“
_STATION_RE = re.compile(r"^(\w+) turns (Retrograde|Direct) in (\w+)$")
_INGRESS_RE = re.compile(r"^(\w+) enters (\w+)( \(moving retrograde\))?$")
_LUNATION_RE = re.compile(r"^(New|Full) Moon in (\w+)")
_ECLIPSE_RE = re.compile(r"^(Solar|Lunar) Eclipse in (\w+)")


def _in_sign(english_sign: str) -> str:
    return bg_text.in_form(SIGN_NAMES.get(english_sign, english_sign))


def _event_text(e: Dict[str, Any]) -> str:
    """Събитие от календара на български (таблицата на износа). Формите са целите изрази на календара, а не замяна дума по дума:
    „New Moon in Libra“ → „Новолуние във Везни“, „Pluto turns Direct in Aquarius“ → „Плутон става директен във Водолей“."""
    text = str(e.get("event") or "").strip()
    m = _STATION_RE.match(text)
    if m:
        planet, direction, sign = m.groups()
        word = {"Retrograde": ("ретрограден", "ретроградна"), "Direct": ("директен", "директна")}[direction][planet in _FEMININE_BODIES]
        return f"{PLANET_NAMES.get(planet, planet)} става {word} {_in_sign(sign)}"
    m = _INGRESS_RE.match(text)
    if m:
        planet, sign, back = m.groups()
        return f"{PLANET_NAMES.get(planet, planet)} влиза {_in_sign(sign)}" + (" (на ретрограден ход)" if back else "")
    m = _LUNATION_RE.match(text)
    if m:
        return f"{'Новолуние' if m.group(1) == 'New' else 'Пълнолуние'} {_in_sign(m.group(2))}"
    m = _ECLIPSE_RE.match(text)
    if m:
        return f"{'Слънчево' if m.group(1) == 'Solar' else 'Лунно'} затъмнение {_in_sign(m.group(2))}"
    # непознат вид събитие: старата замяна дума по дума (планети и знаци на български), без да се измислят изрази
    text = re.sub(r"\b(" + "|".join(PLANET_NAMES) + r")\b", lambda m2: PLANET_NAMES[m2.group(1)], text)
    for en, bg in SIGN_NAMES.items():
        text = text.replace(en, bg)
    return text


def _snapshot_context(report: Report, snapshot: Dict[str, Any]) -> Dict[str, Any]:
    people_raw = snapshot.get("people") or []
    people = [_person_snapshot(p, "Първи човек" if p.get("role") == "user" else "Втори човек") for p in people_raw]
    charts = [{"title": f"Карта: {person['name']}", "chart": entry.get("chart"), "aspects": entry.get("natal_aspects") or []}
              for person, entry in zip(people, people_raw)]
    mode = snapshot.get("mode")
    target = snapshot.get("target") or {}
    zone = snapshot.get("forecast_zone") or ""
    forecast_moment, period = "", ""
    if mode == "snapshot" and target.get("chart"):
        sky = target["chart"]
        offset = _offset_text(sky.get("utc_offset_minutes"))
        local = f"{_bg_date(target.get('date'))} {str(target.get('time') or '')[:5]}".strip()
        forecast_moment = f"{local} ({offset}, {zone}) · UTC {str(target.get('utc') or '')[:16].replace('T', ' ')}"
        if target.get("lat") is not None and target.get("lon") is not None:
            forecast_moment += f" · небе за {target['lat']}, {target['lon']}" + (" (релокация)" if target.get("relocated") else " (мястото на раждане)")
        user_aspects = (target.get("aspects") or {}).get("user") or []
        rows = [{"planet1": f"транзитен {PLANET_NAMES.get(a['transit_planet'], a['transit_planet'])}",
                 "planet2": f"натален {PLANET_NAMES.get(a['natal_planet'], a['natal_planet'])}",
                 "aspect": a["aspect"], "orb": a["orb"]} for a in user_aspects]
        charts.append({"title": f"Небе на {local} ({offset}, транзити)", "chart": sky, "aspects": rows})
    if mode == "period":
        pr = snapshot.get("period") or {}
        period = f"{_bg_date(pr.get('start'))} – {_bg_date(pr.get('end'))}"
    versions = snapshot.get("versions") or {}
    eph = snapshot.get("ephemeris") or {}
    provenance = [
        ("Снимка на изчисленията", f"версия {versions.get('snapshot', '?')}, записана {str(snapshot.get('created_at') or '')[:19].replace('T', ' ')} UTC"),
        ("Ефемериди", f"{eph.get('source') or 'неизвестен източник'}, отпечатък {eph.get('fingerprint') or '?'}"),
        ("Версии", f"календар {versions.get('calendar', '?')}, факти {versions.get('factpack', '?')}, орбиси {versions.get('orbs', '?')}, "
                   f"проверка {versions.get('checker', '?')}, правила {versions.get('prompt', '?')}"),
        ("Отпечатък на текста", f"sha256 {str(snapshot.get('content_sha256') or '')[:16]}"),
    ]
    notes = [f"Домовете на {person['name']} са по системата Porphyry: мястото на раждане е над полярния кръг и Placidus не е определена там."
             for person, entry in zip(people, people_raw) if (entry.get("chart") or {}).get("house_system") == "porphyry_fallback"]
    return {
        "legacy": False, "legacy_note": "", "people": people, "charts": charts, "period": period,
        "relationship": relationship.LABELS.get((snapshot.get("relationship") or {}).get("kind", ""), "") if len(people) > 1 else "",
        "zone": "", "forecast_zone": zone, "forecast_moment": forecast_moment,
        "calendar_rows": _calendar_rows(snapshot) if mode == "period" else [],
        "provenance": provenance, "notes": notes,
    }


def context(report: Report) -> Dict[str, Any]:
    snapshot = report.snapshot
    base = _legacy_context(report) if report_snapshot.is_legacy(snapshot) else _snapshot_context(report, snapshot)
    base.update({
        "title": report.label, "kind": KIND_LABELS.get(report.report_type, "Анализ"),
        "created": report.created_at.strftime("%d.%m.%Y") if report.created_at else "",
        "sections": [{"title": title, "text": text} for title, text in report_text.split_sections(
            bg_text.for_report(report.content, report.params, report.profile_name or ""))],
    })
    return base


def docx_bytes(report: Report) -> bytes:
    return DOCXGenerator().generate_docx(context(report))


# ---------------------------------------------------------------------------------------------------------------------
# Markdown: метаданни, таблици на изчисленията и текст
# ---------------------------------------------------------------------------------------------------------------------
def _md_table(headers: List[str], rows: List[List[str]]) -> List[str]:
    clean = lambda value: str(value).replace("|", "/").replace("\n", " ")
    lines = ["| " + " | ".join(headers) + " |", "|" + "|".join("---" for _ in headers) + "|"]
    lines += ["| " + " | ".join(clean(c) for c in row) + " |" for row in rows]
    return lines + [""]


def _chart_tables(chart_data: Dict[str, Any]) -> List[str]:
    chart = chart_data.get("chart") or {}
    known = chart.get("time_known") is not False
    ranges = chart.get("sign_ranges") or {}
    lines = [f"### {chart_data.get('title') or 'Карта'}", ""]
    rows = []
    for name, planet in (chart.get("planets") or {}).items():
        if not planet:
            continue
        if not known and name in ranges and (name == "Moon" or len(ranges[name].get("signs", [])) > 1):
            position = " или ".join(SIGN_NAMES.get(s, s) for s in ranges[name].get("signs", [])) + " (според часа)"
        elif planet.get("formatted_pos"):
            position = _sign_bg(planet["formatted_pos"])
        else:
            continue
        house = f"{planet['house']}-{HOUSE_SUFFIX.get(int(planet['house']), 'ти')} дом" if known and planet.get("house") else "—"
        motion = "ретрограден" if (planet.get("speed") or 0) < 0 else "директен" if planet.get("speed") is not None else "—"
        rows.append([PLANET_NAMES.get(name, name), position, house, motion])
    angles = chart.get("angles") or {}
    if known and angles.get("Ascendant") is not None:
        rows.append(["Асцендент", _sign_bg(angles.get("Ascendant_formatted", "")), "—", "—"])
        rows.append(["MC", _sign_bg(angles.get("MC_formatted", "")), "—", "—"])
    lines += _md_table(["Тяло", "Позиция", "Дом", "Движение"], rows)
    aspects = chart_data.get("aspects") or []
    if aspects:
        lines += _md_table(["Двойка", "Аспект", "Орбис"], [
            [f"{PLANET_NAMES.get(a['planet1'], a['planet1'])} – {PLANET_NAMES.get(a['planet2'], a['planet2'])}",
             ASPECT_NAMES.get(a["aspect"], a["aspect"]), f"{a['orb']:.2f}°" if a.get("orb") is not None else "—"] for a in aspects])
    return lines


def markdown_text(report: Report) -> str:
    ctx = context(report)
    lines = [f"# {ctx['title']}", ""]
    meta = [("Вид анализ", ctx["kind"]), ("Отношения", ctx["relationship"]), ("Период", ctx["period"]),
            ("Рождена зона", "; ".join(f"{p['name']}: {p['zone']}" for p in ctx["people"] if p.get("zone")) or ctx.get("zone", "")),
            ("Прогнозна зона", ctx["forecast_zone"]), ("Момент на прогнозата", ctx["forecast_moment"]), ("Създаден на", ctx["created"])]
    for person in ctx["people"]:
        lines.append(" · ".join([f"**{person['name']}**"] + [x for x in (person["birth"], person["place"]) if x]))
    lines.append("")
    lines += [f"- {name}: {value}" for name, value in meta if value]
    if ctx["legacy"]:
        lines += ["", f"> {ctx['legacy_note']}"]
    for note in ctx["notes"]:
        lines += ["", f"> {note}"]
    if ctx["provenance"]:
        lines += [""] + [f"- {name}: {value}" for name, value in ctx["provenance"]]
    lines += ["", "---", ""]
    if ctx["people"] and any(p.get("utc") for p in ctx["people"]):
        lines += ["## Рождени данни", ""]
        lines += _md_table(["Човек", "Дата и час", "Координати", "Рождена зона", "UTC момент"],
                           [[p["name"], p["birth"].replace("Дата на раждане: ", ""), p["place"].replace("Координати: ", ""), p["zone"],
                             str(p["utc"])[:16].replace("T", " ")] for p in ctx["people"]])
    for chart_data in ctx["charts"]:
        if chart_data.get("chart"):
            lines += _chart_tables(chart_data)
    if ctx["calendar_rows"]:
        lines += ["### Календар на периода", ""] + _md_table(["Дата и час", "Вид", "Събитие"], [[r["when"], r["kind"], r["what"]] for r in ctx["calendar_rows"]])
    if ctx["charts"] or ctx["calendar_rows"]:
        lines += ["---", ""]
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
