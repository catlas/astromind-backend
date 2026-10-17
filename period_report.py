"""
Прогноза за период: общите части на отчета (Фаза 9; ходът по карти е в period_cards.py).

Тук са само това, което ползват генерацията и period_cards.py: ForecastGenerationError (провален месец или общ преглед след един
автоматичен повторен опит: извикващият не записва отчета и не взема средства от баланса), заглавията на месеците, „днес“ в зоната на
календара, един автоматичен повторен опит на AI заявка (_attempt) и записът на текста за История (saved_content).

Старият ход (свободен текст на AI за всеки месец, общ преглед и последваща поправка) е махнат на 17.10.2026 и е архивиран:
backend/archive/legacy_period_flow_2026-10-12.txt и git тагът archive/legacy-period-flow-2026-10-12.
"""
import asyncio
from datetime import datetime
from typing import Dict, List, Optional, Tuple

import pytz

OVERVIEW_TITLE = "Общ преглед на периода"
RETRY_PAUSE_SECONDS = 3.0      # пауза преди автоматичния повторен опит
USER_MESSAGE = "Не успяхме да завършим прогнозата. Нищо не е записано и не е таксувано. Опитайте отново след малко."
MONTH_NAMES = {
    "01": "Януари", "02": "Февруари", "03": "Март", "04": "Април", "05": "Май", "06": "Юни",
    "07": "Юли", "08": "Август", "09": "Септември", "10": "Октомври", "11": "Ноември", "12": "Декември",
}


class ForecastGenerationError(Exception):
    """Месец или общият преглед не можаха да се генерират. stage: "month:YYYY-MM" или "overview".
    cause: последната грешка (при отхвърлен от проверката текст това е text_guard.TextCheckError);
    checks: описанията на проверките на текстовете до провала (без текст на анализа)."""

    def __init__(self, stage: str, cause: Optional[BaseException] = None, checks: Optional[List[Dict]] = None):
        label = type(cause).__name__ if cause else "празен отговор"
        super().__init__(f"{stage}: {label}")
        self.stage = stage
        self.cause = cause
        self.checks: List[Dict] = list(checks or [])


def month_title(month: str) -> str:
    """"2026-10" -> "Октомври 2026"."""
    return f"{MONTH_NAMES.get(month[5:7], month[5:7])} {month[:4]}"


def today_in_zone(zone: str) -> str:
    """Днешната дата в зоната на календара ("YYYY-MM-DD")."""
    try:
        return datetime.now(pytz.timezone(zone)).strftime("%Y-%m-%d")
    except Exception:
        return datetime.utcnow().strftime("%Y-%m-%d")


async def _attempt(stage: str, make_call, retry_pause: float) -> str:
    """Един опит и един автоматичен повторен опит. Празен отговор се брои за неуспех."""
    last: Optional[BaseException] = None
    for attempt in (1, 2):
        try:
            text = await make_call()
            if text and text.strip():
                return text
            last = None
        except Exception as exc:  # провайдърите хвърлят RuntimeError, httpx и др.
            last = exc
        detail = f"{type(last).__name__}: {str(last)[:160]}" if last else "празен отговор"
        print(f"⚠️ Прогноза, стъпка {stage}: опит {attempt}/2 неуспешен ({detail})")
        if attempt == 1:
            await asyncio.sleep(retry_pause)
    raise ForecastGenerationError(stage, last)


def saved_content(overview: str, month_texts: List[Tuple[str, str]], note: str = "") -> str:
    """Текстът за История: общият преглед е първи, после месеците (както досега: <h2> за всеки месец).
    note е бележката за неизвестен час на раждане (Фаза 12): стои в началото на прегледа."""
    lead = f"{note}\n\n" if note else ""
    parts = [f"<h2>{OVERVIEW_TITLE}</h2>\n{lead}{overview}"]
    parts += [f"<h2>{title}</h2>\n{text}" for title, text in month_texts]
    return "\n\n".join(parts)
