"""
FastAPI сървър за астрологично приложение
Предоставя API endpoints за изчисляване и интерпретация на астрологични карти
"""

from fastapi import BackgroundTasks, FastAPI, HTTPException, Depends, Request  # type: ignore
from fastapi.middleware.cors import CORSMiddleware  # type: ignore
from fastapi.responses import Response  # type: ignore
from pydantic import BaseModel, Field  # type: ignore
from typing import Optional, List, Dict
from datetime import datetime
from sqlalchemy import func  # type: ignore
from sqlalchemy.orm import Session  # type: ignore
import os
import traceback
import uuid
from dotenv import load_dotenv
import engine
from ai_interpreter import get_interpreter
from schemas import ChartRequest, ChartResponse
from docx_generator import DOCXGenerator
from database import User, get_db
from db_migrate import run_migrations
from deps import get_current_user
import account_api
import billing
import billing_api
import data_api
import events
import events_api
import jobs
import geocode_api
import mailer
import onboarding_api
import memory_api
from auth import (
    hash_password, verify_password, create_user_token,
    normalize_email, validate_email, validate_password,
)
from rate_limit import client_ip, enforce

load_dotenv()

# Схемата на базата се обновява с Alembic преди приемане на заявки
run_migrations()

# Инициализация на FastAPI приложението
app = FastAPI(
    title="Astrology API",
    description="API за изчисляване и интерпретация на астрологични карти",
    version="3.0.0"
)

# CORS Middleware - чете allowlist от CORS_ORIGINS (comma-separated)
cors_origins_raw = os.getenv("CORS_ORIGINS", "")
cors_origins = [origin.strip() for origin in cors_origins_raw.split(",") if origin.strip()]
if not cors_origins:
    cors_origins = ["http://localhost:5173", "http://127.0.0.1:5173"]

allow_all_origins = "*" in cors_origins
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"] if allow_all_origins else cors_origins,
    allow_credentials=not allow_all_origins,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(account_api.router)
app.include_router(data_api.router)
app.include_router(billing_api.router)
app.include_router(events_api.router)
app.include_router(geocode_api.router)
app.include_router(onboarding_api.router)
app.include_router(memory_api.router)
app.include_router(jobs.router)

# Записва в лога дали имейлите са настроени и дали пощенският сървър е достъпен
mailer.log_status_in_background()


@app.on_event("startup")
async def start_jobs():
    """Поема прекъснатите задачи за анализ и пуска периодичната им проверка (виж jobs.py)."""
    jobs.start_background()

# Инициализация на AI интерпретатора
ai_interpreter = get_interpreter()


def _internal_error(context: str, exc: Exception, user_message: str) -> HTTPException:
    """
    Логва пълната грешка само на сървъра и връща общо съобщение към клиента.
    Кодът в съобщението позволява грешката да се намери в логовете на Render.
    Извиква се от except блок, за да може traceback-ът да бъде отпечатан.
    """
    error_id = uuid.uuid4().hex[:8]
    print(f"❌ [{error_id}] {context}: {type(exc).__name__}: {exc}")
    traceback.print_exc()
    return HTTPException(status_code=500, detail=f"{user_message} (код: {error_id})")


# Лимити на заявките. Стойностите могат да се сменят през environment в Render.
DOCX_LIMIT_PER_HOUR = int(os.getenv("DOCX_RATE_LIMIT_PER_HOUR", "30"))
CALCULATE_LIMIT_PER_MINUTE = int(os.getenv("CALCULATE_RATE_LIMIT_PER_MINUTE", "60"))
LOGIN_LIMIT_PER_15_MIN = int(os.getenv("LOGIN_RATE_LIMIT_PER_15_MIN", "10"))
REGISTER_LIMIT_PER_HOUR = int(os.getenv("REGISTER_RATE_LIMIT_PER_HOUR", "5"))


def require_docx_quota(current_user: User = Depends(get_current_user)) -> User:
    """Изисква вход и ограничава генерирането на DOCX файлове."""
    enforce(f"docx:{current_user.id}", DOCX_LIMIT_PER_HOUR, 3600, "Достигнахте лимита за DOCX файлове.")
    return current_user


@app.get("/")
async def root():
    """Root endpoint - информация за API"""
    return {
        "message": "Astrology API",
        "version": "3.0.0",
        "endpoints": {
            "POST /calculate": "Изчислява астрологична карта",
            "POST /jobs": "Създава задача за AI анализ (състоянието: GET /jobs/{id})",
            "POST /register": "Регистрация на нов потребител",
            "POST /login": "Вход в системата - връща JWT token",
            "GET /me": "Връща текущия потребител (Bearer token)"
        }
    }


@app.get("/health")
async def health_check():
    """Health check endpoint"""
    return {"status": "healthy"}


@app.post("/calculate", response_model=ChartResponse)
async def calculate_chart(request: ChartRequest, http_request: Request):
    """
    Изчислява астрологична карта без AI интерпретация.
    
    Връща:
    - Позиции на планетите
    - Куспиди на домовете
    - Ъгли (ASC, MC)
    - Julian Day
    - UTC datetime
    - Локация
    """
    enforce(f"calc:{client_ip(http_request)}", CALCULATE_LIMIT_PER_MINUTE, 60, "Твърде много заявки.")
    try:
        # Изчисляване на картата
        chart_data = engine.calculate_chart(
            date=request.date,
            time=request.time,
            lat=request.lat,
            lon=request.lon
        )
        
        # Връщане на данните
        return ChartResponse(
            planets=chart_data["planets"],
            houses=chart_data["houses"],
            angles=chart_data["angles"],
            julian_day=chart_data["julian_day"],
            datetime_utc=chart_data["datetime_utc"],
            timezone=chart_data["timezone"],
            datetime_local=chart_data["datetime_local"],
            location=chart_data["location"]
        )
        
    except ValueError as e:
        raise HTTPException(status_code=400, detail=f"Невалидни входни данни: {str(e)}")
    except Exception as e:
        raise _internal_error("/calculate", e, "Не успяхме да изчислим картата. Опитайте отново след малко.")


