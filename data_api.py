"""
Профили и отчети на потребителя, пазени на сървъра (вместо в браузъра).
"""
import json
import os
import re
from datetime import datetime
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException, Response
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

import bg_text
import engine
import report_export
import report_snapshot
from database import Job, MemoryNote, Profile, Report, User, get_db
from deps import get_current_user
from rate_limit import enforce

router = APIRouter()

EXPORT_LIMIT_PER_HOUR = int(os.getenv("EXPORT_RATE_LIMIT_PER_HOUR", "30"))

MAX_PROFILES = 50
MAX_REPORT_CHARS = 400_000
# Най-много толкова отчета се пазят на потребител; при нов отчет най-старите над границата се трият автоматично (20.10.2026)
MAX_REPORTS_PER_USER = max(1, int(os.getenv("MAX_REPORTS_PER_USER", "50")))
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
    relation: str = Field(default="self", max_length=20)
    gender: Optional[str] = Field(default=None, max_length=20)
    birth_date: str = Field(..., max_length=10)
    birth_time: Optional[str] = Field(default=None, max_length=8)
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


def _real_birth_date(value: str) -> str:
    """Истинска календарна дата (не само YYYY-MM-DD): 2023-02-30 е невалидна; не е в бъдещето и е в диапазона на ефемеридите."""
    if not DATE_RE.match(value or ""):
        raise HTTPException(status_code=400, detail="Невалидна дата на раждане")
    try:
        born = datetime.strptime(value, "%Y-%m-%d").date()
    except ValueError:
        raise HTTPException(status_code=400, detail="Невалидна дата на раждане: такъв ден не съществува в календара")
    low, high = engine.supported_year_range()
    if high >= low and not low <= born.year <= high:
        raise HTTPException(status_code=400, detail=f"Датата на раждане е извън поддържания диапазон от години: {low}–{high}.")
    if born > datetime.utcnow().date():
        raise HTTPException(status_code=400, detail="Датата на раждане не може да е в бъдещето")
    return value


def _real_birth_time(value: str) -> str:
    """Час HH:MM или HH:MM:SS със стойности в реалните граници (часове до 23, минути и секунди до 59)."""
    if not TIME_RE.match(value):
        raise HTTPException(status_code=400, detail="Невалиден час на раждане")
    parts = [int(x) for x in value.split(":")]
    if parts[0] > 23 or parts[1] > 59 or (len(parts) > 2 and parts[2] > 59):
        raise HTTPException(status_code=400, detail="Невалиден час на раждане: часове до 23, минути до 59")
    return value


def _clean_profile(data: ProfileIn) -> dict:
    name = data.name.strip()
    if not name:
        raise HTTPException(status_code=400, detail="Въведете име на профила")
    birth_date = _real_birth_date(data.birth_date)
    birth_time = (data.birth_time or "").strip()
    if data.unknown_time:
        birth_time = ""
    elif birth_time:
        birth_time = _real_birth_time(birth_time)
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
    db.query(MemoryNote).filter(MemoryNote.profile_id == p.id).update({"profile_name": p.name}, synchronize_session=False)   # името е само за показване
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
    db.query(MemoryNote).filter(MemoryNote.profile_id == p.id).delete(synchronize_session=False)     # бележките за този човек (SQLite не налага CASCADE)
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
                 question: Optional[str] = None, is_snapshot: bool = False) -> str:
    label = REPORT_TYPE_LABELS.get(report_type, "Анализ")
    if is_dynamic:
        label = f"Прогноза: {label.lower()}"
    elif is_snapshot:
        label = f"{label} за дата"                      # натален и моментен отчет с една тема не са с еднакво име
    if partner_name:
        label += f" с {partner_name}"
    if question:
        label += " + въпрос"
    return label[:200]


