"""R06: неизменяем изчислителен снимка на отчета (reports.snapshot).

Колоната е празна за старите отчети: те остават „legacy“ и не получават измислен снимка (report_export ги обозначава).

Revision ID: 0014_report_snapshot
Revises: 0013_memory_profile_id
"""
from alembic import op
import sqlalchemy as sa

revision = "0014_report_snapshot"
down_revision = "0013_memory_profile_id"
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table("reports") as batch:
        batch.add_column(sa.Column("snapshot", sa.JSON(), nullable=True))


def downgrade():
    with op.batch_alter_table("reports") as batch:
        batch.drop_column("snapshot")
