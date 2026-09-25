"""Фаза 6: контролирана AI памет (бележки на потребителя).

Revision ID: 0006_memory
Revises: 0005_onboarding
"""
from alembic import op
import sqlalchemy as sa

revision = "0006_memory"
down_revision = "0005_onboarding"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("users", sa.Column("memory_enabled", sa.Boolean(), nullable=False, server_default=sa.true()))
    op.create_table(
        "memory_notes",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("profile_name", sa.String(100), nullable=True),
        sa.Column("text", sa.String(500), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=True, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(), nullable=True, server_default=sa.func.now()),
    )
    op.create_index("ix_memory_notes_user_id", "memory_notes", ["user_id"])


def downgrade():
    op.drop_index("ix_memory_notes_user_id", table_name="memory_notes")
    op.drop_table("memory_notes")
    with op.batch_alter_table("users") as batch:
        batch.drop_column("memory_enabled")
