"""
Задачи за генериране на анализ (Фаза 11).

Генерацията вече не е заявка, която клиентът държи отворена. Клиентът създава задача (POST /jobs), която върви на
сървъра независимо от браузъра, чете състоянието и събитията на задачата (GET /jobs/{id}) и може да затвори страницата: при
връщане задачата се намира по номер. Правила:

- Идемпотентност: един и същ Idempotency-Key връща същата задача, затова двоен клик или повторено изпращане не прави
  втори анализ. Същият ключ с друга заявка е грешка (409).
- Сумата се резервира (дебитира) при създаването: паралелни заявки не могат да похарчат един и същ баланс. При
  неуспех, отказ или прекъсване сумата се връща в същите дялове (подарък / внесени средства). Успехът я запазва.
- Край на задачата е атомарен: записът на отчета и статусът "succeeded" са в една транзакция, която заключва реда на
  задачата. Отказ и край се състезават през условна смяна на статуса: печели само един, затова отказът никога не връща
  сума и едновременно доставя платен отчет.
- Лизинг и срок: работещата задача подновява лизинга си. Задача с изтекъл лизинг (рестарт на сървъра) се връща в опашката
  най-много JOB_MAX_ATTEMPTS пъти, после се проваля и сумата се връща. Цялата задача има срок JOB_DEADLINE_SECONDS.
- Едновременност: най-много JOB_CONCURRENCY задачи генерират наведнъж, а един потребител има най-много
  JOB_MAX_ACTIVE_PER_USER активни задачи.
- Поверителност: свободният текст на въпроса се изтрива от заявката, щом задачата приключи; приключените задачи се
  трият след JOB_RETENTION_DAYS дни (отчетът остава в История).
"""
import asyncio
import hashlib
import json
import os
import weakref
from contextlib import contextmanager
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional, Tuple

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from sqlalchemy import update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

import billing
import data_api
import events
import generation
import safety
from database import Job, SessionLocal, User, get_db
from deps import get_current_user
from rate_limit import enforce, limiter
from schemas import ChartRequest

router = APIRouter(prefix="/jobs")

ACTIVE = ("queued", "running")
STAGES = ("queued", "calculating", "analyzing", "checking", "finishing", "done")

JOB_DEADLINE_SECONDS = int(os.getenv("JOB_DEADLINE_SECONDS", "900"))
JOB_CONCURRENCY = max(1, int(os.getenv("JOB_CONCURRENCY", "3")))
JOB_MAX_ACTIVE_PER_USER = max(1, int(os.getenv("JOB_MAX_ACTIVE_PER_USER", "2")))
JOB_MAX_ATTEMPTS = max(1, int(os.getenv("JOB_MAX_ATTEMPTS", "2")))
JOB_RETENTION_DAYS = max(1, int(os.getenv("JOB_RETENTION_DAYS", "14")))
LEASE_SECONDS = 120
HEARTBEAT_SECONDS = 30
SWEEP_SECONDS = 60
MAX_WAIT_SECONDS = 120

# Лимити на AI заявките на потребител. Стойностите могат да се сменят през environment в Render.
AI_LIMIT_PER_HOUR = int(os.getenv("AI_RATE_LIMIT_PER_HOUR", "20"))
AI_LIMIT_PER_DAY = int(os.getenv("AI_RATE_LIMIT_PER_DAY", "60"))
LIMIT_MESSAGE = "Достигнахте лимита за AI анализи."
JOB_CREATE_LIMIT_PER_10_MIN = int(os.getenv("JOB_CREATE_RATE_LIMIT_PER_10_MIN", "60"))   # опити за създаване, включително невалидните

DEADLINE_MESSAGE = "Анализът отне твърде дълго и беше спрян. Сумата е върната. Опитайте отново след малко."
INTERRUPTED_MESSAGE = "Анализът беше прекъснат от рестарт на сървъра. Сумата е върната. Опитайте отново."

_tasks: Dict[int, "asyncio.Task"] = {}
_semaphores: "weakref.WeakKeyDictionary" = weakref.WeakKeyDictionary()


def _now() -> datetime:
    return datetime.utcnow()


def _semaphore() -> asyncio.Semaphore:
    """Семафор за текущия event loop (в продукция е един; тестовете пускат нов loop за всяка заявка)."""
    loop = asyncio.get_running_loop()
    semaphore = _semaphores.get(loop)
    if semaphore is None:
        semaphore = _semaphores[loop] = asyncio.Semaphore(JOB_CONCURRENCY)
    return semaphore


