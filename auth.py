import os
import re
from datetime import datetime, timedelta
from typing import Optional

from dotenv import load_dotenv
from fastapi import HTTPException, status
from passlib.context import CryptContext
from jose import JWTError, jwt

load_dotenv()

# Използва SECRET_KEY от environment (Render/.env); задължителен за сигурност.
SECRET_KEY = os.getenv("SECRET_KEY")
if not SECRET_KEY:
    raise RuntimeError("Missing required environment variable: SECRET_KEY")
ALGORITHM = "HS256"
ACCESS_TOKEN_EXPIRE_MINUTES = 60 * 24 # 24 часа

pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")

def hash_password(password: str):
    return pwd_context.hash(password)

def verify_password(plain_password, hashed_password):
    return pwd_context.verify(plain_password, hashed_password)

def create_access_token(data: dict):
    to_encode = data.copy()
    expire = datetime.utcnow() + timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES)
    to_encode.update({"exp": expire})
    return jwt.encode(to_encode, SECRET_KEY, algorithm=ALGORITHM)


def create_user_token(user) -> str:
    """Токен за вход. Спира да важи при смяна на паролата (token_version)."""
    return create_access_token({"sub": user.email, "uid": user.id, "tv": user.token_version or 0})


def decode_access_token(token: str) -> dict:
    try:
        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
    except JWTError as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Невалиден или изтекъл токен"
        ) from exc
    # Токените за нулиране на парола и потвърждение на имейл не са токени за вход
    if payload.get("typ"):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Невалиден токен")
    return payload


PURPOSE_TTL_MINUTES = {
    "reset": 60,              # нулиране на парола: 1 час
    "verify": 60 * 24 * 3,    # потвърждение на имейл: 3 дни
}


def create_purpose_token(purpose: str, user) -> str:
    """Еднократен токен за линк в имейл. Обвързан е с имейла и версията на паролата."""
    expire = datetime.utcnow() + timedelta(minutes=PURPOSE_TTL_MINUTES[purpose])
    payload = {"typ": purpose, "uid": user.id, "email": user.email, "tv": user.token_version or 0, "exp": expire}
    return jwt.encode(payload, SECRET_KEY, algorithm=ALGORITHM)


def decode_purpose_token(token: str, purpose: str) -> dict:
    """Връща payload или хвърля 400, ако токенът е невалиден, изтекъл или за друга цел."""
    try:
        payload = jwt.decode(token or "", SECRET_KEY, algorithms=[ALGORITHM])
    except JWTError as exc:
        raise HTTPException(status_code=400, detail="Линкът е невалиден или е изтекъл") from exc
    if payload.get("typ") != purpose:
        raise HTTPException(status_code=400, detail="Линкът е невалиден или е изтекъл")
    return payload


EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[A-Za-z]{2,}$")
PASSWORD_MIN_LENGTH = 10
# bcrypt използва само първите 72 байта от паролата
PASSWORD_MAX_BYTES = 72


def normalize_email(email: str) -> str:
    return (email or "").strip().lower()


def validate_email(email: str) -> Optional[str]:
    """Връща съобщение за грешка или None, ако имейлът е валиден."""
    if not email or len(email) > 254 or not EMAIL_RE.match(email):
        return "Невалиден имейл адрес"
    return None


def validate_password(password: str, email: str = "") -> Optional[str]:
    """Връща съобщение за грешка или None, ако паролата е достатъчно силна."""
    password = password or ""
    if len(password) < PASSWORD_MIN_LENGTH:
        return f"Паролата трябва да е поне {PASSWORD_MIN_LENGTH} символа"
    if len(password.encode("utf-8")) > PASSWORD_MAX_BYTES:
        return "Паролата е твърде дълга"
    if not any(c.isalpha() for c in password) or not any(c.isdigit() for c in password):
        return "Паролата трябва да съдържа поне една буква и една цифра"
    if len(set(password)) < 5:
        return "Паролата е твърде лесна за отгатване"
    local_part = normalize_email(email).split("@")[0]
    if local_part and len(local_part) >= 4 and local_part in password.lower():
        return "Паролата не трябва да съдържа имейла ви"
    return None
