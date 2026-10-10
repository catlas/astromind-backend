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

import ai_budget
import bg_text
import billing
import birthtime
import data_api
import engine
import factpack
import limits
import memory
import period_report
import progress
import relationship
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
    Анализът не може да се завърши. code: invalid_input, no_events, forecast_failed, text_check, stopped, internal.
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
    keys = ("name", "date", "time", "lat", "lon", "birth_fold", "report_type", "is_dynamic", "end_date",
            "target_date", "target_time", "partner_name", "partner_date", "partner_time", "partner_lat", "partner_lon",
            "partner_fold")
    params = {k: getattr(request, k, None) for k in keys if getattr(request, k, None) not in (None, "")}
    if not request.birth_time_known:
        params.pop("time", None)                      # служебният пладне не е час на раждане
        params["birth_time_known"] = False
    if has_partner(request) and not request.partner_time_known:
        params.pop("partner_time", None)
        params["partner_time_known"] = False
    if has_partner(request):
        params["relationship"] = relationship.normalize(request.relationship)
    return params


def validate(request: ChartRequest) -> None:
    """Бързите проверки при създаване на задачата: период и рождени данни. Хвърля GenerationFailure("invalid_input")."""
    if request.profile_id and request.profile_id == request.partner_profile_id:
        raise GenerationFailure("invalid_input", "Първият и вторият човек са един и същ профил. Изберете друг профил за втория човек.")
    if request.is_dynamic:
        if not request.end_date:
            raise GenerationFailure("invalid_input", "end_date е задължително за динамична прогноза")
        error = forecast_period_error(period_start(request), request.end_date, has_partner(request))
        if error:
            raise GenerationFailure("invalid_input", error)
    try:
        natal_chart(request)
        if has_partner(request):
            partner_chart(request)
    except engine.TimeResolutionError as exc:
        raise GenerationFailure("invalid_input", exc.message) from exc
    except (ValueError, OverflowError) as exc:
        raise GenerationFailure("invalid_input", f"Невалидни входни данни: {exc}") from exc


def natal_chart(request: ChartRequest, instance=None) -> Dict:
    """Рождената карта на първия човек. Строго за часа: несъществуващ или повтарящ се час без избран fold е грешка.
    При неизвестен час картата няма домове, Асцендент и МС (виж engine._calculate_chart_without_time)."""
    return (instance or engine.get_engine()).calculate_chart(
        date=request.date, time=request.time, lat=request.lat, lon=request.lon, fold=request.birth_fold, strict=True,
        time_known=request.birth_time_known)


def partner_chart(request: ChartRequest, instance=None) -> Dict:
    """Рождената карта на втория човек (виж natal_chart)."""
    return (instance or engine.get_engine()).calculate_chart(
        date=request.partner_date, time=request.partner_time, lat=request.partner_lat, lon=request.partner_lon,
        fold=request.partner_fold, strict=True, time_known=request.partner_time_known)


def time_note_for(request: ChartRequest, natal: Dict, partner_data: Optional[Dict]) -> str:
    """Бележката в началото на анализа за човек без известен час ("" когато часът е известен за всички)."""
    people = [(factpack.display_name(request.name, factpack.FIRST_PERSON_DEFAULT), natal)]
    if partner_data is not None:
        people.append((factpack.display_name(request.partner_name, factpack.SECOND_PERSON_DEFAULT), partner_data))
    return birthtime.note(people)


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
    # Контекстът на отношенията важи за целия анализ (и за всеки месец), само когато има втори човек
    context_token = relationship.bind(request.relationship) if has_partner(request) else None
    # Човекът без известен час: правило към системния промпт на всяка AI заявка (Асцендент, МС и домове не се измислят)
    unknown_names = []
    if not request.birth_time_known:
        unknown_names.append(factpack.display_name(request.name, factpack.FIRST_PERSON_DEFAULT))
    if has_partner(request) and not request.partner_time_known:
        unknown_names.append(factpack.display_name(request.partner_name, factpack.SECOND_PERSON_DEFAULT))
    time_token = birthtime.bind(unknown_names) if unknown_names else None
    names_token = bg_text.bind([request.name, request.partner_name])         # имената на хората не се превеждат
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
    except ai_budget.BudgetExceeded as exc:
        # Анализите са спрени (аварийно или от бюджета) по време на изпълнение: задачата пропада и сумата се връща
        raise GenerationFailure("stopped", ai_budget.STOPPED_MESSAGE, cause=exc) from exc
    except ValueError as exc:
        raise GenerationFailure("invalid_input", f"Невалидни входни данни: {exc}") from exc
    except Exception as exc:
        what = "прогнозата" if request.is_dynamic else "анализа"
        raise internal_failure("generation", exc, f"Не успяхме да генерираме {what}. Опитайте отново след малко.") from exc
    finally:
        bg_text.unbind(names_token)
        if time_token is not None:
            birthtime.unbind(time_token)
        if context_token is not None:
            relationship.unbind(context_token)


