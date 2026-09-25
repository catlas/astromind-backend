"""
Профили и отчети на потребителя, пазени на сървъра (вместо в браузъра).
"""
import json
import re
from datetime import datetime
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from database import Profile, Report, User, get_db
from deps import get_current_user

router = APIRouter()

MAX_PROFILES = 50
MAX_REPORT_CHARS = 400_000
MAX_SETTINGS_BYTES = 20_000
RELATIONS = {"self", "partner", "spouse", "friend", "child", "relative", "family", "other"}
REPORT_TYPES = {"general", "health", "career", "money", "love", "karmic"}
REPORT_TYPE_LABELS = {
    "general": "Общ анализ", "health": "Здраве", "career": "Кариера",
    "money": "Пари и успех", "love": "Любов", "karmic": "Карма и род",
}
DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
TIME_RE = re.compile(r"^\d{2}:\d{2}(:\d{2})?$")


# ---------------------------------------------------------------------------
# Профили
# ---------------------------------------------------------------------------

class ProfileIn(BaseModel):
    name: str = Field(..., max_length=100)
    relation: str = "self"
    gender: Optional[str] = Field(default=None, max_length=20)
    birth_date: str
    birth_time: Optional[str] = None
    unknown_time: bool = False
    birth_place: Optional[str] = Field(default=None, max_length=200)
    lat: Optional[float] = Field(default=None, ge=-90, le=90)
    lon: Optional[float] = Field(default=None, ge=-180, le=180)
    is_primary: Optional[bool] = None
    settings: Optional[Dict[str, Any]] = None


def profile_payload(p: Profile) -> dict:
    return {
        "id": p.id, "name": p.name, "relation": p.relation, "gender": p.gender,
        "birth_date": p.birth_date, "birth_time": p.birth_time or "", "unknown_time": bool(p.unknown_time),
        "birth_place": p.birth_place or "", "lat": p.lat, "lon": p.lon,
        "is_primary": bool(p.is_primary), "settings": p.settings or {},
        "updated_at": p.updated_at.isoformat() if p.updated_at else None,
    }


def _clean_profile(data: ProfileIn) -> dict:
    name = data.name.strip()
    if not name:
        raise HTTPException(status_code=400, detail="Въведете име на профила")
    if not DATE_RE.match(data.birth_date or ""):
        raise HTTPException(status_code=400, detail="Невалидна дата на раждане")
    birth_time = (data.birth_time or "").strip()
    if data.unknown_time:
        birth_time = ""
    elif birth_time and not TIME_RE.match(birth_time):
        raise HTTPException(status_code=400, detail="Невалиден час на раждане")
    if data.settings is not None and len(json.dumps(data.settings, ensure_ascii=False).encode()) > MAX_SETTINGS_BYTES:
        raise HTTPException(status_code=400, detail="Настройките на профила са твърде големи")
    return {
        "name": name,
        "relation": data.relation if data.relation in RELATIONS else "other",
        "gender": data.gender,
        "birth_date": data.birth_date,
        "birth_time": birth_time or None,
        "unknown_time": bool(data.unknown_time),
        "birth_place": (data.birth_place or "").strip() or None,
        "lat": data.lat,
        "lon": data.lon,
    }


def _set_primary(db: Session, user_id: int, profile: Profile):
    db.query(Profile).filter(Profile.user_id == user_id, Profile.id != profile.id).update(
        {Profile.is_primary: False}, synchronize_session=False)
    profile.is_primary = True


def _get_owned_profile(db: Session, user: User, profile_id: int) -> Profile:
    p = db.query(Profile).filter(Profile.id == profile_id, Profile.user_id == user.id).first()
    if not p:
        raise HTTPException(status_code=404, detail="Профилът не е намерен")
    return p


def _name_taken(db: Session, user_id: int, name: str, exclude_id: Optional[int] = None) -> bool:
    q = db.query(Profile).filter(Profile.user_id == user_id, Profile.name == name)
    if exclude_id is not None:
        q = q.filter(Profile.id != exclude_id)
    return db.query(q.exists()).scalar()


def upsert_profile(db: Session, user: User, data: ProfileIn) -> Profile:
    """Създава профил или обновява съществуващия със същото име."""
    fields = _clean_profile(data)
    p = db.query(Profile).filter(Profile.user_id == user.id, Profile.name == fields["name"]).first()
    if p is None:
        count = db.query(Profile).filter(Profile.user_id == user.id).count()
        if count >= MAX_PROFILES:
            raise HTTPException(status_code=400, detail=f"Можете да имате до {MAX_PROFILES} профила")
        p = Profile(user_id=user.id, **fields)
        db.add(p)
        db.flush()
        if count == 0 or data.is_primary:
            _set_primary(db, user.id, p)
    else:
        for k, v in fields.items():
            setattr(p, k, v)
        if data.is_primary:
            _set_primary(db, user.id, p)
    if data.settings is not None:
        p.settings = data.settings
    p.updated_at = datetime.utcnow()
    return p


