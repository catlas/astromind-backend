"""
FastAPI сървър за астрологично приложение
Предоставя API endpoints за изчисляване и интерпретация на астрологични карти
"""

from fastapi import BackgroundTasks, FastAPI, HTTPException, Depends, Request, status  # type: ignore
from fastapi.middleware.cors import CORSMiddleware  # type: ignore
from fastapi.responses import StreamingResponse, Response  # type: ignore
from pydantic import BaseModel, Field  # type: ignore
from typing import Optional, List, Dict
from datetime import datetime, timedelta
from sqlalchemy import func  # type: ignore
from sqlalchemy.orm import Session  # type: ignore
import json
import os
import traceback
import uuid
from dotenv import load_dotenv
import engine
from ai_interpreter import AIInterpreter, get_interpreter
from scanner import TransitScanner
import period_report
import text_guard
from limits import forecast_period_error
from aspects_engine import calculate_natal_aspects
from docx_generator import DOCXGenerator
from database import SessionLocal, User, get_db
from db_migrate import run_migrations
from deps import get_current_user
import account_api
import billing
import billing_api
import data_api
import events
import events_api
import geocode_api
import mailer
import onboarding_api
import memory
import memory_api
import safety
from auth import (
    hash_password, verify_password, create_user_token,
    normalize_email, validate_email, validate_password,
)
from rate_limit import client_ip, enforce

load_dotenv()

# Схемата на базата се обновява с Alembic преди приемане на заявки
run_migrations()

# Инициализация на FastAPI приложението
app = FastAPI(
    title="Astrology API",
    description="API за изчисляване и интерпретация на астрологични карти",
    version="3.0.0"
)

# CORS Middleware - чете allowlist от CORS_ORIGINS (comma-separated)
cors_origins_raw = os.getenv("CORS_ORIGINS", "")
cors_origins = [origin.strip() for origin in cors_origins_raw.split(",") if origin.strip()]
if not cors_origins:
    cors_origins = ["http://localhost:5173", "http://127.0.0.1:5173"]

allow_all_origins = "*" in cors_origins
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"] if allow_all_origins else cors_origins,
    allow_credentials=not allow_all_origins,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(account_api.router)
app.include_router(data_api.router)
app.include_router(billing_api.router)
app.include_router(events_api.router)
app.include_router(geocode_api.router)
app.include_router(onboarding_api.router)
app.include_router(memory_api.router)

# Записва в лога дали имейлите са настроени и дали пощенският сървър е достъпен
mailer.log_status_in_background()

# Инициализация на AI интерпретатора
ai_interpreter = get_interpreter()


def _internal_error(context: str, exc: Exception, user_message: str) -> HTTPException:
    """
    Логва пълната грешка само на сървъра и връща общо съобщение към клиента.
    Кодът в съобщението позволява грешката да се намери в логовете на Render.
    Извиква се от except блок, за да може traceback-ът да бъде отпечатан.
    """
    error_id = uuid.uuid4().hex[:8]
    print(f"❌ [{error_id}] {context}: {type(exc).__name__}: {exc}")
    traceback.print_exc()
    return HTTPException(status_code=500, detail=f"{user_message} (код: {error_id})")


def _track_text_checks(db, user_id: int, report_type: str, checks) -> None:
    """Телеметрия на проверката на текста (Фаза 10): етап, кодове и броеве, никога текст на анализа.
    Записват се само случаите, в които нещо се е случило (поправка, отхвърляне, предупреждение, почистване, грешка)."""
    for check in checks or []:
        if check.get("result") not in (None, "ok"):
            events.track(db, "text_check", user_id, {"type": report_type, **check})


def _failure_props(report_type: str, dynamic: bool, stage: str, cause) -> dict:
    """Свойствата на събитието analysis_failed; при отхвърлен от проверката текст има причина и кодове на нарушенията."""
    props = {"type": report_type, "dynamic": dynamic, "stage": stage}
    if isinstance(cause, text_guard.TextCheckError):
        props.update({"reason": "text_check", "codes": cause.outcome.codes()})
    return props


# Лимити на заявките. Стойностите могат да се сменят през environment в Render.
AI_LIMIT_PER_HOUR = int(os.getenv("AI_RATE_LIMIT_PER_HOUR", "20"))
AI_LIMIT_PER_DAY = int(os.getenv("AI_RATE_LIMIT_PER_DAY", "60"))
DOCX_LIMIT_PER_HOUR = int(os.getenv("DOCX_RATE_LIMIT_PER_HOUR", "30"))
CALCULATE_LIMIT_PER_MINUTE = int(os.getenv("CALCULATE_RATE_LIMIT_PER_MINUTE", "60"))
LOGIN_LIMIT_PER_15_MIN = int(os.getenv("LOGIN_RATE_LIMIT_PER_15_MIN", "10"))
REGISTER_LIMIT_PER_HOUR = int(os.getenv("REGISTER_RATE_LIMIT_PER_HOUR", "5"))