@contextmanager
def session_scope():
    """Къса транзакция със собствена сесия: никога не държим транзакция през AI заявка."""
    db = SessionLocal()
    try:
        yield db
        db.commit()
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


def _iso(value: Optional[datetime]) -> Optional[str]:
    return value.isoformat() if value else None


def _digest(payload: dict) -> str:
    return hashlib.sha256(json.dumps(payload, sort_keys=True, ensure_ascii=False, default=str).encode()).hexdigest()[:32]


def _locked(db: Session, job_id: int) -> Optional[Job]:
    """Редът на задачата, заключен до края на транзакцията (на Postgres FOR UPDATE; SQLite сериализира сам)."""
    return db.query(Job).filter(Job.id == job_id).populate_existing().with_for_update().one_or_none()


def _push(job: Job, event: Dict[str, Any]) -> None:
    """Добавя събитие към задачата (без commit). Нов списък, за да се види промяната."""
    job.events = list(job.events or []) + [event]
    job.event_count = len(job.events)
    if event.get("type") == "stage" and event.get("stage") in STAGES:
        job.stage = event["stage"]


def _scrub(job: Job) -> None:
    """Свободният текст на въпроса не се пази след края на задачата."""
    request = dict(job.request or {})
    request.pop("question", None)
    job.request = request


def _release(db: Session, job: Job) -> None:
    """Връща резервираната сума в дяловете, от които е взета (идемпотентно по ref)."""
    billing.release(db, job.user_id, job.reserved_paid or 0, job.reserved_gift or 0, ref=f"job:{job.id}:refund")
    job.charged_cents = 0


def _summary(job: Job) -> Dict[str, Any]:
    request = job.request or {}
    keys = ("report_type", "is_dynamic", "name", "partner_name", "target_date", "end_date")
    return {k: request.get(k) for k in keys if request.get(k) not in (None, "")}


def serialize(job: Job, after: Optional[int] = None, balance: Optional[Dict[str, int]] = None) -> Dict[str, Any]:
    """Състоянието на задачата за клиента. С after се връщат и събитията от този индекс нататък."""
    out: Dict[str, Any] = {
        "id": job.id, "kind": job.kind, "sku": job.sku, "status": job.status, "stage": job.stage, "tier": job.tier,
        "quote_cents": job.quote_cents, "charged_cents": job.charged_cents, "report_id": job.report_id,
        "error": {"code": job.error_code, "message": job.error_message} if job.error_code else None,
        "created_at": _iso(job.created_at), "started_at": _iso(job.started_at), "finished_at": _iso(job.finished_at),
        "event_count": job.event_count or 0, "summary": _summary(job),
    }
    if after is not None:
        out["events"] = list(job.events or [])[max(0, after):]
    if balance is not None:
        out["balance"] = balance
    return out


def _balance(db: Session, user_id: int) -> Optional[Dict[str, int]]:
    user = db.query(User).filter(User.id == user_id).populate_existing().one_or_none()
    return billing.balance_payload(user) if user else None


# ---------------------------------------------------------------------------
# Създаване
# ---------------------------------------------------------------------------

def _label(request: ChartRequest) -> str:
    return data_api.report_label(request.report_type or "general", request.partner_name, is_dynamic=request.is_dynamic,
                                 question=request.question)


