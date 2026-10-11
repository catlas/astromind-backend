"""
Бюджет за AI с аварийно спиране (Фаза 14) и регистър на опитите (Фаза 21, R10 във Фаза 25).

Всеки отговор на доставчика се записва с токените си (или с оценка по дължината на текста, ако доставчикът не ги върне) и
приблизителна цена. Преди нова задача и преди всяка AI заявка се проверява:
- AI_EMERGENCY_STOP=1 спира всичко веднага (сменя се в Render без нов деплой);
- AI_DAILY_BUDGET_CENTS / AI_MONTHLY_BUDGET_CENTS (евроценти, 0 = без таван) спират, когато оценката ги достигне.
Спрените анализи не се таксуват: задачата не се създава. Цените са приблизителни и се нагласят през
AI_PRICE_INPUT_CENTS_PER_MTOK и AI_PRICE_OUTPUT_CENTS_PER_MTOK (евроценти за милион токена); за отделен доставчик се добавя
суфикс, например AI_PRICE_OUTPUT_CENTS_PER_MTOK_TOGETHER.

Регистър на опитите (таблица ai_reservations). Преди всяка заявка към доставчика се записва резервация с идентификатор на опита
(attempt_id) за най-високия възможен разход (консервативна горна граница на входа: един токен на знак; изходът е max_tokens).
Състояния:
- reserved: заявката върви; сумата се брои към тавана заедно с вече изразходеното;
- settled: отговорът е платен; реалният разход е записан в същата транзакция (settle е идемпотентен: повторно извикване не
  отчита втори път; грешка при записа НЕ освобождава резервацията, а я оставя несигурна);
- released: доставчикът не е отговорил с платен отговор (например HTTP грешка преди обработка);
- uncertain: цената е неизвестна (таймаут след изпращане, срив, изтекла резервация): сумата продължава да се брои към тавана;
  след UNCERTAIN_HOURS се отчита по резервираната сума (written_off), а не като безплатен опит.
Платен отговор се отчита и когато е празен, отрязан (finish_reason=length) или само с reasoning, преди резервния доставчик.
Паралелни процеси: всички резервации минават през един advisory lock на PostgreSQL (покрива и деня, и месеца) и през заключения
ред на деня; SQLite е с един писач.
"""
import os
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Dict, Optional

from sqlalchemy import func, text, update
from sqlalchemy.exc import IntegrityError

from database import AIReservation, AIUsage, SessionLocal

RESERVATION_TTL_MINUTES = 20          # резервация „в ход“ по-стара от това е несигурна (срив на процеса), не безплатна
UNCERTAIN_HOURS = 24                  # толкова време несигурният опит се брои към тавана; после се отчита по резервираната сума
LEDGER_KEEP_DAYS = 7                  # приключените редове се пазят за диагностика и после се чистят
ADVISORY_LOCK_KEY = 7240001           # един ключ за всички резервации (PostgreSQL)

from contextvars import ContextVar
from typing import List

# Дневник на опитите към доставчика за текущата задача (R16): доставчик, резултат, време, токени. Без текстове и ключове.
attempt_log: ContextVar[Optional[List[Dict]]] = ContextVar("ai_attempt_log", default=None)


def note_attempt(provider: str, outcome: str, started: float, prompt_tokens: Optional[int] = None,
                 completion_tokens: Optional[int] = None, model: Optional[str] = None) -> None:
    """outcome: ok | empty | length | http_error | timeout | error. Записва се само ако задачата е вързала дневник."""
    log = attempt_log.get()
    if log is not None:
        import time
        log.append({"provider": provider, "model": model, "outcome": outcome, "latency_ms": int((time.monotonic() - started) * 1000),
                    "prompt_tokens": prompt_tokens, "completion_tokens": completion_tokens})


def summarize_attempts(log: Optional[List[Dict]]) -> Dict:
    """Кратко резюме за телеметрията: брой опити, по доставчик, токени, общо време и последен резултат."""
    log = log or []
    return {"attempts": len(log), "providers": ",".join(sorted({a["provider"] for a in log})),
            "prompt_tokens": sum(a.get("prompt_tokens") or 0 for a in log), "completion_tokens": sum(a.get("completion_tokens") or 0 for a in log),
            "provider_ms": sum(a.get("latency_ms") or 0 for a in log), "last": log[-1]["outcome"] if log else "",
            "model": next((a["model"] for a in reversed(log) if a.get("model")), "")}


STOPPED_MESSAGE = "Анализите са временно спрени по технически причини. Нищо не е таксувано. Опитайте отново по-късно."

ACTIVE_STATES = ("reserved", "uncertain")


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


def prices(provider: Optional[str] = None, config: Optional[Dict] = None) -> Dict[str, float]:
    """Тарифата на доставчика (AI_PRICE_*_<ДОСТАВЧИК>), иначе общата."""
    cfg = config or settings()
    suffix = f"_{provider.upper()}" if provider else ""
    try:
        return {"in": float(os.getenv(f"AI_PRICE_INPUT_CENTS_PER_MTOK{suffix}", cfg["price_in"])),
                "out": float(os.getenv(f"AI_PRICE_OUTPUT_CENTS_PER_MTOK{suffix}", cfg["price_out"]))}
    except ValueError:
        return {"in": cfg["price_in"], "out": cfg["price_out"]}


