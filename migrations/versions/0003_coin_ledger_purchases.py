"""Фаза 3: регистър на монетите и покупки през Stripe.

Текущите баланси на потребителите се записват като начален ред в регистъра,
за да е сумата на delta равна на users.coins от самото начало.

Revision ID: 0003_coin_ledger_purchases
Revises: 0002_accounts_profiles_reports
"""
from alembic import op
import sqlalchemy as sa

revision = "0003_coin_ledger_purchases"
down_revision = "0002_accounts_profiles_reports"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "coin_transactions",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("delta", sa.Integer(), nullable=False),
        sa.Column("balance_after", sa.Integer(), nullable=False),
        sa.Column("reason", sa.String(30), nullable=False),
        sa.Column("ref", sa.String(120), nullable=True, unique=True),
        sa.Column("description", sa.String(200), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=True, server_default=sa.func.now()),
    )
    op.create_index("ix_coin_transactions_user_id", "coin_transactions", ["user_id"])
    op.create_index("ix_coin_transactions_created_at", "coin_transactions", ["created_at"])

    op.create_table(
        "purchases",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("package_id", sa.String(40), nullable=False),
        sa.Column("coins", sa.Integer(), nullable=False),
        sa.Column("amount_cents", sa.Integer(), nullable=False),
        sa.Column("currency", sa.String(3), nullable=False, server_default="eur"),
        sa.Column("status", sa.String(20), nullable=False, server_default="pending"),
        sa.Column("stripe_session_id", sa.String(255), nullable=True, unique=True),
        sa.Column("stripe_payment_intent", sa.String(255), nullable=True),
        sa.Column("receipt_url", sa.String(500), nullable=True),
        sa.Column("refunded_cents", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("created_at", sa.DateTime(), nullable=True, server_default=sa.func.now()),
        sa.Column("paid_at", sa.DateTime(), nullable=True),
    )
    op.create_index("ix_purchases_user_id", "purchases", ["user_id"])
    op.create_index("ix_purchases_stripe_payment_intent", "purchases", ["stripe_payment_intent"])

    # Начален баланс на съществуващите потребители
    op.execute("UPDATE users SET coins = 0 WHERE coins IS NULL")
    op.execute(
        "INSERT INTO coin_transactions (user_id, delta, balance_after, reason, ref, description) "
        "SELECT id, coins, coins, 'opening_balance', 'opening:' || id, 'Баланс преди въвеждането на регистъра' "
        "FROM users WHERE coins <> 0"
    )


def downgrade():
    op.drop_index("ix_purchases_stripe_payment_intent", table_name="purchases")
    op.drop_index("ix_purchases_user_id", table_name="purchases")
    op.drop_table("purchases")
    op.drop_index("ix_coin_transactions_created_at", table_name="coin_transactions")
    op.drop_index("ix_coin_transactions_user_id", table_name="coin_transactions")
    op.drop_table("coin_transactions")
