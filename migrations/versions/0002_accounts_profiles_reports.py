"""Фаза 2: потвърждение на имейл, версия на токена, профили и отчети на сървъра.

Revision ID: 0002_accounts_profiles_reports
Revises: 0001_baseline
"""
from alembic import op
import sqlalchemy as sa

revision = "0002_accounts_profiles_reports"
down_revision = "0001_baseline"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("users", sa.Column("email_verified", sa.Boolean(), nullable=False, server_default=sa.false()))
    op.add_column("users", sa.Column("token_version", sa.Integer(), nullable=False, server_default="0"))
    op.add_column("users", sa.Column("created_at", sa.DateTime(), nullable=True))

    op.create_table(
        "profiles",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("name", sa.String(100), nullable=False),
        sa.Column("relation", sa.String(20), nullable=False, server_default="self"),
        sa.Column("gender", sa.String(20), nullable=True),
        sa.Column("birth_date", sa.String(10), nullable=False),
        sa.Column("birth_time", sa.String(8), nullable=True),
        sa.Column("unknown_time", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("birth_place", sa.String(200), nullable=True),
        sa.Column("lat", sa.Float(), nullable=True),
        sa.Column("lon", sa.Float(), nullable=True),
        sa.Column("is_primary", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("settings", sa.JSON(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=True, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(), nullable=True, server_default=sa.func.now()),
        sa.UniqueConstraint("user_id", "name", name="uq_profiles_user_name"),
    )
    op.create_index("ix_profiles_user_id", "profiles", ["user_id"])

    op.create_table(
        "reports",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("profile_name", sa.String(100), nullable=True),
        sa.Column("report_type", sa.String(30), nullable=False, server_default="general"),
        sa.Column("label", sa.String(200), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("coins", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("status", sa.String(20), nullable=False, server_default="completed"),
        sa.Column("params", sa.JSON(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=True, server_default=sa.func.now()),
    )
    op.create_index("ix_reports_user_id", "reports", ["user_id"])
    op.create_index("ix_reports_created_at", "reports", ["created_at"])


def downgrade():
    op.drop_index("ix_reports_created_at", table_name="reports")
    op.drop_index("ix_reports_user_id", table_name="reports")
    op.drop_table("reports")
    op.drop_index("ix_profiles_user_id", table_name="profiles")
    op.drop_table("profiles")
    with op.batch_alter_table("users") as batch:
        batch.drop_column("created_at")
        batch.drop_column("token_version")
        batch.drop_column("email_verified")
