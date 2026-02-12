"""
FastAPI сървър за астрологично приложение
Предоставя API endpoints за изчисляване и интерпретация на астрологични карти
"""

from fastapi import FastAPI, HTTPException, Depends, status  # type: ignore
from fastapi.middleware.cors import CORSMiddleware  # type: ignore
from fastapi.responses import StreamingResponse, Response  # type: ignore
from pydantic import BaseModel, Field  # type: ignore
from typing import Optional, List, Dict
from datetime import datetime, timedelta
from sqlalchemy.orm import Session  # type: ignore
import json
import asyncio
import os
from dotenv import load_dotenv
import engine
from ai_interpreter import AIInterpreter, get_interpreter
from scanner import TransitScanner
from aspects_engine import calculate_natal_aspects
from docx_generator import DOCXGenerator
from database import User, get_db
from auth import hash_password, verify_password, create_access_token

load_dotenv()

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

# Инициализация на AI интерпретатора
ai_interpreter = get_interpreter()


def _calculate_max_months_for_token_limit(has_partner: bool = False) -> int:
    """
    Изчислява максималния брой месеци които могат да се поберат в лимита от 30,000 токена.
    
    Базирано на:
    - Лимит: 30,000 токена
    - Приблизително 50-100 събития на месец (зависи от периода)
    - С партньор: ~100-200 събития на месец (събития за двама)
    - Приблизително 50-80 токена на събитие (JSON формат)
    - Натална карта: ~2000 токена (или ~4000 с партньор)
    - System prompt: ~500-1000 токена
    - Остават ~27,500 токена за събития (индивидуално) или ~25,000 (с партньор)
    
    Returns:
        int: Максимален брой месеци (3 за индивидуално, 1 с партньор)
    """
    if has_partner:
        return 1  # С партньор: максимум 1 месец
    return 3  # Индивидуално: максимум 3 месеца


def _filter_and_limit_events(events: List[Dict], max_events: int = 400) -> List[Dict]:
    """
    Филтрира и ограничава timeline events за да намали размера на заявката към AI.
    
    Приоритет:
    1. Eclipses (най-важни)
    2. Retrogrades (Stationary Retrograde/Direct)
    3. Lunations (New Moon/Full Moon)
    4. Major Transits (Jupiter, Saturn, Uranus, Neptune, Pluto към важни планети)
    5. Minor Transits (Mars към важни планети)
    
    Args:
        events: Пълен списък от събития
        max_events: Максимален брой събития (по подразбиране 120)
    
    Returns:
        Филтриран и ограничен списък от събития
    """
    if len(events) <= max_events:
        return events
    
    # Приоритети: по-висок номер = по-висок приоритет
    priority_map = {
        "ECLIPSE": 5,
        "RETROGRADE": 4,
        "LUNATION": 3,
        "TRANSIT": 1
    }
    
    # Важни планети за транзити (по-висок приоритет)
    important_natal_planets = {"Sun", "Moon", "Mercury", "Venus", "Mars", "Ascendant", "MC"}
    important_transit_planets = {"Jupiter", "Saturn", "Uranus", "Neptune", "Pluto"}
    
    def get_event_priority(event: Dict) -> int:
        """Връща приоритет на събитие (по-висок = по-важно)"""
        event_type = event.get("type", "")
        base_priority = priority_map.get(event_type, 0)
        
        # Ако е транзит, проверяваме важността на планетите
        if event_type == "TRANSIT":
            natal_planet = event.get("natal_planet", "")
            transit_planet = event.get("planet", "")
            
            # Major Transits (Jupiter/Saturn/Uranus/Neptune/Pluto към важни планети)
            if transit_planet in important_transit_planets and natal_planet in important_natal_planets:
                return base_priority + 2
            # Mars към важни планети
            elif transit_planet == "Mars" and natal_planet in important_natal_planets:
                return base_priority + 1
            # Други транзити
            else:
                return base_priority
        
        return base_priority
    
    # Сортиране по приоритет (най-висок първо), след това по дата
    sorted_events = sorted(events, key=lambda x: (-get_event_priority(x), x.get("date", "")))
    
    # Вземане на най-важните събития
    filtered_events = sorted_events[:max_events]
    
    # Сортиране отново по дата за финален списък
    filtered_events.sort(key=lambda x: x.get("date", ""))
    
    return filtered_events


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
            "POST /login": "Вход в системата - връща JWT token"
        }
    }


