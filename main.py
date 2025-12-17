"""
FastAPI сървър за астрологично приложение
Предоставя API endpoints за изчисляване и интерпретация на астрологични карти
"""

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field
from typing import Optional, List, Dict
from datetime import datetime, timedelta
import engine
from ai_interpreter import AIInterpreter, get_interpreter
from scanner import TransitScanner

# Инициализация на FastAPI приложението
app = FastAPI(
    title="Astrology API",
    description="API за изчисляване и интерпретация на астрологични карти",
    version="1.0.0"
)

# CORS Middleware - разрешава заявки от React frontend
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # Vite default port
    allow_credentials=True,
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
    # Partner/Relationship полета
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


@app.get("/")
async def root():
    """Root endpoint - информация за API"""
    return {
        "message": "Astrology API",
        "version": "1.0.0",
        "endpoints": {
            "POST /calculate": "Изчислява астрологична карта",
            "POST /interpret": "Изчислява карта и получава AI интерпретация"
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
    - Куспиди на къщите
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
        # Изчисляване на натална карта
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
        
        
        # Получаване на AI интерпретация
        question = request.question or ""
        interpretation = await ai_interpreter.interpret_chart(
            natal_chart=natal_chart_data,
            transit_chart=transit_chart_data,  # Може да е None ако не е заявен транзитен анализ
            partner_chart=partner_chart_data,
            partner_name=request.partner_name,
            question=question,
            target_date=transit_date or "",  # Празен string ако няма транзитна дата
            language="bg",  # По подразбиране български
            report_type=request.report_type or "general",
            user_name=request.name,
            timeline_events=timeline_events  # Timeline events за Dynamic Forecast Mode
        )
        
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
            "interpretation": interpretation
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
        
        return InterpretationResponse(**response_data)
        
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


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)

