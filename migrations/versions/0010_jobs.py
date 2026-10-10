"""Фаза 11: задачи за генериране на анализ (таблица jobs).

Генерацията вече върви на сървъра като задача с номер: клиентът я създава, чете състоянието и събитията на задачата и може да
затвори страницата. Сумата се резервира при създаването и се връща при неуспех или отказ. Двойно изпращане с един и същ
Idempotency-Key връща същата задача (уникален индекс по потребител и ключ).

Revision ID: 0010_jobs
Revises: 0009_legacy_gift_topup
"""
from alembic import op
import sqlalchemy as sa

revision = "0010_jobs"
down_revision = "0009_legacy_gift_topup"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "jobs",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("idempotency_key", sa.String(80), nullable=True),
        sa.Column("kind", sa.String(20), nullable=False),
        sa.Column("sku", sa.String(40), nullable=False, server_default=""),
        sa.Column("status", sa.String(20), nullable=False, server_default="queued"),
        sa.Column("stage", sa.String(30), nullable=False, server_default="queued"),
        sa.Column("request", sa.JSON(), nullable=False),
        sa.Column("tier", sa.String(10), nullable=False, server_default="basic"),
        sa.Column("quote_cents", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("reserved_paid", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("reserved_gift", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("charged_cents", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("report_id", sa.Integer(), sa.ForeignKey("reports.id", ondelete="SET NULL"), nullable=True),
        sa.Column("error_code", sa.String(40), nullable=True),
        sa.Column("error_message", sa.String(500), nullable=True),
        sa.Column("events", sa.JSON(), nullable=False),
        sa.Column("event_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("attempts", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("created_at", sa.DateTime(), nullable=True),
        sa.Column("started_at", sa.DateTime(), nullable=True),
        sa.Column("finished_at", sa.DateTime(), nullable=True),
        sa.Column("lease_until", sa.DateTime(), nullable=True),
        sa.UniqueConstraint("user_id", "idempotency_key", name="uq_jobs_user_key"),
    )
    op.create_index("ix_jobs_user_id", "jobs", ["user_id"])
    op.create_index("ix_jobs_status", "jobs", ["status"])
    op.create_index("ix_jobs_created_at", "jobs", ["created_at"])


def downgrade():
    op.drop_index("ix_jobs_created_at", table_name="jobs")
    op.drop_index("ix_jobs_status", table_name="jobs")
    op.drop_index("ix_jobs_user_id", table_name="jobs")
    op.drop_table("jobs")