class DOCXRequest(BaseModel):
    """Request model for DOCX generation"""
    user_name: str
    birth_date: str
    birth_time: str
    birth_city: str
    report_type: str
    natal_chart: Optional[Dict] = None
    natal_aspects: Optional[List] = None
    monthly_results: List[Dict] = Field(default_factory=list)


@app.post("/generate-docx")
async def generate_docx(request: DOCXRequest, current_user: User = Depends(require_docx_quota)):
    """
    Generate DOCX report for periods > 6 months
    """
    try:
        generator = DOCXGenerator()
        
        # Prepare data for DOCX generation
        docx_data = {
            'user_name': request.user_name,
            'birth_date': request.birth_date,
            'birth_time': request.birth_time,
            'birth_city': request.birth_city,
            'report_type': request.report_type,
            'natal_chart': request.natal_chart,
            'natal_aspects': request.natal_aspects,
            'monthly_results': request.monthly_results
        }
        
        # Generate DOCX
        docx_bytes = generator.generate_docx(docx_data)
        
        # Return DOCX file - URL encode filename for Cyrillic support
        from urllib.parse import quote
        user_name_safe = docx_data.get('user_name', 'Report').replace(' ', '_')
        filename = f"Astrology_Report_{user_name_safe}_{datetime.now().strftime('%Y-%m-%d')}.docx"
        filename_encoded = quote(filename)
        
        return Response(
            content=docx_bytes,
            media_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            headers={
                "Content-Disposition": f"attachment; filename*=UTF-8''{filename_encoded}"
            }
        )
        
    except Exception as e:
        raise _internal_error("/generate-docx", e, "Не успяхме да създадем DOCX файла. Опитайте отново след малко.")


# ============================================================================
# АКАУНТНА СИСТЕМА - АВТЕНТИФИКАЦИЯ
# ============================================================================

class UserRegister(BaseModel):
    email: str
    password: str
    full_name: str
    accept_terms: bool = False

class UserLogin(BaseModel):
    email: str
    password: str

@app.post("/register")
async def register(user_data: UserRegister, http_request: Request, background_tasks: BackgroundTasks,
                   db: Session = Depends(get_db)):
    """Регистрация на нов потребител"""
    enforce(f"register:{client_ip(http_request)}", REGISTER_LIMIT_PER_HOUR, 3600, "Твърде много регистрации от този адрес.")

    email = normalize_email(user_data.email)
    full_name = (user_data.full_name or "").strip()
    error = validate_email(email) or validate_password(user_data.password, email)
    if not error and not (1 <= len(full_name) <= 100):
        error = "Въведете име до 100 символа"
    if not error and not user_data.accept_terms:
        error = "Моля, приемете Общите условия и Политиката за поверителност"
    if error:
        raise HTTPException(status_code=400, detail=error)

    # Проверка дали имейлът съществува, без значение от главни и малки букви
    existing_user = db.query(User).filter(func.lower(func.trim(User.email)) == email).first()
    if existing_user:
        raise HTTPException(status_code=400, detail="Имейлът вече е регистриран")
    
    new_user = User(
        email=email,
        full_name=full_name,
        hashed_password=hash_password(user_data.password),
        paid_cents=0,
        gift_cents=0,
        terms_version=account_api.TERMS_VERSION,
        terms_accepted_at=datetime.utcnow(),
    )
    db.add(new_user)
    db.flush()
    gift = billing.signup_gift_cents()
    if gift > 0:
        billing.apply_transaction(db, new_user.id, "signup_gift", gift=gift, ref=f"signup:{new_user.id}",
                                  description="Подарък при регистрация (за основните анализи)")
    db.commit()
    db.refresh(new_user)
    account_api.queue_verification(background_tasks, new_user)
    account_api._track(db, "register", new_user.id)
    return {"message": "Успешна регистрация"}

@app.post("/login")
def login(user_data: UserLogin, http_request: Request, db: Session = Depends(get_db)):
    """Вход в системата - връща JWT token"""
    email = normalize_email(user_data.email)
    ip = client_ip(http_request)
    message = "Твърде много опити за вход."
    enforce(f"login:{ip}:{email}", LOGIN_LIMIT_PER_15_MIN, 900, message)
    enforce(f"login-ip:{ip}", LOGIN_LIMIT_PER_15_MIN * 3, 900, message)

    user = account_api.find_user_by_email(db, user_data.email)
    if not user or not verify_password(user_data.password, user.hashed_password):
        raise HTTPException(status_code=401, detail="Грешен имейл или парола")
    
    events.track(db, "login", user.id)
    return {
        "access_token": create_user_token(user),
        "token_type": "bearer",
        "user": account_api.user_payload(user),
    }


if __name__ == "__main__":
    import uvicorn  # type: ignore
    uvicorn.run(app, host="0.0.0.0", port=8000)