@router.get("/profiles")
def list_profiles(current_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    rows = db.query(Profile).filter(Profile.user_id == current_user.id).order_by(
        Profile.is_primary.desc(), Profile.name).all()
    return [profile_payload(p) for p in rows]


@router.post("/profiles")
def create_profile(data: ProfileIn, current_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    if _name_taken(db, current_user.id, data.name.strip()):
        raise HTTPException(status_code=400, detail="Вече имате профил с това име")
    p = upsert_profile(db, current_user, data)
    db.commit()
    return profile_payload(p)


@router.put("/profiles/upsert")
def upsert_profile_endpoint(data: ProfileIn, current_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    p = upsert_profile(db, current_user, data)
    db.commit()
    return profile_payload(p)


@router.put("/profiles/{profile_id}")
def update_profile(profile_id: int, data: ProfileIn,
                   current_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    p = _get_owned_profile(db, current_user, profile_id)
    fields = _clean_profile(data)
    if _name_taken(db, current_user.id, fields["name"], exclude_id=p.id):
        raise HTTPException(status_code=400, detail="Вече имате профил с това име")
    for k, v in fields.items():
        setattr(p, k, v)
    if data.settings is not None:
        p.settings = data.settings
    if data.is_primary:
        _set_primary(db, current_user.id, p)
    p.updated_at = datetime.utcnow()
    db.commit()
    return profile_payload(p)


@router.delete("/profiles/{profile_id}")
def delete_profile(profile_id: int, current_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    p = _get_owned_profile(db, current_user, profile_id)
    was_primary = p.is_primary
    db.delete(p)
    db.flush()
    if was_primary:
        nxt = db.query(Profile).filter(Profile.user_id == current_user.id).order_by(Profile.id).first()
        if nxt:
            nxt.is_primary = True
    db.commit()
    return {"message": "Профилът е изтрит"}


class ProfilesImport(BaseModel):
    profiles: List[Dict[str, Any]] = Field(default_factory=list, max_length=MAX_PROFILES)


@router.post("/profiles/import")
def import_profiles(data: ProfilesImport, current_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    """Еднократно пренасяне на профили от браузъра. Съществуващите имена не се презаписват."""
    imported = 0
    for raw in data.profiles:
        try:
            item = ProfileIn(**raw)
            if _name_taken(db, current_user.id, item.name.strip()):
                continue
            upsert_profile(db, current_user, item)
            imported += 1
        except Exception as exc:
            print(f"⚠️ Пропуснат профил при импорт: {type(exc).__name__}")
    db.commit()
    return {"imported": imported}


# ---------------------------------------------------------------------------
# Отчети
# ---------------------------------------------------------------------------

def report_label(report_type: str, partner_name: Optional[str] = None, is_dynamic: bool = False,
                 question: Optional[str] = None) -> str:
    label = REPORT_TYPE_LABELS.get(report_type, "Анализ")
    if is_dynamic:
        label = f"Прогноза: {label.lower()}"
    if partner_name:
        label += f" с {partner_name}"
    if question:
        label += " + въпрос"
    return label[:200]


def save_report(db: Session, user: User, *, content: str, report_type: str, profile_name: Optional[str],
                label: str, coins: int = 0, params: Optional[dict] = None) -> Report:
    r = Report(
        user_id=user.id,
        profile_name=(profile_name or "").strip()[:100] or None,
        report_type=report_type if report_type in REPORT_TYPES else "general",
        label=label[:200],
        content=(content or "")[:MAX_REPORT_CHARS],
        coins=coins,
        status="completed",
        params=params,
    )
    db.add(r)
    db.flush()
    return r


def report_summary(r: Report) -> dict:
    return {
        "id": r.id, "type": r.report_type, "label": r.label, "profile": r.profile_name or "",
        "coins": r.coins, "status": r.status,
        "created_at": r.created_at.isoformat() if r.created_at else None,
    }


@router.get("/reports")
def list_reports(limit: int = 200, current_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    rows = db.query(Report).filter(Report.user_id == current_user.id).order_by(
        Report.created_at.desc(), Report.id.desc()).limit(max(1, min(limit, 500))).all()
    return [report_summary(r) for r in rows]


@router.get("/reports/{report_id}")
def get_report(report_id: int, current_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    r = db.query(Report).filter(Report.id == report_id, Report.user_id == current_user.id).first()
    if not r:
        raise HTTPException(status_code=404, detail="Отчетът не е намерен")
    return {**report_summary(r), "content": r.content, "params": r.params or {}}


@router.delete("/reports/{report_id}")
def delete_report(report_id: int, current_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    r = db.query(Report).filter(Report.id == report_id, Report.user_id == current_user.id).first()
    if not r:
        raise HTTPException(status_code=404, detail="Отчетът не е намерен")
    db.delete(r)
    db.commit()
    return {"message": "Отчетът е изтрит"}


class ReportsImport(BaseModel):
    reports: List[Dict[str, Any]] = Field(default_factory=list, max_length=500)


@router.post("/reports/import")
def import_reports(data: ReportsImport, current_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    """Еднократно пренасяне на историята от браузъра."""
    imported = 0
    for raw in data.reports:
        content = str(raw.get("content") or "").strip()
        if not content:
            continue
        r = save_report(
            db, current_user, content=content, report_type=str(raw.get("type") or "general"),
            profile_name=str(raw.get("profile") or ""), label=str(raw.get("label") or "Анализ"),
            coins=0, params={"imported": True},
        )
        created = raw.get("created_at") or raw.get("date")
        try:
            if created:
                r.created_at = datetime.fromisoformat(str(created)[:19])
        except ValueError:
            pass
        imported += 1
    db.commit()
    return {"imported": imported}