def require_ai_quota(current_user: User = Depends(get_current_user)) -> User:
    """Изисква вход и ограничава AI анализите на потребител."""
    message = "Достигнахте лимита за AI анализи."
    enforce(f"ai-hour:{current_user.id}", AI_LIMIT_PER_HOUR, 3600, message)
    enforce(f"ai-day:{current_user.id}", AI_LIMIT_PER_DAY, 86400, message)
    return current_user


def require_docx_quota(current_user: User = Depends(get_current_user)) -> User:
    """Изисква вход и ограничава генерирането на DOCX файлове."""
    enforce(f"docx:{current_user.id}", DOCX_LIMIT_PER_HOUR, 3600, "Достигнахте лимита за DOCX файлове.")
    return current_user


# Pydantic модели за заявки
class ChartRequest(BaseModel):
    """Модел за заявка за изчисляване на карта"""
    name: Optional[str] = Field(
        default=None,
        description="Име на потребителя"
    )
    date: str = Field(..., description="Дата на раждане във формат YYYY-MM-DD или YYYY/MM/DD")
    time: str = Field(..., description="Час на раждане във формат HH:MM:SS или HH:MM")
    lat: float = Field(..., ge=-90, le=90, description="Географска ширина в градуси (-90 до 90)")
    lon: float = Field(..., ge=-180, le=180, description="Географска дължина в градуси (-180 до 180)")
    # timezone_offset е премахнат - сега се изчислява автоматично от координатите
    question: Optional[str] = Field(
        default=None,
        description="Опционален въпрос за AI интерпретация"
    )
    report_type: Optional[str] = Field(
        default="general",
        description="Type of report: general, health, career, love, money, karmic"
    )
    is_dynamic: bool = Field(
        default=False,
        description="Активира Dynamic Forecast Mode (Timeline Scanner)"
    )
    end_date: Optional[str] = Field(
        default=None,
        description="Крайна дата за Dynamic Forecast Mode (YYYY-MM-DD). Използва се само ако is_dynamic=True."
    )
    target_date: Optional[str] = Field(
        default=None,
        description="Дата за транзит анализ (прогнозна дата). Ако не е предоставена, използва се текущата дата."
    )
    target_time: Optional[str] = Field(
        default=None,
        description="Час за транзит анализ. Ако не е предоставен, използва се текущият час."
    )
    target_lat: Optional[float] = Field(
        default=None,
        ge=-90, le=90,
        description="Географска ширина за транзит (за релокация). Ако не е предоставена, използва се birth lat."
    )
    target_lon: Optional[float] = Field(
        default=None,
        ge=-180, le=180,
        description="Географска дължина за транзит (за релокация). Ако не е предоставена, използва се birth lon."
    )
    # Partner/Relationship fields
    partner_name: Optional[str] = Field(
        default=None,
        description="Име на партньора (за synastry анализ)"
    )
    partner_date: Optional[str] = Field(
        default=None,
        description="Дата на раждане на партньора"
    )
    partner_time: Optional[str] = Field(
        default=None,
        description="Час на раждане на партньора"
    )
    partner_lat: Optional[float] = Field(
        default=None,
        ge=-90, le=90,
        description="Географска ширина на партньора"
    )
    partner_lon: Optional[float] = Field(
        default=None,
        ge=-180, le=180,
        description="Географска дължина на партньора"
    )
    # Известен пол (male/female). Без него AI пише неутрално и не гадае пола.
    gender: Optional[str] = Field(default=None, max_length=20, description="Пол на първия човек, ако е известен")
    partner_gender: Optional[str] = Field(default=None, max_length=20, description="Пол на втория човек, ако е известен")


class ChartResponse(BaseModel):
    """Модел за отговор с данни от картата"""
    planets: dict
    houses: dict
    angles: dict
    julian_day: float
    datetime_utc: str
    timezone: str
    datetime_local: str
    location: dict


class InterpretationResponse(BaseModel):
    """Модел за отговор с карта и интерпретация"""
    natal_chart: ChartResponse
    transit_chart: Optional[ChartResponse] = None
    partner_chart: Optional[ChartResponse] = None
    interpretation: str
    natal_aspects: Optional[List[Dict]] = None
    partner_natal_aspects: Optional[List[Dict]] = None
    report_id: Optional[int] = None
    coins_charged: int = 0
    balance: Optional[int] = None
    crisis: bool = False


