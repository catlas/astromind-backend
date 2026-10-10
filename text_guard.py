"""
Проверка на готовия текст преди показване (Фаза 10).

Готовият AI текст се сверява с изчислените факти (text_check.py). При тежко нарушение се прави най-много един опит за
поправка: втори AI вик получава текста и списък от грешки с верните стойности. Ако и след него остане тежко нарушение,
текстът се отхвърля (TextCheckError): извикващият не записва отчета и не взема средства от баланса.

Настройки (променливи на средата, четат се при всяка проверка):
- TEXT_CHECK_MODE: "enforce" (по подразбиране) | "warn" (само отчита, нищо не поправя и не отхвърля) | "off" (само маха
  служебните етикети). Неразпознат режим означава "enforce".
- TEXT_CHECK_IGNORE: кодове на нарушения, които се пренебрегват, например "date,event" (бърза спасителна ръчка, ако
  някой вид проверка дава фалшиви тревоги, без нов деплой на кода).

Грешка в самия проверител никога не спира отчета: текстът минава непроверен и се записва в телеметрията (fail-open).
В телеметрията влизат само етап, кодове и броеве, никога текст на анализа.
"""
import asyncio
import os
import re
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable, Dict, List, Optional, Sequence, Set

import text_check
from text_check import Facts, Violation

MODE_ENFORCE, MODE_WARN, MODE_OFF = "enforce", "warn", "off"
CHECK_TIMEOUT_SECONDS = 30.0       # дълга обработка се прекъсва и текстът минава непроверен (обичайно под 0,2 секунди)
MAX_ITEMS_IN_REPAIR = 20           # най-много толкова грешки отиват към поправката (тежките първи)
MIN_REPAIRED_RATIO = 0.6           # поправеният текст по-къс от 60% от оригинала е отрязан или обобщен, не поправен
REPAIR_TEMPERATURE = 0.2

USER_MESSAGE = "Не успяхме да завършим анализа. Нищо не е записано и не е таксувано. Опитайте отново след малко."

RepairCall = Callable[[str, str], Awaitable[str]]


def check_mode() -> str:
    mode = os.getenv("TEXT_CHECK_MODE", MODE_ENFORCE).strip().lower()
    return mode if mode in (MODE_ENFORCE, MODE_WARN, MODE_OFF) else MODE_ENFORCE


def ignored_codes() -> Set[str]:
    return {c.strip().lower() for c in os.getenv("TEXT_CHECK_IGNORE", "").split(",") if c.strip()}


@dataclass
class GuardOutcome:
    """Какво стана с един текст. summary() е безопасно за телеметрия: няма текст на анализа."""
    stage: str
    mode: str
    text: str
    cleaned: int = 0                                   # махнати служебни етикети и имена на полета
    before: List[Violation] = field(default_factory=list)
    after: List[Violation] = field(default_factory=list)
    repaired: bool = False
    rejected: bool = False
    error: Optional[str] = None                        # грешка в проверителя (текстът е минал непроверен)

    @property
    def hard_before(self) -> List[Violation]:
        return text_check.hard(self.before)

    @property
    def hard_after(self) -> List[Violation]:
        return text_check.hard(self.after)

    def codes(self) -> str:
        """Кодовете на тежките нарушения (при отхвърляне: тези, които са останали след поправката), най-много 60 знака
        (границата на свойствата на събитие)."""
        seen: List[str] = []
        for v in ((self.hard_after if self.rejected else self.hard_before) or self.before):
            if v.code not in seen:
                seen.append(v.code)
        return ",".join(seen)[:60]

    @property
    def result(self) -> str:
        if self.error:
            return "error"
        if self.rejected:
            return "rejected"
        if self.repaired:
            return "repaired"
        if self.mode == MODE_WARN and self.hard_before:
            return "warned"
        if self.cleaned:
            return "cleaned"
        return "ok"

    def summary(self) -> Dict[str, Any]:
        return {"stage": self.stage[:40], "mode": self.mode, "result": self.result, "cleaned": self.cleaned,
                "hard_before": len(self.hard_before), "hard_after": len(self.hard_after), "codes": self.codes()}


class TextCheckError(Exception):
    """Текстът има тежко нарушение и след поправката. Не се записва и не се таксува."""

    def __init__(self, stage: str, outcome: GuardOutcome):
        super().__init__(f"{stage}: {outcome.codes() or 'нарушение'}")
        self.stage = stage
        self.outcome = outcome


