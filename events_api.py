"""Събития от фронтенда и админ статистика."""
import os
from typing import Any, Dict, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

import events
from database import User, get_db
from deps import get_current_user
from rate_limit import enforce

router = APIRouter()


def is_admin(user: User) -> bool:
    admins = {e.strip().lower() for e in os.getenv("ADMIN_EMAILS", "").split(",") if e.strip()}
    return bool(user and user.email and user.email.lower() in admins and user.email_verified)


def require_admin(current_user: User = Depends(get_current_user)) -> User:
    if not is_admin(current_user):
        raise HTTPException(status_code=403, detail="Нямате достъп")
    return current_user


class EventIn(BaseModel):
    name: str
    props: Optional[Dict[str, Any]] = None


@router.post("/events")
def client_event(data: EventIn, current_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    if data.name not in events.CLIENT_EVENTS:
        raise HTTPException(status_code=400, detail="Непознато събитие")
    enforce(f"events:{current_user.id}", 120, 3600, "Твърде много събития.")
    events.track(db, data.name, current_user.id, data.props)
    return {"ok": True}


@router.get("/admin/metrics")
def admin_metrics(days: int = 30, _: User = Depends(require_admin), db: Session = Depends(get_db)):
    return events.funnel(db, days=max(1, min(days, 365)))
