import os
from datetime import datetime

from sqlalchemy import (
    create_engine, Boolean, Column, DateTime, Float, ForeignKey, Integer, JSON, String, Text,
    UniqueConstraint,
)
from sqlalchemy.orm import declarative_base, relationship, sessionmaker
from dotenv import load_dotenv

load_dotenv()

SQLALCHEMY_DATABASE_URL = os.getenv("DATABASE_URL")

# Корекция за Render/Heroku специфични линкове + изричен драйвер psycopg2.
# SQLAlchemy 2.1 смени драйвера по подразбиране за "postgresql://" на psycopg (v3),
# а ние инсталираме psycopg2-binary — затова драйверът се задава изрично.
if SQLALCHEMY_DATABASE_URL:
    for prefix in ("postgres://", "postgresql://"):
        if SQLALCHEMY_DATABASE_URL.startswith(prefix):
            SQLALCHEMY_DATABASE_URL = "postgresql+psycopg2://" + SQLALCHEMY_DATABASE_URL[len(prefix):]
            break

# Ако няма DATABASE_URL, използваме SQLite за локално тестване
if not SQLALCHEMY_DATABASE_URL:
    SQLALCHEMY_DATABASE_URL = "sqlite:///./astromind.db"
    print("⚠️  DATABASE_URL не е зададен - използва се SQLite за локално тестване")

engine = create_engine(SQLALCHEMY_DATABASE_URL, connect_args={"check_same_thread": False} if "sqlite" in SQLALCHEMY_DATABASE_URL else {})
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()

# Схемата на базата се управлява с Alembic миграции (виж migrations/ и db_migrate.py).
# Моделите тук трябва да съвпадат с последната миграция.


class User(Base):
    __tablename__ = "users"

    id = Column(Integer, primary_key=True, index=True)
    email = Column(String, unique=True, index=True)
    hashed_password = Column(String)
    full_name = Column(String)
    coins = Column(Integer, default=10)  # Начален бонус
    email_verified = Column(Boolean, nullable=False, default=False, server_default="0")
    # Увеличава се при смяна на парола, за да спрат да важат старите токени
    token_version = Column(Integer, nullable=False, default=0, server_default="0")
    created_at = Column(DateTime, default=datetime.utcnow)
    onboarding_completed = Column(Boolean, nullable=False, default=False, server_default="0")
    # Потребителят решава дали бележките му се подават на AI
    memory_enabled = Column(Boolean, nullable=False, default=True, server_default="1")

    profiles = relationship("Profile", back_populates="user", cascade="all, delete-orphan", passive_deletes=True)
    reports = relationship("Report", back_populates="user", cascade="all, delete-orphan", passive_deletes=True)


class Profile(Base):
    """Астрологичен профил (аз, партньор, близък) на потребител."""
    __tablename__ = "profiles"
    __table_args__ = (UniqueConstraint("user_id", "name", name="uq_profiles_user_name"),)

    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    name = Column(String(100), nullable=False)
    relation = Column(String(20), nullable=False, default="self")
    gender = Column(String(20), nullable=True)
    birth_date = Column(String(10), nullable=False)
    birth_time = Column(String(8), nullable=True)
    unknown_time = Column(Boolean, nullable=False, default=False)
    birth_place = Column(String(200), nullable=True)
    lat = Column(Float, nullable=True)
    lon = Column(Float, nullable=True)
    is_primary = Column(Boolean, nullable=False, default=False)
    # Настройки на формата за анализ (транзит, партньор), пазени заедно с профила
    settings = Column(JSON, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    user = relationship("User", back_populates="profiles")


class Report(Base):
    """Генериран AI анализ, запазен за историята на потребителя."""
    __tablename__ = "reports"

    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    profile_name = Column(String(100), nullable=True)
    report_type = Column(String(30), nullable=False, default="general")
    label = Column(String(200), nullable=False)
    content = Column(Text, nullable=False)
    coins = Column(Integer, nullable=False, default=0)
    status = Column(String(20), nullable=False, default="completed")
    params = Column(JSON, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow, index=True)

    user = relationship("User", back_populates="reports")


class CoinTransaction(Base):
    """
    Регистър на монетите. Всяка промяна на users.coins има ред тук;
    сумата на delta винаги е равна на баланса. ref е уникален, за да не
    се запише едно и също събитие (напр. повторен webhook) два пъти.
    """
    __tablename__ = "coin_transactions"

    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    delta = Column(Integer, nullable=False)
    balance_after = Column(Integer, nullable=False)
    reason = Column(String(30), nullable=False)
    ref = Column(String(120), nullable=True, unique=True)
    description = Column(String(200), nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow, index=True)


class Purchase(Base):
    """Покупка на монети през Stripe Checkout."""
    __tablename__ = "purchases"

    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    package_id = Column(String(40), nullable=False)
    coins = Column(Integer, nullable=False)
    amount_cents = Column(Integer, nullable=False)
    currency = Column(String(3), nullable=False, default="eur")
    status = Column(String(20), nullable=False, default="pending")  # pending, paid, refunded, expired
    stripe_session_id = Column(String(255), nullable=True, unique=True)
    stripe_payment_intent = Column(String(255), nullable=True, index=True)
    receipt_url = Column(String(500), nullable=True)
    refunded_cents = Column(Integer, nullable=False, default=0)
    created_at = Column(DateTime, default=datetime.utcnow)
    paid_at = Column(DateTime, nullable=True)


class Event(Base):
    """Събитие за аналитиката на фунията. Без свободен текст и лични данни."""
    __tablename__ = "events"

    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=True, index=True)
    name = Column(String(50), nullable=False, index=True)
    props = Column(JSON, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow, index=True)


class MemoryNote(Base):
    """
    Бележка, която потребителят иска AI да знае (напр. „работя като учител“).
    Пише се само от потребителя, винаги е видима и може да се изтрие.
    profile_name = None означава, че важи за всички анализи.
    """
    __tablename__ = "memory_notes"

    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    profile_name = Column(String(100), nullable=True)
    text = Column(String(500), nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


# Модели с колона user_id: трият се и се експортират заедно с акаунта
USER_OWNED_MODELS = ["Profile", "Report", "CoinTransaction", "Purchase", "Event", "MemoryNote"]


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()

