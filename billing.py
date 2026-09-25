"""
Монети: цени на анализите, пакети за покупка и атомарни записи в регистъра.

Балансът е users.coins, но всяка промяна минава през apply_transaction(),
която записва ред в coin_transactions в същата транзакция. Затова сумата
на регистъра винаги е равна на баланса.
"""
import json
import os
from datetime import datetime
from typing import Optional

from fastapi import HTTPException
from sqlalchemy import update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from database import CoinTransaction, User

DEFAULT_PACKAGES = [
    {"id": "starter", "name": "Начинаещ", "coins": 50, "amount_cents": 499,
     "description": "Около 6 подробни анализа", "icon": "star"},
    {"id": "popular", "name": "Популярен", "coins": 150, "amount_cents": 1299,
     "description": "Най-добрата стойност за редовни потребители", "icon": "auto_awesome", "recommended": True},
    {"id": "expert", "name": "Експерт", "coins": 500, "amount_cents": 3599,
     "description": "За професионалисти и сериозни изследователи", "icon": "workspace_premium"},
]


def _int_env(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, str(default)))
    except ValueError:
        return default


def packages() -> list:
    """Пакетите могат да се сменят с COIN_PACKAGES (JSON) в Render, без нов код."""
    raw = os.getenv("COIN_PACKAGES")
    if raw:
        try:
            items = json.loads(raw)
            if isinstance(items, list) and all({"id", "coins", "amount_cents"} <= set(i) for i in items):
                return items
        except ValueError:
            print("⚠️ COIN_PACKAGES не е валиден JSON; използват се пакетите по подразбиране")
    return DEFAULT_PACKAGES


def pricing_variant(user_id: Optional[int]) -> Optional[str]:
    """
    Ценови тест: PRICING_EXPERIMENT='{"A": [...пакети...], "B": [...]}'.
    Всеки потребител винаги получава един и същ вариант (по id).
    """
    raw = os.getenv("PRICING_EXPERIMENT")
    if not raw or user_id is None:
        return None
    try:
        variants = json.loads(raw)
    except ValueError:
        print("⚠️ PRICING_EXPERIMENT не е валиден JSON")
        return None
    names = sorted(k for k, v in variants.items() if isinstance(v, list) and v)
    return names[user_id % len(names)] if names else None


def packages_for(user_id: Optional[int]) -> list:
    variant = pricing_variant(user_id)
    if variant:
        items = json.loads(os.environ["PRICING_EXPERIMENT"])[variant]
        if all({"id", "coins", "amount_cents"} <= set(i) for i in items):
            return items
    return packages()


def find_package(package_id: str, user_id: Optional[int] = None) -> Optional[dict]:
    return next((p for p in packages_for(user_id) if p["id"] == package_id), None)


def costs() -> dict:
    return {
        "analysis": _int_env("COST_ANALYSIS", 8),
        "partner_extra": _int_env("COST_PARTNER_EXTRA", 4),
        "forecast_month": _int_env("COST_FORECAST_MONTH", 5),
        "signup_bonus": _int_env("SIGNUP_BONUS_COINS", 10),
    }


def payments_enabled() -> bool:
    return bool(os.getenv("STRIPE_SECRET_KEY") and os.getenv("STRIPE_WEBHOOK_SECRET"))


def coins_enforced() -> bool:
    """
    Дали анализите изразходват монети. По подразбиране: само когато плащанията
    са включени, за да не остане никой без начин да си купи монети.
    COINS_ENFORCED=1/0 го задава изрично.
    """
    explicit = os.getenv("COINS_ENFORCED")
    if explicit in ("0", "1"):
        return explicit == "1"
    return payments_enabled()


def analysis_cost(has_partner: bool = False) -> int:
    c = costs()
    return c["analysis"] + (c["partner_extra"] if has_partner else 0)


def forecast_cost(months: int, has_partner: bool = False) -> int:
    c = costs()
    return max(1, months) * c["forecast_month"] + (c["partner_extra"] if has_partner else 0)


def require_balance(user: User, cost: int):
    if coins_enforced() and (user.coins or 0) < cost:
        raise HTTPException(
            status_code=402,
            detail=f"Нямате достатъчно монети за този анализ (нужни: {cost}, налични: {user.coins or 0}).",
        )


def apply_transaction(db: Session, user_id: int, delta: int, reason: str,
                      ref: Optional[str] = None, description: Optional[str] = None,
                      allow_negative: bool = False) -> Optional[CoinTransaction]:
    """
    Променя баланса и записва реда в регистъра атомарно (в текущата транзакция,
    без commit). Връща None, ако ref вече е записан (идемпотентност) или ако
    дебитът би направил баланса отрицателен. При рядкото състезание за един и
    същ ref сесията се връща назад (rollback).
    """
    if ref:
        exists = db.query(CoinTransaction.id).filter(CoinTransaction.ref == ref).first()
        if exists:
            return None

    stmt = update(User).where(User.id == user_id).values(coins=User.coins + delta)
    if delta < 0 and not allow_negative:
        stmt = stmt.where(User.coins + delta >= 0)
    result = db.execute(stmt)
    if result.rowcount != 1:
        return None

    balance = db.query(User.coins).filter(User.id == user_id).scalar()
    tx = CoinTransaction(user_id=user_id, delta=delta, balance_after=balance, reason=reason,
                         ref=ref, description=(description or "")[:200] or None,
                         created_at=datetime.utcnow())
    db.add(tx)
    try:
        db.flush()
    except IntegrityError:
        # Паралелна заявка е записала същия ref между проверката и записа.
        # Отказваме цялата транзакция, включително промяната на баланса.
        db.rollback()
        return None
    # Обектите в сесията да отразят новия баланс
    user = db.get(User, user_id)
    if user is not None:
        db.refresh(user, attribute_names=["coins"])
    return tx


def charge_for_report(db: Session, user: User, report, cost: int, description: str) -> int:
    """Дебит след успешен анализ. Връща реално изразходваните монети."""
    if not coins_enforced() or cost <= 0:
        return 0
    tx = apply_transaction(db, user.id, -cost, "analysis", ref=f"report:{report.id}", description=description)
    charged = cost if tx else 0
    report.coins = charged
    return charged
