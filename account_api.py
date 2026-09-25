"""
Акаунт: профил на потребителя, смяна и нулиране на парола, потвърждение на
имейл, изтриване на акаунт и експорт на данните (GDPR).
"""
from datetime import datetime
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel
from sqlalchemy import func
from sqlalchemy.orm import Session

import mailer
from auth import (
    create_purpose_token, create_user_token, decode_purpose_token, hash_password,
    normalize_email, validate_email, validate_password, verify_password,
)
from database import Profile, Report, User, get_db
from deps import get_current_user
from rate_limit import client_ip, enforce

router = APIRouter()


def user_payload(user: User) -> dict:
    return {
        "id": user.id,
        "email": user.email,
        "full_name": user.full_name,
        "coins": user.coins or 0,
        "email_verified": bool(user.email_verified),
        "created_at": user.created_at.isoformat() if user.created_at else None,
        "is_admin": _is_admin(user),
    }


def _is_admin(user: User) -> bool:
    from events_api import is_admin
    return is_admin(user)


def find_user_by_email(db: Session, raw_email: str) -> Optional[User]:
    """Точно съвпадение първо (стари акаунти с главни букви), после без значение от регистъра."""
    user = db.query(User).filter(User.email == (raw_email or "").strip()).first()
    if user:
        return user
    matches = db.query(User).filter(func.lower(func.trim(User.email)) == normalize_email(raw_email)).limit(2).all()
    return matches[0] if len(matches) == 1 else None


def email_taken(db: Session, email: str, exclude_user_id: Optional[int] = None) -> bool:
    q = db.query(User).filter(func.lower(func.trim(User.email)) == normalize_email(email))
    if exclude_user_id is not None:
        q = q.filter(User.id != exclude_user_id)
    return db.query(q.exists()).scalar()


async def send_verification(user: User):
    await mailer.send_verification_email(user.email, create_purpose_token("verify", user))


# ---------------------------------------------------------------------------
# Профил на потребителя
# ---------------------------------------------------------------------------

class UpdateMe(BaseModel):
    full_name: Optional[str] = None
    email: Optional[str] = None


class ChangePassword(BaseModel):
    current_password: str
    new_password: str


@router.get("/me")
def me(current_user: User = Depends(get_current_user)):
    return user_payload(current_user)


