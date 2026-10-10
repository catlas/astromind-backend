#!/usr/bin/env python3
"""
Проверка, че резервното копие на базата наистина се възстановява (Фаза 14).

Прави pg_dump на SOURCE, възстановява го в ПРАЗНА база TARGET и сравнява броя редове във всяка таблица и версията на миграциите.
Не променя SOURCE. TARGET трябва да е празна база за проба (не продукцията!): скриптът отказва, ако в нея вече има таблици.

Пускане (нужни са pg_dump и pg_restore с версия, не по-стара от сървъра; продукцията е PostgreSQL 18):
    python scripts/backup_restore_check.py "postgresql://…/astromind" "postgresql://…/astromind_restore_check"
Препоръка: веднъж месечно и преди по-голяма промяна на схемата. Само потребителят пуска това срещу продукцията (паролата е негова).
"""
import os
import subprocess
import sys
import tempfile

from sqlalchemy import create_engine, inspect, text


def counts(url: str):
    engine = create_engine(url.replace("postgresql://", "postgresql+psycopg2://", 1) if url.startswith("postgresql://") else url)
    with engine.connect() as conn:
        tables = sorted(inspect(engine).get_table_names())
        result = {t: conn.execute(text(f'SELECT COUNT(*) FROM "{t}"')).scalar() for t in tables}
        try:
            version = conn.execute(text("SELECT version_num FROM alembic_version")).scalar()
        except Exception:
            version = None
    engine.dispose()
    return result, version


def main(source: str, target: str) -> int:
    before_target, _ = counts(target)
    if before_target:
        print(f"❌ В TARGET вече има таблици ({', '.join(before_target)}). Използвайте празна база за проба.")
        return 2
    with tempfile.TemporaryDirectory() as folder:
        dump = os.path.join(folder, "backup.dump")
        subprocess.run(["pg_dump", "--format=custom", "--no-owner", "--no-privileges", "--file", dump, source], check=True)
        print(f"✅ Копието е направено ({os.path.getsize(dump) / 1e6:.2f} МБ)")
        subprocess.run(["pg_restore", "--no-owner", "--no-privileges", "--exit-on-error", "--dbname", target, dump], check=True)
        print("✅ Копието е възстановено")
    source_counts, source_version = counts(source)
    target_counts, target_version = counts(target)
    problems = []
    if source_version != target_version:
        problems.append(f"версия на миграциите: {source_version} ≠ {target_version}")
    for table in sorted(set(source_counts) | set(target_counts)):
        if source_counts.get(table) != target_counts.get(table):
            problems.append(f"{table}: {source_counts.get(table)} ≠ {target_counts.get(table)}")
    for table in sorted(source_counts):
        print(f"   {table:20} {source_counts[table]:>8} → {target_counts.get(table)}")
    if problems:
        print("❌ Разлики: " + "; ".join(problems))
        return 1
    print(f"✅ Всички {len(source_counts)} таблици съвпадат; версия на миграциите {target_version}")
    return 0


if __name__ == "__main__":
    if len(sys.argv) != 3:
        print(__doc__)
        sys.exit(64)
    sys.exit(main(sys.argv[1], sys.argv[2]))
