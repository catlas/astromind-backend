"""
Миграция 0009: подаръчният дял на акаунтите отпреди баланса в евро се довежда до 5,00 €.

Тестът създава база в ревизия 0008, слага акаунти от различен вид и мигрира до последната ревизия. Сверява подаръка,
внесените средства и регистъра на всеки акаунт. По подразбиране върви на временна SQLite; с MIGRATION_TEST_DATABASE_URL
върви на Postgres (виж test_euro_balance_migration.py).

Пускане: python -m unittest discover -s tests -p "test_legacy_gift_topup.py"
"""
import unittest

import testenv  # noqa: F401  (подготвя пътищата)
import sqlalchemy as sa  # noqa: E402
from alembic import command  # noqa: E402

from test_euro_balance_migration import alembic_config, make_engine  # noqa: E402

# (id, имейл, внесени, подарък, [(reason, delta, delta_gift)] в регистъра)
USERS = [
    # стар акаунт само със старите безплатни монети (1,00 € подарък)
    (1, "old-bonus@x.bg", 0, 100, [("signup_bonus", 100, 100)]),
    # стар купувач: внесени средства, без подарък
    (2, "old-buyer@x.bg", 340, 0, [("purchase", 340, 0)]),
    # стар акаунт с подарък над 5 € (корекция от админ): не се пипа
    (3, "old-rich@x.bg", 0, 700, [("admin", 700, 700)]),
    # нов акаунт със signup_gift, който е похарчил част от подаръка: не се пипа
    (4, "new-spent@x.bg", 0, 340, [("signup_gift", 500, 500), ("analysis", -160, -160)]),
    # нов акаунт, който е похарчил целия подарък: не получава втори
    (5, "new-empty@x.bg", 0, 0, [("signup_gift", 500, 500), ("analysis", -500, -500)]),
    # стар акаунт без нито един ред в регистъра
    (6, "old-nothing@x.bg", 0, 0, []),
    # стар акаунт, който е точно на 5,00 €: не се пипа
    (7, "old-exact@x.bg", 0, 500, [("opening_balance", 500, 500)]),
]


class LegacyGiftTopupTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.engine = make_engine("topup")
        with cls.engine.begin() as conn:
            command.upgrade(alembic_config(conn), "0008_euro_balance")
            for uid, email, paid, gift, rows in USERS:
                conn.execute(sa.text("INSERT INTO users (id, email, hashed_password, full_name, paid_cents, gift_cents) "
                                     "VALUES (:id, :email, 'x', 'Акаунт', :paid, :gift)"),
                             {"id": uid, "email": email, "paid": paid, "gift": gift})
                balance = gift_after = 0
                for i, (reason, delta, delta_gift) in enumerate(rows):
                    balance += delta
                    gift_after += delta_gift
                    conn.execute(sa.text(
                        "INSERT INTO coin_transactions (user_id, delta, delta_gift, balance_after, gift_after, reason, ref) "
                        "VALUES (:u, :d, :dg, :b, :g, :r, :ref)"),
                        {"u": uid, "d": delta, "dg": delta_gift, "b": balance, "g": gift_after, "r": reason, "ref": f"seed:{uid}:{i}"})
            command.upgrade(alembic_config(conn), "head")

    def balances(self):
        with self.engine.connect() as conn:
            return {r[0]: (r[1], r[2]) for r in conn.execute(sa.text("SELECT id, paid_cents, gift_cents FROM users ORDER BY id"))}

    def test_old_accounts_are_brought_to_five_euros_of_gift(self):
        self.assertEqual(self.balances(), {
            1: (0, 500),        # 1,00 € → 5,00 €
            2: (340, 500),      # внесените средства остават, подаръкът е 5,00 €
            3: (0, 700),        # над 5 €: без промяна
            4: (0, 340),        # нов акаунт: без промяна
            5: (0, 0),          # нов акаунт, похарчил подаръка: без втори подарък
            6: (0, 500),        # стар акаунт без регистър
            7: (0, 500),        # точно 5 €: без промяна
        })

    def test_the_ledger_has_one_row_per_topped_up_account_and_sums_match(self):
        with self.engine.connect() as conn:
            topups = conn.execute(sa.text("SELECT user_id, delta, delta_gift, balance_after, gift_after, ref FROM coin_transactions "
                                          "WHERE reason = 'legacy_gift_topup' ORDER BY user_id")).fetchall()
            self.assertEqual([(r[0], r[1]) for r in topups], [(1, 400), (2, 500), (6, 500)])
            self.assertEqual([(r[3], r[4]) for r in topups], [(500, 500), (840, 500), (500, 500)])
            self.assertEqual([r[5] for r in topups], ["legacy-gift-topup:1", "legacy-gift-topup:2", "legacy-gift-topup:6"])
            for uid, (paid, gift) in self.balances().items():
                total, total_gift = conn.execute(sa.text(
                    "SELECT COALESCE(SUM(delta), 0), COALESCE(SUM(delta_gift), 0) FROM coin_transactions WHERE user_id = :u"), {"u": uid}).one()
                if uid == 3:
                    self.assertEqual((total, total_gift), (700, 700), uid)
                else:
                    self.assertEqual((total, total_gift), (paid + gift, gift), uid)

    def test_running_the_migration_again_does_not_grant_twice(self):
        # Повторно пускане на тялото на миграцията: всички акаунти вече са на 5 €, не се добавя нищо
        import importlib.util
        from pathlib import Path
        path = Path(__file__).resolve().parent.parent / "migrations" / "versions" / "0009_legacy_gift_topup.py"
        spec = importlib.util.spec_from_file_location("legacy_gift_topup", path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        before = self.balances()
        with self.engine.begin() as conn:
            from alembic.runtime.migration import MigrationContext
            from alembic.operations import Operations
            with Operations.context(MigrationContext.configure(conn)):
                module.upgrade()
        self.assertEqual(self.balances(), before)
        with self.engine.connect() as conn:
            count = conn.execute(sa.text("SELECT COUNT(*) FROM coin_transactions WHERE reason = 'legacy_gift_topup'")).scalar()
        self.assertEqual(count, 3)


if __name__ == "__main__":
    unittest.main()