@app.get("/health")
async def health_check():
    """Health check endpoint"""
    return {"status": "healthy"}


@app.post("/calculate", response_model=ChartResponse)
async def calculate_chart(request: ChartRequest):
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
    except FileNotFoundError as e:
        raise HTTPException(
            status_code=500,
            detail=f"Ефемеридите не са намерени. Моля изпълнете scripts/download_ephe.py: {str(e)}"
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Грешка при изчисляване на картата: {str(e)}")


@app.post("/interpret-stream")
async def interpret_chart_stream(request: ChartRequest):
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
            
            # Scan period for timeline events
            # For dynamic forecast, use target_date as start (or current date if not provided)
            # NEVER use birth date (request.date) as start_date for forecast!
            if request.target_date:
                start_date = request.target_date
            else:
                # Default to current date if no target_date provided
                from datetime import datetime
                start_date = datetime.now().strftime("%Y-%m-%d")
            
            end_date = request.end_date
            
            scanner = TransitScanner()
            all_events = scanner.scan_period(
                natal_chart=natal_chart_data,
                start_date=start_date,
                end_date=end_date,
                lat=request.lat,
                lon=request.lon,
                partner_chart=partner_chart_data
            )
            
            timeline_events = _filter_and_limit_events(all_events)
            
            # Group events by month
            from collections import defaultdict
            events_by_month = defaultdict(list)
            for event in timeline_events:
                month_key = event['date'][:7]  # "YYYY-MM"
                events_by_month[month_key].append(event)
            
            sorted_months = sorted(events_by_month.keys())
            
            if not sorted_months:
                yield f"data: {json.dumps({'type': 'error', 'message': 'Няма събития за анализиране в избрания период'}, ensure_ascii=False)}\n\n"
                return
            
            # Month names in Bulgarian
            month_names = {
                "01": "Януари", "02": "Февруари", "03": "Март", "04": "Април",
                "05": "Май", "06": "Юни", "07": "Юли", "08": "Август",
                "09": "Септември", "10": "Октомври", "11": "Ноември", "12": "Декември"
            }
            
            # Send initial metadata with natal chart data
            start_month = f"{month_names.get(sorted_months[0][5:7], sorted_months[0][5:7])} {sorted_months[0][:4]}"
            end_month = f"{month_names.get(sorted_months[-1][5:7], sorted_months[-1][5:7])} {sorted_months[-1][:4]}"
            
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
            
            # Process each month
            for idx, month in enumerate(sorted_months):
                monthly_events = events_by_month[month]
                month_display = f"{month_names.get(month[5:7], month[5:7])} {month[:4]}"
                
                # Send month_start event
                yield f"data: {json.dumps({'type': 'month_start', 'month': month_display, 'index': idx, 'total': len(sorted_months)}, ensure_ascii=False)}\n\n"
                
                # Process month with AI interpreter using callback
                monthly_text = await ai_interpreter._process_monthly_chunk(
                    month=month,
                    monthly_events=monthly_events,
                    report_type=request.report_type or "general",
                    language="bg",
                    natal_chart=natal_chart_data,
                    partner_chart=partner_chart_data,
                    user_display_name=request.name or "User",
                    partner_display_name=request.partner_name or "Partner",
                    question=request.question or "",
                    has_partner=bool(partner_chart_data)
                )
                
                # Send month_complete event
                yield f"data: {json.dumps({'type': 'month_complete', 'month': month_display, 'text': monthly_text, 'index': idx, 'total': len(sorted_months)}, ensure_ascii=False)}\n\n"
                
                # Small delay to prevent overwhelming the client
                await asyncio.sleep(0.1)
            
            # Send completion event
            yield f"data: {json.dumps({'type': 'complete'}, ensure_ascii=False)}\n\n"
            
        except Exception as e:
            error_message = f"Грешка при генериране на прогноза: {str(e)}"
            yield f"data: {json.dumps({'type': 'error', 'message': error_message}, ensure_ascii=False)}\n\n"
    
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
async def interpret_chart(request: ChartRequest):
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
        
        # Dynamic Forecast Mode (Timeline Scanner)
        timeline_events = None
        if request.is_dynamic:
            if not request.end_date:
                raise HTTPException(
                    status_code=400,
                    detail="end_date е задължително когато is_dynamic=True"
                )
            
            # Използваме target_date като start_date, или текущата дата
            start_date = request.target_date if request.target_date else datetime.now().strftime("%Y-%m-%d")
            end_date = request.end_date
            
            # Note: Monthly chunking now handles token limits, so we don't restrict period length
            
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
            
            # Инициализация на scanner
            scanner = TransitScanner()
            all_events = scanner.scan_period(
                natal_chart=natal_chart_data,
                start_date=start_date,
                end_date=end_date,
                lat=request.lat,
                lon=request.lon,
                partner_chart=partner_chart_data_for_timeline  # Предаваме partner chart за Relationship Forecast Mode
            )
            
            # Филтриране и ограничаване на събитията за да намалим токените
            timeline_events = _filter_and_limit_events(all_events)
        
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
            target_date_for_ai = transit_chart_data["datetime_local"]
        elif transit_date:
            # Fallback: комбинираме датата и часа ако са отделни
            if request.target_time:
                target_date_for_ai = f"{transit_date} {request.target_time}"
            else:
                target_date_for_ai = transit_date
        else:
            target_date_for_ai = ""
        
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
            timeline_events=timeline_events  # Timeline events за Dynamic Forecast Mode
        )
        
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
        
        # Логване на целия response_data преди създаване на InterpretationResponse
        response_obj = InterpretationResponse(**response_data)
        return response_obj
        
    except ValueError as e:
        raise HTTPException(status_code=400, detail=f"Невалидни входни данни: {str(e)}")
    except FileNotFoundError as e:
        raise HTTPException(
            status_code=500,
            detail=f"Ефемеридите не са намерени. Моля изпълнете scripts/download_ephe.py: {str(e)}"
        )
    except RuntimeError as e:
        # Грешки от AI интерпретатора
        if "OpenAI" in str(e):
            raise HTTPException(
                status_code=500,
                detail=f"Грешка при комуникация с OpenAI API. Проверете OPENAI_API_KEY: {str(e)}"
            )
        raise HTTPException(status_code=500, detail=f"Грешка при обработка: {str(e)}")
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Неочаквана грешка: {str(e)}")


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
async def generate_docx(request: DOCXRequest):
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
        raise HTTPException(status_code=500, detail=f"Грешка при генериране на DOCX: {str(e)}")


