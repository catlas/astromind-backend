"""
Баланс в евро: цени на услугите, пакети за зареждане и атомарни записи в регистъра.

Балансът има два дяла:
- внесени средства (users.paid_cents): заредени с плащане, важат за всички услуги;
- подаръчен кредит (users.gift_cents): 5 € при регистрация, важи само за основните анализи.

Услугите са на две нива. Основни (basic) са анализите за един човек: натален или за избрана дата. Премиум (premium) са
анализите за двама и прогнозите за период. Основните първо ползват подаръка, после внесените средства. Премиум се плащат
САМО с внесени средства, затова стават достъпни след първото зареждане.

Всяка промяна на баланса минава през apply_transaction(), която записва ред в coin_transactions (името на таблицата е
историческо: регистър на баланса) в същата транзакция. Затова сумата на регистъра винаги е равна на баланса, поотделно
за двата дяла. Всички суми са в евроценти.
"""
import json
import os
from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional, Tuple

from fastapi import HTTPException
from sqlalchemy import update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from database import CoinTransaction, User

BASIC, PREMIUM = "basic", "premium"

# Пакети за зареждане: плащате amount_cents, в баланса влиза credit_cents (при по-големите има малък бонус).
DEFAULT_TOPUPS = [
    {"id": "topup5", "name": "5 €", "amount_cents": 500, "credit_cents": 500},
    {"id": "topup10", "name": "10 €", "amount_cents": 1000, "credit_cents": 1050, "recommended": True},
    {"id": "topup20", "name": "20 €", "amount_cents": 2000, "credit_cents": 2200},
]
TOPUP_KEYS = {"id", "amount_cents", "credit_cents"}


def _int_env(name: str, default: int) -> int:
    try:
        return max(0, int(os.getenv(name, str(default))))
    except ValueError:
        return default


def _valid_topups(items) -> bool:
    return (isinstance(items, list) and bool(items)
            and all(isinstance(i, dict) and TOPUP_KEYS <= set(i) and int(i["amount_cents"]) > 0 and int(i["credit_cents"]) > 0
                    for i in items))


def topups() -> list:
    """Пакетите могат да се сменят с TOPUP_PACKAGES (JSON) в Render, без нов код."""
    raw = os.getenv("TOPUP_PACKAGES")
    if raw:
        try:
            items = json.loads(raw)
            if _valid_topups(items):
                return items
        except (ValueError, TypeError, KeyError):
            pass
        print("⚠️ TOPUP_PACKAGES не е валиден JSON; използват се пакетите по подразбиране")
    return DEFAULT_TOPUPS


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


def topups_for(user_id: Optional[int]) -> list:
    variant = pricing_variant(user_id)
    if variant:
        try:
            items = json.loads(os.environ["PRICING_EXPERIMENT"])[variant]
            if _valid_topups(items):
                return items
        except (ValueError, TypeError, KeyError):
            pass
    return topups()


def find_topup(package_id: str, user_id: Optional[int] = None) -> Optional[dict]:
    return next((p for p in topups_for(user_id) if p["id"] == package_id), None)


def prices() -> dict:
    """Цените на услугите в евроценти. Сменят се от Render без нов код (PRICE_*_CENTS)."""
    return {
        "basic_analysis": _int_env("PRICE_BASIC_ANALYSIS_CENTS", 160),       # анализ за един човек (натален или за дата)
        "pair_analysis": _int_env("PRICE_PAIR_ANALYSIS_CENTS", 180),         # анализ за двама (премиум)
        "forecast_month": _int_env("PRICE_FORECAST_MONTH_CENTS", 75),        # един месец от прогноза за период (премиум)
        "forecast_partner_extra": _int_env("PRICE_FORECAST_PARTNER_CENTS", 60),   # надбавка за двама при прогноза (веднъж)
    }


def signup_gift_cents() -> int:
    """Подаръчен кредит при регистрация. Важи само за основните анализи."""
    return _int_env("SIGNUP_GIFT_CENTS", 500)


def payments_enabled() -> bool:
    return bool(os.getenv("STRIPE_SECRET_KEY") and os.getenv("STRIPE_WEBHOOK_SECRET"))


def balance_enforced() -> bool:
    """
    Дали услугите се плащат от баланса. По подразбиране: само когато плащанията са включени, за да не остане никой без
    начин да зареди баланс (дотогава всичко е безплатно и нищо не е заключено). BALANCE_ENFORCED=1/0 го задава изрично;
    старото име COINS_ENFORCED още се чете.
    """
    explicit = os.getenv("BALANCE_ENFORCED") or os.getenv("COINS_ENFORCED")
    if explicit in ("0", "1"):
        return explicit == "1"
    return payments_enabled()


def format_eur(cents: int) -> str:
    """160 -> "1,60 €"."""
    return f"{cents / 100:.2f}".replace(".", ",") + " €"


@dataclass(frozen=True)
class Quote:
    """Цената на една услуга: сума в евроценти, ниво (basic | premium) и код на продукта (не участва в сравнението)."""
    cents: int
    tier: str
    sku: str = field(default="", compare=False)


def analysis_quote(has_partner: bool = False) -> Quote:
    p = prices()
    if has_partner:
        return Quote(p["pair_analysis"], PREMIUM, "analysis.pair")
    return Quote(p["basic_analysis"], BASIC, "analysis.basic")


def forecast_quote(months: int, has_partner: bool = False) -> Quote:
    p = prices()
    cents = max(1, months) * p["forecast_month"] + (p["forecast_partner_extra"] if has_partner else 0)
    return Quote(cents, PREMIUM, "forecast.pair" if has_partner else "forecast.single")


