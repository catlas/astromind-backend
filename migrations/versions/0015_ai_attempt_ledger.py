"""R10: регистър на AI опитите (ai_reservations): attempt_id, състояние, доставчик и токени.

Старите редове са резервации в ход: получават state=reserved и без attempt_id (не са идемпотентни по идентификатор).

Revision ID: 0015_ai_attempt_ledger
Revises: 0014_report_snapshot
"""
from alembic import op
import sqlalchemy as sa

revision = "0015_ai_attempt_ledger"
down_revision = "0014_report_snapshot"
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table("ai_reservations") as batch:
        batch.add_column(sa.Column("attempt_id", sa.String(32), nullable=True))
        batch.add_column(sa.Column("state", sa.String(12), nullable=False, server_default="reserved"))
        batch.add_column(sa.Column("provider", sa.String(16), nullable=True))
        batch.add_column(sa.Column("prompt_tokens", sa.BigInteger(), nullable=True))
        batch.add_column(sa.Column("completion_tokens", sa.BigInteger(), nullable=True))
        batch.add_column(sa.Column("settled_at", sa.DateTime(), nullable=True))
        batch.create_index("ix_ai_reservations_state", ["state"])
        batch.create_unique_constraint("uq_ai_reservations_attempt_id", ["attempt_id"])


def downgrade():
    with op.batch_alter_table("ai_reservations") as batch:
        batch.drop_constraint("uq_ai_reservations_attempt_id", type_="unique")
        batch.drop_index("ix_ai_reservations_state")
        for column in ("settled_at", "completion_tokens", "prompt_tokens", "provider", "state", "attempt_id"):
            batch.drop_column(column)
