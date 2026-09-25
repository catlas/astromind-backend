"""Фаза 7: приемане на Общите условия (версия и дата).

Revision ID: 0007_terms
Revises: 0006_memory
"""
from alembic import op
import sqlalchemy as sa

revision = "0007_terms"
down_revision = "0006_memory"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("users", sa.Column("terms_version", sa.String(20), nullable=True))
    op.add_column("users", sa.Column("terms_accepted_at", sa.DateTime(), nullable=True))


def downgrade():
    with op.batch_alter_table("users") as batch:
        batch.drop_column("terms_accepted_at")
        batch.drop_column("terms_version")
