"""API за AI паметта и времевата линия."""
from collections import OrderedDict
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

import memory
from data_api import report_summary
from database import MemoryNote, Profile, Report, User, get_db
from deps import get_current_user

router = APIRouter()

MONTHS_BG = ["Януари", "Февруари", "Март", "Април", "Май", "Юни", "Юли", "Август",
             "Септември", "Октомври", "Ноември", "Декември"]


class NoteIn(BaseModel):
    text: str = Field(..., max_length=500)
    profile_id: Optional[int] = Field(default=None, ge=1)                    # профилът, за който е бележката (без него: за собственика)
    profile_name: Optional[str] = Field(default=None, max_length=100)        # само за стари клиенти: свързва се, ако съвпада с един профил


class MemorySettings(BaseModel):
    enabled: bool


def _names(db: Session, user: User) -> dict:
    return {p.id: p.name for p in db.query(Profile).filter(Profile.user_id == user.id).all()}


@router.get("/memory")
def get_memory(profile_name: Optional[str] = None, profile_id: Optional[int] = None,
               current_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    notes = db.query(MemoryNote).filter(MemoryNote.user_id == current_user.id).order_by(MemoryNote.id).all()
    names = _names(db, current_user)
    # Прозрачност: точно това ще бъде добавено към следващия анализ (за избрания профил, по подразбиране за основния)
    if profile_id and profile_id in names:
        preview = memory.build_context(db, current_user, names[profile_id], profile_id=profile_id)
    elif profile_name:
        pid = memory.unique_profile_id(db, current_user, profile_name)
        preview = memory.build_context(db, current_user, names.get(pid, profile_name), profile_id=pid)
    else:
        primary = memory.primary_profile(db, current_user)
        preview = memory.build_context(db, current_user, primary.name if primary else None,
                                       profile_id=primary.id if primary else None)
    return {"enabled": bool(current_user.memory_enabled), "notes": [memory.note_payload(n, names) for n in notes], "preview": preview}


def _clean(data: NoteIn, db: Session, user: User) -> dict:
    text = " ".join(data.text.split())
    if not text:
        raise HTTPException(status_code=400, detail="Бележката е празна")
    names = _names(db, user)
    if data.profile_id:
        if data.profile_id not in names:
            raise HTTPException(status_code=404, detail="Профилът не е намерен")
        return {"text": text, "profile_id": data.profile_id, "profile_name": names[data.profile_id]}
    name = (data.profile_name or "").strip()
    if name:
        pid = memory.unique_profile_id(db, user, name)
        return {"text": text, "profile_id": pid, "profile_name": names[pid] if pid else name}
    return {"text": text, "profile_id": None, "profile_name": None}


@router.post("/memory")
def add_note(data: NoteIn, current_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    if db.query(MemoryNote).filter(MemoryNote.user_id == current_user.id).count() >= memory.MAX_NOTES:
        raise HTTPException(status_code=400, detail=f"Можете да имате до {memory.MAX_NOTES} бележки")
    note = MemoryNote(user_id=current_user.id, **_clean(data, db, current_user))
    db.add(note)
    db.commit()
    return memory.note_payload(note, _names(db, current_user))


@router.put("/memory/settings")
def memory_settings(data: MemorySettings, current_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    current_user.memory_enabled = data.enabled
    db.commit()
    return {"enabled": current_user.memory_enabled}


@router.put("/memory/{note_id}")
def update_note(note_id: int, data: NoteIn, current_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    note = db.query(MemoryNote).filter(MemoryNote.id == note_id, MemoryNote.user_id == current_user.id).first()
    if not note:
        raise HTTPException(status_code=404, detail="Бележката не е намерена")
    for k, v in _clean(data, db, current_user).items():
        setattr(note, k, v)
    db.commit()
    return memory.note_payload(note, _names(db, current_user))


@router.delete("/memory/{note_id}")
def delete_note(note_id: int, current_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    note = db.query(MemoryNote).filter(MemoryNote.id == note_id, MemoryNote.user_id == current_user.id).first()
    if not note:
        raise HTTPException(status_code=404, detail="Бележката не е намерена")
    db.delete(note)
    db.commit()
    return {"message": "Бележката е изтрита"}


@router.delete("/memory")
def clear_memory(current_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    db.query(MemoryNote).filter(MemoryNote.user_id == current_user.id).delete(synchronize_session=False)
    db.commit()
    return {"message": "Паметта е изчистена"}


@router.get("/timeline")
def timeline(profile: Optional[str] = None, current_user: User = Depends(get_current_user),
             db: Session = Depends(get_db)):
    """Отчетите, групирани по месец (най-новите първи), по желание за един профил."""
    q = db.query(Report).filter(Report.user_id == current_user.id)
    if profile:
        q = q.filter(Report.profile_name == profile)
    groups: "OrderedDict[str, dict]" = OrderedDict()
    for r in q.order_by(Report.created_at.desc(), Report.id.desc()).limit(500).all():
        key = r.created_at.strftime("%Y-%m") if r.created_at else "unknown"
        if key not in groups:
            label = f"{MONTHS_BG[r.created_at.month - 1]} {r.created_at.year}" if r.created_at else "Без дата"
            groups[key] = {"month": key, "label": label, "reports": []}
        item = report_summary(r)
        params = r.params or {}
        if params.get("is_dynamic"):
            item["covers"] = {"from": params.get("target_date"), "to": params.get("end_date")}
        item["memory_used"] = bool(params.get("memory_used"))
        groups[key]["reports"].append(item)
    profiles = sorted({p for (p,) in db.query(Report.profile_name).filter(
        Report.user_id == current_user.id, Report.profile_name.isnot(None)).distinct().all()})
    return {"groups": list(groups.values()), "profiles": profiles}
