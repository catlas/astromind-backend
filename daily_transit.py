"""
Дневен транзит (03.11.2026): за основния профил, за днес в 12:00 (Europe/Sofia), тема „Общ анализ“, без въпрос. От готовия анализ се пази и показва
само блокът „Обща картина“: в Таблото и в писмото всяка сутрин в 08:00. Генераторът не се пипа (generation.run). Анализът за деня не е отчет:
не влиза в История, не се таксува и не брои към часовия лимит.
"""
import asyncio
import re
from datetime import datetime, timedelta
from typing import Dict, Optional, Set, Tuple

import jwt
import pytz
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import exists
from sqlalchemy.orm import Session

import ai_budget
import events
import generation
import mailer
from auth import ALGORITHM, SECRET_KEY
from database import DailyTransit, Profile, SessionLocal, User, get_db
from deps import get_current_user
from rate_limit import enforce
from schemas import ChartRequest

router = APIRouter()

ZONE = "Europe/Sofia"
TRANSIT_TIME = "12:00"
PREPARE_HOUR = 5               # от 05:00 се подготвя анализът за хората с включено писмо
SEND_HOUR = 8                  # писмото тръгва от 08:00 българско време
MAX_EMAIL_ATTEMPTS = 3
PENDING_STALE = timedelta(minutes=10)
GENERATION_TIMEOUT = 600
KEEP_DAYS = 30
UNSUBSCRIBE_DAYS = 400

_running: Set[Tuple[int, str]] = set()
_tasks: Set["asyncio.Task"] = set()


def now_sofia() -> datetime:
    return datetime.now(pytz.timezone(ZONE)).replace(tzinfo=None)


def today() -> str:
    return now_sofia().date().isoformat()


# ---------------------------------------------------------------------------
# Блокът „Обща картина“
# ---------------------------------------------------------------------------
_HEADING_TAG = re.compile(r"<h([1-6])[^>]*>([\s\S]*?)</h\1\s*>", re.IGNORECASE)
_HEADING = re.compile(r"^\s*#{1,6}\s+(.*?)\s*#*\s*$")
_NOTE = re.compile(r"^\s*<p[^>]*astro-disclaimer", re.IGNORECASE)


def general_picture(content: str) -> str:
    """Само текстът на „Обща картина“ (без заглавието и без другите блокове и бележки); празен низ, ако блокът го няма."""
    text = _HEADING_TAG.sub(lambda m: f"\n\n{'#' * int(m.group(1))} {m.group(2).strip()}\n\n", str(content or "").replace("\r\n", "\n"))
    lines = text.split("\n")
    out, inside = [], False
    for line in lines:
        m = _HEADING.match(line)
        if m:
            if inside:
                break
            inside = m.group(1).strip() == "Обща картина"
            continue
        if inside:
            if _NOTE.match(line):
                break
            out.append(line)
    plain = "\n".join(out)
    plain = re.sub(r"</p>\s*<p[^>]*>", "\n\n", plain, flags=re.IGNORECASE)
    plain = re.sub(r"<br\s*/?>", "\n", plain, flags=re.IGNORECASE)
    plain = re.sub(r"<[^>]+>", "", plain).strip()
    return re.sub(r"\*\*(.+?)\*\*", r"\1", plain)


# ---------------------------------------------------------------------------
# Заявката и генерирането
# ---------------------------------------------------------------------------

def primary_profile(db: Session, user_id: int) -> Optional[Profile]:
    q = db.query(Profile).filter(Profile.user_id == user_id, Profile.lat.isnot(None), Profile.lon.isnot(None))
    return q.order_by(Profile.is_primary.desc(), Profile.id).first()


def request_for(profile: Profile, day: str) -> ChartRequest:
    known = bool(profile.birth_time) and not profile.unknown_time
    return ChartRequest(
        name=profile.name, date=profile.birth_date, time=(profile.birth_time or "12:00")[:5], birth_time_known=known,
        lat=profile.lat, lon=profile.lon, report_type="general", target_date=day, target_time=TRANSIT_TIME, forecast_timezone=ZONE,
        gender=profile.gender if profile.gender in ("male", "female") else None, profile_id=profile.id)


def _row(db: Session, user_id: int, day: str) -> Optional[DailyTransit]:
    return db.query(DailyTransit).filter(DailyTransit.user_id == user_id, DailyTransit.day == day).first()


def _mark(user_id: int, day: str, **fields) -> None:
    db = SessionLocal()
    try:
        row = _row(db, user_id, day)
        if row is not None:
            for k, v in fields.items():
                setattr(row, k, v)
            row.updated_at = datetime.utcnow()
            db.commit()
    finally:
        db.close()


