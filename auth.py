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


def decode_access_token(token: str) -> dict:
    try:
        return jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
    except JWTError as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Невалиден или изтекъл токен"
        ) from exc


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
