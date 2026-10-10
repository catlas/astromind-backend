"""
Генерацията на анализ без HTTP и без пари (Фаза 11): изчисления, AI и проверка на текста.

Вика се от задачата (jobs.py). Съобщава хода като събития през emit: start (картите), stage, month_start, month_complete,
overview_start, overview_complete, text. Връща Outcome или хвърля GenerationFailure. Резервирането и връщането на сумата,
записът на отчета и телеметрията на неуспеха са работа на задачата.

Двата вида анализ минават през този модул:
- единичен анализ (натален, за дата, за двама): run_plain;
- прогноза за период, месец по месец с общ преглед: run_period.
"""
import traceback
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Callable, Dict, List, Optional

import billing
import data_api
import engine
import limits
import memory
import period_report
import progress
import safety
import text_guard
from ai_interpreter import get_interpreter
from aspects_engine import calculate_natal_aspects
from database import SessionLocal, User
from limits import forecast_period_error
from scanner import TransitScanner
from schemas import ChartRequest

Emit = Callable[[Dict[str, Any]], None]


class GenerationFailure(Exception):
    """
    Анализът не може да се завърши. code: invalid_input, no_events, forecast_failed, text_check, internal.
    message е текстът за потребителя. cause и checks остават за телеметрията (без текст на анализа).
    """

    def __init__(self, code: str, message: str, *, stage: str = "", cause: Optional[BaseException] = None,
                 checks: Optional[List[Dict]] = None):
        super().__init__(message)
        self.code = code
        self.message = message
        self.stage = stage
        self.cause = cause
        self.checks: List[Dict] = list(checks or [])


@dataclass
class Outcome:
    """Резултатът от генерацията: какво да се запише в История и какво да се отчете в телеметрията."""
    content: str
    report_type: str
    label: str
    params: Dict[str, Any]
    months: int = 0                       # реален брой месеци при прогноза (0 при единичен анализ)
    flags: List[str] = field(default_factory=list)
    checks: List[Dict] = field(default_factory=list)


def has_partner(request: ChartRequest) -> bool:
    return bool(request.partner_date and request.partner_time
                and request.partner_lat is not None and request.partner_lon is not None)


def kind_of(request: ChartRequest) -> str:
    return "forecast" if request.is_dynamic else "analysis"


def period_start(request: ChartRequest) -> str:
    """Началото на прогнозата: target_date или днешната дата, НИКОГА датата на раждане."""
    return request.target_date or datetime.now().strftime("%Y-%m-%d")


def quote_for(request: ChartRequest) -> billing.Quote:
    """Цената и нивото на услугата, определени от сървъра. При прогноза: календарните месеци на периода (горна граница)."""
    partner = has_partner(request)
    if request.is_dynamic:
        months = limits.period_months(period_start(request), request.end_date or "")
        return billing.forecast_quote(months or 1, partner)
    return billing.analysis_quote(partner)


def report_params(request: ChartRequest) -> dict:
    """Параметрите на анализа, без свободния текст на въпроса."""
    keys = ("name", "date", "time", "lat", "lon", "report_type", "is_dynamic", "end_date",
            "target_date", "target_time", "partner_name", "partner_date", "partner_time")
    return {k: getattr(request, k, None) for k in keys if getattr(request, k, None) not in (None, "")}


def validate(request: ChartRequest) -> None:
    """Бързите проверки при създаване на задачата: период и рождени данни. Хвърля GenerationFailure("invalid_input")."""
    if request.is_dynamic:
        if not request.end_date:
            raise GenerationFailure("invalid_input", "end_date е задължително за динамична прогноза")
        error = forecast_period_error(period_start(request), request.end_date, has_partner(request))
        if error:
            raise GenerationFailure("invalid_input", error)
    try:
        engine.calculate_chart(date=request.date, time=request.time, lat=request.lat, lon=request.lon)
        if has_partner(request):
            engine.calculate_chart(date=request.partner_date, time=request.partner_time,
                                   lat=request.partner_lat, lon=request.partner_lon)
    except ValueError as exc:
        raise GenerationFailure("invalid_input", f"Невалидни входни данни: {exc}") from exc


