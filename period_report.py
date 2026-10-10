"""
Прогноза за период: месеците и общият преглед (Фаза 9).

Един общ ред на работата за стрийминга и за обикновената заявка:
1. календарът се изчислява веднъж за целия период (scanner.py);
2. всеки месец е отделна AI заявка върху календара на месеца;
3. накрая общ преглед на периода обединява месеците в един съгласуван план.

Провален месец или общ преглед (след един автоматичен повторен опит) означава неуспешен отчет: хвърля се
ForecastGenerationError, а извикващият не записва отчета и не взема средства от баланса. Преди Фаза 9 грешката влизаше в отчета
като текст "*Грешка при генериране...*", а отчетът пак се записваше и таксуваше.
"""
import asyncio
import dataclasses
from datetime import datetime
from typing import Any, AsyncIterator, Dict, List, Optional, Tuple

import pytz

import factpack
import safety
from scanner import PeriodCalendar

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


async def run_period_report(
    interpreter,
    *,
    calendar: PeriodCalendar,
    natal_chart: Dict,
    partner_chart: Optional[Dict],
    report_type: str,
    user_name: Optional[str],
    partner_name: Optional[str],
    question: str,
    gender: Optional[str] = None,
    partner_gender: Optional[str] = None,
    language: str = "bg",
    report_date: Optional[str] = None,
    retry_pause: Optional[float] = None,
) -> AsyncIterator[Dict[str, Any]]:
    """
    Стъпките на отчета като събития: month_start, month_complete (за всеки месец), overview_start,
    overview_complete и накрая finished (със събраните текстове). Хвърля ForecastGenerationError.
    """
    has_partner = partner_chart is not None
    user_display = factpack.display_name(user_name, factpack.FIRST_PERSON_DEFAULT)
    partner_display = factpack.display_name(partner_name, factpack.SECOND_PERSON_DEFAULT)
    months = calendar.months_with_events()
    report_date = report_date or today_in_zone(calendar.timezone)
    retry_pause = RETRY_PAUSE_SECONDS if retry_pause is None else retry_pause
    period = (calendar.start, calendar.end)

    # Фактите за проверката на готовите текстове (Фаза 10): строят се веднъж; при грешка текстовете минават непроверени
    facts = None
    if language == "bg":
        facts = interpreter.build_text_facts(mode="period", user_name=user_name, natal_chart=natal_chart,
                                             partner_name=partner_name, partner_chart=partner_chart,
                                             calendar=calendar, report_date=report_date)
    overview_facts = dataclasses.replace(facts, mode="overview") if facts is not None else None

    checks: List[Dict] = []
    flagged: List[str] = []
    month_texts: List[Tuple[str, str]] = []
    try:
        for idx, month in enumerate(months):
            title = month_title(month)
            yield {"type": "month_start", "month": title, "index": idx, "total": len(months)}

            async def month_call(m=month) -> str:
                raw = await interpreter._process_monthly_chunk(
                    month=m,
                    monthly_events=calendar.month_events(m),
                    report_type=report_type,
                    language=language,
                    natal_chart=natal_chart,
                    partner_chart=partner_chart,
                    user_display_name=user_display,
                    partner_display_name=partner_display,
                    question=question,
                    has_partner=has_partner,
                    gender=gender,
                    partner_gender=partner_gender,
                    zone=calendar.timezone,
                    report_date=report_date,
                    period=period)
                if not (raw and raw.strip()):
                    return raw
                return await interpreter.guard_text(raw, facts, stage=f"month:{m}", checks=checks)

            text = await _attempt(f"month:{month}", month_call, retry_pause)
            text, flags = safety.check_output(text, report_type)
            flagged.extend(flags)
            month_texts.append((title, text))
            yield {"type": "month_complete", "month": title, "text": text, "index": idx, "total": len(months)}
            await asyncio.sleep(0.1)

        yield {"type": "overview_start", "title": OVERVIEW_TITLE}

        async def overview_call() -> str:
            raw = await interpreter.compose_period_overview(
                calendar_rows=calendar.public_events(),
                month_texts=month_texts,
                report_type=report_type,
                user_display_name=user_display,
                partner_display_name=partner_display,
                has_partner=has_partner,
                question=question,
                gender=gender,
                partner_gender=partner_gender,
                zone=calendar.timezone,
                report_date=report_date,
                period=period)
            if not (raw and raw.strip()):
                return raw
            return await interpreter.guard_text(raw, overview_facts, stage="overview", checks=checks, max_tokens=4000)

        overview = await _attempt("overview", overview_call, retry_pause)
    except ForecastGenerationError as failure:
        failure.checks = checks
        raise
    overview, flags = safety.check_output(overview, report_type)
    flagged.extend(flags)
    yield {"type": "overview_complete", "title": OVERVIEW_TITLE, "text": overview}
    yield {"type": "finished", "overview": overview, "month_texts": month_texts, "flags": sorted(set(flagged)),
           "months": months, "checks": checks}


def saved_content(overview: str, month_texts: List[Tuple[str, str]], note: str = "") -> str:
    """Текстът за История: общият преглед е първи, после месеците (както досега: <h2> за всеки месец).
    note е бележката за неизвестен час на раждане (Фаза 12): стои в началото на прегледа."""
    lead = f"{note}\n\n" if note else ""
    parts = [f"<h2>{OVERVIEW_TITLE}</h2>\n{lead}{overview}"]
    parts += [f"<h2>{title}</h2>\n{text}" for title, text in month_texts]
    return "\n\n".join(parts)


def markdown_report(overview: str, month_texts: List[Tuple[str, str]], *, has_partner: bool, question: str,
                    user_display: str, partner_display: str) -> str:
    """Целият отчет като един текст (обикновената заявка, без стрийминг)."""
    first, last = month_texts[0][0], month_texts[-1][0]
    title = "Прогноза за Връзка" if has_partner else "Астрологична Прогноза"
    text = f"# {title} ({first} - {last})\n\n"
    if question:
        text += f"**Въпрос:** {question}\n\n"
    if has_partner:
        text += f"**Анализ за {user_display} и {partner_display}**\n\n"
    text += f"---\n\n## {OVERVIEW_TITLE}\n\n{overview}\n\n---\n"
    for month_title_text, month_text in month_texts:
        text += f"\n\n## Прогноза за {month_title_text}\n\n{month_text}\n\n---\n"
    return text
