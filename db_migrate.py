"""
Пуска Alembic миграциите при старт на приложението.

Продукционната база е създадена с Base.metadata.create_all преди въвеждането
на Alembic. Ако таблицата users съществува, но няма alembic_version, базата се
маркира като базовата ревизия (0001) и след това се прилагат новите миграции.
"""
from pathlib import Path

from alembic import command
from alembic.config import Config
from sqlalchemy import inspect

from database import engine

BASELINE_REVISION = "0001_baseline"


def _config(connection) -> Config:
    cfg = Config()
    cfg.set_main_option("script_location", str(Path(__file__).parent / "migrations"))
    cfg.attributes["connection"] = connection
    return cfg


def run_migrations():
    with engine.begin() as connection:
        cfg = _config(connection)
        tables = set(inspect(connection).get_table_names())
        if "alembic_version" not in tables and "users" in tables:
            print("🗄️  Съществуваща база без Alembic: маркирам като", BASELINE_REVISION)
            command.stamp(cfg, BASELINE_REVISION)
        command.upgrade(cfg, "head")