@app.get("/")
async def root():
    """Root endpoint - информация за API"""
    return {
        "message": "Astrology API",
        "version": "3.0.0",
        "endpoints": {
            "POST /calculate": "Изчислява астрологична карта",
            "POST /interpret": "Изчислява карта и получава AI интерпретация",
            "POST /register": "Регистрация на нов потребител",
            "POST /login": "Вход в системата - връща JWT token",
            "GET /me": "Връща текущия потребител (Bearer token)"
        }
    }


@app.get("/health")
async def health_check():
    """Health check endpoint"""
    return {"status": "healthy"}


@app.post("/calculate", response_model=ChartResponse)
async def calculate_chart(request: ChartRequest, http_request: Request):
    """
    Изчислява астрологична карта без AI интерпретация.
    
    Връща:
    - Позиции на планетите
    - Куспиди на домовете
    - Ъгли (ASC, MC)
    - Julian Day
    - UTC datetime
    - Локация
    """
    enforce(f"calc:{client_ip(http_request)}", CALCULATE_LIMIT_PER_MINUTE, 60, "Твърде много заявки.")
    try:
        # Изчисляване на картата
        chart_data = engine.calculate_chart(
            date=request.date,
            time=request.time,
            lat=request.lat,
            lon=request.lon
        )
        
        # Връщане на данните
        return ChartResponse(
            planets=chart_data["planets"],
            houses=chart_data["houses"],
            angles=chart_data["angles"],
            julian_day=chart_data["julian_day"],
            datetime_utc=chart_data["datetime_utc"],
            timezone=chart_data["timezone"],
            datetime_local=chart_data["datetime_local"],
            location=chart_data["location"]
        )
        
    except ValueError as e:
        raise HTTPException(status_code=400, detail=f"Невалидни входни данни: {str(e)}")
    except Exception as e:
        raise _internal_error("/calculate", e, "Не успяхме да изчислим картата. Опитайте отново след малко.")


def _report_params(request: "ChartRequest") -> dict:
    """Параметрите на анализа, без свободния текст на въпроса."""
    keys = ("name", "date", "time", "lat", "lon", "report_type", "is_dynamic", "end_date",
            "target_date", "target_time", "partner_name", "partner_date", "partner_time")
    return {k: getattr(request, k, None) for k in keys if getattr(request, k, None) not in (None, "")}