def balance_of(user: User) -> Tuple[int, int]:
    """(внесени средства, подаръчен кредит) в евроценти."""
    return int(user.paid_cents or 0), int(user.gift_cents or 0)


def balance_payload(user: User) -> dict:
    paid, gift = balance_of(user)
    return {"balance_cents": paid + gift, "paid_cents": paid, "gift_cents": gift}


def available_cents(paid: int, gift: int, tier: str) -> int:
    """Колко може да се похарчи за услуга от даденото ниво: основните ползват и подаръка, премиум само внесените."""
    return paid + gift if tier == BASIC else paid


def split_charge(paid: int, gift: int, cents: int, tier: str) -> Optional[Tuple[int, int]]:
    """(от внесени средства, от подарък) за сума cents, или None при недостиг. Основните първо ползват подаръка."""
    if cents > available_cents(paid, gift, tier):
        return None
    from_gift = min(gift, cents) if tier == BASIC else 0
    return cents - from_gift, from_gift


def insufficient_message(user: User, quote: Quote) -> str:
    paid, gift = balance_of(user)
    if quote.tier == PREMIUM:
        return (f"Премиум услугите се плащат с внесени средства. Нужни са {format_eur(quote.cents)}, внесени са "
                f"{format_eur(paid)}. Подаръчният кредит важи само за основните анализи. Заредете баланса си.")
    return (f"Нямате достатъчно средства за този анализ (нужни: {format_eur(quote.cents)}, налични: "
            f"{format_eur(paid + gift)}). Заредете баланса си.")


def require_balance(user: User, quote: Quote):
    if not balance_enforced():
        return
    paid, gift = balance_of(user)
    if quote.cents > available_cents(paid, gift, quote.tier):
        raise HTTPException(status_code=402, detail=insufficient_message(user, quote))


def apply_transaction(db: Session, user_id: int, reason: str, *, paid: int = 0, gift: int = 0,
                      ref: Optional[str] = None, description: Optional[str] = None,
                      allow_negative: bool = False) -> Optional[CoinTransaction]:
    """
    Променя двата дяла на баланса и записва реда в регистъра атомарно (в текущата транзакция, без commit).
    paid и gift са промените в евроценти (с минус при харчене). Връща None, ако ref вече е записан (идемпотентност) или
    ако дебитът би направил някой от двата дяла отрицателен. При рядкото състезание за един и същ ref сесията се връща
    назад (rollback).
    """
    if ref:
        exists = db.query(CoinTransaction.id).filter(CoinTransaction.ref == ref).first()
        if exists:
            return None

    stmt = update(User).where(User.id == user_id).values(
        paid_cents=User.paid_cents + paid, gift_cents=User.gift_cents + gift)
    if not allow_negative:
        if paid < 0:
            stmt = stmt.where(User.paid_cents + paid >= 0)
        if gift < 0:
            stmt = stmt.where(User.gift_cents + gift >= 0)
    result = db.execute(stmt)
    if result.rowcount != 1:
        return None

    now_paid, now_gift = db.query(User.paid_cents, User.gift_cents).filter(User.id == user_id).one()
    tx = CoinTransaction(user_id=user_id, delta=paid + gift, delta_gift=gift, balance_after=now_paid + now_gift,
                         gift_after=now_gift, reason=reason, ref=ref, description=(description or "")[:200] or None,
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
        db.refresh(user, attribute_names=["paid_cents", "gift_cents"])
    return tx


def charge(db: Session, user_id: int, quote: Quote, reason: str, ref: str, description: str) -> Optional[CoinTransaction]:
    """
    Взема цената от баланса по правилата за нивото: основните първо от подаръка, премиум само от внесените средства.
    Връща реда от регистъра или None (недостиг или вече взето). При състезание между две заявки чете наново веднъж.
    """
    for _ in range(2):
        row = db.query(User.paid_cents, User.gift_cents).filter(User.id == user_id).first()
        if row is None:
            return None
        split = split_charge(int(row[0] or 0), int(row[1] or 0), quote.cents, quote.tier)
        if split is None:
            return None
        from_paid, from_gift = split
        tx = apply_transaction(db, user_id, reason, paid=-from_paid, gift=-from_gift, ref=ref, description=description)
        if tx is not None:
            return tx
        if db.query(CoinTransaction.id).filter(CoinTransaction.ref == ref).first():
            return None                                 # вече е взето за този отчет
    return None


def reserve(db: Session, user_id: int, quote: Quote, ref: str, description: str) -> Optional[Tuple[int, int]]:
    """
    Резервира цената на задача: взема я от баланса по правилата за нивото (като charge) преди генерацията.
    Връща (от внесени средства, от подарък) или None при недостиг. Частите се пазят в задачата, за да се върнат в същите
    дялове при неуспех или отказ (release). Без commit: извиква се в транзакцията, която създава задачата.
    """
    tx = charge(db, user_id, quote, "analysis", ref, description)
    if tx is None:
        return None
    gift = -int(tx.delta_gift or 0)
    return -int(tx.delta) - gift, gift


def release(db: Session, user_id: int, paid: int, gift: int, ref: str,
            description: str = "Върната сума: анализът не успя") -> Optional[CoinTransaction]:
    """Връща резервирана сума в дяловете, от които е взета. Идемпотентно (по ref). Без commit."""
    if paid <= 0 and gift <= 0:
        return None
    return apply_transaction(db, user_id, "job_refund", paid=max(0, paid), gift=max(0, gift), ref=ref,
                             description=description)
