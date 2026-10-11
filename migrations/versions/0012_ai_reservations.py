"""Фаза 21: резервации на най-високия възможен разход за AI (таблица ai_reservations): паралелни заявки не надвишават бюджета.

Revision ID: 0012_ai_reservations
Revises: 0011_ai_usage
"""
from alembic import op
import sqlalchemy as sa

revision = "0012_ai_reservations"
down_revision = "0011_ai_usage"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "ai_reservations",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("day", sa.String(10), nullable=False),
        sa.Column("millicents", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("created_at", sa.DateTime(), nullable=False),
    )
    op.create_index("ix_ai_reservations_day", "ai_reservations", ["day"])


def downgrade():
    op.drop_index("ix_ai_reservations_day", table_name="ai_reservations")
    op.drop_table("ai_reservations")