@app.post("/interpret-stream")
async def interpret_chart_stream(request: ChartRequest, current_user: User = Depends(require_ai_quota)):
    """
    Streaming endpoint за динамична прогноза (месец по месец).
    Използва Server-Sent Events (SSE) за да изпраща резултатите в реално време.
    
    Този endpoint се използва само когато is_dynamic=True.
    """
    
    async def generate_monthly_stream():
        """Generator функция за streaming на месечни прогнози"""
        try:
            # Validate dynamic mode
            if not request.is_dynamic:
                yield f"data: {json.dumps({'type': 'error', 'message': 'Този endpoint изисква is_dynamic=True'}, ensure_ascii=False)}\n\n"
                return
            
            if not request.end_date:
                yield f"data: {json.dumps({'type': 'error', 'message': 'end_date е задължително за динамична прогноза'}, ensure_ascii=False)}\n\n"
                return

            if safety.detect_crisis(request.question):
                track_db = SessionLocal()
                try:
                    events.track(track_db, "crisis_detected", current_user.id)
                finally:
                    track_db.close()
                yield f"data: {json.dumps({'type': 'crisis', 'html': safety.CRISIS_MESSAGE_HTML}, ensure_ascii=False)}\n\n"
                return

            stream_has_partner = bool(request.partner_date and request.partner_time
                                      and request.partner_lat is not None and request.partner_lon is not None)
            period_error = forecast_period_error(
                request.target_date or datetime.now().strftime("%Y-%m-%d"), request.end_date, stream_has_partner)
            if period_error:
                yield f"data: {json.dumps({'type': 'error', 'message': period_error}, ensure_ascii=False)}\n\n"
                return
            
            # Initialize engine
            engine_instance = engine.AstrologyEngine()
            
            # Calculate natal chart
            natal_chart_data = engine_instance.calculate_chart(
                date=request.date,
                time=request.time,
                lat=request.lat,
                lon=request.lon
            )
            
            # Calculate partner chart if provided
            partner_chart_data = None
            if request.partner_date and request.partner_time and request.partner_lat is not None and request.partner_lon is not None:
                partner_chart_data = engine_instance.calculate_chart(
                    date=request.partner_date,
                    time=request.partner_time,
                    lat=request.partner_lat,
                    lon=request.partner_lon
                )
            
            # Calculate natal aspects for user
            natal_aspects_data = None
            try:
                natal_aspects_data = calculate_natal_aspects(natal_chart_data, use_wider_orbs=False)
            except Exception as e:
                print(f"Warning: Could not calculate natal aspects for streaming: {e}")
            
            # Calculate natal aspects for partner if present
            partner_natal_aspects_data = None
            if partner_chart_data:
                try:
                    partner_natal_aspects_data = calculate_natal_aspects(partner_chart_data, use_wider_orbs=False)
                except Exception as e:
                    print(f"Warning: Could not calculate partner natal aspects for streaming: {e}")
            
            # Календарът на периода (Фаза 9): точните моменти се изчисляват веднъж за целия период.
            # За начало на прогнозата се ползва target_date (или днешната дата), НИКОГА датата на раждане.
            if request.target_date:
                start_date = request.target_date
            else:
                start_date = datetime.now().strftime("%Y-%m-%d")
            end_date = request.end_date

            calendar = TransitScanner(engine_instance=engine_instance).build_calendar(
                natal_chart=natal_chart_data,
                start_date=start_date,
                end_date=end_date,
                lat=request.lat,
                lon=request.lon,
                partner_chart=partner_chart_data
            )
            sorted_months = calendar.months_with_events()

            if not sorted_months:
                yield f"data: {json.dumps({'type': 'error', 'message': 'Няма събития за анализиране в избрания период'}, ensure_ascii=False)}\n\n"
                return

            cost = billing.forecast_cost(len(sorted_months), bool(partner_chart_data))
            try:
                billing.require_balance(current_user, cost)
            except HTTPException as e:
                yield f"data: {json.dumps({'type': 'error', 'code': 402, 'message': e.detail}, ensure_ascii=False)}\n\n"
                return

            # Send initial metadata with natal chart data
            start_month = period_report.month_title(sorted_months[0])
            end_month = period_report.month_title(sorted_months[-1])

            # Calculate transit chart for the start date (target_date) for visualization
            # This is needed especially when partner is enabled to show the transit chart
            transit_chart_data = None
            if start_date:
                try:
                    # Use target_date as transit date, or start_date if target_date not provided
                    transit_date = start_date
                    # Default to noon (12:00) for transit chart
                    transit_time = "12:00:00"

                    transit_chart_data = engine_instance.calculate_chart(
                        date=transit_date,
                        time=transit_time,
                        lat=request.lat,
                        lon=request.lon
                    )
                except Exception as e:
                    print(f"Warning: Could not calculate transit chart for start date: {e}")

            start_event_data = {
                'type': 'start',
                'total_months': len(sorted_months),
                'start_month': start_month,
                'end_month': end_month,
                'natal_chart': natal_chart_data,
                'partner_chart': partner_chart_data,
                'transit_chart': transit_chart_data,  # Add transit chart for start date
                'natal_aspects': natal_aspects_data,
                'partner_natal_aspects': partner_natal_aspects_data
            }

            yield f"data: {json.dumps(start_event_data, ensure_ascii=False)}\n\n"

            mem_db = SessionLocal()
            try:
                memory_used = memory.activate(
                    mem_db, mem_db.get(User, current_user.id), request.name,
                    request.partner_name if partner_chart_data else None)
            finally:
                mem_db.close()

            # Месеците и общият преглед. Провал (след един повторен опит) = без запис и без такса.
            final = None
            try:
                async for step in period_report.run_period_report(
                    ai_interpreter,
                    calendar=calendar,
                    natal_chart=natal_chart_data,
                    partner_chart=partner_chart_data,
                    report_type=request.report_type or "general",
                    user_name=request.name,
                    partner_name=request.partner_name if partner_chart_data else None,
                    question=request.question or "",
                    gender=request.gender,
                    partner_gender=request.partner_gender,
                ):
                    if step["type"] == "finished":
                        final = step
                        continue
                    yield f"data: {json.dumps(step, ensure_ascii=False)}\n\n"
            except period_report.ForecastGenerationError as failure:
                print(f"⚠️ /interpret-stream: прогнозата не завърши ({failure})")
                fail_db = SessionLocal()
                try:
                    events.track(fail_db, "analysis_failed", current_user.id,
                                 _failure_props(request.report_type or "general", True, failure.stage, failure.cause))
                    _track_text_checks(fail_db, current_user.id, request.report_type or "general", failure.checks)
                finally:
                    fail_db.close()
                yield f"data: {json.dumps({'type': 'error', 'code': 502, 'message': period_report.USER_MESSAGE}, ensure_ascii=False)}\n\n"
                return
            flagged = final["flags"]

            # Запазване в историята (собствена сесия: генераторът живее след края на зависимостите)
            report_id = None
            coins_charged = 0
            balance = None
            db = SessionLocal()
            try:
                report = data_api.save_report(
                    db, db.get(User, current_user.id),
                    content=period_report.saved_content(final["overview"], final["month_texts"]),
                    report_type=request.report_type or "general",
                    profile_name=request.name,
                    label=data_api.report_label(request.report_type or "general", request.partner_name,
                                                is_dynamic=True, question=request.question),
                    params={**_report_params(request), "months": len(sorted_months), "memory_used": memory_used},
                )
                coins_charged = billing.charge_for_report(db, db.get(User, current_user.id), report, cost,
                                                          description=report.label)
                db.commit()
                report_id = report.id
                balance = db.get(User, current_user.id).coins or 0
                is_first = db.query(data_api.Report.id).filter(data_api.Report.user_id == current_user.id).count() == 1
                events.track(db, "analysis_completed", current_user.id,
                             {"type": report.report_type, "dynamic": True, "first": is_first,
                              "months": len(sorted_months), "coins": coins_charged})
                if flagged:
                    events.track(db, "ai_output_flagged", current_user.id, {"flags": ",".join(sorted(set(flagged)))})
                _track_text_checks(db, current_user.id, report.report_type, final.get("checks"))
            except Exception as e:
                # Прогнозата вече е при потребителя; само записът в историята не успя
                _internal_error("/interpret-stream save_report", e, "")
            finally:
                db.close()

            # Send completion event
            yield f"data: {json.dumps({'type': 'complete', 'report_id': report_id, 'coins_charged': coins_charged, 'balance': balance}, ensure_ascii=False)}\n\n"
            
        except ValueError as e:
            error_message = f"Невалидни входни данни: {str(e)}"
            yield f"data: {json.dumps({'type': 'error', 'message': error_message}, ensure_ascii=False)}\n\n"
        except Exception as e:
            err = _internal_error("/interpret-stream", e, "Не успяхме да генерираме прогнозата. Опитайте отново след малко.")
            yield f"data: {json.dumps({'type': 'error', 'message': err.detail}, ensure_ascii=False)}\n\n"
    
    return StreamingResponse(
        generate_monthly_stream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no"  # Disable nginx buffering
        }
    )