def build_repair_prompts(text: str, violations: Sequence[Violation], language_rules: str = "") -> tuple:
    """(системна подкана, потребителска подкана) за поправката: текстът и списък от грешки с верните стойности."""
    ordered = sorted(violations, key=lambda v: 0 if v.severity == "hard" else 1)
    unique: List[Violation] = []
    seen: Set[tuple] = set()
    for v in ordered:
        key = (v.code, v.detail)
        if key not in seen:
            seen.add(key)
            unique.append(v)
    system_prompt = (
        "MODE: FACT CORRECTION OF A FINISHED TEXT\n"
        "You are the editor of an astrology text written in Bulgarian. You receive the finished text and a numbered list of "
        "factual mistakes found by an automatic check against the calculated data. Each item quotes the passage and states "
        "the correct fact.\n\n"
        "RULES:\n"
        "- Correct every listed mistake so that the text agrees with the stated correct fact: replace the wrong house, sign, "
        "degree, aspect, date or time. If a sentence describes something that does not exist in the data (for example an "
        "aspect between two planets that have none), rewrite the sentence so that it matches the data, or remove it.\n"
        "- If the same mistake is repeated elsewhere in the text, correct it everywhere.\n"
        "- Change nothing else. Keep the structure, headings, order, tone, language, Markdown and the length as close to the "
        "original as possible. Do not add any new astrological fact, date, house, aspect or number that is not in the list.\n"
        "- Never describe an aspect as exact unless the list says it has an exact moment.\n"
        "- Do not mention the check, the list or the corrections.\n"
        "- Output the COMPLETE corrected text and nothing else (no comments, no code fences)."
    )
    if language_rules:
        system_prompt += language_rules
    user_prompt = (
        "TEXT TO CORRECT:\n<<<TEXT\n" + text.strip() + "\nTEXT>>>\n\n"
        "MISTAKES TO CORRECT (the correct values come after 'Вярно'):\n"
        + text_check.violations_for_prompt(unique, limit=MAX_ITEMS_IN_REPAIR)
        + "\n\nReturn the complete corrected text."
    )
    return system_prompt, user_prompt


def _strip_fences(text: str) -> str:
    text = (text or "").strip()
    text = re.sub(r"^```[a-zA-Z]*\s*\n", "", text)
    text = re.sub(r"\n```\s*$", "", text)
    return text.strip()


def _check_sync(text: str, facts: Facts, ignore: Set[str]) -> List[Violation]:
    return [v for v in text_check.check_text(text, facts) if v.code not in ignore]


async def _check(text: str, facts: Facts, ignore: Set[str]) -> List[Violation]:
    """Проверката е чист CPU код: върви в отделна нишка, за да не блокира сървъра, и има таван на времето."""
    return await asyncio.wait_for(asyncio.to_thread(_check_sync, text, facts, ignore), timeout=CHECK_TIMEOUT_SECONDS)


async def guard_text(text: str, facts: Optional[Facts], *, stage: str, repair_call: RepairCall,
                     language_rules: str = "", mode: Optional[str] = None) -> GuardOutcome:
    """Проверява текста срещу фактите. Връща GuardOutcome (с поправения текст) или хвърля TextCheckError."""
    mode = mode or check_mode()
    cleaned_text, cleaned = text_check.clean_labels(text or "")
    outcome = GuardOutcome(stage=stage, mode=mode, text=cleaned_text, cleaned=cleaned)
    if mode == MODE_OFF or facts is None:
        return outcome
    ignore = ignored_codes()
    try:
        outcome.before = outcome.after = await _check(cleaned_text, facts, ignore)
    except Exception as exc:               # проверителят не бива да спира отчета
        outcome.error = f"{type(exc).__name__}: {str(exc)[:80]}"
        print(f"⚠️ Проверка на текста ({stage}): грешка в проверителя, текстът минава непроверен ({outcome.error})")
        return outcome
    if not outcome.hard_before:
        return outcome
    codes = outcome.codes()
    print(f"🔎 Проверка на текста ({stage}): {len(outcome.hard_before)} тежки нарушения ({codes}), режим {mode}")
    if mode == MODE_WARN:
        return outcome

    # enforce: най-много един опит за поправка
    fixed = ""
    try:
        system_prompt, user_prompt = build_repair_prompts(cleaned_text, outcome.before, language_rules)
        fixed = _strip_fences(await repair_call(system_prompt, user_prompt))
    except Exception as exc:
        print(f"⚠️ Проверка на текста ({stage}): поправката не успя ({type(exc).__name__}: {str(exc)[:120]})")
    if fixed and len(fixed) >= MIN_REPAIRED_RATIO * len(cleaned_text):
        fixed, more = text_check.clean_labels(fixed)
        outcome.cleaned += more
        try:
            after = await _check(fixed, facts, ignore)
        except Exception as exc:
            outcome.error = f"{type(exc).__name__}: {str(exc)[:80]}"
            print(f"⚠️ Проверка на текста ({stage}): грешка в проверителя след поправката ({outcome.error})")
            outcome.text = fixed                 # поправеният текст е по-добър от оригинала с известните грешки
            return outcome
        outcome.after = after
        if not text_check.hard(after):
            outcome.text = fixed
            outcome.repaired = True
            print(f"✅ Проверка на текста ({stage}): поправен текст, тежки нарушения 0")
            return outcome
        print(f"❌ Проверка на текста ({stage}): след поправката остават {len(text_check.hard(after))} тежки нарушения")
    else:
        print(f"❌ Проверка на текста ({stage}): поправката е празна или твърде къса")
    outcome.rejected = True
    raise TextCheckError(stage, outcome)
