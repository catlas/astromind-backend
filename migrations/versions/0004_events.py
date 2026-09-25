"""Фаза 4: събития за аналитиката на фунията.

Revision ID: 0004_events
Revises: 0003_coin_ledger_purchases
"""
from alembic import op
import sqlalchemy as sa

revision = "0004_events"
down_revision = "0003_coin_ledger_purchases"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "events",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=True),
        sa.Column("name", sa.String(50), nullable=False),
        sa.Column("props", sa.JSON(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=True, server_default=sa.func.now()),
    )
    op.create_index("ix_events_user_id", "events", ["user_id"])
    op.create_index("ix_events_name", "events", ["name"])
    op.create_index("ix_events_created_at", "events", ["created_at"])


def downgrade():
    op.drop_index("ix_events_created_at", table_name="events")
    op.drop_index("ix_events_name", table_name="events")
    op.drop_index("ix_events_user_id", table_name="events")
    op.drop_table("events")