@app.post("/interpret", response_model=InterpretationResponse)
async def interpret_chart(request: ChartRequest, current_user: User = Depends(require_ai_quota),
                          db: Session = Depends(get_db)):
    """
    Изчислява натална и транзитна карта и получава AI интерпретация.
    
    Изчислява:
    1. Натална карта (от дата на раждане)
    2. Транзитна карта (от target_date или текуща дата)
    
    След това извиква AI интерпретатора за сравнителен анализ.
    
    Връща:
    - Натална карта
    - Транзитна карта (ако е изчислена)
    - AI интерпретация като текст
    """
    has_partner = bool(request.partner_date and request.partner_time and request.partner_lat is not None and request.partner_lon is not None)
    cost = billing.analysis_cost(has_partner)
    if not safety.detect_crisis(request.question):
        billing.require_balance(current_user, cost)
    text_checks: List[Dict] = []        # описания на проверката на текста (Фаза 10), за телеметрията

    try:
        natal_chart_data = engine.calculate_chart(
            date=request.date,
            time=request.time,
            lat=request.lat,
            lon=request.lon
        )
        
        # Определяне дали има partner данни
        has_partner = bool(
            request.partner_date and 
            request.partner_time and 
            request.partner_lat is not None and 
            request.partner_lon is not None
        )
        
        # Изчисляване на partner карта (ако е предоставена) - използваме я и за timeline и за synastry
        partner_chart_data = None
        if has_partner:
            # Type narrowing: след проверката на has_partner знаем, че стойностите не са None
            assert request.partner_date is not None, "partner_date is required when has_partner is True"
            assert request.partner_time is not None, "partner_time is required when has_partner is True"
            assert request.partner_lat is not None, "partner_lat is required when has_partner is True"
            assert request.partner_lon is not None, "partner_lon is required when has_partner is True"
            
            partner_chart_data = engine.calculate_chart(
                date=request.partner_date,
                time=request.partner_time,
                lat=request.partner_lat,
                lon=request.partner_lon
            )
        
        # Dynamic Forecast Mode (точен календар)
        timeline_calendar = None
        if request.is_dynamic:
            if not request.end_date:
                raise HTTPException(
                    status_code=400,
                    detail="end_date е задължително когато is_dynamic=True"
                )
            
            # Използваме target_date като start_date, или текущата дата
            start_date = request.target_date if request.target_date else datetime.now().strftime("%Y-%m-%d")
            end_date = request.end_date

            # Първо безопасността: при криза не се връща грешка за периода (отговорът е подкрепящото съобщение)
            period_error = None if safety.detect_crisis(request.question) else forecast_period_error(
                start_date, end_date, has_partner)
            if period_error:
                raise HTTPException(status_code=400, detail=period_error)
            
            # Дължината на периода е проверена по-горе (limits.forecast_period_error); всеки месец е отделна AI заявка
            
            # Изчисляване на partner карта (ако е предоставена) - за Relationship Forecast Mode
            partner_chart_data_for_timeline = None
            if has_partner:
                # Type narrowing: след проверката на has_partner знаем, че стойностите не са None
                assert request.partner_date is not None, "partner_date is required when has_partner is True"
                assert request.partner_time is not None, "partner_time is required when has_partner is True"
                assert request.partner_lat is not None, "partner_lat is required when has_partner is True"
                assert request.partner_lon is not None, "partner_lon is required when has_partner is True"
                
                partner_chart_data_for_timeline = engine.calculate_chart(
                    date=request.partner_date,
                    time=request.partner_time,
                    lat=request.partner_lat,
                    lon=request.partner_lon
                )
            
            # Точният календар на периода (Фаза 9); месеците и общият преглед се правят в ai_interpreter
            timeline_calendar = TransitScanner().build_calendar(
                natal_chart=natal_chart_data,
                start_date=start_date,
                end_date=end_date,
                lat=request.lat,
                lon=request.lon,
                partner_chart=partner_chart_data_for_timeline
            )
        
        # Условна логика за транзитна карта (само ако НЕ е Dynamic Mode)
        transit_chart_data = None
        transit_date = None
        
        # Проверка дали е заявен транзитен анализ (и НЕ е Dynamic Mode)
        if request.target_date is not None and not request.is_dynamic:
            # Определяне на транзитна дата и време
            transit_date = request.target_date
            transit_time = request.target_time
            
            # Ако датата е предоставена, но часът не е, използваме текущия час
            if not transit_time:
                now = datetime.now()
                transit_time = now.strftime("%H:%M:%S")
            
            # Определяне на транзитни координати (за релокация)
            transit_lat = request.target_lat if request.target_lat is not None else request.lat
            transit_lon = request.target_lon if request.target_lon is not None else request.lon
            
            # Изчисляване на транзитна карта
            transit_chart_data = engine.calculate_chart(
                date=transit_date,
                time=transit_time,
                lat=transit_lat,
                lon=transit_lon
            )
            
            # Форматиране на пълната дата и час за AI prompt (използваме datetime_local от изчислената карта)
            # Това гарантира че AI вижда точната дата и час с правилния timezone
            if transit_chart_data and transit_chart_data.get("datetime_local"):
                # Използваме datetime_local за да покажем точната дата и час на транзита
                formatted_transit_datetime = transit_chart_data["datetime_local"]
            else:
                # Fallback: комбинираме датата и часа
                formatted_transit_datetime = f"{transit_date} {transit_time}"
        
        
        # Получаване на AI интерпретация
        question = request.question or ""
        
        # Определяне на правилната target_date за AI prompt
        # Ако имаме транзитна карта, използваме datetime_local (което включва дата, час и timezone)
        if transit_chart_data and transit_chart_data.get("datetime_local"):
            zone = transit_chart_data.get("timezone")
            target_date_for_ai = f"{transit_chart_data['datetime_local']} ({zone})" if zone else transit_chart_data["datetime_local"]
        elif transit_date:
            # Fallback: комбинираме датата и часа ако са отделни
            if request.target_time:
                target_date_for_ai = f"{transit_date} {request.target_time}"
            else:
                target_date_for_ai = transit_date
        else:
            target_date_for_ai = ""
        
        crisis = safety.detect_crisis(question, request.question)
        if crisis:
            # Не викаме AI и не таксуваме; показваме подкрепящо съобщение
            interpretation = safety.CRISIS_MESSAGE_HTML
            events.track(db, "crisis_detected", current_user.id)
        else:
            memory_used = memory.activate(db, current_user, request.name, request.partner_name if has_partner else None)
            interpretation = await ai_interpreter.interpret_chart(
                natal_chart=natal_chart_data,
                transit_chart=transit_chart_data,  # Може да е None ако не е заявен транзитен анализ
                partner_chart=partner_chart_data,
                partner_name=request.partner_name,
                question=question,
                target_date=target_date_for_ai,  # Използваме пълната дата и час от транзитната карта
                language="bg",  # По подразбиране български
                report_type=request.report_type or "general",
                user_name=request.name,
                calendar=timeline_calendar,  # Календарът на периода (Dynamic Forecast Mode)
                gender=request.gender,
                partner_gender=request.partner_gender,
                checks=text_checks,
            )
            interpretation, flags = safety.check_output(interpretation, request.report_type or "general")
            if flags:
                events.track(db, "ai_output_flagged", current_user.id, {"flags": ",".join(flags)})
        
        # Изчисляване на натални аспекти
        natal_aspects_data = None
        try:
            natal_aspects_data = calculate_natal_aspects(natal_chart_data, use_wider_orbs=False)
        except Exception as e:
            print(f"Warning: Could not calculate natal aspects: {e}")
            natal_aspects_data = None
        
        # Изчисляване на partner натални аспекти, ако partner chart е налична
        print(f"🔍 DEBUG: partner_chart_data exists: {partner_chart_data is not None}")
        partner_natal_aspects_data = None
        if partner_chart_data:
            try:
                print("🔍 DEBUG: Starting partner natal aspects calculation...")
                partner_natal_aspects_data = calculate_natal_aspects(partner_chart_data, use_wider_orbs=False)
                print(f"✅ DEBUG: Calculated {len(partner_natal_aspects_data) if partner_natal_aspects_data else 0} partner natal aspects")
            except Exception as e:
                print(f"⚠️ Warning: Could not calculate partner natal aspects: {e}")
                partner_natal_aspects_data = None
        
        # Връщане на комбинирания отговор
        response_data = {
            "natal_chart": ChartResponse(
                planets=natal_chart_data["planets"],
                houses=natal_chart_data["houses"],
                angles=natal_chart_data["angles"],
                julian_day=natal_chart_data["julian_day"],
                datetime_utc=natal_chart_data["datetime_utc"],
                timezone=natal_chart_data["timezone"],
                datetime_local=natal_chart_data["datetime_local"],
                location=natal_chart_data["location"]
            ),
            "transit_chart": None,  # По подразбиране None
            "interpretation": interpretation,
            "natal_aspects": natal_aspects_data,
            "partner_natal_aspects": partner_natal_aspects_data
        }
        
        # Добавяне на транзитна карта, ако е изчислена
        if transit_chart_data:
            response_data["transit_chart"] = ChartResponse(
                planets=transit_chart_data["planets"],
                houses=transit_chart_data["houses"],
                angles=transit_chart_data["angles"],
                julian_day=transit_chart_data["julian_day"],
                datetime_utc=transit_chart_data["datetime_utc"],
                timezone=transit_chart_data["timezone"],
                datetime_local=transit_chart_data["datetime_local"],
                location=transit_chart_data["location"]
            )
        
        # Добавяне на partner карта, ако е налична
        if partner_chart_data:
            response_data["partner_chart"] = ChartResponse(
                planets=partner_chart_data["planets"],
                houses=partner_chart_data["houses"],
                angles=partner_chart_data["angles"],
                julian_day=partner_chart_data["julian_day"],
                datetime_utc=partner_chart_data["datetime_utc"],
                timezone=partner_chart_data["timezone"],
                datetime_local=partner_chart_data["datetime_local"],
                location=partner_chart_data["location"]
            )
        
        if crisis:
            response_data["crisis"] = True
            return InterpretationResponse(**response_data)

        # Запазване в историята на потребителя
        is_first = not db.query(data_api.Report.id).filter(data_api.Report.user_id == current_user.id).first()
        report = data_api.save_report(
            db, current_user,
            content=interpretation,
            report_type=request.report_type or "general",
            profile_name=request.name,
            label=data_api.report_label(request.report_type or "general", request.partner_name,
                                        question=request.question),
            params={**_report_params(request), "memory_used": memory_used},
        )
        response_data["coins_charged"] = billing.charge_for_report(
            db, current_user, report, cost, description=report.label)
        db.commit()
        response_data["report_id"] = report.id
        response_data["balance"] = current_user.coins or 0
        events.track(db, "analysis_completed", current_user.id,
                     {"type": report.report_type, "dynamic": False, "first": is_first,
                      "coins": response_data["coins_charged"]})
        _track_text_checks(db, current_user.id, report.report_type, text_checks)

        return InterpretationResponse(**response_data)
        
    except HTTPException:
        # Умишлени грешки (напр. липсващ end_date) минават непроменени
        raise
    except period_report.ForecastGenerationError as failure:
        print(f"⚠️ /interpret: прогнозата не завърши ({failure})")
        events.track(db, "analysis_failed", current_user.id,
                     _failure_props(request.report_type or "general", True, failure.stage, failure.cause))
        _track_text_checks(db, current_user.id, request.report_type or "general", failure.checks)
        raise HTTPException(status_code=502, detail=period_report.USER_MESSAGE)
    except text_guard.TextCheckError as failure:
        # Текстът не мина проверката и след поправката: нищо не се записва и не се таксува
        print(f"⚠️ /interpret: анализът не мина проверката ({failure})")
        events.track(db, "analysis_failed", current_user.id,
                     _failure_props(request.report_type or "general", False, failure.stage, failure))
        _track_text_checks(db, current_user.id, request.report_type or "general", text_checks)
        raise HTTPException(status_code=502, detail=text_guard.USER_MESSAGE)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=f"Невалидни входни данни: {str(e)}")
    except Exception as e:
        raise _internal_error("/interpret", e, "Не успяхме да генерираме анализа. Опитайте отново след малко.")


