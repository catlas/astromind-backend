"""Фаза 14: дневно отчитане на разхода за AI (таблица ai_usage) за дневен и месечен бюджет с аварийно спиране.

Revision ID: 0011_ai_usage
Revises: 0010_jobs
"""
from alembic import op
import sqlalchemy as sa

revision = "0011_ai_usage"
down_revision = "0010_jobs"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "ai_usage",
        sa.Column("day", sa.String(10), primary_key=True),                      # YYYY-MM-DD (UTC)
        sa.Column("calls", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("prompt_tokens", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("completion_tokens", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("cost_millicents", sa.BigInteger(), nullable=False, server_default="0"),   # 1/1000 от евроцент
    )


def downgrade():
    op.drop_table("ai_usage")