async def generate(user_id: int, day: str) -> None:
    """Прави анализа за деня и записва само „Обща картина“. Не хвърля грешки: неуспехът се записва като статус failed."""
    key = (user_id, day)
    if key in _running:
        return
    _running.add(key)
    try:
        db = SessionLocal()
        try:
            profile = primary_profile(db, user_id)
            if profile is None:
                return
            request = request_for(profile, day)
            name = profile.name
            row = _row(db, user_id, day)
            if row is None:
                db.add(DailyTransit(user_id=user_id, day=day, profile_name=name, status="pending"))
            else:
                row.status, row.error, row.profile_name, row.updated_at = "pending", None, name, datetime.utcnow()
            db.commit()
        finally:
            db.close()
        attempts: list = []
        token = ai_budget.attempt_log.set(attempts)
        try:
            outcome = await asyncio.wait_for(generation.run(request, user_id, lambda event: None), GENERATION_TIMEOUT)
        finally:
            ai_budget.attempt_log.reset(token)
        text = general_picture(outcome.content)
        if not text:
            _mark(user_id, day, status="failed", error="no_general_picture")
        else:
            _mark(user_id, day, status="ready", text=text, error=None)
    except asyncio.CancelledError:
        raise
    except Exception as exc:
        code = getattr(exc, "code", None) or type(exc).__name__
        print(f"⚠️ Дневният транзит не успя ({code}): {str(exc)[:160]}")
        _mark(user_id, day, status="failed", error=str(code)[:200])
    finally:
        _running.discard(key)


def start(user_id: int, day: str) -> None:
    task = asyncio.get_running_loop().create_task(generate(user_id, day))
    _tasks.add(task)
    task.add_done_callback(_tasks.discard)


def view(row: Optional[DailyTransit], day: str, profile_name: str = "") -> Dict:
    return {"day": day, "time": TRANSIT_TIME, "profile": (row.profile_name if row else None) or profile_name,
            "status": row.status if row else "pending", "text": row.text if row and row.status == "ready" else ""}


# ---------------------------------------------------------------------------
# API
# ---------------------------------------------------------------------------

