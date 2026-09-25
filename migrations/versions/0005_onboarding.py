"""Фаза 5: флаг за завършен onboarding.

Съществуващите потребители вече ползват приложението, затова се маркират
като завършили onboarding. Новите минават през него след първия вход.

Revision ID: 0005_onboarding
Revises: 0004_events
"""
from alembic import op
import sqlalchemy as sa

revision = "0005_onboarding"
down_revision = "0004_events"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("users", sa.Column("onboarding_completed", sa.Boolean(), nullable=False, server_default=sa.false()))
    op.execute(sa.text("UPDATE users SET onboarding_completed = :t").bindparams(t=True))


def downgrade():
    with op.batch_alter_table("users") as batch:
        batch.drop_column("onboarding_completed")