def save_report(db: Session, user: User, *, content: str, report_type: str, profile_name: Optional[str],
                label: str, cost_cents: int = 0, params: Optional[dict] = None, snapshot: Optional[dict] = None) -> Report:
    saved_content = (content or "")[:MAX_REPORT_CHARS]
    r = Report(
        user_id=user.id,
        profile_name=(profile_name or "").strip()[:100] or None,
        report_type=report_type if report_type in REPORT_TYPES else "general",
        label=label[:200],
        content=saved_content,
        cost_cents=cost_cents,
        status="completed",
        params=params,
        snapshot=report_snapshot.seal(snapshot, saved_content) if snapshot else None,       # отпечатък на текста точно както е записан
    )
    db.add(r)
    db.flush()
    prune_reports(db, user.id)
    return r


def prune_reports(db: Session, user_id: int, keep: Optional[int] = None) -> int:
    """Пази последните `keep` (по подразбиране MAX_REPORTS_PER_USER) отчета на потребителя; по-старите се трият. Връща колко са изтрити."""
    keep = MAX_REPORTS_PER_USER if keep is None else keep
    old_ids = [row[0] for row in db.query(Report.id).filter(Report.user_id == user_id)
               .order_by(Report.created_at.desc(), Report.id.desc()).offset(keep).all()]
    if not old_ids:
        return 0
    db.query(Job).filter(Job.report_id.in_(old_ids)).update({Job.report_id: None}, synchronize_session=False)
    db.query(Report).filter(Report.id.in_(old_ids)).delete(synchronize_session=False)
    return len(old_ids)


def _bg_date(value: str) -> str:
    return ".".join(reversed(str(value).split("-"))) if value else ""


def report_mode(params: Optional[dict]) -> dict:
    """Вид на анализа за История: натален, за дата (с момента) или за период (с дати), и вторият човек."""
    params = params or {}
    if params.get("is_dynamic"):
        mode, when = "period", " – ".join(x for x in (_bg_date(params.get("target_date")), _bg_date(params.get("end_date"))) if x)
    elif params.get("target_date"):
        mode, when = "snapshot", f"{_bg_date(params['target_date'])} {str(params.get('target_time') or '')[:5]}".strip()
    else:
        mode, when = "natal", ""
    return {"mode": mode, "when": when, "partner": params.get("partner_name") or ""}


def report_summary(r: Report) -> dict:
    return {
        "id": r.id, "type": r.report_type, "label": r.label, "profile": r.profile_name or "",
        "profile_id": (r.params or {}).get("profile_id"),                      # идентичност на профила (името е само етикет)
        "cost_cents": r.cost_cents, "status": r.status,
        "created_at": r.created_at.isoformat() if r.created_at else None,
        "legacy": report_snapshot.is_legacy(r.snapshot),          # преди запазването на изчисленията: износът е възстановен, без гаранция
        **report_mode(r.params),
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
    return {**report_summary(r), "content": bg_text.for_report(r.content, r.params, r.profile_name or ""), "params": r.params or {}}


@router.get("/reports/{report_id}/export")
def export_report(report_id: int, format: str = "docx", current_user: User = Depends(get_current_user),
                  db: Session = Depends(get_db)):
    """
    DOCX или Markdown на запазен отчет на собственика. Без AI и без такса: същият текст като в Историята.
    Името на файла е безопасно (без имена и имейл); няма публичен адрес за изтегляне.
    """
    if format not in ("docx", "md"):
        raise HTTPException(status_code=400, detail="Непознат формат. Възможни: docx, md.")
    enforce(f"export:{current_user.id}", EXPORT_LIMIT_PER_HOUR, 3600, "Достигнахте лимита за износ на отчети.")
    r = db.query(Report).filter(Report.id == report_id, Report.user_id == current_user.id).first()
    if not r:
        raise HTTPException(status_code=404, detail="Отчетът не е намерен")
    if format == "md":
        body, media = report_export.markdown_text(r).encode("utf-8"), report_export.MARKDOWN_TYPE
    else:
        body, media = report_export.docx_bytes(r), report_export.DOCX_TYPE
    return Response(content=body, media_type=media, headers={
        "Content-Disposition": f'attachment; filename="{report_export.filename(r, format)}"',
        "Cache-Control": "no-store", "X-Content-Type-Options": "nosniff"})


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
            cost_cents=0, params={"imported": True},
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
