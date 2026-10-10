"""
Зареждане на баланса през Stripe Checkout в евро.

Кредитът влиза в баланса САМО от подписания webhook (checkout.session.completed),
никога от връщането на браузъра към сайта. Повторен webhook не добавя нищо,
защото всеки запис в регистъра има уникален ref. Заредените средства са "внесени" и
отключват премиум услугите (виж billing.py).

Нужни environment променливи: STRIPE_SECRET_KEY, STRIPE_WEBHOOK_SECRET.
"""
import hashlib
import hmac
import json
import os
import time
from datetime import datetime
from typing import Optional

import httpx
from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

import billing
import limits
import mailer
from database import CoinTransaction, Purchase, User, get_db
from deps import get_current_user, get_optional_user
from rate_limit import enforce

router = APIRouter(prefix="/billing")

STRIPE_API = "https://api.stripe.com/v1"
SIGNATURE_TOLERANCE_SECONDS = 300


def _track(db, name, user_id=None, props=None):
    try:
        import events
        events.track(db, name, user_id, props)
    except Exception as exc:
        print(f"⚠️ Събитието {name} не беше записано: {exc}")


# ---------------------------------------------------------------------------
# Публична информация за цените
# ---------------------------------------------------------------------------

@router.get("/config")
def billing_config(user: Optional[User] = Depends(get_optional_user)):
    user_id = user.id if user else None
    return {
        "payments_enabled": billing.payments_enabled(),
        # "test": плащанията не са истински (тестови карти); "live": истински; None: изключени
        "payments_mode": billing.stripe_mode() if billing.payments_enabled() else None,
        "balance_enforced": billing.balance_enforced(),
        "currency": "eur",
        "topups": [{**t, "bonus_cents": int(t["credit_cents"]) - int(t["amount_cents"])} for t in billing.topups_for(user_id)],
        "pricing_variant": billing.pricing_variant(user_id),
        # Цени в евроценти. Премиум са анализът за двама и прогнозата за период: плащат се само с внесени средства
        "prices": billing.prices(),
        "premium_services": ["pair_analysis", "forecast"],
        "signup_gift_cents": billing.signup_gift_cents(),
        # Горна граница на периода на прогнозата (календарни месеци)
        "limits": {
            "forecast_max_months_single": limits.FORECAST_MAX_MONTHS_SINGLE,
            "forecast_max_months_pair": limits.FORECAST_MAX_MONTHS_PAIR,
        },
    }


# ---------------------------------------------------------------------------
# Checkout
# ---------------------------------------------------------------------------

class CheckoutIn(BaseModel):
    package_id: str = Field(..., max_length=40)   # id на пакета за зареждане (topup5, topup10, topup20)
    # Потребителят се съгласява доставката на дигиталното съдържание да започне
    # веднага и приема, че губи правото на отказ (чл. 16, буква м от Директива 2011/83/ЕС)
    accept_immediate_delivery: bool = False


async def _stripe_post(path: str, data: dict, idempotency_key: Optional[str] = None) -> dict:
    headers = {"Authorization": f"Bearer {os.environ['STRIPE_SECRET_KEY']}"}
    if idempotency_key:
        headers["Idempotency-Key"] = idempotency_key
    async with httpx.AsyncClient(timeout=20) as client:
        r = await client.post(f"{STRIPE_API}{path}", data=data, headers=headers)
    if r.status_code >= 400:
        print(f"❌ Stripe {path} {r.status_code}: {r.text[:500]}")
        raise HTTPException(status_code=502, detail="Плащането не може да започне в момента. Опитайте отново след малко.")
    return r.json()


async def _stripe_get(path: str, params: Optional[dict] = None) -> Optional[dict]:
    try:
        async with httpx.AsyncClient(timeout=20) as client:
            r = await client.get(f"{STRIPE_API}{path}", params=params,
                                 headers={"Authorization": f"Bearer {os.environ['STRIPE_SECRET_KEY']}"})
        return r.json() if r.status_code < 400 else None
    except Exception as exc:
        print(f"⚠️ Stripe GET {path}: {exc}")
        return None


