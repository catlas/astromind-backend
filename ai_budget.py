"""
Бюджет за AI с аварийно спиране (Фаза 14).

Всеки успешен отговор на доставчика се записва с токените си (или с оценка по дължината на текста, ако доставчикът не ги върне)
и приблизителна цена. Преди нова задача и преди всяка AI заявка се проверява:
- AI_EMERGENCY_STOP=1 спира всичко веднага (сменя се в Render без нов деплой);
- AI_DAILY_BUDGET_CENTS / AI_MONTHLY_BUDGET_CENTS (евроценти, 0 = без таван) спират, когато оценката ги достигне.
Спрените анализи не се таксуват: задачата не се създава. Цените са приблизителни и се нагласят през
AI_PRICE_INPUT_CENTS_PER_MTOK и AI_PRICE_OUTPUT_CENTS_PER_MTOK (евроценти за милион токена).

Резервация (Фаза 21): преди всяка заявка към доставчика се резервира най-високият възможен разход (токените на подканата и
max_tokens на отговора). Резервациите на текущите заявки се броят към бюджета заедно с вече изразходеното, затова паралелни
заявки не минават заедно над тавана. След отговора резервацията се заменя с реалния разход (settle), при грешка се освобождава
(release). Всеки опит (първичен, резервен, поправка) е отделна заявка с отделна резервация. Резервацията е в базата, така че
важи за всички процеси; необявена резервация изтича след RESERVATION_TTL_MINUTES. Редът на деня се заключва (FOR UPDATE), за да
няма надбягване между процеси.
"""
import os
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Dict, Optional

from sqlalchemy import func, update
from sqlalchemy.exc import IntegrityError

from database import AIReservation, AIUsage, SessionLocal

RESERVATION_TTL_MINUTES = 20

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


@dataclass
class Reservation:
    id: int
    millicents: int


def reserve(prompt_tokens: int, max_completion_tokens: int, now: Optional[datetime] = None) -> Optional[Reservation]:
    """
    Резервира най-високия възможен разход на една заявка или хвърля BudgetExceeded (аварийно спиране, дневен или месечен таван,
    броейки и резервациите на другите текущи заявки). Връща None, когато няма таван (нищо за резервиране).
    """
    cfg = settings()
    if cfg["emergency_stop"]:
        raise BudgetExceeded("emergency")
    if not cfg["daily_cents"] and not cfg["monthly_cents"]:
        return None
    moment = now or datetime.utcnow()
    day, month = moment.strftime("%Y-%m-%d"), moment.strftime("%Y-%m")
    amount = cost_millicents(int(prompt_tokens), int(max_completion_tokens), cfg)
    db = SessionLocal()
    try:
        for _ in range(2):
            if db.query(AIUsage).filter(AIUsage.day == day).first() is not None:
                break
            try:
                db.add(AIUsage(day=day, calls=0, prompt_tokens=0, completion_tokens=0, cost_millicents=0))
                db.flush()
            except IntegrityError:
                db.rollback()              # друг процес го е създал междувременно
        row = db.query(AIUsage).filter(AIUsage.day == day).with_for_update().one()      # сериализира резервациите на деня
        db.query(AIReservation).filter(AIReservation.created_at < moment - timedelta(minutes=RESERVATION_TTL_MINUTES)).delete()
        active = db.query(AIReservation).all()
        active_today = sum(r.millicents for r in active if r.day == day)
        active_total = sum(r.millicents for r in active)
        month_cost = db.query(func.coalesce(func.sum(AIUsage.cost_millicents), 0)).filter(AIUsage.day.like(f"{month}-%")).scalar()
        # Заявка, чийто най-висок възможен разход не се побира в оставащото, не тръгва: паралелните заявки не минават над тавана
        if cfg["daily_cents"] and int(row.cost_millicents or 0) + active_today + amount > cfg["daily_cents"] * 1000:
            raise BudgetExceeded("daily")
        if cfg["monthly_cents"] and int(month_cost or 0) + active_total + amount > cfg["monthly_cents"] * 1000:
            raise BudgetExceeded("monthly")
        reservation = AIReservation(day=day, millicents=amount, created_at=moment)
        db.add(reservation)
        db.commit()
        return Reservation(id=reservation.id, millicents=amount)
    except BudgetExceeded:
        db.rollback()
        raise
    finally:
        db.close()


def release(reservation: Optional[Reservation]) -> None:
    """Освобождава резервацията (заявката не е стигнала до платен отговор). Идемпотентно; без резервация не прави нищо."""
    if reservation is None:
        return
    db = SessionLocal()
    try:
        db.query(AIReservation).filter(AIReservation.id == reservation.id).delete()
        db.commit()
    except Exception as exc:
        db.rollback()
        print(f"⚠️ ai_budget: освобождаването на резервацията не успя: {type(exc).__name__}: {exc}")
    finally:
        db.close()


def settle(reservation: Optional[Reservation], prompt_tokens: int, completion_tokens: int) -> None:
    """Заменя резервацията с реалния разход на отговора."""
    record(prompt_tokens, completion_tokens)
    release(reservation)


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
