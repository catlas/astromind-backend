"""Работник за теста с два процеса (test_phase25_ledger.py): чака бариера и после се опитва да резервира. Не е тест."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import testenv  # noqa: F401,E402  (пътищата; базата идва от средата на родителя)


def run(barrier, attempts, out):
    os.environ.update({"AI_DAILY_BUDGET_CENTS": "1", "AI_MONTHLY_BUDGET_CENTS": "100000", "AI_PRICE_INPUT_CENTS_PER_MTOK": "0",
                       "AI_PRICE_OUTPUT_CENTS_PER_MTOK": "1000"})
    import ai_budget
    barrier.wait(timeout=60)                           # и двата процеса тръгват едновременно
    granted = 0
    for _ in range(attempts):
        try:
            if ai_budget.reserve(0, 400) is not None:
                granted += 1
        except ai_budget.BudgetExceeded:
            pass
    out.put(granted)
