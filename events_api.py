"""Събития от фронтенда и админ статистика."""
import os
from typing import Any, Dict, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

import ai_budget
import events
from database import User, get_db
from deps import auth_scheme, get_current_user
from rate_limit import enforce

router = APIRouter()


def is_admin(user: User) -> bool:
    admins = {e.strip().lower() for e in os.getenv("ADMIN_EMAILS", "").split(",") if e.strip()}
    return bool(user and user.email and user.email.lower() in admins and user.email_verified)


def admin_token_ok(token: str) -> bool:
    """Отделният вход на собственика (admin_api.login): токен с typ=admin."""
    import jwt
    from auth import ALGORITHM, SECRET_KEY
    try:
        return jwt.decode(token or "", SECRET_KEY, algorithms=[ALGORITHM]).get("typ") == "admin"
    except Exception:
        return False


def require_admin(credentials=Depends(auth_scheme), db: Session = Depends(get_db)):
    """Администратор е или токенът от отделния вход (ADMIN_PASSWORD), или потребител от ADMIN_EMAILS с потвърден имейл."""
    if credentials and credentials.scheme.lower() == "bearer" and admin_token_ok(credentials.credentials):
        return None
    user = get_current_user(credentials, db)
    if not is_admin(user):
        raise HTTPException(status_code=403, detail="Нямате достъп")
    return user


class EventIn(BaseModel):
    name: str = Field(..., max_length=60)
    props: Optional[Dict[str, Any]] = None


@router.post("/events")
def client_event(data: EventIn, current_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    if data.name not in events.CLIENT_EVENTS:
        raise HTTPException(status_code=400, detail="Непознато събитие")
    enforce(f"events:{current_user.id}", 120, 3600, "Твърде много събития.")
    props = dict(data.props or {})
    if data.name == "pricing_viewed":
        import billing
        props["variant"] = billing.pricing_variant(current_user.id)
    events.track(db, data.name, current_user.id, props)
    return {"ok": True}


@router.get("/admin/metrics")
def admin_metrics(days: int = 30, _: User = Depends(require_admin), db: Session = Depends(get_db)):
    return {**events.funnel(db, days=max(1, min(days, 365))), "ai_budget": ai_budget.status()}
