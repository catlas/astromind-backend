"""Баланс в евро вместо AstroМонети: два дяла (внесени средства и подарък) в евроценти.

Старата монета е 0,10 €. Всеки ред от регистъра се преобразува (delta и balance_after се умножават по 10) и се пуска
отново по новите правила, за да получи разделянето на подарък и внесени средства:
- signup_bonus, opening_balance и всяко друго положително движение без покупка са подарък;
- purchase (покупка) са внесени средства;
- харчене (анализ) първо взема от подаръка, после от внесените средства;
- refund (възстановена покупка) първо маха от внесените средства.
Така потребителите, които са купували монети, не губят платеното, а останалите имат подаръчен баланс. Накрая
users.coins, purchases.coins и reports.coins се махат.

Revision ID: 0008_euro_balance
Revises: 0007_terms
"""
from alembic import op
import sqlalchemy as sa

revision = "0008_euro_balance"
down_revision = "0007_terms"
branch_labels = None
depends_on = None

CENTS_PER_COIN = 10


def _replay(rows):
    """rows: [(id, delta_coins, reason)] за един потребител по ред на id. Връща [(id, delta, delta_gift, balance, gift)] и (paid, gift)."""
    paid = gift = 0
    out = []
    for tx_id, coins, reason in rows:
        cents = int(coins) * CENTS_PER_COIN
        if cents >= 0:
            if reason == "purchase":
                paid += cents
                delta_gift = 0
            else:
                gift += cents
                delta_gift = cents
        else:
            spend = -cents
            if reason == "refund":
                from_paid = min(paid, spend)
                from_gift = min(gift, spend - from_paid)
            else:
                from_gift = min(gift, spend)
                from_paid = min(paid, spend - from_gift)
            paid -= from_paid
            gift -= from_gift
            delta_gift = -from_gift
        out.append((tx_id, cents, delta_gift, paid + gift, gift))
    return out, (paid, gift)


def upgrade():
    bind = op.get_bind()
    op.add_column("users", sa.Column("paid_cents", sa.Integer(), nullable=False, server_default="0"))
    op.add_column("users", sa.Column("gift_cents", sa.Integer(), nullable=False, server_default="0"))
    op.add_column("coin_transactions", sa.Column("delta_gift", sa.Integer(), nullable=False, server_default="0"))
    op.add_column("coin_transactions", sa.Column("gift_after", sa.Integer(), nullable=False, server_default="0"))
    op.add_column("purchases", sa.Column("credit_cents", sa.Integer(), nullable=False, server_default="0"))
    op.add_column("reports", sa.Column("cost_cents", sa.Integer(), nullable=False, server_default="0"))

    ledger = bind.execute(sa.text(
        "SELECT id, user_id, delta, reason FROM coin_transactions ORDER BY user_id, id")).fetchall()
    per_user = {}
    for tx_id, user_id, delta, reason in ledger:
        per_user.setdefault(user_id, []).append((tx_id, delta, reason))

    balances = {uid: (coins or 0) for uid, coins in bind.execute(sa.text("SELECT id, coins FROM users")).fetchall()}
    update_tx = sa.text("UPDATE coin_transactions SET delta = :delta, delta_gift = :delta_gift, "
                        "balance_after = :balance, gift_after = :gift WHERE id = :id")
    update_user = sa.text("UPDATE users SET paid_cents = :paid, gift_cents = :gift WHERE id = :id")

    for user_id, coins in balances.items():
        rows, (paid, gift) = _replay(per_user.get(user_id, []))
        for tx_id, delta, delta_gift, balance, gift_after in rows:
            bind.execute(update_tx, {"id": tx_id, "delta": delta, "delta_gift": delta_gift, "balance": balance, "gift": gift_after})
        expected = int(coins) * CENTS_PER_COIN
        if paid + gift != expected:
            # Регистърът не съвпада с баланса (не би трябвало): вярваме на баланса; внесеното не надхвърля общото
            paid = max(0, min(paid, expected))
            gift = expected - paid
        bind.execute(update_user, {"id": user_id, "paid": paid, "gift": gift})

    op.execute(f"UPDATE purchases SET credit_cents = coins * {CENTS_PER_COIN}")
    op.execute(f"UPDATE reports SET cost_cents = coins * {CENTS_PER_COIN}")

    with op.batch_alter_table("users") as batch:
        batch.drop_column("coins")
    with op.batch_alter_table("purchases") as batch:
        batch.drop_column("coins")
    with op.batch_alter_table("reports") as batch:
        batch.drop_column("coins")


def downgrade():
    op.add_column("users", sa.Column("coins", sa.Integer(), nullable=True, server_default="0"))
    op.add_column("purchases", sa.Column("coins", sa.Integer(), nullable=False, server_default="0"))
    op.add_column("reports", sa.Column("coins", sa.Integer(), nullable=False, server_default="0"))
    op.execute(f"UPDATE users SET coins = (paid_cents + gift_cents) / {CENTS_PER_COIN}")
    op.execute(f"UPDATE purchases SET coins = credit_cents / {CENTS_PER_COIN}")
    op.execute(f"UPDATE reports SET coins = cost_cents / {CENTS_PER_COIN}")
    op.execute(f"UPDATE coin_transactions SET delta = delta / {CENTS_PER_COIN}, balance_after = balance_after / {CENTS_PER_COIN}")
    with op.batch_alter_table("coin_transactions") as batch:
        batch.drop_column("gift_after")
        batch.drop_column("delta_gift")
    with op.batch_alter_table("users") as batch:
        batch.drop_column("gift_cents")
        batch.drop_column("paid_cents")
    with op.batch_alter_table("purchases") as batch:
        batch.drop_column("credit_cents")
    with op.batch_alter_table("reports") as batch:
        batch.drop_column("cost_cents")
