"""
Моделите на заявката за карта и анализ и на отговора с данните на картата.

Живеят отделно от main.py, за да ги ползват и главният файл, и задачите за генериране (jobs.py, generation.py),
без кръгов import.
"""
from typing import Optional

from pydantic import BaseModel, Field  # type: ignore


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
