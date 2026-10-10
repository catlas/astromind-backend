"""
Горна граница на периода на прогнозата.

Всеки календарен месец от периода е отделна AI заявка (време и цена), затова периодът е
ограничен: до FORECAST_MAX_MONTHS_SINGLE месеца за един човек и до FORECAST_MAX_MONTHS_PAIR за двама.
Стойностите се сменят през environment в Render; екранът ги чете от /billing/config.
Месеците се броят като календарни, включително първия и последния (01.10–30.11 са 2 месеца).
"""
import os
from datetime import datetime
from typing import Optional

FORECAST_MAX_MONTHS_SINGLE = int(os.getenv("FORECAST_MAX_MONTHS_SINGLE", "3"))
FORECAST_MAX_MONTHS_PAIR = int(os.getenv("FORECAST_MAX_MONTHS_PAIR", "2"))


def max_forecast_months(has_partner: bool) -> int:
    return FORECAST_MAX_MONTHS_PAIR if has_partner else FORECAST_MAX_MONTHS_SINGLE


def period_months(start_date: str, end_date: str) -> Optional[int]:
    """Брой календарни месеци на периода, включително първия и последния; None при невалидни дати или обратен ред."""
    try:
        start = datetime.strptime(start_date, "%Y-%m-%d")
        end = datetime.strptime(end_date, "%Y-%m-%d")
    except (TypeError, ValueError):
        return None
    if end < start:
        return None
    return (end.year - start.year) * 12 + end.month - start.month + 1


def forecast_period_error(start_date: str, end_date: str, has_partner: bool) -> Optional[str]:
    """Съобщение за грешка, ако периодът е невалиден или твърде дълъг; иначе None."""
    try:
        start = datetime.strptime(start_date, "%Y-%m-%d")
        end = datetime.strptime(end_date, "%Y-%m-%d")
    except (TypeError, ValueError):
        return "Невалидна начална или крайна дата на прогнозата."
    if end < start:
        return "Крайната дата на прогнозата трябва да е след началната."
    months = period_months(start_date, end_date)
    limit = max_forecast_months(has_partner)
    if months > limit:
        who = "за двама души" if has_partner else "за един човек"
        return f"Прогнозата може да обхваща най-много {limit} месеца {who}. Изберете по-кратък период."
    return None