def create_job(db: Session, user: User, request: ChartRequest, key: Optional[str]) -> Tuple[Optional[Job], str]:
    """
    Създава задачата и резервира сумата. Връща (задача, "created" | "replay") или (None, "crisis").
    Хвърля HTTPException: 400 невалидни данни, 402 недостиг, 409 друг ключ, 429 лимит.
    """
    enforce(f"jobs-create:{user.id}", JOB_CREATE_LIMIT_PER_10_MIN, 600, "Твърде много опити за анализ.")
    payload = request.model_dump()
    digest = _digest(payload)
    if key:
        existing = db.query(Job).filter(Job.user_id == user.id, Job.idempotency_key == key).first()
        if existing is not None:
            if (existing.request or {}).get("_hash") != digest:
                raise HTTPException(status_code=409, detail="Този ключ вече е използван за друга заявка.")
            return existing, "replay"

    # Първо безопасността: при криза няма задача, няма резервиране и няма AI
    if safety.detect_crisis(request.question):
        events.track(db, "crisis_detected", user.id)
        return None, "crisis"

    active = db.query(Job).filter(Job.user_id == user.id, Job.status.in_(ACTIVE)).count()
    if active >= JOB_MAX_ACTIVE_PER_USER:
        raise HTTPException(status_code=429, detail="Вече имате анализ в процес. Изчакайте го да приключи или го откажете.")

    try:
        generation.validate(request)
    except generation.GenerationFailure as failure:
        raise HTTPException(status_code=400, detail=failure.message)

    quote = generation.quote_for(request)
    billing.require_balance(user, quote)            # бърз отказ с обяснение, преди да броим заявката към лимита
    enforce(f"ai-hour:{user.id}", AI_LIMIT_PER_HOUR, 3600, LIMIT_MESSAGE)
    enforce(f"ai-day:{user.id}", AI_LIMIT_PER_DAY, 86400, LIMIT_MESSAGE)

    now = _now()
    job = Job(user_id=user.id, idempotency_key=key, kind=generation.kind_of(request), sku=quote.sku, status="queued",
              stage="queued", request={**payload, "_hash": digest}, tier=quote.tier, quote_cents=quote.cents,
              events=[], event_count=0, created_at=now, lease_until=now + timedelta(seconds=LEASE_SECONDS))
    try:
        db.add(job)
        db.flush()
        if billing.balance_enforced() and quote.cents > 0:
            split = billing.reserve(db, user.id, quote, f"job:{job.id}", _label(request))
            if split is None:
                # Балансът е изхарчен между проверката и резервирането (паралелна заявка)
                user_id = user.id
                db.rollback()
                fresh = db.get(User, user_id)
                raise HTTPException(status_code=402, detail=billing.insufficient_message(fresh, quote))
            job.reserved_paid, job.reserved_gift = split
            job.charged_cents = quote.cents
        db.commit()
    except IntegrityError:
        # Две едновременни изпращания с един ключ: печели първото, второто получава същата задача
        db.rollback()
        existing = db.query(Job).filter(Job.user_id == user.id, Job.idempotency_key == key).first() if key else None
        if existing is None:
            raise
        return existing, "replay"
    return job, "created"


def launch(job_id: int) -> "asyncio.Task":
    """Пуска изпълнението на задачата в event loop-а на сървъра (не чака резултата). Втори изпълнител не се пуска."""
    running = _tasks.get(job_id)
    if running is not None and not running.done():
        return running
    task = asyncio.get_running_loop().create_task(run_job(job_id))
    _tasks[job_id] = task
    task.add_done_callback(lambda finished, jid=job_id: _tasks.pop(jid, None) if _tasks.get(jid) is finished else None)
    return task


# ---------------------------------------------------------------------------
# Изпълнение
# ---------------------------------------------------------------------------

def _claim(job_id: int) -> Optional[Dict[str, Any]]:
    """Опашка → работи: само една от конкурентните заявки печели. Връща данните за изпълнение или None."""
    with session_scope() as db:
        now = _now()
        result = db.execute(update(Job).where(Job.id == job_id, Job.status == "queued").values(
            status="running", stage="calculating", started_at=now, lease_until=now + timedelta(seconds=LEASE_SECONDS),
            attempts=Job.attempts + 1))
        if result.rowcount != 1:
            return None
        job = db.query(Job).filter(Job.id == job_id).populate_existing().one()
        request = {k: v for k, v in (job.request or {}).items() if not k.startswith("_")}
        return {"user_id": job.user_id, "request": request}


def _append_event(job_id: int, event: Dict[str, Any]) -> bool:
    """Записва събитие, докато задачата още работи. Връща False, ако вече не е активна (отказана, приключила, изтрита)."""
    with session_scope() as db:
        job = _locked(db, job_id)
        if job is None or job.status not in ACTIVE:
            return False
        _push(job, event)
        job.lease_until = _now() + timedelta(seconds=LEASE_SECONDS)
        return True


async def _heartbeat(job_id: int) -> None:
    """Подновява лизинга, докато задачата работи: изтекъл лизинг означава, че сървърът е прекъснат."""
    while True:
        await asyncio.sleep(HEARTBEAT_SECONDS)
        try:
            with session_scope() as db:
                db.execute(update(Job).where(Job.id == job_id, Job.status == "running").values(
                    lease_until=_now() + timedelta(seconds=LEASE_SECONDS)))
        except Exception as exc:
            print(f"⚠️ Лизингът на задача {job_id} не беше подновен ({type(exc).__name__})")


