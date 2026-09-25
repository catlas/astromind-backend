"""API за AI паметта и времевата линия."""
from collections import OrderedDict
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

import memory
from data_api import report_summary
from database import MemoryNote, Report, User, get_db
from deps import get_current_user

router = APIRouter()

MONTHS_BG = ["Януари", "Февруари", "Март", "Април", "Май", "Юни", "Юли", "Август",
             "Септември", "Октомври", "Ноември", "Декември"]


class NoteIn(BaseModel):
    text: str = Field(..., max_length=500)
    profile_name: Optional[str] = Field(default=None, max_length=100)


class MemorySettings(BaseModel):
    enabled: bool


@router.get("/memory")
def get_memory(profile_name: Optional[str] = None, current_user: User = Depends(get_current_user),
               db: Session = Depends(get_db)):
    notes = db.query(MemoryNote).filter(MemoryNote.user_id == current_user.id).order_by(MemoryNote.id).all()
    return {
        "enabled": bool(current_user.memory_enabled),
        "notes": [memory.note_payload(n) for n in notes],
        # Прозрачност: точно това ще бъде добавено към следващия анализ
        "preview": memory.build_context(db, current_user, profile_name),
    }


def _clean(data: NoteIn) -> dict:
    text = " ".join(data.text.split())
    if not text:
        raise HTTPException(status_code=400, detail="Бележката е празна")
    return {"text": text, "profile_name": (data.profile_name or "").strip() or None}


@router.post("/memory")
def add_note(data: NoteIn, current_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    if db.query(MemoryNote).filter(MemoryNote.user_id == current_user.id).count() >= memory.MAX_NOTES:
        raise HTTPException(status_code=400, detail=f"Можете да имате до {memory.MAX_NOTES} бележки")
    note = MemoryNote(user_id=current_user.id, **_clean(data))
    db.add(note)
    db.commit()
    return memory.note_payload(note)


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
    for k, v in _clean(data).items():
        setattr(note, k, v)
    db.commit()
    return memory.note_payload(note)


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
