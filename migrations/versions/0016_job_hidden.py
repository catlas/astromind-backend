"""Скриване на неуспешни и отказани задачи от История (jobs.hidden).

Колоната е само за показване: скритата задача си остава в базата до обичайното изчистване (JOB_RETENTION_DAYS), а таксуването и
баланса не се пипат. Старите редове получават hidden=false.

Revision ID: 0016_job_hidden
Revises: 0015_ai_attempt_ledger
"""
from alembic import op
import sqlalchemy as sa

revision = "0016_job_hidden"
down_revision = "0015_ai_attempt_ledger"
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table("jobs") as batch:
        batch.add_column(sa.Column("hidden", sa.Boolean(), nullable=False, server_default="0"))


def downgrade():
    with op.batch_alter_table("jobs") as batch:
        batch.drop_column("hidden")
