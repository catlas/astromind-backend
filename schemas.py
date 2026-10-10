"""
Моделите на заявката за карта и анализ и на отговора с данните на картата.

Живеят отделно от main.py, за да ги ползват и главният файл, и задачите за генериране (jobs.py, generation.py),
без кръгов import.
"""
from typing import Optional

from pydantic import BaseModel, Field, field_validator, model_validator  # type: ignore

import relationship
from engine import UNKNOWN_TIME_ANCHOR


class ChartRequest(BaseModel):
    """Модел за заявка за изчисляване на карта"""
    name: Optional[str] = Field(
        default=None,
        max_length=100,
        description="Име на потребителя"
    )
    date: str = Field(..., description="Дата на раждане във формат YYYY-MM-DD или YYYY/MM/DD")
    time: Optional[str] = Field(
        default=None,
        description="Час на раждане във формат HH:MM:SS или HH:MM; не е нужен, когато birth_time_known е false"
    )
    # Неизвестен час: без Асцендент, МС и домове; часът не се измисля (виж engine._calculate_chart_without_time)
    birth_time_known: bool = Field(default=True, description="False, когато часът на раждане е неизвестен")
    partner_time_known: bool = Field(default=True, description="False, когато часът на раждане на втория човек е неизвестен")
    lat: float = Field(..., ge=-90, le=90, description="Географска ширина в градуси (-90 до 90)")
    lon: float = Field(..., ge=-180, le=180, description="Географска дължина в градуси (-180 до 180)")
    # timezone_offset е премахнат - сега се изчислява автоматично от координатите
    question: Optional[str] = Field(
        default=None,
        max_length=1500,
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
        max_length=100,
        description="Име на втория човек (за анализ за двама)"
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
    # Избор при час, който се повтаря (връщане на часовника назад): 0 е първото преминаване, 1 е второто.
    # Без избор такъв час е грешка, а не тихо поправяне.
    birth_fold: Optional[int] = Field(default=None, ge=0, le=1, description="Първо (0) или второ (1) преминаване на повтарящ се час")
    partner_fold: Optional[int] = Field(default=None, ge=0, le=1, description="Същото за втория човек")
    # Контекст на отношенията между двамата (Фаза 12): friend, family, parent_child, work, romantic; без избор: общо взаимодействие
    relationship: Optional[str] = Field(default=None, max_length=20, description="Отношенията между двамата души")
    # Номерата на избраните профили: един и същ профил не може да е и първи, и втори човек
    profile_id: Optional[int] = Field(default=None, ge=1, description="Профил на първия човек, ако е избран")
    partner_profile_id: Optional[int] = Field(default=None, ge=1, description="Профил на втория човек, ако е избран")
    # Известен пол (male/female). Без него AI пише неутрално и не гадае пола.
    gender: Optional[str] = Field(default=None, max_length=20, description="Пол на първия човек, ако е известен")
    partner_gender: Optional[str] = Field(default=None, max_length=20, description="Пол на втория човек, ако е известен")


    @field_validator("relationship")
    @classmethod
    def _known_relationship(cls, value):
        if value in (None, ""):
            return None
        if value not in relationship.CONTEXTS:
            raise ValueError("Непознат вид отношения")
        return value

    @model_validator(mode="after")
    def _birth_times(self):
        """Часът е задължителен, освен ако не е отбелязан като неизвестен; тогава се слага служебният пладне."""
        if self.birth_time_known:
            if not (self.time or "").strip():
                raise ValueError("Въведете час на раждане или отбележете, че часът е неизвестен")
        else:
            self.time = UNKNOWN_TIME_ANCHOR
            self.birth_fold = None
        if self.partner_date and not self.partner_time_known:
            self.partner_time = UNKNOWN_TIME_ANCHOR
            self.partner_fold = None
        # Непълни данни за втория човек не се губят тихо (иначе анализът за двама би станал за един): или всичко, или нищо
        given = [self.partner_date, self.partner_time, self.partner_lat, self.partner_lon]
        if any(value not in (None, "") for value in given) and not all(value not in (None, "") for value in given):
            raise ValueError("Данните за втория човек са непълни: нужни са дата, час (или „часът е неизвестен“) и място на раждане")
        return self

class ChartResponse(BaseModel):
    """Модел за отговор с данни от картата"""
    planets: dict
    houses: dict
    angles: dict
    time_known: bool = True        # False при неизвестен час на раждане: без домове, Асцендент и МС
    sign_ranges: dict = {}         # при неизвестен час: знаци на тела, зависещи от часа (Луната винаги)
    julian_day: float
    datetime_utc: str
    timezone: str
    datetime_local: str
    location: dict
