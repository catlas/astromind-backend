"""
Плащания през Stripe Checkout в евро.

Монетите се добавят САМО от подписания webhook (checkout.session.completed),
никога от връщането на браузъра към сайта. Повторен webhook не добавя нищо,
защото всеки запис в регистъра има уникален ref.

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
from pydantic import BaseModel
from sqlalchemy.orm import Session

import billing
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
        "coins_enforced": billing.coins_enforced(),
        "currency": "eur",
        "packages": billing.packages_for(user_id),
        "pricing_variant": billing.pricing_variant(user_id),
        "costs": billing.costs(),
    }


# ---------------------------------------------------------------------------
# Checkout
# ---------------------------------------------------------------------------

class CheckoutIn(BaseModel):
    package_id: str
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
    package = billing.find_package(data.package_id, current_user.id)
    if not package:
        raise HTTPException(status_code=400, detail="Непознат пакет")
    enforce(f"checkout:{current_user.id}", 10, 3600, "Твърде много опити за плащане.")

    purchase = Purchase(user_id=current_user.id, package_id=package["id"], coins=int(package["coins"]),
                        amount_cents=int(package["amount_cents"]), currency="eur", status="pending")
    db.add(purchase)
    db.commit()

    base = mailer.FRONTEND_URL
    form = {
        "mode": "payment",
        "locale": "bg",
        "success_url": f"{base}/#/buy-coins?status=success&purchase={purchase.id}",
        "cancel_url": f"{base}/#/buy-coins?status=cancel",
        "client_reference_id": str(current_user.id),
        "customer_email": current_user.email,
        "line_items[0][quantity]": "1",
        "line_items[0][price_data][currency]": "eur",
        "line_items[0][price_data][unit_amount]": str(purchase.amount_cents),
        "line_items[0][price_data][product_data][name]": f"{purchase.coins} AstroМонети",
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
        return
    if (session_obj.get("currency") or "eur").lower() != purchase.currency:
        print(f"❌ Валутата не съвпада за покупка {purchase.id}")
        return

    tx = billing.apply_transaction(db, purchase.user_id, purchase.coins, "purchase",
                                   ref=f"purchase:{purchase.id}",
                                   description=f"Покупка: {purchase.coins} монети")
    if tx is None:
        return  # вече е обработена
    purchase.status = "paid"
    purchase.paid_at = datetime.utcnow()
    purchase.stripe_session_id = purchase.stripe_session_id or session_obj.get("id")
    purchase.stripe_payment_intent = session_obj.get("payment_intent")
    db.commit()
    _track(db, "purchase_completed", purchase.user_id,
           {"package": purchase.package_id, "amount_cents": purchase.amount_cents})

    # Разписката от Stripe (по желание)
    if purchase.stripe_payment_intent:
        pi = await _stripe_get(f"/payment_intents/{purchase.stripe_payment_intent}", {"expand[]": "latest_charge"})
        charge = (pi or {}).get("latest_charge")
        if isinstance(charge, dict) and charge.get("receipt_url"):
            purchase.receipt_url = charge["receipt_url"]
            db.commit()


def _refund(db: Session, charge: dict):
    purchase = db.query(Purchase).filter(Purchase.stripe_payment_intent == charge.get("payment_intent")).first()
    if purchase is None:
        return
    refunded = int(charge.get("amount_refunded") or 0)
    if refunded <= purchase.refunded_cents:
        return
    # Отнемат се монети пропорционално на върнатата сума, но не повече от баланса
    newly_refunded = refunded - purchase.refunded_cents
    coins_back = round(purchase.coins * newly_refunded / purchase.amount_cents)
    user = db.get(User, purchase.user_id)
    coins_back = min(coins_back, user.coins or 0) if user else 0
    if coins_back > 0:
        billing.apply_transaction(db, purchase.user_id, -coins_back, "refund",
                                  ref=f"refund:{purchase.id}:{refunded}",
                                  description=f"Възстановена сума за покупка #{purchase.id}")
    purchase.refunded_cents = refunded
    if refunded >= purchase.amount_cents:
        purchase.status = "refunded"
    db.commit()


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

    if kind in ("checkout.session.completed", "checkout.session.async_payment_succeeded"):
        await _fulfil(db, obj)
    elif kind == "checkout.session.expired":
        purchase = db.query(Purchase).filter(Purchase.stripe_session_id == obj.get("id")).first()
        if purchase and purchase.status == "pending":
            purchase.status = "expired"
            db.commit()
    elif kind == "charge.refunded":
        _refund(db, obj)
    return {"received": True}


# ---------------------------------------------------------------------------
# История на монетите и покупките
# ---------------------------------------------------------------------------

@router.get("/transactions")
def transactions(current_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    txs = db.query(CoinTransaction).filter(CoinTransaction.user_id == current_user.id).order_by(
        CoinTransaction.id.desc()).limit(100).all()
    purchases = db.query(Purchase).filter(Purchase.user_id == current_user.id, Purchase.status != "pending").order_by(
        Purchase.id.desc()).limit(50).all()
    return {
        "balance": current_user.coins or 0,
        "transactions": [{
            "id": t.id, "delta": t.delta, "balance_after": t.balance_after, "reason": t.reason,
            "description": t.description, "created_at": t.created_at.isoformat() if t.created_at else None,
        } for t in txs],
        "purchases": [{
            "id": p.id, "package_id": p.package_id, "coins": p.coins, "amount_cents": p.amount_cents,
            "currency": p.currency, "status": p.status, "receipt_url": p.receipt_url,
            "paid_at": p.paid_at.isoformat() if p.paid_at else None,
        } for p in purchases],
    }