def _track(name: str, user_id: int, props: Optional[dict] = None) -> None:
    with session_scope() as db:
        events.track(db, name, user_id, props)


def _failure_props(job: Job, failure: "generation.GenerationFailure") -> dict:
    props = {"type": (job.request or {}).get("report_type") or "general", "dynamic": job.kind == "forecast",
             "stage": failure.stage or failure.code, "sku": job.sku, "code": failure.code}
    cause = failure.cause
    if failure.code == "text_check" or type(cause).__name__ == "TextCheckError":
        props["reason"] = "text_check"
        outcome = getattr(cause, "outcome", None)
        if outcome is not None:
            props["codes"] = outcome.codes()
    return props


def _track_checks(user_id: int, report_type: str, checks) -> None:
    """Телеметрия на проверката на текста: етап, кодове и броеве, никога текст на анализа."""
    for check in checks or []:
        if check.get("result") not in (None, "ok"):
            _track("text_check", user_id, {"type": report_type, **check})


def _finalize(job_id: int, request: ChartRequest, outcome: "generation.Outcome") -> bool:
    """Успех: отчетът и статусът "succeeded" се записват заедно. Връща False, ако задачата вече е отказана или изтрита."""
    final_months = outcome.months
    with session_scope() as db:
        job = _locked(db, job_id)
        if job is None or job.status != "running":
            return False
        user = db.get(User, job.user_id)
        if user is None:
            return False
        charged = job.charged_cents or 0
        if job.kind == "forecast" and billing.balance_enforced() and charged > 0 and final_months:
            # Резервирани са календарните месеци; ако някой месец е без събития, разликата се връща
            real = billing.forecast_quote(final_months, generation.has_partner(request)).cents
            difference = charged - real
            if difference > 0:
                give_paid = min(difference, job.reserved_paid or 0)
                give_gift = difference - give_paid
                billing.release(db, job.user_id, give_paid, give_gift, ref=f"job:{job.id}:adjust",
                                description="Върната разлика: по-малко месеци от резервираните")
                charged = real
        report = data_api.save_report(
            db, user, content=outcome.content, report_type=outcome.report_type, profile_name=request.name,
            label=outcome.label, cost_cents=charged, params=outcome.params)
        is_first = db.query(data_api.Report.id).filter(data_api.Report.user_id == job.user_id).count() == 1
        job.status = "succeeded"
        job.stage = "done"
        job.finished_at = _now()
        job.report_id = report.id
        job.charged_cents = charged
        _scrub(job)
        user_after = db.query(User).filter(User.id == job.user_id).populate_existing().one()
        _push(job, {"type": "complete", "report_id": report.id, "charged_cents": charged,
                    **billing.balance_payload(user_after)})
        telemetry = {"type": outcome.report_type, "dynamic": job.kind == "forecast", "first": is_first,
                     "cents": charged, "sku": job.sku}
        if job.kind == "forecast":
            telemetry["months"] = final_months
        user_id, report_type = job.user_id, outcome.report_type
    _track("analysis_completed", user_id, telemetry)
    if outcome.flags:
        _track("ai_output_flagged", user_id, {"flags": ",".join(sorted(set(outcome.flags)))})
    _track_checks(user_id, report_type, outcome.checks)
    return True


def _fail(job_id: int, failure: "generation.GenerationFailure") -> bool:
    """Неуспех: статусът "failed", сумата се връща. Връща False, ако задачата вече е приключила или отказана."""
    with session_scope() as db:
        job = _locked(db, job_id)
        if job is None or job.status not in ACTIVE:
            return False
        job.status = "failed"
        job.stage = "done"
        job.finished_at = _now()
        job.error_code = failure.code[:40]
        job.error_message = failure.message[:500]
        _scrub(job)
        _release(db, job)
        _push(job, {"type": "error", "code": failure.code, "message": failure.message})
        user_id, props = job.user_id, _failure_props(job, failure)
        report_type = props["type"]
    if failure.code != "invalid_input":
        _track("analysis_failed", user_id, props)
    _track_checks(user_id, report_type, failure.checks)
    return True