def _natal_aspects(chart: Optional[Dict], who: str) -> Optional[list]:
    if not chart:
        return None
    try:
        return calculate_natal_aspects(chart, use_wider_orbs=False)
    except Exception as exc:
        print(f"Warning: Could not calculate {who} natal aspects: {exc}")
        return None


def _activate_memory(user_id: int, name: Optional[str], partner_name: Optional[str]):
    db = SessionLocal()
    try:
        return memory.activate(db, db.get(User, user_id), name, partner_name)
    finally:
        db.close()


def internal_failure(context: str, exc: Exception, user_message: str) -> GenerationFailure:
    """Пълната грешка се логва само на сървъра; потребителят получава общо съобщение с код за търсене в логовете."""
    error_id = uuid.uuid4().hex[:8]
    print(f"❌ [{error_id}] {context}: {type(exc).__name__}: {exc}")
    traceback.print_exc()
    return GenerationFailure("internal", f"{user_message} (код: {error_id})", cause=exc)


async def run(request: ChartRequest, user_id: int, emit: Emit) -> Outcome:
    """Генерира анализа по заявката. Единичният и периодичният анализ имат отделни пътища."""
    emit({"type": "stage", "stage": "calculating"})
    try:
        if request.is_dynamic:
            return await run_period(request, user_id, emit)
        return await run_plain(request, user_id, emit)
    except GenerationFailure:
        raise
    except text_guard.TextCheckError as failure:
        # Текстът не мина проверката и след поправката: нищо не се записва и не се таксува
        raise GenerationFailure("text_check", text_guard.USER_MESSAGE, stage=failure.stage, cause=failure,
                                checks=[failure.outcome.summary()]) from failure
    except ValueError as exc:
        raise GenerationFailure("invalid_input", f"Невалидни входни данни: {exc}") from exc
    except Exception as exc:
        what = "прогнозата" if request.is_dynamic else "анализа"
        raise internal_failure("generation", exc, f"Не успяхме да генерираме {what}. Опитайте отново след малко.") from exc


async def run_plain(request: ChartRequest, user_id: int, emit: Emit) -> Outcome:
    """Единичен анализ: натален, за дата (транзити) или за двама."""
    partner = has_partner(request)
    natal_chart = engine.calculate_chart(date=request.date, time=request.time, lat=request.lat, lon=request.lon)
    partner_chart = None
    if partner:
        partner_chart = engine.calculate_chart(date=request.partner_date, time=request.partner_time,
                                               lat=request.partner_lat, lon=request.partner_lon)

    # Транзитна карта само ако е заявена дата за анализа
    transit_chart = None
    transit_date = None
    if request.target_date is not None:
        transit_date = request.target_date
        transit_time = request.target_time or datetime.now().strftime("%H:%M:%S")
        transit_chart = engine.calculate_chart(
            date=transit_date, time=transit_time,
            lat=request.target_lat if request.target_lat is not None else request.lat,
            lon=request.target_lon if request.target_lon is not None else request.lon)

    # Датата за AI: пълната дата, час и зона от транзитната карта
    if transit_chart and transit_chart.get("datetime_local"):
        zone = transit_chart.get("timezone")
        target_date_for_ai = f"{transit_chart['datetime_local']} ({zone})" if zone else transit_chart["datetime_local"]
    elif transit_date:
        target_date_for_ai = f"{transit_date} {request.target_time}" if request.target_time else transit_date
    else:
        target_date_for_ai = ""

    emit({"type": "start", "kind": "analysis", "natal_chart": natal_chart, "partner_chart": partner_chart,
          "transit_chart": transit_chart, "natal_aspects": _natal_aspects(natal_chart, "user"),
          "partner_natal_aspects": _natal_aspects(partner_chart, "partner")})

    memory_used = _activate_memory(user_id, request.name, request.partner_name if partner else None)
    emit({"type": "stage", "stage": "analyzing"})
    checks: List[Dict] = []
    token = progress.bind(lambda name: emit({"type": "stage", "stage": name}))
    try:
        interpretation = await get_interpreter().interpret_chart(
            natal_chart=natal_chart,
            transit_chart=transit_chart,
            partner_chart=partner_chart,
            partner_name=request.partner_name,
            question=request.question or "",
            target_date=target_date_for_ai,
            language="bg",
            report_type=request.report_type or "general",
            user_name=request.name,
            calendar=None,
            gender=request.gender,
            partner_gender=request.partner_gender,
            checks=checks,
        )
    except text_guard.TextCheckError as failure:
        raise GenerationFailure("text_check", text_guard.USER_MESSAGE, stage=failure.stage, cause=failure,
                                checks=checks) from failure
    finally:
        progress.unbind(token)
    interpretation, flags = safety.check_output(interpretation, request.report_type or "general")

    emit({"type": "stage", "stage": "finishing"})
    emit({"type": "text", "interpretation": interpretation})
    return Outcome(
        content=interpretation,
        report_type=request.report_type or "general",
        label=data_api.report_label(request.report_type or "general", request.partner_name, question=request.question),
        params={**report_params(request), "memory_used": memory_used},
        months=0, flags=list(flags), checks=checks)


