"""Onboarding на нов потребител и безплатното първо прозрение."""
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field, field_validator
from sqlalchemy.orm import Session

import engine
import events
import insights
from data_api import ProfileIn, _clean_profile, profile_payload, upsert_profile
from database import Profile, User, get_db
from deps import get_current_user
from rate_limit import client_ip, enforce

router = APIRouter()


class OnboardingIn(BaseModel):
    name: str = Field(..., max_length=100)
    birth_date: str = Field(..., max_length=10)
    birth_time: Optional[str] = Field(default=None, max_length=8)
    unknown_time: bool = False
    birth_place: Optional[str] = Field(default=None, max_length=200)
    lat: float = Field(..., ge=-90, le=90)
    lon: float = Field(..., ge=-180, le=180)
    gender: Optional[str] = Field(default=None, max_length=20)


def _insight_from(birth_date: str, birth_time: Optional[str], unknown_time: bool, lat: float, lon: float) -> dict:
    time_known = bool(birth_time) and not unknown_time
    try:
        chart = engine.calculate_chart(date=birth_date, time=birth_time if time_known else "12:00", lat=lat, lon=lon)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=f"Невалидни данни за раждане: {exc}")
    return insights.big_three(chart, time_known=time_known)


def _insight_for(profile: Profile) -> dict:
    return {"profile": profile_payload(profile),
            **_insight_from(profile.birth_date, profile.birth_time, bool(profile.unknown_time), profile.lat, profile.lon)}


GENDERS = ("male", "female")


class StartProfile(BaseModel):
    """Данните от страницата „Безплатен анализ“: преди да има акаунт. Полът е задължителен."""
    name: str = Field(..., min_length=1, max_length=100)
    gender: str = Field(..., max_length=10)
    birth_date: str = Field(..., max_length=10)
    birth_time: Optional[str] = Field(default=None, max_length=8)
    unknown_time: bool = False
    birth_place: Optional[str] = Field(default=None, max_length=200)
    lat: float = Field(..., ge=-90, le=90)
    lon: float = Field(..., ge=-180, le=180)

    @field_validator("gender")
    @classmethod
    def _gender(cls, value: str) -> str:
        if value not in GENDERS:
            raise ValueError("Изберете пол")
        return value


def start_profile_in(data: StartProfile) -> ProfileIn:
    """ProfileIn за основния профил; валидира данните (дата, час) както профилите."""
    profile = ProfileIn(name=data.name, relation="self", gender=data.gender, birth_date=data.birth_date, birth_time=data.birth_time,
                        unknown_time=data.unknown_time, birth_place=data.birth_place, lat=data.lat, lon=data.lon, is_primary=True)
    _clean_profile(profile)
    return profile


@router.post("/public/preview")
def public_preview(data: StartProfile, request: Request):
    """Безплатният преглед (Голямата тройка и елементите) без акаунт: нищо не се записва."""
    enforce(f"preview:{client_ip(request)}", 30, 3600, "Твърде много прегледи от този адрес. Опитайте по-късно.")
    profile = start_profile_in(data)
    fields = _clean_profile(profile)
    return _insight_from(fields["birth_date"], fields.get("birth_time"), bool(fields.get("unknown_time")), fields["lat"], fields["lon"])


@router.post("/onboarding")
def complete_onboarding(data: OnboardingIn, current_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    enforce(f"onboarding:{current_user.id}", 20, 3600, "Твърде много опити.")
    if data.gender not in GENDERS:
        raise HTTPException(status_code=400, detail="Изберете пол")
    profile = upsert_profile(db, current_user, ProfileIn(
        name=data.name, relation="self", gender=data.gender, birth_date=data.birth_date,
        birth_time=data.birth_time, unknown_time=data.unknown_time, birth_place=data.birth_place,
        lat=data.lat, lon=data.lon, is_primary=True,
    ))
    insight = _insight_for(profile)  # валидира данните преди да запазим
    first_time = not current_user.onboarding_completed
    current_user.onboarding_completed = True
    db.commit()
    if first_time:
        events.track(db, "onboarding_completed", current_user.id)
    return insight


@router.post("/onboarding/skip")
def skip_onboarding(current_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    current_user.onboarding_completed = True
    db.commit()
    return {"ok": True}


@router.get("/insights/big-three")
def big_three(profile_id: Optional[int] = None, current_user: User = Depends(get_current_user),
              db: Session = Depends(get_db)):
    q = db.query(Profile).filter(Profile.user_id == current_user.id)
    profile = q.filter(Profile.id == profile_id).first() if profile_id else q.order_by(
        Profile.is_primary.desc(), Profile.id).first()
    if not profile:
        raise HTTPException(status_code=404, detail="Още нямате профил")
    if profile.lat is None or profile.lon is None:
        raise HTTPException(status_code=400, detail="Профилът няма място на раждане")
    return _insight_for(profile)
