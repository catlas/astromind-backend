"""
Миграция 0008: монетите стават евроценти (1 монета = 0,10 €) и балансът се дели на подарък и внесени средства.
Подаръкът за старите акаунти (0009) има свой тест: test_legacy_gift_topup.py.

Тестът създава база с реалната структура от ревизия 0007, слага стари данни (покупки, харчене, възстановяване, баланс,
който не съвпада с регистъра), мигрира до последната ревизия и сверява всеки потребител. После връща назад.

По подразбиране върви на временна SQLite. С MIGRATION_TEST_DATABASE_URL=postgresql+psycopg2://...@host:port/име върви на
Postgres, както е в продукция (създават се бази <име>_legacy и <име>_downgrade, които се трият първо).

Пускане: python -m unittest discover -s tests -p "test_euro_balance_migration.py"
"""
import os
import tempfile
import unittest
from pathlib import Path

import testenv  # noqa: F401  (подготвя пътищата)
import sqlalchemy as sa  # noqa: E402
from alembic import command  # noqa: E402
from alembic.config import Config  # noqa: E402

MIGRATIONS = Path(__file__).resolve().parent.parent / "migrations"


def make_engine(name="legacy"):
    """Нова празна база. На Postgres: отделна база с име <име в URL>_<name>, за да не си пречат тестовете."""
    url = os.getenv("MIGRATION_TEST_DATABASE_URL")
    if url:
        base = sa.engine.make_url(url)
        database = f"{base.database}_{name}"
        admin = sa.create_engine(base.set(database="postgres"), isolation_level="AUTOCOMMIT")
        with admin.connect() as conn:
            conn.execute(sa.text(f'DROP DATABASE IF EXISTS "{database}" WITH (FORCE)'))
            conn.execute(sa.text(f'CREATE DATABASE "{database}"'))
        admin.dispose()
        return sa.create_engine(base.set(database=database))
    path = os.path.join(tempfile.mkdtemp(), f"{name}.db")
    return sa.create_engine(f"sqlite:///{path}")


def alembic_config(connection):
    cfg = Config()
    cfg.set_main_option("script_location", str(MIGRATIONS))
    cfg.attributes["connection"] = connection
    return cfg


# (user_id, email, coins в users, [(delta, reason), ...] в регистъра)
LEGACY_USERS = [
    (1, "bonus@legacy.bg", 10, [(10, "signup_bonus")]),
    # подарък 10, покупка 50, два анализа по 8, възстановяване на 10: харчи първо подаръка, възстановяването маха внесените
    (2, "buyer@legacy.bg", 34, [(10, "opening_balance"), (50, "purchase"), (-8, "analysis"), (-8, "analysis"), (-10, "refund")]),
    (3, "paid-only@legacy.bg", 45, [(50, "purchase"), (-5, "analysis")]),
    (4, "empty@legacy.bg", 0, []),
    # балансът (7 монети) не съвпада с регистъра (5): вярваме на баланса
    (5, "broken@legacy.bg", 7, [(5, "signup_bonus")]),
    (6, "spent-all@legacy.bg", 0, [(10, "signup_bonus"), (-8, "analysis"), (-2, "analysis")]),
]


class EuroBalanceMigrationTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.engine = make_engine("legacy")
        with cls.engine.begin() as conn:
            command.upgrade(alembic_config(conn), "0007_terms")
            for uid, email, coins, rows in LEGACY_USERS:
                conn.execute(sa.text("INSERT INTO users (id, email, hashed_password, full_name, coins) "
                                     "VALUES (:id, :email, 'x', 'Стар', :coins)"), {"id": uid, "email": email, "coins": coins})
                balance = 0
                for i, (delta, reason) in enumerate(rows):
                    balance += delta
                    conn.execute(sa.text("INSERT INTO coin_transactions (user_id, delta, balance_after, reason, ref) "
                                         "VALUES (:u, :d, :b, :r, :ref)"),
                                 {"u": uid, "d": delta, "b": balance, "r": reason, "ref": f"legacy:{uid}:{i}"})
            conn.execute(sa.text("INSERT INTO purchases (id, user_id, package_id, coins, amount_cents, currency, status, refunded_cents) "
                                 "VALUES (1, 2, 'starter', 50, 499, 'eur', 'paid', 0)"))
            conn.execute(sa.text("INSERT INTO reports (id, user_id, report_type, label, content, coins, status) "
                                 "VALUES (1, 2, 'general', 'Анализ', '<p>x</p>', 8, 'completed')"))
            conn.execute(sa.text("INSERT INTO reports (id, user_id, report_type, label, content, coins, status) "
                                 "VALUES (2, 3, 'love', 'Анализ', '<p>y</p>', 0, 'completed')"))
            command.upgrade(alembic_config(conn), "0008_euro_balance")

    def row(self, sql, **params):
        with self.engine.connect() as conn:
            return conn.execute(sa.text(sql), params).fetchall()

    def balances(self):
        return {r[0]: (r[1], r[2]) for r in self.row("SELECT id, paid_cents, gift_cents FROM users ORDER BY id")}

    def test_each_user_gets_the_right_split(self):
        self.assertEqual(self.balances(), {
            1: (0, 100),        # само подарък (10 монети = 1,00 €)
            2: (340, 0),        # платеното остава внесено: подаръкът е изхарчен първо, възстановяването е взело от внесените
            3: (450, 0),        # само покупка
            4: (0, 0),
            5: (0, 70),         # регистърът не съвпада: вярваме на баланса, като подарък
            6: (0, 0),
        })

    def test_the_ledger_is_converted_and_sums_match_the_balances(self):
        for uid, (paid, gift) in self.balances().items():
            rows = self.row("SELECT delta, delta_gift, balance_after, gift_after FROM coin_transactions "
                            "WHERE user_id = :u ORDER BY id", u=uid)
            if uid == 5:
                continue            # за него регистърът не съвпада с баланса още от стария вид
            self.assertEqual(sum(r[0] for r in rows), paid + gift, uid)
            self.assertEqual(sum(r[1] for r in rows), gift, uid)
            if rows:
                self.assertEqual((rows[-1][2], rows[-1][3]), (paid + gift, gift), uid)

    def test_user_2_ledger_row_by_row(self):
        rows = self.row("SELECT reason, delta, delta_gift, balance_after, gift_after FROM coin_transactions "
                        "WHERE user_id = 2 ORDER BY id")
        self.assertEqual(rows, [
            ("opening_balance", 100, 100, 100, 100),
            ("purchase", 500, 0, 600, 100),
            ("analysis", -80, -80, 520, 20),          # подаръкът първо
            ("analysis", -80, -20, 440, 0),           # останалите 20 цента подарък + 60 от внесените
            ("refund", -100, 0, 340, 0),
        ])

    def test_purchases_and_reports_are_converted(self):
        self.assertEqual(self.row("SELECT credit_cents, amount_cents FROM purchases WHERE id = 1"), [(500, 499)])
        self.assertEqual(self.row("SELECT id, cost_cents FROM reports ORDER BY id"), [(1, 80), (2, 0)])

    def test_the_coin_columns_are_gone(self):
        inspector = sa.inspect(self.engine)
        for table in ("users", "purchases", "reports"):
            self.assertNotIn("coins", {c["name"] for c in inspector.get_columns(table)}, table)
        self.assertEqual({c["name"] for c in inspector.get_columns("coin_transactions")} >= {"delta_gift", "gift_after"}, True)

    def test_downgrade_restores_the_coins(self):
        engine = make_engine("downgrade")
        with engine.begin() as conn:
            command.upgrade(alembic_config(conn), "0007_terms")
            conn.execute(sa.text("INSERT INTO users (id, email, hashed_password, full_name, coins) VALUES (1, 'a@legacy.bg', 'x', 'А', 34)"))
            conn.execute(sa.text("INSERT INTO coin_transactions (user_id, delta, balance_after, reason, ref) "
                                 "VALUES (1, 34, 34, 'purchase', 'legacy:1')"))
            conn.execute(sa.text("INSERT INTO reports (id, user_id, report_type, label, content, coins, status) "
                                 "VALUES (1, 1, 'general', 'А', 'x', 8, 'completed')"))
            command.upgrade(alembic_config(conn), "0008_euro_balance")
            command.downgrade(alembic_config(conn), "0007_terms")
            self.assertEqual(conn.execute(sa.text("SELECT coins FROM users WHERE id = 1")).scalar(), 34)
            self.assertEqual(conn.execute(sa.text("SELECT delta, balance_after FROM coin_transactions")).fetchall(), [(34, 34)])
            self.assertEqual(conn.execute(sa.text("SELECT coins FROM reports WHERE id = 1")).scalar(), 8)


if __name__ == "__main__":
    unittest.main()