async def run_job(job_id: int) -> None:
    """Изпълнява задачата: генерира, записва отчета (или връща сумата). Не хвърля грешки към извикващия."""
    async with _semaphore():
        claim = _claim(job_id)
        if claim is None:
            return
        heartbeat = asyncio.get_running_loop().create_task(_heartbeat(job_id))

        def emit(event: Dict[str, Any]) -> None:
            _append_event(job_id, event)

        request: Optional[ChartRequest] = None
        try:
            request = ChartRequest(**claim["request"])
            outcome = await asyncio.wait_for(generation.run(request, claim["user_id"], emit), JOB_DEADLINE_SECONDS)
            _finalize(job_id, request, outcome)
        except asyncio.CancelledError:
            raise                                   # отказът вече е сменил статуса и е върнал сумата (cancel_job)
        except asyncio.TimeoutError:
            _fail(job_id, generation.GenerationFailure("timeout", DEADLINE_MESSAGE))
        except generation.GenerationFailure as failure:
            _fail(job_id, failure)
        except Exception as exc:
            _fail(job_id, generation.internal_failure("job", exc, "Не успяхме да завършим анализа. Опитайте отново след малко."))
        finally:
            heartbeat.cancel()


# ---------------------------------------------------------------------------
# Отказ, възстановяване, почистване
# ---------------------------------------------------------------------------

def cancel_job(db: Session, user: User, job_id: int) -> Tuple[Job, bool]:
    """
    Отказва активна задача и връща сумата. Връща (задача, отказана). Ако задачата вече е приключила, нищо не се
    променя и вторият елемент е False (клиентът получава резултата, не връщане на сума).
    """
    job = _locked(db, job_id)
    if job is None or job.user_id != user.id:
        raise HTTPException(status_code=404, detail="Задачата не е намерена")
    if job.status not in ACTIVE:
        db.rollback()
        return job, False
    job.status = "cancelled"
    job.stage = "done"
    job.finished_at = _now()
    _scrub(job)
    _release(db, job)
    _push(job, {"type": "cancelled"})
    db.commit()
    task = _tasks.get(job_id)
    if task is not None:
        task.cancel()
    return job, True


def recover_stale() -> List[int]:
    """
    Задачи с изтекъл лизинг (сървърът е бил прекъснат): връщат се в опашката, а след JOB_MAX_ATTEMPTS опита се провалят
    и сумата се връща. Връща номерата, които трябва да се пуснат наново.
    """
    relaunch: List[int] = []
    failed: List[Tuple[int, dict]] = []
    with session_scope() as db:
        stale = db.query(Job).filter(Job.status.in_(ACTIVE), Job.lease_until < _now()).populate_existing().with_for_update().all()
        for job in stale:
            if (job.attempts or 0) >= JOB_MAX_ATTEMPTS:
                job.status = "failed"
                job.stage = "done"
                job.finished_at = _now()
                job.error_code = "interrupted"
                job.error_message = INTERRUPTED_MESSAGE
                _scrub(job)
                _release(db, job)
                _push(job, {"type": "error", "code": "interrupted", "message": INTERRUPTED_MESSAGE})
                failed.append((job.user_id, {"type": (job.request or {}).get("report_type") or "general",
                                             "dynamic": job.kind == "forecast", "stage": "interrupted", "sku": job.sku}))
            else:
                job.status = "queued"
                job.stage = "queued"
                job.events = []
                job.event_count = 0
                job.lease_until = _now() + timedelta(seconds=LEASE_SECONDS)
                relaunch.append(job.id)
    for user_id, props in failed:
        _track("analysis_failed", user_id, props)
    return relaunch


def purge_old() -> int:
    """Приключилите задачи се трият след JOB_RETENTION_DAYS дни; отчетите в История остават."""
    cutoff = _now() - timedelta(days=JOB_RETENTION_DAYS)
    with session_scope() as db:
        return db.query(Job).filter(Job.status.notin_(ACTIVE), Job.created_at < cutoff).delete(synchronize_session=False)


async def sweeper() -> None:
    """Периодично: връща прекъснатите задачи и чисти старите."""
    while True:
        await asyncio.sleep(SWEEP_SECONDS)
        try:
            for job_id in recover_stale():
                launch(job_id)
            purge_old()
        except Exception as exc:
            print(f"⚠️ Проверката на задачите не успя ({type(exc).__name__}: {str(exc)[:120]})")