@router.get("/daily-transit")
async def get_daily(current_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    day = today()
    profile = primary_profile(db, current_user.id)
    if profile is None:
        return {"day": day, "time": TRANSIT_TIME, "status": "no_profile", "text": "", "profile": ""}
    row = _row(db, current_user.id, day)
    stale = row is not None and row.status == "pending" and (current_user.id, day) not in _running and \
        datetime.utcnow() - (row.updated_at or row.created_at or datetime.utcnow()) > PENDING_STALE
    if row is None or stale:
        enforce(f"daily-start:{current_user.id}", 12, 3600, "Твърде много опити.")
        start(current_user.id, day)
        return {**view(None, day, profile.name), "status": "pending"}
    return view(row, day, profile.name)


@router.post("/daily-transit/retry")
async def retry_daily(current_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    day = today()
    profile = primary_profile(db, current_user.id)
    if profile is None:
        raise HTTPException(status_code=400, detail="Добавете профил с данни за раждане.")
    row = _row(db, current_user.id, day)
    if row is not None and row.status == "ready":
        return view(row, day, profile.name)
    if (current_user.id, day) in _running:
        return {**view(None, day, profile.name), "status": "pending"}
    enforce(f"daily-retry:{current_user.id}", 6, 3600, "Твърде много опити. Опитайте по-късно.")
    start(current_user.id, day)
    return {**view(None, day, profile.name), "status": "pending"}


# ---------------------------------------------------------------------------
# Писмото
# ---------------------------------------------------------------------------

def unsubscribe_token(user: User) -> str:
    return jwt.encode({"typ": "unsub", "uid": user.id, "exp": datetime.utcnow() + timedelta(days=UNSUBSCRIBE_DAYS)}, SECRET_KEY, algorithm=ALGORITHM)


def _bg_date(day: str) -> str:
    months = ["януари", "февруари", "март", "април", "май", "юни", "юли", "август", "септември", "октомври", "ноември", "декември"]
    y, m, d = (int(x) for x in day.split("-"))
    return f"{d} {months[m - 1]} {y}"


def build_email(user: User, row: DailyTransit) -> Tuple[str, str, str]:
    """(тема, текст, html): само „Обща картина“, връзка към таблото и връзка за спиране на писмата."""
    title = f"Вашият ден: {_bg_date(row.day)}"
    open_link = mailer.frontend_link("/dashboard")
    stop_link = mailer.frontend_link(f"/unsubscribe?token={unsubscribe_token(user)}")
    paragraphs = [p.strip() for p in (row.text or "").split("\n\n") if p.strip()]
    note = "Астрологията е инструмент за себепознание: това са тенденции, не предсказания."
    text = f"{title}\nТранзити към картата на {row.profile_name or 'вас'} за 12:00\n\n{row.text}\n\n{note}\nОтворете таблото: {open_link}\nСпиране на тези писма: {stop_link}"
    body = "".join(f"<p style=\"line-height:1.55\">{_escape(p)}</p>" for p in paragraphs)
    html = (f"<div style=\"font-family:Arial,sans-serif;max-width:560px;margin:auto;color:#1f1733\"><h2 style=\"color:#5211d4\">{title}</h2>"
            f"<p style=\"color:#777;font-size:13px\">Транзити към картата на {_escape(row.profile_name or 'вас')} за 12:00</p>{body}"
            f"<p><a href=\"{open_link}\" style=\"background:#5211d4;color:#fff;padding:12px 20px;border-radius:8px;text-decoration:none;display:inline-block\">Отвори таблото</a></p>"
            f"<p style=\"font-size:12px;color:#777\">{note}</p>"
            f"<p style=\"font-size:12px;color:#777\"><a href=\"{stop_link}\">Спри тези писма</a></p></div>")
    return title, text, html


def _escape(value: str) -> str:
    return str(value).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


async def send_row(user: User, row: DailyTransit) -> bool:
    subject, text, html = build_email(user, row)
    return await mailer.send_email(user.email, subject, text, html)


async def _send_pending(db: Session, day: str) -> bool:
    """Пуска едно чакащо писмо за деня. Връща True, ако е имало работа."""
    row = (db.query(DailyTransit).join(User, User.id == DailyTransit.user_id)
           .filter(DailyTransit.day == day, DailyTransit.status == "ready", DailyTransit.emailed_at.is_(None),
                   DailyTransit.email_attempts < MAX_EMAIL_ATTEMPTS, User.daily_email.is_(True), User.email_verified.is_(True))
           .order_by(DailyTransit.id).first())
    if row is None:
        return False
    user = db.get(User, row.user_id)
    row.email_attempts = (row.email_attempts or 0) + 1
    db.commit()
    ok = await send_row(user, row)
    if ok:
        row.emailed_at = datetime.utcnow()
        db.commit()
        events.track(db, "daily_email_sent", user.id)
    else:
        events.track(db, "daily_email_failed", user.id)
    return True


async def _prepare_next(db: Session, day: str) -> bool:
    user_id = (db.query(User.id)
               .filter(User.daily_email.is_(True), User.email_verified.is_(True),
                       exists().where(Profile.user_id == User.id, Profile.lat.isnot(None)),
                       ~exists().where(DailyTransit.user_id == User.id, DailyTransit.day == day))
               .order_by(User.id).limit(1).scalar())
    if user_id is None:
        return False
    await generate(user_id, day)
    return True


def purge_old(db: Session) -> int:
    cutoff = (now_sofia() - timedelta(days=KEEP_DAYS)).date().isoformat()
    n = db.query(DailyTransit).filter(DailyTransit.day < cutoff).delete(synchronize_session=False)
    db.commit()
    return n


async def step(now: Optional[datetime] = None) -> bool:
    """Една стъпка на сутрешната работа: първо писмата (след 08:00), после подготовка на анализите (след 05:00). True = имало е работа."""
    now = now or now_sofia()
    day = now.date().isoformat()
    db = SessionLocal()
    try:
        if now.hour >= SEND_HOUR and await _send_pending(db, day):
            return True
        if now.hour >= PREPARE_HOUR and await _prepare_next(db, day):
            return True
        return False
    finally:
        db.close()


async def worker() -> None:
    """Фонов цикъл: при работа продължава веднага, иначе проверява на всяка минута. Грешка не спира цикъла."""
    last_purge = ""
    while True:
        busy = False
        try:
            busy = await step()
            if last_purge != today():
                db = SessionLocal()
                try:
                    purge_old(db)
                finally:
                    db.close()
                last_purge = today()
        except Exception as exc:
            print(f"⚠️ Сутрешният транзит не успя ({type(exc).__name__}: {str(exc)[:120]})")
        await asyncio.sleep(2 if busy else 60)


class TestEmailOut(BaseModel):
    sent: bool
    message: str


@router.post("/daily-transit/test-email")
async def test_email(current_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    if not mailer.is_configured():
        raise HTTPException(status_code=503, detail="Изпращането на имейли още не е активирано.")
    enforce(f"daily-test:{current_user.id}", 5, 3600, "Вече изпратихме няколко писма.")
    row = _row(db, current_user.id, today())
    if row is None or row.status != "ready":
        return {"sent": False, "message": "Анализът за днес още се подготвя. Опитайте след минута."}
    ok = await send_row(current_user, row)
    return {"sent": ok, "message": "Изпратихме тестово писмо." if ok else "Не успяхме да изпратим писмо. Опитайте след малко."}


class UnsubscribeIn(BaseModel):
    token: str = Field(..., max_length=2000)


@router.post("/daily-transit/unsubscribe")
def unsubscribe(data: UnsubscribeIn, db: Session = Depends(get_db)):
    try:
        payload = jwt.decode(data.token, SECRET_KEY, algorithms=[ALGORITHM])
    except Exception:
        raise HTTPException(status_code=400, detail="Линкът е невалиден или е изтекъл")
    if payload.get("typ") != "unsub":
        raise HTTPException(status_code=400, detail="Линкът е невалиден или е изтекъл")
    user = db.get(User, payload.get("uid"))
    if user is None:
        raise HTTPException(status_code=400, detail="Линкът е невалиден или е изтекъл")
    user.daily_email = False
    db.commit()
    return {"message": "Дневните писма са спрени. Можете да ги включите отново от Настройки.", "email": user.email}
