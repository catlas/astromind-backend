"""Onboarding на нов потребител и безплатното първо прозрение."""
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

import engine
import events
import insights
from data_api import ProfileIn, profile_payload, upsert_profile
from database import Profile, User, get_db
from deps import get_current_user
from rate_limit import enforce

router = APIRouter()


class OnboardingIn(BaseModel):
    name: str = Field(..., max_length=100)
    birth_date: str
    birth_time: Optional[str] = None
    unknown_time: bool = False
    birth_place: Optional[str] = Field(default=None, max_length=200)
    lat: float = Field(..., ge=-90, le=90)
    lon: float = Field(..., ge=-180, le=180)
    gender: Optional[str] = None


def _insight_for(profile: Profile) -> dict:
    time_known = bool(profile.birth_time) and not profile.unknown_time
    try:
        chart = engine.calculate_chart(date=profile.birth_date, time=profile.birth_time if time_known else "12:00",
                                       lat=profile.lat, lon=profile.lon)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=f"Невалидни данни за раждане: {exc}")
    return {"profile": profile_payload(profile), **insights.big_three(chart, time_known=time_known)}


@router.post("/onboarding")
def complete_onboarding(data: OnboardingIn, current_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    enforce(f"onboarding:{current_user.id}", 20, 3600, "Твърде много опити.")
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
