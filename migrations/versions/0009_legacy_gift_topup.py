"""Подарък до 5,00 € за акаунтите, създадени преди баланса в евро.

Новите акаунти получават 5,00 € подарък при регистрация (ред signup_gift в регистъра). Акаунтите отпреди баланса в евро
нямат такъв ред, а след миграция 0008 подаръчният им дял идва само от старите безплатни монети (най-много 1,00 €).
Тук подаръчният им дял се довежда до 5,00 €: добавя се разликата, ако е положителна. Внесените средства не се пипат.
Акаунт със signup_gift в регистъра не се пипа: той вече е получил подаръка и може да го е харчил.
Решение на собственика от 10.10.2026.

Миграцията не връща данните при downgrade: подаръкът може вече да е похарчен, а отнемането му би объркало регистъра.

Revision ID: 0009_legacy_gift_topup
Revises: 0008_euro_balance
"""
from alembic import op
import sqlalchemy as sa

revision = "0009_legacy_gift_topup"
down_revision = "0008_euro_balance"
branch_labels = None
depends_on = None

GIFT_TARGET_CENTS = 500
REASON = "legacy_gift_topup"
DESCRIPTION = "Подарък до 5,00 € за съществуващите акаунти"


def upgrade():
    bind = op.get_bind()
    rows = bind.execute(sa.text(
        "SELECT u.id, u.paid_cents, u.gift_cents FROM users u "
        "WHERE u.gift_cents < :target "
        "AND NOT EXISTS (SELECT 1 FROM coin_transactions t WHERE t.user_id = u.id AND t.reason = 'signup_gift') "
        "ORDER BY u.id"), {"target": GIFT_TARGET_CENTS}).fetchall()
    for user_id, paid, gift in rows:
        paid, gift = int(paid or 0), int(gift or 0)
        add = GIFT_TARGET_CENTS - gift
        bind.execute(sa.text("UPDATE users SET gift_cents = :gift WHERE id = :id"),
                     {"gift": GIFT_TARGET_CENTS, "id": user_id})
        bind.execute(sa.text(
            "INSERT INTO coin_transactions (user_id, delta, delta_gift, balance_after, gift_after, reason, ref, description, created_at) "
            "VALUES (:user_id, :delta, :delta_gift, :balance_after, :gift_after, :reason, :ref, :description, CURRENT_TIMESTAMP)"),
            {"user_id": user_id, "delta": add, "delta_gift": add, "balance_after": paid + GIFT_TARGET_CENTS,
             "gift_after": GIFT_TARGET_CENTS, "reason": REASON, "ref": f"legacy-gift-topup:{user_id}",
             "description": DESCRIPTION})


def downgrade():
    # Данните не се връщат: подаръкът може вече да е похарчен (виж горе)
    pass