class DOCXRequest(BaseModel):
    """Request model for DOCX generation"""
    user_name: str
    birth_date: str
    birth_time: str
    birth_city: str
    report_type: str
    natal_chart: Optional[Dict] = None
    natal_aspects: Optional[List] = None
    monthly_results: List[Dict] = Field(default_factory=list)


@app.post("/generate-docx")
async def generate_docx(request: DOCXRequest, current_user: User = Depends(require_docx_quota)):
    """
    Generate DOCX report for periods > 6 months
    """
    try:
        generator = DOCXGenerator()
        
        # Prepare data for DOCX generation
        docx_data = {
            'user_name': request.user_name,
            'birth_date': request.birth_date,
            'birth_time': request.birth_time,
            'birth_city': request.birth_city,
            'report_type': request.report_type,
            'natal_chart': request.natal_chart,
            'natal_aspects': request.natal_aspects,
            'monthly_results': request.monthly_results
        }
        
        # Generate DOCX
        docx_bytes = generator.generate_docx(docx_data)
        
        # Return DOCX file - URL encode filename for Cyrillic support
        from urllib.parse import quote
        user_name_safe = docx_data.get('user_name', 'Report').replace(' ', '_')
        filename = f"Astrology_Report_{user_name_safe}_{datetime.now().strftime('%Y-%m-%d')}.docx"
        filename_encoded = quote(filename)
        
        return Response(
            content=docx_bytes,
            media_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            headers={
                "Content-Disposition": f"attachment; filename*=UTF-8''{filename_encoded}"
            }
        )
        
    except Exception as e:
        raise _internal_error("/generate-docx", e, "Не успяхме да създадем DOCX файла. Опитайте отново след малко.")


