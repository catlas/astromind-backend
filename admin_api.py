"""Табло за собственика: активност, анализи, откази, разход за AI и преглед (само за четене) на данните. Само за ADMIN_EMAILS."""
from collections import Counter, defaultdict
from datetime import datetime, timedelta
from typing import Any, Dict, List

import hmac
import os
from datetime import timezone

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy import func
from sqlalchemy.orm import Session

import ai_budget
from database import AIUsage, CoinTransaction, Event, Job, Purchase, Report, User, get_db
from events_api import require_admin
from rate_limit import client_ip, enforce

router = APIRouter(prefix="/admin")

ACTIVE = ("queued", "running")
ADMIN_TOKEN_MINUTES = 480


class AdminLogin(BaseModel):
    username: str = Field(..., max_length=100)
    password: str = Field(..., max_length=200)


@router.post("/login")
def admin_login(data: AdminLogin, request: Request):
    """Отделен вход на собственика: ADMIN_USERNAME (по подразбиране „admin“) и ADMIN_PASSWORD от environment в Render. Дава токен за 8 часа."""
    expected = os.getenv("ADMIN_PASSWORD", "")
    if len(expected) < 10:
        raise HTTPException(status_code=503, detail="Админ входът не е настроен: задайте ADMIN_PASSWORD (поне 10 символа) в Render.")
    enforce(f"admin-login:{client_ip(request)}", 5, 900, "Твърде много опити за вход.")
    user_ok = hmac.compare_digest(data.username.strip().encode(), os.getenv("ADMIN_USERNAME", "admin").strip().encode())
    pass_ok = hmac.compare_digest(data.password.encode(), expected.encode())
    if not (user_ok and pass_ok):
        raise HTTPException(status_code=401, detail="Грешно потребителско име или парола")
    import jwt
    from auth import ALGORITHM, SECRET_KEY
    exp = datetime.utcnow() + timedelta(minutes=ADMIN_TOKEN_MINUTES)
    return {"token": jwt.encode({"typ": "admin", "exp": exp}, SECRET_KEY, algorithm=ALGORITHM), "expires_minutes": ADMIN_TOKEN_MINUTES}


def _day(value) -> str:
    return str(value)[:10]


def _days(n: int) -> List[str]:
    today = datetime.utcnow().date()
    return [(today - timedelta(days=i)).isoformat() for i in range(n - 1, -1, -1)]


@router.get("/overview")
def overview(days: int = 30, _: User = Depends(require_admin), db: Session = Depends(get_db)) -> Dict[str, Any]:
    days = max(1, min(days, 365))
    since = datetime.utcnow() - timedelta(days=days)
    labels = _days(days)
    series: Dict[str, Dict[str, int]] = {k: defaultdict(int) for k in ("registered", "verified", "logins", "ok", "failed", "cancelled", "active")}

    users = db.query(User.id, User.created_at, User.email_verified).filter(User.created_at >= since).all()
    for _id, created, verified in users:
        series["registered"][_day(created)] += 1
        if verified:
            series["verified"][_day(created)] += 1
    for (created,) in db.query(Event.created_at).filter(Event.name == "login", Event.created_at >= since).all():
        series["logins"][_day(created)] += 1

    jobs = db.query(Job.user_id, Job.status, Job.created_at, Job.started_at, Job.finished_at, Job.error_code, Job.kind, Job.sku, Job.tier).filter(
        Job.created_at >= since).all()
    active_by_day: Dict[str, set] = defaultdict(set)
    durations: List[float] = []
    errors: Counter = Counter()
    kinds: Counter = Counter()
    for user_id, status, created, started, finished, error_code, kind, sku, tier in jobs:
        d = _day(created)
        if status == "succeeded":
            series["ok"][d] += 1
            kinds[sku or kind] += 1
            if started and finished:
                durations.append((finished - started).total_seconds())
        elif status == "failed":
            series["failed"][d] += 1
            errors[error_code or "unknown"] += 1
        elif status == "cancelled":
            series["cancelled"][d] += 1
        active_by_day[d].add(user_id)
    for d, ids in active_by_day.items():
        series["active"][d] = len(ids)

    usage = {row.day: row for row in db.query(AIUsage).filter(AIUsage.day >= labels[0]).all()}
    ai_series = {d: round(int(usage[d].cost_millicents or 0) / 1000, 2) if d in usage else 0.0 for d in labels}
    calls_series = {d: int(usage[d].calls or 0) if d in usage else 0 for d in labels}

    revenue = db.query(func.coalesce(func.sum(Purchase.amount_cents - Purchase.refunded_cents), 0)).filter(
        Purchase.paid_at >= since, Purchase.status.in_(["paid", "refunded"])).scalar()
    succeeded, failed = sum(series["ok"].values()), sum(series["failed"].values())
    active_users = len({uid for ids in active_by_day.values() for uid in ids})
    totals = {
        "users": db.query(func.count(User.id)).scalar(),
        "verified_users": db.query(func.count(User.id)).filter(User.email_verified.is_(True)).scalar(),
        "reports": db.query(func.count(Report.id)).scalar(),
        "running_now": db.query(func.count(Job.id)).filter(Job.status == "running").scalar(),
        "queued_now": db.query(func.count(Job.id)).filter(Job.status == "queued").scalar(),
    }
    return {
        "days": days,
        "labels": labels,
        "series": {k: [int(v.get(d, 0)) for d in labels] for k, v in series.items()},
        "ai_cost_eur": [ai_series[d] for d in labels],
        "ai_calls": [calls_series[d] for d in labels],
        "summary": {
            "registered": len(users), "verified": sum(1 for u in users if u[2]), "active_users": active_users,
            "analyses_ok": succeeded, "analyses_failed": failed, "analyses_cancelled": sum(series["cancelled"].values()),
            "failure_rate": round(100.0 * failed / (succeeded + failed), 1) if succeeded + failed else 0.0,
            "avg_seconds": round(sum(durations) / len(durations), 1) if durations else 0.0,
            "max_seconds": round(max(durations), 1) if durations else 0.0,
            "revenue_eur": round((revenue or 0) / 100, 2),
            "ai_cost_eur": round(sum(ai_series.values()), 2),
        },
        "totals": totals,
        "errors": errors.most_common(10),
        "kinds": kinds.most_common(10),
        "ai_budget": ai_budget.status(),
    }


