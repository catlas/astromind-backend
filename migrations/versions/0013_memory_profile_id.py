"""Фаза 21: бележките на AI паметта се свързват с профил по номер (profile_id), не по име.

Еднозначните записи се мигрират: име, което съвпада с точно един профил на същия собственик (без значение от главни букви).
Нееднозначните (няма такъв профил или има няколко) остават без profile_id и не се ползват в анализи, докато собственикът не ги
свърже с профил в Настройки: нищо не се присвоява автоматично.

Revision ID: 0013_memory_profile_id
Revises: 0012_ai_reservations
"""
from alembic import op
import sqlalchemy as sa

revision = "0013_memory_profile_id"
down_revision = "0012_ai_reservations"
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table("memory_notes") as batch:
        batch.add_column(sa.Column("profile_id", sa.Integer(), nullable=True))
        batch.create_index("ix_memory_notes_profile_id", ["profile_id"])
        batch.create_foreign_key("fk_memory_notes_profile", "profiles", ["profile_id"], ["id"], ondelete="CASCADE")
    bind = op.get_bind()
    notes = bind.execute(sa.text(
        "SELECT id, user_id, profile_name FROM memory_notes WHERE profile_name IS NOT NULL AND profile_name <> ''")).fetchall()
    by_user = {}
    for profile_id, user_id, name in bind.execute(sa.text("SELECT id, user_id, name FROM profiles")).fetchall():
        by_user.setdefault(user_id, []).append((profile_id, (name or "").strip().lower()))
    for note_id, user_id, name in notes:
        # Сравнението е в Python: lower() в SQLite не знае кирилица, а в PostgreSQL знае; резултатът не бива да зависи от базата
        wanted = (name or "").strip().lower()
        rows = [pid for pid, profile_name in by_user.get(user_id, []) if profile_name == wanted]
        if len(rows) == 1:
            bind.execute(sa.text("UPDATE memory_notes SET profile_id = :p WHERE id = :i"), {"p": rows[0], "i": note_id})


def downgrade():
    with op.batch_alter_table("memory_notes") as batch:
        batch.drop_constraint("fk_memory_notes_profile", type_="foreignkey")
        batch.drop_index("ix_memory_notes_profile_id")
        batch.drop_column("profile_id")
