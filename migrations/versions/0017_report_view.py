"""Изглед на анализите на потребителя (users.report_view): short или full.

Старите потребители получават short (кратък изглед по подразбиране). Записаните отчети не се променят: пълният текст остава в базата.

Revision ID: 0017_report_view
Revises: 0016_job_hidden
"""
from alembic import op
import sqlalchemy as sa

revision = "0017_report_view"
down_revision = "0016_job_hidden"
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table("users") as batch:
        batch.add_column(sa.Column("report_view", sa.String(10), nullable=False, server_default="short"))


def downgrade():
    with op.batch_alter_table("users") as batch:
        batch.drop_column("report_view")