# Само за четене. Колоните са избрани нарочно: без пароли, токени, текстове на анализи, въпроси и рождени данни.
TABLES = {
    "users": (User, ["id", "email", "full_name", "created_at", "email_verified", "paid_cents", "gift_cents", "onboarding_completed", "terms_version"], "id"),
    "jobs": (Job, ["id", "user_id", "kind", "sku", "status", "stage", "tier", "quote_cents", "charged_cents", "error_code", "error_message", "attempts",
                   "created_at", "started_at", "finished_at"], "id"),
    "reports": (Report, ["id", "user_id", "report_type", "label", "profile_name", "cost_cents", "status", "created_at"], "id"),
    "events": (Event, ["id", "user_id", "name", "props", "created_at"], "id"),
    "purchases": (Purchase, ["id", "user_id", "package_id", "credit_cents", "amount_cents", "currency", "status", "refunded_cents", "created_at", "paid_at"], "id"),
    "balance": (CoinTransaction, None, "id"),
    "ai_usage": (AIUsage, ["day", "calls", "prompt_tokens", "completion_tokens", "cost_millicents"], "day"),
}
TABLE_LABELS = {"users": "Потребители", "jobs": "Анализи (задачи)", "reports": "Отчети (без текста)", "events": "Събития",
                "purchases": "Плащания", "balance": "Движения по баланса", "ai_usage": "AI разход по дни"}


def _cell(value):
    if isinstance(value, datetime):
        return value.isoformat(timespec="seconds")
    if isinstance(value, (dict, list)):
        return str(value)[:200]
    return value if not isinstance(value, str) else value[:300]


@router.get("/data")
def tables(_: User = Depends(require_admin)):
    return [{"id": k, "label": TABLE_LABELS[k]} for k in TABLES]


@router.get("/data/{table}")
def table_rows(table: str, limit: int = 50, offset: int = 0, user_id: int = 0, _: User = Depends(require_admin), db: Session = Depends(get_db)):
    if table not in TABLES:
        raise HTTPException(status_code=404, detail="Непозната таблица")
    model, columns, order = TABLES[table]
    if columns is None:
        columns = [c.name for c in model.__table__.columns if c.name not in ("description",)]
    limit, offset = max(1, min(limit, 200)), max(0, offset)
    query = db.query(model)
    if user_id:
        owner = model.id if table == "users" else getattr(model, "user_id", None)
        if owner is not None:
            query = query.filter(owner == user_id)
    total = query.count()
    rows = query.order_by(getattr(model, order).desc()).offset(offset).limit(limit).all()
    return {"table": table, "label": TABLE_LABELS[table], "columns": columns, "total": total, "offset": offset, "limit": limit,
            "rows": [[_cell(getattr(r, c, None)) for c in columns] for r in rows]}