async def run_plain(request: ChartRequest, user_id: int, emit: Emit) -> Outcome:
    """Единичен анализ: натален, за дата (транзити) или за двама."""
    partner = has_partner(request)
    natal = natal_chart(request)
    partner_data = partner_chart(request) if partner else None
    time_note = time_note_for(request, natal, partner_data)

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

    emit({"type": "start", "kind": "analysis", "natal_chart": natal, "partner_chart": partner_data,
          "transit_chart": transit_chart, "natal_aspects": _natal_aspects(natal, "user"),
          "partner_natal_aspects": _natal_aspects(partner_data, "partner")})

    memory_used = _activate_memory(user_id, request.name, request.partner_name if partner else None)
    emit({"type": "stage", "stage": "analyzing"})
    checks: List[Dict] = []
    token = progress.bind(lambda name: emit({"type": "stage", "stage": name}))
    try:
        interpretation = await get_interpreter().interpret_chart(
            natal_chart=natal,
            transit_chart=transit_chart,
            partner_chart=partner_data,
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

    if time_note:
        interpretation = f"{time_note}\n\n{interpretation}"
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
    engine_instance = engine.get_engine()
    natal = natal_chart(request, engine_instance)
    partner_data = partner_chart(request, engine_instance) if has_partner(request) else None
    time_note = time_note_for(request, natal, partner_data)

    start_date = period_start(request)
    calendar = TransitScanner(engine_instance=engine_instance).build_calendar(
        natal_chart=natal, start_date=start_date, end_date=request.end_date,
        lat=request.lat, lon=request.lon, partner_chart=partner_data)
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
          "natal_chart": natal, "partner_chart": partner_data, "transit_chart": transit_chart,
          "natal_aspects": _natal_aspects(natal, "user"),
          "partner_natal_aspects": _natal_aspects(partner_data, "partner")})

    memory_used = _activate_memory(user_id, request.name, request.partner_name if partner_data else None)
    emit({"type": "stage", "stage": "analyzing"})

    final: Optional[Dict[str, Any]] = None
    try:
        async for step in period_report.run_period_report(
                get_interpreter(),
                calendar=calendar,
                natal_chart=natal,
                partner_chart=partner_data,
                report_type=request.report_type or "general",
                user_name=request.name,
                partner_name=request.partner_name if partner_data else None,
                question=request.question or "",
                gender=request.gender,
                partner_gender=request.partner_gender):
            if step["type"] == "finished":
                final = step
                continue
            if step["type"] == "overview_complete" and time_note:
                step = {**step, "text": f"{time_note}\n\n{step['text']}"}
            emit(step)
    except period_report.ForecastGenerationError as failure:
        print(f"⚠️ Прогнозата не завърши ({failure})")
        raise GenerationFailure("forecast_failed", period_report.USER_MESSAGE, stage=failure.stage, cause=failure.cause,
                                checks=failure.checks) from failure
    if final is None:
        raise GenerationFailure("forecast_failed", period_report.USER_MESSAGE, stage="period")

    emit({"type": "stage", "stage": "finishing"})
    return Outcome(
        content=period_report.saved_content(final["overview"], final["month_texts"], note=time_note),
        report_type=request.report_type or "general",
        label=data_api.report_label(request.report_type or "general", request.partner_name, is_dynamic=True,
                                    question=request.question),
        params={**report_params(request), "months": len(months), "memory_used": memory_used},
        months=len(months), flags=list(final["flags"]), checks=list(final.get("checks", [])))