async def run_period(request: ChartRequest, user_id: int, emit: Emit) -> Outcome:
    """Прогноза за период: точният календар, месец по месец и общ преглед (Фаза 9)."""
    engine_instance = engine.AstrologyEngine()
    natal_chart = engine_instance.calculate_chart(date=request.date, time=request.time, lat=request.lat, lon=request.lon)
    partner_chart = None
    if has_partner(request):
        partner_chart = engine_instance.calculate_chart(date=request.partner_date, time=request.partner_time,
                                                        lat=request.partner_lat, lon=request.partner_lon)

    start_date = period_start(request)
    calendar = TransitScanner(engine_instance=engine_instance).build_calendar(
        natal_chart=natal_chart, start_date=start_date, end_date=request.end_date,
        lat=request.lat, lon=request.lon, partner_chart=partner_chart)
    months = calendar.months_with_events()
    if not months:
        raise GenerationFailure("no_events", "Няма събития за анализиране в избрания период")

    # Транзитната карта към началната дата е само за показване на екрана
    transit_chart = None
    try:
        transit_chart = engine_instance.calculate_chart(date=start_date, time="12:00:00", lat=request.lat, lon=request.lon)
    except Exception as exc:
        print(f"Warning: Could not calculate transit chart for start date: {exc}")

    emit({"type": "start", "kind": "forecast", "total_months": len(months),
          "start_month": period_report.month_title(months[0]), "end_month": period_report.month_title(months[-1]),
          "natal_chart": natal_chart, "partner_chart": partner_chart, "transit_chart": transit_chart,
          "natal_aspects": _natal_aspects(natal_chart, "user"),
          "partner_natal_aspects": _natal_aspects(partner_chart, "partner")})

    memory_used = _activate_memory(user_id, request.name, request.partner_name if partner_chart else None)
    emit({"type": "stage", "stage": "analyzing"})

    final: Optional[Dict[str, Any]] = None
    try:
        async for step in period_report.run_period_report(
                get_interpreter(),
                calendar=calendar,
                natal_chart=natal_chart,
                partner_chart=partner_chart,
                report_type=request.report_type or "general",
                user_name=request.name,
                partner_name=request.partner_name if partner_chart else None,
                question=request.question or "",
                gender=request.gender,
                partner_gender=request.partner_gender):
            if step["type"] == "finished":
                final = step
                continue
            emit(step)
    except period_report.ForecastGenerationError as failure:
        print(f"⚠️ Прогнозата не завърши ({failure})")
        raise GenerationFailure("forecast_failed", period_report.USER_MESSAGE, stage=failure.stage, cause=failure.cause,
                                checks=failure.checks) from failure
    if final is None:
        raise GenerationFailure("forecast_failed", period_report.USER_MESSAGE, stage="period")

    emit({"type": "stage", "stage": "finishing"})
    return Outcome(
        content=period_report.saved_content(final["overview"], final["month_texts"]),
        report_type=request.report_type or "general",
        label=data_api.report_label(request.report_type or "general", request.partner_name, is_dynamic=True,
                                    question=request.question),
        params={**report_params(request), "months": len(months), "memory_used": memory_used},
        months=len(months), flags=list(final["flags"]), checks=list(final.get("checks", [])))
