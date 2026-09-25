"""
Събития за аналитиката на фунията (регистрация → потвърждение → първи анализ → покупка).

Записват се само име, потребител и малко технически полета. Свободен текст
(въпроси, рождени данни, съдържание на анализи) никога не влиза в събитията.
"""
from datetime import datetime, timedelta
from typing import Optional

from sqlalchemy import func
from sqlalchemy.orm import Session

from database import Event, Purchase, Report, User

# Събития, които фронтендът може да изпраща през POST /events
CLIENT_EVENTS = {"pricing_viewed", "onboarding_started", "report_form_opened", "report_downloaded"}
MAX_PROP_LEN = 60


def _clean_props(props: Optional[dict]) -> Optional[dict]:
    if not props:
        return None
    out = {}
    for k, v in list(props.items())[:10]:
        if isinstance(v, (bool, int, float)) or v is None:
            out[str(k)[:30]] = v
        else:
            out[str(k)[:30]] = str(v)[:MAX_PROP_LEN]
    return out


def track(db: Session, name: str, user_id: Optional[int] = None, props: Optional[dict] = None):
    """Записва събитие в отделна транзакция. Грешка тук никога не спира заявката."""
    try:
        db.add(Event(name=name[:50], user_id=user_id, props=_clean_props(props), created_at=datetime.utcnow()))
        db.commit()
    except Exception as exc:
        db.rollback()
        print(f"⚠️ Събитието {name} не беше записано: {type(exc).__name__}")


def funnel(db: Session, days: int = 30) -> dict:
    since = datetime.utcnow() - timedelta(days=days)
    counts = dict(
        db.query(Event.name, func.count(Event.id)).filter(Event.created_at >= since).group_by(Event.name).all()
    )

    new_users = db.query(User.id).filter(User.created_at >= since)
    new_ids = [u for (u,) in new_users.all()]
    registered = len(new_ids)
    verified = db.query(func.count(User.id)).filter(User.id.in_(new_ids), User.email_verified.is_(True)).scalar() if new_ids else 0
    with_report = (db.query(func.count(func.distinct(Report.user_id))).filter(Report.user_id.in_(new_ids)).scalar()
                   if new_ids else 0)
    paying = (db.query(func.count(func.distinct(Purchase.user_id)))
              .filter(Purchase.user_id.in_(new_ids), Purchase.status.in_(["paid", "refunded"])).scalar()
              if new_ids else 0)
    revenue = db.query(func.coalesce(func.sum(Purchase.amount_cents - Purchase.refunded_cents), 0)).filter(
        Purchase.paid_at >= since, Purchase.status.in_(["paid", "refunded"])).scalar()

    def rate(a, b):
        return round(100.0 * a / b, 1) if b else 0.0

    return {
        "days": days,
        "funnel": [
            {"step": "Регистрирани", "users": registered, "rate": 100.0 if registered else 0.0},
            {"step": "Потвърден имейл", "users": verified, "rate": rate(verified, registered)},
            {"step": "Първи анализ", "users": with_report, "rate": rate(with_report, registered)},
            {"step": "Покупка", "users": paying, "rate": rate(paying, registered)},
        ],
        "events": counts,
        "revenue_eur": round((revenue or 0) / 100, 2),
        "totals": {
            "users": db.query(func.count(User.id)).scalar(),
            "reports": db.query(func.count(Report.id)).scalar(),
        },
    }
