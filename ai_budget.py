"""
Бюджет за AI с аварийно спиране (Фаза 14).

Всеки успешен отговор на доставчика се записва с токените си (или с оценка по дължината на текста, ако доставчикът не ги върне)
и приблизителна цена. Преди нова задача и преди всяка AI заявка се проверява:
- AI_EMERGENCY_STOP=1 спира всичко веднага (сменя се в Render без нов деплой);
- AI_DAILY_BUDGET_CENTS / AI_MONTHLY_BUDGET_CENTS (евроценти, 0 = без таван) спират, когато оценката ги достигне.
Спрените анализи не се таксуват: задачата не се създава. Цените са приблизителни и се нагласят през
AI_PRICE_INPUT_CENTS_PER_MTOK и AI_PRICE_OUTPUT_CENTS_PER_MTOK (евроценти за милион токена).
"""
import os
from datetime import datetime
from typing import Dict, Optional

from sqlalchemy import func, update
from sqlalchemy.exc import IntegrityError

from database import AIUsage, SessionLocal

STOPPED_MESSAGE = "Анализите са временно спрени по технически причини. Нищо не е таксувано. Опитайте отново по-късно."


class BudgetExceeded(Exception):
    """reason: emergency | daily | monthly"""

    def __init__(self, reason: str):
        super().__init__(reason)
        self.reason = reason


def _int_env(name: str, default: int) -> int:
    try:
        return int(float(os.getenv(name, str(default))))
    except ValueError:
        return default


def settings() -> Dict:
    return {
        "emergency_stop": os.getenv("AI_EMERGENCY_STOP", "") == "1",
        "daily_cents": _int_env("AI_DAILY_BUDGET_CENTS", 1000),         # 10 € на ден
        "monthly_cents": _int_env("AI_MONTHLY_BUDGET_CENTS", 10000),    # 100 € на месец
        "price_in": float(os.getenv("AI_PRICE_INPUT_CENTS_PER_MTOK", "30")),
        "price_out": float(os.getenv("AI_PRICE_OUTPUT_CENTS_PER_MTOK", "120")),
    }


def estimate_tokens(text: str) -> int:
    """Груба оценка, когато доставчикът не върне usage: около 3 знака на токен (български)."""
    return max(1, len(text or "") // 3)


def cost_millicents(prompt_tokens: int, completion_tokens: int, config: Optional[Dict] = None) -> int:
    cfg = config or settings()
    cents = prompt_tokens / 1_000_000 * cfg["price_in"] + completion_tokens / 1_000_000 * cfg["price_out"]
    return int(round(cents * 1000))


def record(prompt_tokens: int, completion_tokens: int, now: Optional[datetime] = None) -> None:
    """Записва един успешен отговор. Грешка при записа не бива да чупи анализа: само се логва."""
    day = (now or datetime.utcnow()).strftime("%Y-%m-%d")
    cost = cost_millicents(prompt_tokens, completion_tokens)
    values = {"calls": AIUsage.calls + 1, "prompt_tokens": AIUsage.prompt_tokens + int(prompt_tokens),
              "completion_tokens": AIUsage.completion_tokens + int(completion_tokens),
              "cost_millicents": AIUsage.cost_millicents + cost}
    db = SessionLocal()
    try:
        for _ in range(2):
            changed = db.execute(update(AIUsage).where(AIUsage.day == day).values(**values)).rowcount
            if changed:
                break
            try:
                db.add(AIUsage(day=day, calls=0, prompt_tokens=0, completion_tokens=0, cost_millicents=0))
                db.flush()
            except IntegrityError:
                db.rollback()              # друг процес го е създал междувременно: пак обновяваме
        db.commit()
    except Exception as exc:
        db.rollback()
        print(f"⚠️ ai_budget: записът на разхода не успя: {type(exc).__name__}: {exc}")
    finally:
        db.close()


def spent(now: Optional[datetime] = None) -> Dict:
    """Приблизителен разход днес и този месец, в евроценти."""
    moment = now or datetime.utcnow()
    day, month = moment.strftime("%Y-%m-%d"), moment.strftime("%Y-%m")
    db = SessionLocal()
    try:
        today = db.query(AIUsage).filter(AIUsage.day == day).first()
        month_cost = db.query(func.coalesce(func.sum(AIUsage.cost_millicents), 0)).filter(
            AIUsage.day.like(f"{month}-%")).scalar()
        return {"today_cents": round((today.cost_millicents if today else 0) / 1000, 2),
                "month_cents": round(int(month_cost or 0) / 1000, 2),
                "today_calls": today.calls if today else 0}
    finally:
        db.close()


def check(now: Optional[datetime] = None) -> None:
    """Хвърля BudgetExceeded, ако анализите са спрени (аварийно или от бюджета)."""
    cfg = settings()
    if cfg["emergency_stop"]:
        raise BudgetExceeded("emergency")
    if not cfg["daily_cents"] and not cfg["monthly_cents"]:
        return
    used = spent(now)
    if cfg["daily_cents"] and used["today_cents"] >= cfg["daily_cents"]:
        raise BudgetExceeded("daily")
    if cfg["monthly_cents"] and used["month_cents"] >= cfg["monthly_cents"]:
        raise BudgetExceeded("monthly")


def status(now: Optional[datetime] = None) -> Dict:
    """За администратора: разход, тавани и състояние."""
    cfg = settings()
    used = spent(now)
    try:
        check(now)
        state = "ok"
    except BudgetExceeded as stop:
        state = f"stopped:{stop.reason}"
    return {"state": state, **used, "daily_budget_cents": cfg["daily_cents"], "monthly_budget_cents": cfg["monthly_cents"],
            "emergency_stop": cfg["emergency_stop"]}
