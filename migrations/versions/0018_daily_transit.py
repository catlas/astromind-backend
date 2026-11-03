"""Дневен транзит: таблица daily_transits и users.daily_email (по подразбиране включен).

Revision ID: 0018_daily_transit
Revises: 0017_report_view
"""
from alembic import op
import sqlalchemy as sa

revision = "0018_daily_transit"
down_revision = "0017_report_view"
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table("users") as batch:
        batch.add_column(sa.Column("daily_email", sa.Boolean(), nullable=False, server_default="1"))
    op.create_table(
        "daily_transits",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("day", sa.String(10), nullable=False),
        sa.Column("profile_name", sa.String(100), nullable=True),
        sa.Column("status", sa.String(10), nullable=False, server_default="pending"),
        sa.Column("text", sa.Text(), nullable=True),
        sa.Column("error", sa.String(200), nullable=True),
        sa.Column("email_attempts", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("emailed_at", sa.DateTime(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=True),
        sa.Column("updated_at", sa.DateTime(), nullable=True),
        sa.UniqueConstraint("user_id", "day", name="uq_daily_transit_user_day"),
    )
    op.create_index("ix_daily_transits_user_id", "daily_transits", ["user_id"])
    op.create_index("ix_daily_transits_day", "daily_transits", ["day"])


def downgrade():
    op.drop_index("ix_daily_transits_day", table_name="daily_transits")
    op.drop_index("ix_daily_transits_user_id", table_name="daily_transits")
    op.drop_table("daily_transits")
    with op.batch_alter_table("users") as batch:
        batch.drop_column("daily_email")
