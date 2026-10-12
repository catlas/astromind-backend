"""
Проверка на готовия текст преди показване (Фаза 10).

Готовият AI текст се сверява с изчислените факти (text_check.py). При тежко нарушение се правят най-много два опита за
поправка: AI получава текста и списък от грешки с верните стойности, а вторият опит - останалите грешки. Ако и след тях
остане тежко нарушение, текстът се отхвърля (TextCheckError): извикващият не записва отчета и не взема средства от баланса.

Настройки (променливи на средата, четат се при всяка проверка):
- TEXT_CHECK_MODE: "enforce" (по подразбиране) | "warn" (само отчита, нищо не поправя и не отхвърля) | "off" (само маха
  служебните етикети). Неразпознат режим означава "enforce".
- TEXT_CHECK_IGNORE: кодове на нарушения, които се пренебрегват, например "date,event" (бърза спасителна ръчка, ако
  някой вид проверка дава фалшиви тревоги, без нов деплой на кода).

Липсващи факти (MissingFacts, R01): в "enforce" анализът се отказва ПРЕДИ разход на AI заявка (код "facts_unavailable"); None
означава „проверката не се прилага“ (текст на друг език). В "warn" и "off" текстът минава и причината се записва в телеметрията.

Грешка или забавяне на самия проверител: в "enforce" текстът се отхвърля (нищо не се записва и не се таксува, код "checker"),
защото непроверен текст не се доставя като проверен; в "warn" и "off" текстът минава и грешката се записва в телеметрията.
В телеметрията влизат само етап, причина, кодове и броеве, никога текст на анализа.
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
MAX_REPAIR_ATTEMPTS = 2          # втори опит, когато след първата поправка са останали тежки нарушения (проверката остава същата)
MIN_REPAIRED_RATIO = 0.6           # поправеният текст по-къс от 60% от оригинала е отрязан или обобщен, не поправен
REPAIR_TEMPERATURE = 0.2

USER_MESSAGE = "Не успяхме да завършим анализа. Нищо не е записано и не е таксувано. Опитайте отново след малко."

RepairCall = Callable[[str, str], Awaitable[str]]


@dataclass(frozen=True)
class MissingFacts:
    """Фактите за проверка не можаха да се построят. reason е кратка причина без лични данни (напр. cusps_missing:partner)."""
    reason: str = "facts_error"


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
    error: Optional[str] = None                        # грешка в проверителя (в enforce текстът се отхвърля, иначе минава непроверен)
    reason: str = ""                                   # защо е отхвърлен: facts_unavailable | checker_error | repair_unavailable | violations_remain

    @property
    def hard_before(self) -> List[Violation]:
        return text_check.hard(self.before)

    @property
    def hard_after(self) -> List[Violation]:
        return text_check.hard(self.after)

    def codes(self) -> str:
        """Кодовете на тежките нарушения (при отхвърляне: тези, които са останали след поправката), най-много 60 знака
        (границата на свойствата на събитие)."""
        if self.reason == "facts_unavailable":
            return "facts"
        if self.error and self.rejected:
            return "checker"
        seen: List[str] = []
        for v in ((self.hard_after if self.rejected else self.hard_before) or self.before):
            if v.code not in seen:
                seen.append(v.code)
        return ",".join(seen)[:60]

    @property
    def result(self) -> str:
        if self.rejected:
            return "rejected"
        if self.error:
            return "error"
        if self.repaired:
            return "repaired"
        if self.mode == MODE_WARN and self.hard_before:
            return "warned"
        if self.cleaned:
            return "cleaned"
        return "ok"

    def summary(self) -> Dict[str, Any]:
        return {"stage": self.stage[:40], "mode": self.mode, "result": self.result, "cleaned": self.cleaned,
                "hard_before": len(self.hard_before), "hard_after": len(self.hard_after), "codes": self.codes(),
                "reason": self.reason}


def _safe_detail(detail: str, facts) -> str:
    """Какво не е наред, без имената на хората (само планети, знаци, аспекти, градуси и дати): за диагностиката при отказ."""
    names = getattr(facts, "names", None) or {}
    safe = detail or ""
    for full in names.values():
        for token in sorted({str(full), *str(full).replace("—", " ").replace("-", " ").split()}, key=len, reverse=True):
            if len(token.strip()) >= 3:
                safe = re.sub(re.escape(token.strip()), "[име]", safe, flags=re.IGNORECASE)
    return " ".join(safe.split())[:150]


def _log_remaining(stage: str, violations: Sequence[Violation], facts) -> None:
    """Остатъчните тежки нарушения при отказ: код и верните стойности, без текст на анализа и без имена (за да се види дали грешката е в текста или в проверката)."""
    for v in violations[:4]:
        print(f"🔎 Остава ({stage}): {v.code}: {_safe_detail(v.detail, facts)}")


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
        "- Exception for completeness items: when an item says a required section is missing or the text is cut off, write the missing "
        "part or finish the last sentence using ONLY facts that are already stated in the text; add no new date, aspect, house or sign.\n"
        "- Items about health: never delay medical help because of an astrological period; keep practical habits (rest, sleep, routine).\n"
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


ExtraCheck = Callable[[str], List[Violation]]


def _check_sync(text: str, facts: Facts, ignore: Set[str], extra: Optional[ExtraCheck] = None) -> List[Violation]:
    found = text_check.check_text(text, facts) + (extra(text) if extra else [])
    return [v for v in found if v.code not in ignore]


async def _check(text: str, facts: Facts, ignore: Set[str], extra: Optional[ExtraCheck] = None) -> List[Violation]:
    """Проверката е чист CPU код: върви в отделна нишка, за да не блокира сървъра, и има таван на времето.
    extra е допълнителна проверка на кандидат-текста (например съгласуваност с другите части на отчета)."""
    return await asyncio.wait_for(asyncio.to_thread(_check_sync, text, facts, ignore, extra), timeout=CHECK_TIMEOUT_SECONDS)


def refuse_missing_facts(facts: Any, *, stage: str, mode: Optional[str] = None, text: str = "") -> Optional[GuardOutcome]:
    """MissingFacts в „enforce“ -> TextCheckError (reason facts_unavailable); в другите режими връща описание за телеметрията.
    За каквото и да е друго (Facts или None) връща None. Вика се и ПРЕДИ разход на AI заявка, и като последна защита в guard_text."""
    if not isinstance(facts, MissingFacts):
        return None
    mode = mode or check_mode()
    outcome = GuardOutcome(stage=stage, mode=mode, text=text, error=f"facts:{facts.reason}"[:80])
    if mode == MODE_ENFORCE:
        outcome.rejected, outcome.reason = True, "facts_unavailable"
        print(f"❌ Проверка на текста ({stage}): фактите липсват ({facts.reason}), анализът се отказва преди AI заявка")
        raise TextCheckError(stage, outcome)
    print(f"⚠️ Проверка на текста ({stage}): фактите липсват ({facts.reason}), текстът минава непроверен")
    return outcome


async def guard_text(text: str, facts: Any, *, stage: str, repair_call: RepairCall,
                     language_rules: str = "", mode: Optional[str] = None, extra_check: Optional[ExtraCheck] = None) -> GuardOutcome:
    """Проверява текста срещу фактите. Връща GuardOutcome (с поправения текст) или хвърля TextCheckError."""
    mode = mode or check_mode()
    cleaned_text, cleaned = text_check.clean_labels(text or "")
    outcome = GuardOutcome(stage=stage, mode=mode, text=cleaned_text, cleaned=cleaned)
    if mode != MODE_OFF:
        missing = refuse_missing_facts(facts, stage=stage, mode=mode, text=cleaned_text)
        if missing is not None:                                  # warn: текстът минава непроверен, причината остава в резюмето
            outcome.error = missing.error
            return outcome
    if mode == MODE_OFF or facts is None or isinstance(facts, MissingFacts):
        return outcome
    ignore = ignored_codes()
    try:
        outcome.before = outcome.after = await _check(cleaned_text, facts, ignore, extra_check)
    except Exception as exc:
        outcome.error = f"{type(exc).__name__}: {str(exc)[:80]}"
        if mode == MODE_ENFORCE:               # непроверен текст не се доставя като проверен
            outcome.rejected, outcome.reason = True, "checker_error"
            print(f"❌ Проверка на текста ({stage}): грешка в проверителя, анализът се отхвърля ({outcome.error})")
            raise TextCheckError(stage, outcome)
        print(f"⚠️ Проверка на текста ({stage}): грешка в проверителя, текстът минава непроверен ({outcome.error})")
        return outcome
    if not outcome.hard_before:
        return outcome
    codes = outcome.codes()
    print(f"🔎 Проверка на текста ({stage}): {len(outcome.hard_before)} тежки нарушения ({codes}), режим {mode}")
    if mode == MODE_WARN:
        return outcome

    # enforce: най-много MAX_REPAIR_ATTEMPTS опита за поправка; всеки следващ получава последния текст и грешките, които са останали
    current_text, current_violations = cleaned_text, outcome.before
    for attempt in range(1, MAX_REPAIR_ATTEMPTS + 1):
        fixed = ""
        try:
            system_prompt, user_prompt = build_repair_prompts(current_text, current_violations, language_rules)
            fixed = _strip_fences(await repair_call(system_prompt, user_prompt))
        except Exception as exc:
            print(f"⚠️ Проверка на текста ({stage}): поправката не успя ({type(exc).__name__}: {str(exc)[:120]})")
        if not (fixed and len(fixed) >= MIN_REPAIRED_RATIO * len(cleaned_text)):
            outcome.reason = "repair_unavailable"
            print(f"❌ Проверка на текста ({stage}): поправката е празна или твърде къса")
            break
        fixed, more = text_check.clean_labels(fixed)
        outcome.cleaned += more
        try:
            after = await _check(fixed, facts, ignore, extra_check)
        except Exception as exc:
            outcome.error = f"{type(exc).__name__}: {str(exc)[:80]}"
            print(f"❌ Проверка на текста ({stage}): грешка в проверителя след поправката, анализът се отхвърля ({outcome.error})")
            outcome.rejected, outcome.reason = True, "checker_error"
            raise TextCheckError(stage, outcome)
        outcome.after = after
        if not text_check.hard(after):
            outcome.text = fixed
            outcome.repaired = True
            print(f"✅ Проверка на текста ({stage}): поправен текст след {attempt}. опит, тежки нарушения 0")
            return outcome
        outcome.reason = "violations_remain"
        print(f"❌ Проверка на текста ({stage}): след {attempt}. поправка остават {len(text_check.hard(after))} тежки нарушения")
        current_text, current_violations = fixed, after
    outcome.rejected = True
    _log_remaining(stage, outcome.hard_after, facts)
    raise TextCheckError(stage, outcome)
