"""Alembic среда. Миграциите се пускат автоматично при старт от db_migrate.py."""
from alembic import context

from database import Base, engine

target_metadata = Base.metadata


def run_migrations_online():
    connection = context.config.attributes.get("connection")
    if connection is None:
        with engine.begin() as conn:
            _run(conn)
    else:
        _run(connection)


def _run(connection):
    context.configure(
        connection=connection,
        target_metadata=target_metadata,
        render_as_batch=connection.dialect.name == "sqlite",
    )
    with context.begin_transaction():
        context.run_migrations()


run_migrations_online()