# ============================================================================
# АКАУНТНА СИСТЕМА - АВТЕНТИФИКАЦИЯ
# ============================================================================

class UserRegister(BaseModel):
    email: str
    password: str
    full_name: str
    accept_terms: bool = False

class UserLogin(BaseModel):
    email: str
    password: str

@app.post("/register")
async def register(user_data: UserRegister, http_request: Request, background_tasks: BackgroundTasks,
                   db: Session = Depends(get_db)):
    """Регистрация на нов потребител"""
    enforce(f"register:{client_ip(http_request)}", REGISTER_LIMIT_PER_HOUR, 3600, "Твърде много регистрации от този адрес.")

    email = normalize_email(user_data.email)
    full_name = (user_data.full_name or "").strip()
    error = validate_email(email) or validate_password(user_data.password, email)
    if not error and not (1 <= len(full_name) <= 100):
        error = "Въведете име до 100 символа"
    if not error and not user_data.accept_terms:
        error = "Моля, приемете Общите условия и Политиката за поверителност"
    if error:
        raise HTTPException(status_code=400, detail=error)

    # Проверка дали имейлът съществува, без значение от главни и малки букви
    existing_user = db.query(User).filter(func.lower(func.trim(User.email)) == email).first()
    if existing_user:
        raise HTTPException(status_code=400, detail="Имейлът вече е регистриран")
    
    new_user = User(
        email=email,
        full_name=full_name,
        hashed_password=hash_password(user_data.password),
        coins=0,
        terms_version=account_api.TERMS_VERSION,
        terms_accepted_at=datetime.utcnow(),
    )
    db.add(new_user)
    db.flush()
    bonus = billing.costs()["signup_bonus"]
    if bonus > 0:
        billing.apply_transaction(db, new_user.id, bonus, "signup_bonus", ref=f"signup:{new_user.id}",
                                  description="Бонус при регистрация")
    db.commit()
    db.refresh(new_user)
    account_api.queue_verification(background_tasks, new_user)
    account_api._track(db, "register", new_user.id)
    return {"message": "Успешна регистрация"}

@app.post("/login")
def login(user_data: UserLogin, http_request: Request, db: Session = Depends(get_db)):
    """Вход в системата - връща JWT token"""
    email = normalize_email(user_data.email)
    ip = client_ip(http_request)
    message = "Твърде много опити за вход."
    enforce(f"login:{ip}:{email}", LOGIN_LIMIT_PER_15_MIN, 900, message)
    enforce(f"login-ip:{ip}", LOGIN_LIMIT_PER_15_MIN * 3, 900, message)

    user = account_api.find_user_by_email(db, user_data.email)
    if not user or not verify_password(user_data.password, user.hashed_password):
        raise HTTPException(status_code=401, detail="Грешен имейл или парола")
    
    events.track(db, "login", user.id)
    return {
        "access_token": create_user_token(user),
        "token_type": "bearer",
        "user": account_api.user_payload(user),
    }


if __name__ == "__main__":
    import uvicorn  # type: ignore
    uvicorn.run(app, host="0.0.0.0", port=8000)