def estimate_tokens(text: str) -> int:
    """Оценка на изразходените токени, когато доставчикът не върне usage: около 2 знака на токен (български)."""
    return max(1, -(-len(text or "") // 2))


def upper_bound_tokens(text: str) -> int:
    """Консервативна горна граница на входа за резервация: един токен на знак (токенизаторите не дават повече токени от знаци
    за обикновен текст), така че резервацията не е по-ниска от реалния вход."""
    return max(1, len(text or ""))


def cost_millicents(prompt_tokens: int, completion_tokens: int, config: Optional[Dict] = None, provider: Optional[str] = None) -> int:
    cfg = config or settings()
    tariff = prices(provider, cfg)
    cents = prompt_tokens / 1_000_000 * tariff["in"] + completion_tokens / 1_000_000 * tariff["out"]
    return int(round(cents * 1000))


def _add_usage(db, day: str, prompt_tokens: int, completion_tokens: int, cost: int) -> None:
    """Добавя разход към реда на деня (създава го при нужда) в текущата транзакция."""
    values = {"calls": AIUsage.calls + 1, "prompt_tokens": AIUsage.prompt_tokens + int(prompt_tokens),
              "completion_tokens": AIUsage.completion_tokens + int(completion_tokens), "cost_millicents": AIUsage.cost_millicents + int(cost)}
    for _ in range(2):
        if db.execute(update(AIUsage).where(AIUsage.day == day).values(**values)).rowcount:
            return
        try:
            with db.begin_nested():
                db.add(AIUsage(day=day, calls=0, prompt_tokens=0, completion_tokens=0, cost_millicents=0))
        except IntegrityError:
            pass                                  # друг процес го е създал междувременно: пак обновяваме
    raise RuntimeError("редът на деня не може да се създаде")


def record(prompt_tokens: int, completion_tokens: int, now: Optional[datetime] = None, provider: Optional[str] = None) -> bool:
    """Записва един платен отговор без резервация (няма таван). Връща True при успех; грешка при записа не чупи анализа."""
    day = (now or datetime.utcnow()).strftime("%Y-%m-%d")
    db = SessionLocal()
    try:
        _add_usage(db, day, prompt_tokens, completion_tokens, cost_millicents(prompt_tokens, completion_tokens, provider=provider))
        db.commit()
        return True
    except Exception as exc:
        db.rollback()
        print(f"⚠️ ai_budget: записът на разхода не успя: {type(exc).__name__}: {exc}")
        return False
    finally:
        db.close()


@dataclass
class Reservation:
    id: int
    millicents: int
    attempt_id: str = ""


def _maintain(db, moment: datetime) -> None:
    """Под заключването: изтекла резервация става несигурна (не безплатна); несигурна над UNCERTAIN_HOURS се отчита по резервираната
    сума; приключените редове се чистят."""
    stale = moment - timedelta(minutes=RESERVATION_TTL_MINUTES)
    db.execute(update(AIReservation).where(AIReservation.state == "reserved", AIReservation.created_at < stale).values(state="uncertain"))
    old = moment - timedelta(hours=UNCERTAIN_HOURS)
    for row in db.query(AIReservation).filter(AIReservation.state == "uncertain", AIReservation.created_at < old).all():
        _add_usage(db, row.day, 0, 0, int(row.millicents or 0))
        row.state, row.settled_at = "written_off", moment
    db.query(AIReservation).filter(AIReservation.state.in_(("settled", "released", "written_off")),
                                   AIReservation.created_at < moment - timedelta(days=LEDGER_KEEP_DAYS)).delete(synchronize_session=False)


def reserve(prompt_tokens: int, max_completion_tokens: int, now: Optional[datetime] = None,
            provider: Optional[str] = None) -> Optional[Reservation]:
    """
    Резервира най-високия възможен разход на една заявка или хвърля BudgetExceeded (аварийно спиране, дневен или месечен таван,
    броейки и резервациите в ход и несигурните). Връща None, когато няма таван (нищо за резервиране).
    """
    cfg = settings()
    if cfg["emergency_stop"]:
        raise BudgetExceeded("emergency")
    if not cfg["daily_cents"] and not cfg["monthly_cents"]:
        return None
    moment = now or datetime.utcnow()
    day, month = moment.strftime("%Y-%m-%d"), moment.strftime("%Y-%m")
    amount = cost_millicents(int(prompt_tokens), int(max_completion_tokens), cfg, provider)
    db = SessionLocal()
    try:
        if db.bind.dialect.name == "postgresql":
            db.execute(text("SELECT pg_advisory_xact_lock(:k)"), {"k": ADVISORY_LOCK_KEY})      # деня и месеца заедно, за всички процеси
        for _ in range(2):
            if db.query(AIUsage).filter(AIUsage.day == day).first() is not None:
                break
            try:
                with db.begin_nested():
                    db.add(AIUsage(day=day, calls=0, prompt_tokens=0, completion_tokens=0, cost_millicents=0))
            except IntegrityError:
                pass                       # друг процес го е създал междувременно
        row = db.query(AIUsage).filter(AIUsage.day == day).with_for_update().one()      # сериализира резервациите на деня
        _maintain(db, moment)
        active = db.query(AIReservation).filter(AIReservation.state.in_(ACTIVE_STATES)).all()
        active_today = sum(r.millicents for r in active if r.day == day)
        active_month = sum(r.millicents for r in active if str(r.day).startswith(month))      # резервация от предния месец не брои към този
        month_cost = db.query(func.coalesce(func.sum(AIUsage.cost_millicents), 0)).filter(AIUsage.day.like(f"{month}-%")).scalar()
        # Заявка, чийто най-висок възможен разход не се побира в оставащото, не тръгва: паралелните заявки не минават над тавана
        if cfg["daily_cents"] and int(row.cost_millicents or 0) + active_today + amount > cfg["daily_cents"] * 1000:
            raise BudgetExceeded("daily")
        if cfg["monthly_cents"] and int(month_cost or 0) + active_month + amount > cfg["monthly_cents"] * 1000:
            raise BudgetExceeded("monthly")
        reservation = AIReservation(day=day, millicents=amount, created_at=moment, attempt_id=uuid.uuid4().hex, state="reserved",
                                    provider=provider)
        db.add(reservation)
        db.commit()
        return Reservation(id=reservation.id, millicents=amount, attempt_id=reservation.attempt_id)
    except BudgetExceeded:
        db.commit()                        # поддръжката (изтекли, отписани, почистени) се запазва; нова резервация няма
        raise
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


def _transition(reservation: Optional[Reservation], new_state: str, **extra) -> bool:
    """Условен преход reserved -> new_state; True само ако този извик го е извършил (идемпотентност)."""
    if reservation is None:
        return False
    db = SessionLocal()
    try:
        changed = db.execute(update(AIReservation).where(AIReservation.id == reservation.id, AIReservation.state == "reserved")
                             .values(state=new_state, **extra)).rowcount
        db.commit()
        return bool(changed)
    except Exception as exc:
        db.rollback()
        print(f"⚠️ ai_budget: преходът на резервацията не успя: {type(exc).__name__}: {exc}")
        return False
    finally:
        db.close()


def release(reservation: Optional[Reservation]) -> bool:
    """Заявката не е стигнала до платен отговор: резервацията е released. Идемпотентно; не пипа вече отчетена или несигурна резервация."""
    return _transition(reservation, "released", settled_at=datetime.utcnow())


def uncertain(reservation: Optional[Reservation]) -> bool:
    """Цената е неизвестна (таймаут след изпращане, срив): резервацията остава в тавана като несигурна, не се освобождава."""
    return _transition(reservation, "uncertain")


def settle(reservation: Optional[Reservation], prompt_tokens: int, completion_tokens: int, provider: Optional[str] = None,
           now: Optional[datetime] = None) -> bool:
    """
    Отчита платен отговор в ЕДНА транзакция: условен преход reserved|uncertain -> settled и добавяне на разхода. Повторно извикване
    за същия опит не прави нищо (False). Грешка при записа връща резервацията като несигурна, не като нулев разход.
    Без резервация (няма таван) само се записва разходът.
    """
    if reservation is None:
        return record(prompt_tokens, completion_tokens, now, provider)
    moment = now or datetime.utcnow()
    db = SessionLocal()
    try:
        changed = db.execute(update(AIReservation).where(AIReservation.id == reservation.id, AIReservation.state.in_(ACTIVE_STATES))
                             .values(state="settled", provider=provider, prompt_tokens=int(prompt_tokens),
                                     completion_tokens=int(completion_tokens), settled_at=moment)).rowcount
        if not changed:
            db.rollback()
            return False                   # вече отчетена, освободена или неизвестна: не се отчита втори път
        _add_usage(db, moment.strftime("%Y-%m-%d"), prompt_tokens, completion_tokens,
                   cost_millicents(prompt_tokens, completion_tokens, provider=provider))
        db.commit()
        return True
    except Exception as exc:
        db.rollback()
        print(f"⚠️ ai_budget: отчитането на разхода не успя, резервацията остава несигурна: {type(exc).__name__}: {exc}")
        uncertain(reservation)
        return False
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


def uncertain_summary() -> Dict:
    """За оператора: колко опита са с неизвестна цена и на каква обща резервирана сума (евроценти)."""
    db = SessionLocal()
    try:
        rows = db.query(AIReservation).filter(AIReservation.state == "uncertain").all()
        return {"uncertain_attempts": len(rows), "uncertain_cents": round(sum(int(r.millicents or 0) for r in rows) / 1000, 2)}
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
    return {"state": state, **used, **uncertain_summary(), "daily_budget_cents": cfg["daily_cents"],
            "monthly_budget_cents": cfg["monthly_cents"], "emergency_stop": cfg["emergency_stop"]}