@router.post("/checkout")
async def create_checkout(data: CheckoutIn, current_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    if not billing.payments_enabled():
        raise HTTPException(status_code=503, detail="Плащанията все още не са активирани.")
    if not data.accept_immediate_delivery:
        raise HTTPException(status_code=400, detail="Моля, потвърдете условията за дигитално съдържание.")
    package = billing.find_topup(data.package_id, current_user.id)
    if not package:
        raise HTTPException(status_code=400, detail="Непознат пакет")
    enforce(f"checkout:{current_user.id}", 10, 3600, "Твърде много опити за плащане.")

    purchase = Purchase(user_id=current_user.id, package_id=package["id"], credit_cents=int(package["credit_cents"]),
                        amount_cents=int(package["amount_cents"]), currency="eur", status="pending")
    db.add(purchase)
    db.commit()

    base = mailer.FRONTEND_URL
    form = {
        "mode": "payment",
        "locale": "bg",
        "success_url": f"{base}/#/balance?status=success&purchase={purchase.id}",
        "cancel_url": f"{base}/#/balance?status=cancel",
        "client_reference_id": str(current_user.id),
        "customer_email": current_user.email,
        "line_items[0][quantity]": "1",
        "line_items[0][price_data][currency]": "eur",
        "line_items[0][price_data][unit_amount]": str(purchase.amount_cents),
        "line_items[0][price_data][product_data][name]": f"Зареждане на баланс в AstroMind: {billing.format_eur(purchase.credit_cents)}",
        "metadata[purchase_id]": str(purchase.id),
        "metadata[user_id]": str(current_user.id),
        "payment_intent_data[metadata][purchase_id]": str(purchase.id),
        "consent_collection[terms_of_service]": "required" if os.getenv("STRIPE_REQUIRE_TOS") == "1" else "none",
    }
    session = await _stripe_post("/checkout/sessions", form, idempotency_key=f"purchase-{purchase.id}")
    purchase.stripe_session_id = session.get("id")
    db.commit()
    _track(db, "checkout_started", current_user.id,
           {"package": package["id"], "variant": billing.pricing_variant(current_user.id)})
    return {"url": session.get("url"), "purchase_id": purchase.id}


# ---------------------------------------------------------------------------
# Webhook
# ---------------------------------------------------------------------------

def verify_signature(payload: bytes, header: str, secret: str, now: Optional[float] = None) -> bool:
    """Проверка на Stripe-Signature (HMAC-SHA256 върху "{timestamp}.{payload}")."""
    try:
        parts = [p.split("=", 1) for p in (header or "").split(",") if "=" in p]
        timestamp = next(v for k, v in parts if k == "t")
        signatures = [v for k, v in parts if k == "v1"]
        ts = int(timestamp)
    except (StopIteration, ValueError):
        return False
    if abs((now or time.time()) - ts) > SIGNATURE_TOLERANCE_SECONDS:
        return False
    expected = hmac.new(secret.encode(), f"{timestamp}.".encode() + payload, hashlib.sha256).hexdigest()
    return any(hmac.compare_digest(expected, s) for s in signatures)


async def _fulfil(db: Session, session_obj: dict):
    if session_obj.get("payment_status") != "paid":
        return
    purchase_id = (session_obj.get("metadata") or {}).get("purchase_id")
    purchase = db.get(Purchase, int(purchase_id)) if purchase_id and str(purchase_id).isdigit() else None
    if purchase is None:
        purchase = db.query(Purchase).filter(Purchase.stripe_session_id == session_obj.get("id")).first()
    if purchase is None:
        print(f"⚠️ Webhook за непозната покупка: {session_obj.get('id')}")
        return
    if session_obj.get("amount_total") is not None and int(session_obj["amount_total"]) != purchase.amount_cents:
        print(f"❌ Сумата не съвпада за покупка {purchase.id}: {session_obj.get('amount_total')} != {purchase.amount_cents}")
        _track(db, "payment_mismatch", purchase.user_id, {"purchase_id": purchase.id})
        return
    if (session_obj.get("currency") or "eur").lower() != purchase.currency:
        print(f"❌ Валутата не съвпада за покупка {purchase.id}")
        return

    tx = billing.apply_transaction(db, purchase.user_id, "purchase", paid=purchase.credit_cents,
                                   ref=f"purchase:{purchase.id}",
                                   description=f"Зареждане на баланса: {billing.format_eur(purchase.credit_cents)}")
    if tx is None:
        return  # вече е обработена
    already_refunded = purchase.refunded_cents or 0            # връщане, дошло преди плащането (събитията не са по ред)
    purchase.status = "paid"
    purchase.paid_at = datetime.utcnow()
    purchase.stripe_session_id = purchase.stripe_session_id or session_obj.get("id")
    purchase.stripe_payment_intent = session_obj.get("payment_intent")
    db.commit()
    if already_refunded:
        purchase.refunded_cents = 0
        _claw_back(db, purchase, already_refunded, "refund", "refunded")
    _track(db, "purchase_completed", purchase.user_id,
           {"package": purchase.package_id, "amount_cents": purchase.amount_cents})

    # Разписката от Stripe (по желание)
    if purchase.stripe_payment_intent:
        pi = await _stripe_get(f"/payment_intents/{purchase.stripe_payment_intent}", {"expand[]": "latest_charge"})
        charge = (pi or {}).get("latest_charge")
        if isinstance(charge, dict) and charge.get("receipt_url"):
            purchase.receipt_url = charge["receipt_url"]
            db.commit()


def _purchase_for_charge(db: Session, charge: dict) -> Optional[Purchase]:
    """Покупката на таксата: по платежното намерение, а ако още не е записано (събитията идват не по ред), по метаданните."""
    intent = charge.get("payment_intent")
    purchase = db.query(Purchase).filter(Purchase.stripe_payment_intent == intent).first() if intent else None
    if purchase is None:
        purchase_id = str((charge.get("metadata") or {}).get("purchase_id") or "")
        purchase = db.get(Purchase, int(purchase_id)) if purchase_id.isdigit() else None
    return purchase


def _claw_back(db: Session, purchase: Purchase, total_cents: int, reason: str, status: str) -> None:
    """
    Отнема кредита, съответстващ на върнатата или оспорената сума (total_cents е общата сума до момента).
    Кредитът се отнема пропорционално, само от внесените средства и не повече от тях. Покупка, която още не е платена, само
    запомня сумата: когато плащането дойде, кредитът се нетира (виж _fulfil).
    """
    if total_cents <= purchase.refunded_cents:
        return
    newly = total_cents - purchase.refunded_cents
    purchase.refunded_cents = total_cents
    if purchase.status == "paid" or purchase.status in ("refunded", "disputed"):
        credit_back = round(purchase.credit_cents * newly / purchase.amount_cents)
        user = db.get(User, purchase.user_id)
        credit_back = min(credit_back, user.paid_cents or 0) if user else 0
        if credit_back > 0:
            billing.apply_transaction(db, purchase.user_id, "refund", paid=-credit_back,
                                      ref=f"{reason}:{purchase.id}:{total_cents}",
                                      description=f"Възстановена сума за зареждане #{purchase.id}")
        if status == "disputed":
            purchase.status = "disputed"
        elif total_cents >= purchase.amount_cents:
            purchase.status = "refunded"
    db.commit()


def _refund(db: Session, charge: dict):
    purchase = _purchase_for_charge(db, charge)
    if purchase is None:
        print(f"⚠️ Връщане за непозната покупка: {charge.get('payment_intent')}")
        return
    _claw_back(db, purchase, int(charge.get("amount_refunded") or 0), "refund", "refunded")


def _dispute(db: Session, dispute: dict):
    """Оспорване от картодържателя: кредитът за оспорената сума се отнема веднага; покупката става „disputed“."""
    purchase = _purchase_for_charge(db, {"payment_intent": dispute.get("payment_intent"),
                                         "metadata": dispute.get("metadata")})
    if purchase is None:
        print(f"⚠️ Оспорване за непозната покупка: {dispute.get('payment_intent')}")
        return
    _claw_back(db, purchase, max(int(dispute.get("amount") or 0), purchase.refunded_cents), "dispute", "disputed")
    _track(db, "payment_disputed", purchase.user_id, {"purchase_id": purchase.id})


@router.post("/webhook")
async def stripe_webhook(request: Request, db: Session = Depends(get_db)):
    secret = os.getenv("STRIPE_WEBHOOK_SECRET")
    if not secret:
        raise HTTPException(status_code=503, detail="Webhook не е настроен")
    payload = await request.body()
    if not verify_signature(payload, request.headers.get("stripe-signature", ""), secret):
        raise HTTPException(status_code=400, detail="Невалиден подпис")
    event = json.loads(payload)
    obj = (event.get("data") or {}).get("object") or {}
    kind = event.get("type")
    mode = billing.stripe_mode()
    if mode and event.get("livemode") is not None and bool(event["livemode"]) != (mode == "live"):
        # Събитие от другия режим (тестово при ключ за живо или обратно): грешна настройка, нищо не се записва
        print(f"⛔ Webhook от друг режим: livemode={event.get('livemode')}, ключът е за {mode}")
        raise HTTPException(status_code=400, detail="Режимът на събитието не съвпада с настройката")

    if kind in ("checkout.session.completed", "checkout.session.async_payment_succeeded"):
        await _fulfil(db, obj)
    elif kind == "checkout.session.expired":
        purchase = db.query(Purchase).filter(Purchase.stripe_session_id == obj.get("id")).first()
        if purchase and purchase.status == "pending":
            purchase.status = "expired"
            db.commit()
    elif kind == "checkout.session.async_payment_failed":
        purchase = db.query(Purchase).filter(Purchase.stripe_session_id == obj.get("id")).first()
        if purchase and purchase.status == "pending":
            purchase.status = "failed"
            db.commit()
    elif kind == "charge.refunded":
        _refund(db, obj)
    elif kind == "charge.dispute.created":
        _dispute(db, obj)
    return {"received": True}


# ---------------------------------------------------------------------------
# История на баланса и зарежданията
# ---------------------------------------------------------------------------

@router.get("/transactions")
def transactions(current_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    txs = db.query(CoinTransaction).filter(CoinTransaction.user_id == current_user.id).order_by(
        CoinTransaction.id.desc()).limit(100).all()
    purchases = db.query(Purchase).filter(Purchase.user_id == current_user.id, Purchase.status != "pending").order_by(
        Purchase.id.desc()).limit(50).all()
    return {
        **billing.balance_payload(current_user),
        "transactions": [{
            "id": t.id, "delta": t.delta, "delta_gift": t.delta_gift or 0, "balance_after": t.balance_after,
            "gift_after": t.gift_after or 0, "reason": t.reason,
            "description": t.description, "created_at": t.created_at.isoformat() if t.created_at else None,
        } for t in txs],
        "purchases": [{
            "id": p.id, "package_id": p.package_id, "credit_cents": p.credit_cents, "amount_cents": p.amount_cents,
            "currency": p.currency, "status": p.status, "receipt_url": p.receipt_url,
            "paid_at": p.paid_at.isoformat() if p.paid_at else None,
        } for p in purchases],
    }