@router.patch("/me")
async def update_me(data: UpdateMe, current_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    if data.full_name is not None:
        full_name = data.full_name.strip()
        if not (1 <= len(full_name) <= 100):
            raise HTTPException(status_code=400, detail="Въведете име до 100 символа")
        current_user.full_name = full_name

    email_changed = False
    if data.email is not None and normalize_email(data.email) != normalize_email(current_user.email):
        email = normalize_email(data.email)
        error = validate_email(email)
        if error:
            raise HTTPException(status_code=400, detail=error)
        if email_taken(db, email, exclude_user_id=current_user.id):
            raise HTTPException(status_code=400, detail="Имейлът вече се използва")
        current_user.email = email
        current_user.email_verified = False
        email_changed = True

    db.commit()
    db.refresh(current_user)
    if email_changed:
        await send_verification(current_user)
    # Токенът съдържа имейла, затова връщаме нов
    return {**user_payload(current_user), "access_token": create_user_token(current_user)}


@router.post("/change-password")
def change_password(data: ChangePassword, request: Request,
                    current_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    enforce(f"change-pw:{current_user.id}", 10, 900, "Твърде много опити.")
    if not verify_password(data.current_password, current_user.hashed_password):
        raise HTTPException(status_code=400, detail="Текущата парола е грешна")
    error = validate_password(data.new_password, current_user.email)
    if error:
        raise HTTPException(status_code=400, detail=error)
    current_user.hashed_password = hash_password(data.new_password)
    # Всички стари сесии (на други устройства) спират да важат
    current_user.token_version = (current_user.token_version or 0) + 1
    db.commit()
    return {"message": "Паролата е сменена", "access_token": create_user_token(current_user)}


@router.delete("/me")
def delete_me(current_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    user_id = current_user.id
    # Изрично изтриване на свързаните данни (SQLite не налага ON DELETE CASCADE)
    for model in _user_owned_models():
        db.query(model).filter(model.user_id == user_id).delete(synchronize_session=False)
    db.delete(current_user)
    db.commit()
    return {"message": "Акаунтът и всички данни са изтрити"}


def _user_owned_models():
    """Всички таблици с user_id. Нови модели се добавят тук, за да се трият с акаунта."""
    import database
    return [m for m in (getattr(database, n, None) for n in database.USER_OWNED_MODELS) if m is not None]


@router.get("/me/export")
def export_me(current_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    """Всички данни на потребителя в JSON (право на достъп и преносимост по GDPR)."""
    def row(obj):
        out = {}
        for col in obj.__table__.columns:
            if col.name in ("hashed_password", "token_version"):
                continue
            val = getattr(obj, col.name)
            out[col.name] = val.isoformat() if isinstance(val, datetime) else val
        return out

    data = {"user": row(current_user), "exported_at": datetime.utcnow().isoformat()}
    for model in _user_owned_models():
        data[model.__tablename__] = [row(o) for o in db.query(model).filter(model.user_id == current_user.id).all()]
    return data


# ---------------------------------------------------------------------------
# Потвърждение на имейл
# ---------------------------------------------------------------------------

class TokenIn(BaseModel):
    token: str


@router.post("/verify-email")
def verify_email(data: TokenIn, db: Session = Depends(get_db)):
    payload = decode_purpose_token(data.token, "verify")
    user = db.get(User, payload.get("uid"))
    if not user or user.email != payload.get("email"):
        raise HTTPException(status_code=400, detail="Линкът е невалиден или е изтекъл")
    if not user.email_verified:
        user.email_verified = True
        db.commit()
        _track(db, "email_verified", user.id)
    return {"message": "Имейлът е потвърден", "email": user.email}


@router.post("/resend-verification")
async def resend_verification(current_user: User = Depends(get_current_user)):
    if current_user.email_verified:
        return {"message": "Имейлът вече е потвърден", "sent": False}
    enforce(f"resend-verify:{current_user.id}", 3, 3600, "Вече изпратихме няколко писма.")
    await send_verification(current_user)
    return {"message": "Изпратихме ново писмо за потвърждение", "sent": mailer.is_configured()}


# ---------------------------------------------------------------------------
# Забравена парола
# ---------------------------------------------------------------------------

class ForgotPassword(BaseModel):
    email: str


class ResetPassword(BaseModel):
    token: str
    new_password: str


@router.post("/forgot-password")
async def forgot_password(data: ForgotPassword, request: Request, db: Session = Depends(get_db)):
    email = normalize_email(data.email)
    enforce(f"forgot:{client_ip(request)}", 5, 3600, "Твърде много заявки.")
    enforce(f"forgot-email:{email}", 3, 3600, "Твърде много заявки за този имейл.")
    user = find_user_by_email(db, data.email)
    if user:
        await mailer.send_password_reset_email(user.email, create_purpose_token("reset", user))
    # Един и същ отговор, за да не се разкрива дали имейлът е регистриран
    return {"message": "Ако имейлът е регистриран, ще получите писмо с линк за нова парола."}


@router.post("/reset-password")
def reset_password(data: ResetPassword, request: Request, db: Session = Depends(get_db)):
    enforce(f"reset:{client_ip(request)}", 10, 3600, "Твърде много опити.")
    payload = decode_purpose_token(data.token, "reset")
    user = db.get(User, payload.get("uid"))
    # След смяна на паролата token_version се увеличава и линкът спира да важи
    if not user or user.email != payload.get("email") or payload.get("tv") != (user.token_version or 0):
        raise HTTPException(status_code=400, detail="Линкът е невалиден или е изтекъл")
    error = validate_password(data.new_password, user.email)
    if error:
        raise HTTPException(status_code=400, detail=error)
    user.hashed_password = hash_password(data.new_password)
    user.token_version = (user.token_version or 0) + 1
    # Линкът е стигнал до пощата на потребителя, значи имейлът е негов
    user.email_verified = True
    db.commit()
    return {"message": "Паролата е сменена. Влезте с новата парола."}


def _track(db, name, user_id=None, props=None):
    """Събития за аналитиката (Фаза 4). Не спира заявката при грешка."""
    try:
        import events
        events.track(db, name, user_id, props)
    except Exception as exc:
        print(f"⚠️ Събитието {name} не беше записано: {exc}")