def start_background() -> None:
    """Извиква се при старта на сървъра: поема прекъснатите задачи и пуска периодичната проверка."""
    try:
        for job_id in recover_stale():
            launch(job_id)
        purge_old()
    except Exception as exc:
        print(f"⚠️ Възстановяването на задачите не успя ({type(exc).__name__}: {str(exc)[:120]})")
    asyncio.get_running_loop().create_task(sweeper())


# ---------------------------------------------------------------------------
# HTTP
# ---------------------------------------------------------------------------

def limits_payload(db: Session, user: User) -> Dict[str, Any]:
    """Колко анализа остават и кога може нов опит: показва се преди старт."""
    hour_left, hour_wait = limiter.peek(f"ai-hour:{user.id}", AI_LIMIT_PER_HOUR, 3600)
    day_left, day_wait = limiter.peek(f"ai-day:{user.id}", AI_LIMIT_PER_DAY, 86400)
    active = db.query(Job).filter(Job.user_id == user.id, Job.status.in_(ACTIVE)).count()
    blocked = hour_left == 0 or day_left == 0
    return {
        "hour_limit": AI_LIMIT_PER_HOUR, "hour_remaining": hour_left,
        "day_limit": AI_LIMIT_PER_DAY, "day_remaining": day_left,
        "retry_after_seconds": max(hour_wait, day_wait) if blocked else 0,
        "active_jobs": active, "max_active_jobs": JOB_MAX_ACTIVE_PER_USER,
        "can_start": not blocked and active < JOB_MAX_ACTIVE_PER_USER,
    }


@router.post("")
async def create(request: ChartRequest, http_request: Request, response: Response, wait: int = 0,
                 user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    """
    Създава задача за анализ. wait (секунди) позволява да се изчака резултатът в същата заявка; без него клиентът
    чете GET /jobs/{id}. Отговорът е {"job": ...}, а при криза {"crisis": true, "html": ...} без задача и без такса.
    """
    key = (http_request.headers.get("idempotency-key") or "").strip()[:80] or None
    user_id = user.id
    job, outcome = create_job(db, user, request, key)
    if outcome == "crisis":
        return {"crisis": True, "html": safety.CRISIS_MESSAGE_HTML}
    job_id = job.id
    db.commit()                    # краят на транзакцията на заявката: докато чакаме, не държим заключване на базата
    task = launch(job_id) if outcome == "created" else _tasks.get(job_id)
    wait = max(0, min(int(wait or 0), MAX_WAIT_SECONDS))
    if task is not None and wait:
        try:
            await asyncio.wait_for(asyncio.shield(task), timeout=wait)
        except Exception:               # изтичане на чакането: задачата продължава на сървъра
            pass
    with session_scope() as fresh:
        current = fresh.query(Job).filter(Job.id == job_id).one()
        body = {"job": serialize(current, after=0 if wait else None, balance=_balance(fresh, user_id)),
                "created": outcome == "created"}
        finished = current.status not in ACTIVE
    response.status_code = 202 if outcome == "created" and not finished else 200
    return body


@router.get("/limits")
def limits(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    return limits_payload(db, user)


@router.get("")
def list_jobs(status: str = "", limit: int = 20, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    """Последните задачи на потребителя. status=active връща само активните."""
    query = db.query(Job).filter(Job.user_id == user.id)
    if status == "active":
        query = query.filter(Job.status.in_(ACTIVE))
    rows = query.order_by(Job.id.desc()).limit(max(1, min(limit, 50))).all()
    return {"jobs": [serialize(j) for j in rows]}


@router.get("/{job_id}")
def get_job(job_id: int, after: int = 0, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    """Състоянието на задачата и събитията от индекс after нататък (клиентът пази колко е получил)."""
    job = db.query(Job).filter(Job.id == job_id, Job.user_id == user.id).populate_existing().first()
    if job is None:
        raise HTTPException(status_code=404, detail="Задачата не е намерена")
    return {"job": serialize(job, after=after, balance=_balance(db, user.id))}


@router.post("/{job_id}/cancel")
async def cancel(job_id: int, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    # async: задачата се прекъсва от нишката на event loop-а (task.cancel не е безопасен от друга нишка)
    job, cancelled = cancel_job(db, user, job_id)
    if not cancelled:
        raise HTTPException(status_code=409, detail="Анализът вече приключи и не може да бъде отказан.")
    _track("job_cancelled", user.id, {"sku": job.sku})
    return {"job": serialize(job, after=0, balance=_balance(db, user.id)), "cancelled": True}
