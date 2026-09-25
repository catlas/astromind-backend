"""Общи FastAPI зависимости: текущ потребител по Bearer токен."""
from typing import Optional

from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.orm import Session

from auth import decode_access_token
from database import User, get_db

auth_scheme = HTTPBearer(auto_error=False)


def get_current_user(
    credentials: Optional[HTTPAuthorizationCredentials] = Depends(auth_scheme),
    db: Session = Depends(get_db)
) -> User:
    if not credentials or credentials.scheme.lower() != "bearer":
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Липсва Bearer токен"
        )

    payload = decode_access_token(credentials.credentials)
    user = None
    if payload.get("uid") is not None:
        user = db.get(User, payload["uid"])
    elif payload.get("sub"):
        # Токени, издадени преди въвеждането на uid
        user = db.query(User).filter(User.email == payload["sub"]).first()
    else:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Невалиден токен payload"
        )

    if not user:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Потребителят не е намерен"
        )
    if payload.get("tv", 0) != (user.token_version or 0):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Сесията е изтекла. Моля, влезте отново."
        )
    return user