# ============================================================================
# АКАУНТНА СИСТЕМА - АВТЕНТИФИКАЦИЯ
# ============================================================================

class UserRegister(BaseModel):
    email: str
    password: str
    full_name: str

class UserLogin(BaseModel):
    email: str
    password: str

@app.post("/register")
def register(user_data: UserRegister, db: Session = Depends(get_db)):
    """Регистрация на нов потребител"""
    # Проверка дали имейлът съществува
    existing_user = db.query(User).filter(User.email == user_data.email).first()
    if existing_user:
        raise HTTPException(status_code=400, detail="Имейлът вече е регистриран")
    
    new_user = User(
        email=user_data.email,
        full_name=user_data.full_name,
        hashed_password=hash_password(user_data.password)
    )
    db.add(new_user)
    db.commit()
    return {"message": "Успешна регистрация"}

@app.post("/login")
def login(user_data: UserLogin, db: Session = Depends(get_db)):
    """Вход в системата - връща JWT token"""
    user = db.query(User).filter(User.email == user_data.email).first()
    if not user or not verify_password(user_data.password, user.hashed_password):
        raise HTTPException(status_code=401, detail="Грешен имейл или парола")
    
    token = create_access_token(data={"sub": user.email})
    return {
        "access_token": token, 
        "token_type": "bearer",
        "user": {"full_name": user.full_name, "coins": user.coins}
    }


if __name__ == "__main__":
    import uvicorn  # type: ignore
    uvicorn.run(app, host="0.0.0.0", port=8000)
