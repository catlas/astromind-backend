"""
AI Интерпретатор за астрологични карти
Използва Together.ai API за анализ и интерпретация
"""

from safety import QUALITY_RULES, SAFETY_RULES
import pair_snapshot
import single_snapshot
import natal_pair
import natal_single
import memory
import os
import asyncio
import time
from typing import Dict, Optional, List, Tuple
import httpx
from dotenv import load_dotenv
from engine import AstrologyEngine
from aspects_engine import calculate_natal_aspects
import factpack
import progress
import ai_budget
import bg_text
import birthtime
import relationship
from datetime import timezone
import text_check
import text_guard

# Зареждане на environment променливи
load_dotenv()

# ---------------------------------------------------------------------------
# Общи текстове за данните към AI (Фаза 8)
# ---------------------------------------------------------------------------
NATAL_NOTE = (
    "Everything here is PRE-CALCULATED. Use 'formatted_pos', the 'house' number of each planet (its house in THIS person's "
    "natal chart) and the 'cusp' and 'ruler' of each house exactly as given. Never convert degrees to signs or houses yourself. "
    "'retrograde_planets' and 'retrograde_count' are already counted."
)
NATAL_NOTE_NO_TIME = (
    "The birth time of this person is UNKNOWN. Everything here is PRE-CALCULATED for local noon of the birth date. There are NO "
    "houses, NO Ascendant, NO MC, NO cusps and NO house rulers for this person: never write or imply any. Use "
    "'formatted_pos' exactly as given. Where 'time_dependent_signs' lists 'possible_signs', name those signs and say the sign "
    "depends on the birth time; never pick one and never give a degree for it. The Moon has no position or aspects. "
    "'retrograde_planets' and 'retrograde_count' are already counted."
)
ASPECTS_NOTE = (
    "PRE-CALCULATED by the backend. Use only these aspects; do not recalculate or assume others. "
    "Each has the aspect type, the exact angle and the orb."
)

# Темата на анализа: подава се на AI в подканите по карти (THEME)
THEME_FOCUS = {
    "general": "General life overview: main patterns, tensions, resources and priorities.",
    "health": "Health and well-being: workload, rhythm, recovery and daily routine (tendencies only, never diagnoses).",
    "career": "Career and work: responsibilities, communication, negotiation and the concrete action asked about.",
    "money": "Money and success: resources, priorities, habits and the alternatives named in the question (no investment advice).",
    "love": "Relationships and love: emotions, communication, boundaries and the exact context of the relationship.",
    "karmic": "Karma and family patterns: symbolic and personal patterns of reaction (never facts about ancestors or parents).",
}


def _utc_today() -> str:
    """Днешната дата (UTC) за проверката на текста: „днес“ при избрана друга дата и дати в натален анализ."""
    from datetime import datetime as _dt
    return _dt.now(timezone.utc).date().isoformat()


class AIInterpreter:
    """Клас за AI интерпретация на астрологични карти"""
    
    def __init__(self, api_key: Optional[str] = None):
        """
        Инициализация на AI интерпретатора.
        
        Args:
            api_key: Together.ai API ключ (ако не е предоставен, чете от environment)
        """
        # --- Ollama Cloud (Primary Provider) ---
        self.ollama_url = os.getenv("OLLAMA_BASE_URL", "").rstrip("/")
        self.ollama_key = os.getenv("OLLAMA_API_KEY")
        self.ollama_model = os.getenv("OLLAMA_MODEL", "deepseek-v4.1-flash:cloud")
        self.ollama_timeout = 300.0  # 300s timeout for Ollama Cloud (5 min)
        # Таван на изходните токени. Кирилицата е скъпа на токени — при 6000
        # дългите български анализи се отрязваха по средата на думата.
        self.max_output_tokens = int(os.getenv("AI_MAX_OUTPUT_TOKENS", "12000"))
        # Температура за текстовете с много факти. По-ниска от старите 0,7, за да не се "измислят" домове и дати.
        self.default_temperature = float(os.getenv("AI_TEMPERATURE", "0.4"))
        
        # --- Together.ai (Fallback Provider) ---
        self.together_key = api_key or os.getenv("OPENAI_API_KEY")
        self.together_url = os.getenv("TOGETHER_API_URL", "https://api.together.xyz/v1/chat/completions")
        self.together_model = os.getenv("TOGETHER_MODEL", "deepseek-ai/DeepSeek-V4.1-Flash")
        self.together_timeout = 120.0  # 120s timeout for chunked monthly requests
        
        # --- Shared config ---
        self.timeout = self.together_timeout  # Default for backward compatibility
        
        if not self.together_key:
            raise ValueError(
                "OPENAI_API_KEY не е намерен. Моля задайте го в .env файл или като environment променлива."
            )
        
        # Warning if Ollama is not configured (will fallback to Together only)
        if not self.ollama_key:
            print("⚠️ OLLAMA_API_KEY не е намерен. Ще се използва само Together.ai (fallback).")
        elif not self.ollama_url:
            print("⚠️ OLLAMA_BASE_URL не е намерен. Ще се използва само Together.ai (fallback).")
        
        # Initialize engine for house ruler calculations
        self.engine = AstrologyEngine()
    
    async def _call_api(
        self,
        system_prompt: str,
        user_prompt: str,
        max_tokens: int,
        *,
        temperature: Optional[float] = None,
        max_retries: int = 3,
        timeout: Optional[float] = None,
        add_context: bool = True,
    ) -> str:
        """
        Универсален API caller с fallback логика.
        
        1. Опитва Ollama Cloud (primary provider)
        2. Ако fail → опитва Together.ai (fallback)
        
        Args:
            system_prompt: Системен prompt
            user_prompt: Потребителски prompt
            max_tokens: Максимален брой токени
            temperature: Температура на модела (None = AI_TEMPERATURE, по подразбиране 0.4)
            max_retries: Опити към Ollama преди fallback към Together (по подразбиране 3)
            timeout: Таймаут в секунди за заявка; None = стойностите по подразбиране на провайдърите
            add_context: Добавя правилата за безопасност и паметта на потребителя.
                Изключва се само за кратки технически заявки (напр. търсене на координати),
                които не генерират астрологичен текст.
            
        Returns:
            Текстов отговор от AI
            
        Raises:
            RuntimeError: ако и двата провайдъра fail
        """
        if temperature is None:
            temperature = self.default_temperature

        # Аварийно спиране и бюджет: без заявка към доставчика, когато анализите са спрени (хвърля ai_budget.BudgetExceeded)
        ai_budget.check()

        if add_context:
            # Правилата за безопасност важат за всеки анализ, независимо от режима
            system_prompt = f"{relationship.adapt(system_prompt)}\n\n{SAFETY_RULES}\n\n{QUALITY_RULES}"
            # Контекстът на отношенията между двамата души (Фаза 12), ако анализът е за двама
            relation_context = relationship.current.get()
            if relation_context:
                system_prompt = f"{system_prompt}\n\n{relation_context}"
            # Човек без известен час на раждане: без Асцендент, МС и домове (последното правило, за да надделее над шаблоните)
            time_context = birthtime.current.get()
            if time_context:
                system_prompt = f"{system_prompt}\n\n{time_context}"
                user_prompt = f"{user_prompt}\n\n{birthtime.reminder.get()}"
            # Бележките от контролираната памет на потребителя (ако ги има и са включени)
            user_context = memory.current_context.get()
            if user_context:
                user_prompt = f"{user_prompt}\n\n{user_context}"

        # --- OLLAMA CLOUD ATTEMPT (Primary) ---
        if self.ollama_key and self.ollama_url:
            for attempt in range(1, max_retries + 1):
                slot = self._reserve_call(system_prompt, user_prompt, max_tokens, "ollama")      # BudgetExceeded излиза навън
                started = time.monotonic()
                try:
                    print(f"🔄 Ollama опит {attempt}/{max_retries}...")
                    ollama_data = {
                        "model": self.ollama_model,
                        "messages": [
                            {"role": "system", "content": system_prompt},
                            {"role": "user", "content": user_prompt}
                        ],
                        "temperature": temperature,
                        "max_tokens": max_tokens,
                        # Мислещите модели (напр. deepseek-v4.1-flash) иначе пишат в
                        # message.reasoning и оставят content празен / изчерпват max_tokens.
                        "reasoning_effort": "none"
                    }

                    async with httpx.AsyncClient(timeout=timeout or self.ollama_timeout) as client:
                        ollama_response = await client.post(
                            f"{self.ollama_url}/chat/completions",
                            headers={
                                "Authorization": f"Bearer {self.ollama_key}",
                                "Content-Type": "application/json"
                            },
                            json=ollama_data
                        )
                        
                        if ollama_response.status_code == 200:
                            resp_json = ollama_response.json()
                            content = resp_json.get("choices", [{}])[0].get("message", {}).get("content")
                            if content and content.strip():
                                finish_reason = resp_json.get("choices", [{}])[0].get("finish_reason")
                                print(f"✅ Ollama успешно отговори (опит {attempt}, finish_reason={finish_reason}, usage={resp_json.get('usage')})")
                                if finish_reason == "length":
                                    print(f"⚠️ Отговорът е отрязан от max_tokens={max_tokens} — вдигнете AI_MAX_OUTPUT_TOKENS.")
                                used = self._record_usage(resp_json, system_prompt + user_prompt, content, slot, "ollama")
                                slot = None
                                ai_budget.note_attempt("ollama", "length" if finish_reason == "length" else "ok", started, *used, model=self.ollama_model)
                                return self._finish_text(content.strip(), add_context)
                            choice = resp_json.get("choices", [{}])[0]
                            # Платен 200 без текст (празен content, само reasoning): разходът се отчита ПРЕДИ резервния доставчик
                            used = self._record_usage(resp_json, system_prompt + user_prompt, "", slot, "ollama")
                            slot = None
                            ai_budget.note_attempt("ollama", "empty", started, *used, model=self.ollama_model)
                            print(
                                f"⚠️ Ollama върна празен content (model={self.ollama_model}, "
                                f"finish_reason={choice.get('finish_reason')}, "
                                f"has_reasoning={bool(choice.get('message', {}).get('reasoning'))}, "
                                f"usage={resp_json.get('usage')}). Fallback към Together..."
                            )
                            break  # Празен отговор - не retry, веднага fallback
                        else:
                            ai_budget.note_attempt("ollama", "http_error", started, model=self.ollama_model)
                            print(f"⚠️ Ollama HTTP {ollama_response.status_code} (опит {attempt})")
                            if attempt < max_retries:
                                print(f"⏳ Изчакване 5 секунди преди следващия опит...")
                                await asyncio.sleep(5)
                            else:
                                print("⚠️ Всички опити изчерпани. Fallback към Together...")
                                
                except Exception as e:
                    # Таймаут или прекъсване СЛЕД изпращането: цената е неизвестна, резервацията остава несигурна (не е безплатна)
                    timed_out = isinstance(e, httpx.TimeoutException) and not self._is_unsent_failure(e)
                    if not self._is_unsent_failure(e):          # освен ако връзката не е била отворена, доставчикът може да е таксувал
                        ai_budget.uncertain(slot)
                        slot = None
                    ai_budget.note_attempt("ollama", "timeout" if timed_out else "error", started, model=self.ollama_model)
                    print(f"⚠️ Ollama грешка (опит {attempt}): {type(e).__name__}: {e}")
                    if attempt < max_retries:
                        print(f"⏳ Изчакване 5 секунди преди следващия опит...")
                        await asyncio.sleep(5)
                    else:
                        print("⚠️ Всички опити изчерпани. Fallback към Together...")
                        import traceback
                        traceback.print_exc()
                finally:
                    ai_budget.release(slot)                  # без платен отговор резервацията се освобождава (при успех slot е None)
        
        # --- TOGETHER.AI FALLBACK ---
        slot = self._reserve_call(system_prompt, user_prompt, max_tokens, "together")
        started = time.monotonic()
        try:
            together_data = {
                "model": self.together_model,
                "messages": [
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt}
                ],
                "temperature": temperature,
                "max_tokens": max_tokens
            }
            
            async with httpx.AsyncClient(timeout=timeout or self.together_timeout) as client:
                together_response = await client.post(
                    self.together_url,
                    headers={
                        "Authorization": f"Bearer {self.together_key}",
                        "Content-Type": "application/json"
                    },
                    json=together_data
                )
                
                if together_response.status_code != 200:
                    ai_budget.note_attempt("together", "http_error", started, model=self.together_model)
                    raise RuntimeError(
                        f"Together.ai HTTP {together_response.status_code}: {together_response.text[:500]}"
                    )
                
                resp_json = together_response.json()
                content = resp_json.get("choices", [{}])[0].get("message", {}).get("content")
                
                finish_reason = (resp_json.get("choices", [{}])[0] or {}).get("finish_reason")
                if content and content.strip():
                    used = self._record_usage(resp_json, system_prompt + user_prompt, content, slot, "together")
                    slot = None
                    ai_budget.note_attempt("together", "length" if finish_reason == "length" else "ok", started, *used, model=self.together_model)
                    return self._finish_text(content.strip(), add_context)

                used = self._record_usage(resp_json, system_prompt + user_prompt, "", slot, "together")      # платен празен отговор
                slot = None
                ai_budget.note_attempt("together", "empty", started, *used, model=self.together_model)
                raise RuntimeError("Together.ai върна празен отговор")
                
        except Exception as e:
            if slot is not None and not self._is_unsent_failure(e) and not isinstance(e, RuntimeError):
                ai_budget.uncertain(slot)                       # прекъсване след изпращане: цената е неизвестна
                slot = None
                ai_budget.note_attempt("together", "timeout" if isinstance(e, httpx.TimeoutException) else "error", started, model=self.together_model)
            raise RuntimeError(f"Грешка при комуникация с AI провайдърите: {e}")
        finally:
            ai_budget.release(slot)
    
    @staticmethod
    def _finish_text(text: str, analysis: bool) -> str:
        """Текстът на анализ минава през замяната на английските думи с български; техническите отговори (JSON за места) не."""
        return bg_text.bulgarianize(text) if analysis else text

    @staticmethod
    def _reserve_call(system_prompt: str, user_prompt: str, max_tokens: int, provider: Optional[str] = None):
        """Резервира най-високия възможен разход на една заявка към доставчика (хвърля ai_budget.BudgetExceeded). Входът се
        резервира по консервативна горна граница (един токен на знак), изходът по max_tokens и тарифата на доставчика."""
        return ai_budget.reserve(ai_budget.upper_bound_tokens(system_prompt + user_prompt), int(max_tokens or 0), provider=provider)

    @staticmethod
    def _record_usage(resp_json: Dict, prompt_text: str, content: str, slot=None, provider: Optional[str] = None) -> Tuple[int, int]:
        """Отчита платения отговор (идемпотентно, по резервацията); без usage от доставчика токените се оценяват по дължината.
        Връща използваните (prompt, completion) токени."""
        usage = resp_json.get("usage") or {}
        prompt = usage.get("prompt_tokens")
        completion = usage.get("completion_tokens")
        prompt = prompt if isinstance(prompt, int) else ai_budget.estimate_tokens(prompt_text)
        completion = completion if isinstance(completion, int) else ai_budget.estimate_tokens(content or "")
        ai_budget.settle(slot, prompt, completion, provider=provider)
        return prompt, completion

    @staticmethod
    def _is_unsent_failure(exc: BaseException) -> bool:
        """Грешка, при която заявката със сигурност не е стигнала до доставчика (няма платен разход): връзката не е отворена."""
        return isinstance(exc, (httpx.ConnectError, httpx.ConnectTimeout, httpx.PoolTimeout))

    @staticmethod
    def _get_bulgarian_language_rules() -> str:
        """
        Връща строги правила за изход на български език.
        
        Returns:
            String с инструкции за задължителен български изход
        """
        return (
            "\n\n*** IMPORTANT LANGUAGE RULES ***\n"
            "1. **OUTPUT LANGUAGE:** You MUST write the entire report in **BULGARIAN (Български)**.\n\n"
            "2. **NO ENGLISH:** Do NOT output any English text. Translate all astrological terms.\n"
            "   - \"Trine\" -> \"Тригон\"\n"
            "   - \"Square\" -> \"Квадратура\"\n"
            "   - \"Opposition\" -> \"Опозиция\"\n"
            "   - \"Conjunction\" -> \"Съвпад\"\n"
            "   - \"Sextile\" -> \"Секстил\"\n"
            "   - \"Retrograde\" -> \"Ретрограден\"\n"
            "   - \"Direct\" -> \"Директен\"\n"
            "   - \"Ingress\" -> \"Навлизане\" / \"Ингрес\"\n"
            "   - \"natal\" -> \"натален / натална / натално\" (по рода на думата след него), \"transit\" -> \"транзитен\"\n"
            "   - ZODIAC SIGNS in Bulgarian ALWAYS, even when the data gives them in English: Aries=Овен, Taurus=Телец, "
            "Gemini=Близнаци, Cancer=Рак, Leo=Лъв, Virgo=Дева, Libra=Везни, Scorpio=Скорпион, Sagittarius=Стрелец, "
            "Capricorn=Козирог, Aquarius=Водолей, Pisces=Риби. Planets too: Sun=Слънце, Moon=Луна, Mercury=Меркурий, "
            "Venus=Венера, Mars=Марс, Jupiter=Юпитер, Saturn=Сатурн, Uranus=Уран, Neptune=Нептун, Pluto=Плутон.\n"
            "   - The data below is in English: never copy its English words or event labels into the text. Latin letters are "
            "allowed only for a person's own name and for degrees such as 17°22'.\n\n"
            "3. **Terminology:** Use professional Bulgarian astrological terminology.\n\n"
            "4. **🚨 CRITICAL: HOUSES TERMINOLOGY (STRICTLY ENFORCED):**\n"
            "   - ALWAYS use \"дом\" (house), NEVER \"поле\" (field)\n"
            "   - ✅ CORRECT: \"1-ви дом\", \"5-ти дом\", \"12-ти дом\", \"в 7-ми дом\"\n"
            "   - ❌ WRONG: \"1-во поле\", \"5-то поле\", \"12-то поле\", \"в седмото поле\"\n"
            "   - This is a PROFESSIONAL STANDARD in Bulgarian astrology\n"
            "   - \"Поле\" is NOT an accepted term and sounds unprofessional\n"
            "   - EVERY mention of astrological houses MUST use \"дом\"\n\n"
            "5. **Tone:** Professional, empathetic, and grammatically correct in Bulgarian.\n\n"
            "6. **TERM \"Съвпад\":** it means ONLY a conjunction (0°). Never write \"съвпад\" for a trine, square, sextile or opposition, "
            "and never use it to say that an aspect is exact. Say \"точен\" only when the data gives an exact moment; otherwise give the orb from the data or say \"почти точен\".\n\n"
            "7. **NO PERCENTAGES:** never give percentages or numeric probabilities for life events or decisions; use plain words "
            "(for example благоприятен, смесен, затруднен период).\n\n"
            "8. **NO INTERNAL LABELS:** never print instruction labels, field names or section markers "
            "(for example \"задължителна секция\", \"само при Марс/Уран\", house_impact, JSON keys, factpack, CALCULATED).\n\n"
            "9. **GENDER:** use a person's gender only if the 'PEOPLE' section says male or female; otherwise write gender-neutrally "
            "and never guess gender from a name.\n\n"
            "10. **HEALTH:** never diagnose, never recommend treatment or medication, never reassure that there is no illness, accident or danger "
            "(forbidden: \"няма данни за заболявания или инциденти\"), and never present a bodily reaction as a fact derived from the chart. "
            "Write tendencies and themes (\"картата подсказва тема за ...\") and practical routine advice: sleep, rest, load limits, boundaries, "
            "and a medical check-up when the person is worried.\n\n"
            "11. **DECISIONS (career, money, contracts):** do not give absolute orders based only on an aspect or a retrograde "
            "(forbidden: \"не напускайте\", \"не приемайте оферта\", \"не инвестирайте\", \"не подписвайте\"). Keep the direct tone but give a "
            "concrete action to check the real conditions: \"прегледайте условията, бюджета и алтернативите и си дайте време за обмисляне\".\n\n"
            "12. **OTHER PEOPLE'S MOTIVES:** never state a person's real motive, loyalty or intention as an established fact; suggest a checkable "
            "action instead (for example: ask what prevented the agreement and agree on a new deadline).\n\n"
            "13. **WORDING:** do not call an aspect the \"most exact\" unless its orb is the smallest of the listed ones; prefer \"водещ за темата\". "
            "\"родова ДНК\" and \"семейна ДНК\" are metaphors only; prefer \"родови модели\" and never present them as genetics.\n\n"
            "14. **CONSISTENCY:** the answer to the question and the closing advice must never contradict the plan and the chronology written "
            "earlier in the same text (do not recommend an action on a date and then forbid it). Every date and time in the answer is copied from "
            "the same event rows as the chronology. Never add a date that is not in the calendar. The calendar covers only the listed planets and "
            "targets: never claim that other aspects, to the Ascendant or to any point that is not listed, do not exist.\n\n"
            "15. **TRANSIT OR NATAL:** every statement about the sign, house, retrograde or direct motion of a TRANSITING planet comes from the "
            "transit or calendar data of that moment, never from the natal chart. Write \"транзитният Юпитер\" and \"наталният Юпитер\". "
            "The aspect word always matches the 'aspect' field and the angle (0° съвпад, 60° секстил, 90° квадратура, 120° тригон, 180° опозиция); "
            "when you name an aspect between two people, say whose planet is whose.\n\n"
            "16. **TIME ZONES:** never write a time zone identifier such as Europe/Sofia or America/New_York. Write the local time and, "
            "when the zone matters, \"по местно време\" or the UTC offset (for example 21:00 (UTC+3)).\n"
        )
    
    # ------------------------------------------------------------------
    # Блокове с проверени данни (Фаза 8): един надписан блок на факт
    # ------------------------------------------------------------------
    @staticmethod
    def _natal_block(name: str, chart: Dict) -> str:
        """Натална карта (знак, градус, дом, 12 куспиди с управители) и натални аспекти на един човек."""
        upper = name.upper()
        note = NATAL_NOTE if factpack.has_houses(chart) else NATAL_NOTE_NO_TIME
        text = factpack.section(f"{upper} NATAL CHART", note, factpack.natal_view(chart))
        try:
            aspects = calculate_natal_aspects(chart, use_wider_orbs=False)
            text += factpack.section(f"{upper} NATAL ASPECTS (CALCULATED)", ASPECTS_NOTE, aspects)
        except Exception as e:
            print(f"Warning: Could not calculate natal aspects: {e}")
        return text

    async def guard_text(self, text: str, facts, *, stage: str, checks: Optional[List[Dict]] = None,
                         max_tokens: Optional[int] = None, extra_check=None) -> str:
        """
        Проверка на готовия текст срещу фактите (Фаза 10, виж text_guard.py): най-много една поправка с втора AI заявка.
        Връща текста (поправен при нужда). Хвърля text_guard.TextCheckError, ако и след поправката има тежко нарушение.
        checks получава кратко описание на проверката (без текст на анализа) и при успех, и при провал.
        """
        progress.stage("checking")      # етапът на задачата за екрана (виж progress.py)

        async def repair_call(system_prompt: str, user_prompt: str) -> str:
            return await self._call_api(system_prompt=system_prompt, user_prompt=user_prompt,
                                        max_tokens=max_tokens or self.max_output_tokens,
                                        temperature=text_guard.REPAIR_TEMPERATURE)

        try:
            outcome = await text_guard.guard_text(text, facts, stage=stage, repair_call=repair_call,
                                                  language_rules=self._get_bulgarian_language_rules(), extra_check=extra_check)
        except text_guard.TextCheckError as failure:
            if checks is not None:
                checks.append(failure.outcome.summary())
            raise
        if checks is not None:
            checks.append(outcome.summary())
        return outcome.text

    @staticmethod
    def build_text_facts(**kwargs):
        """Фактите за проверката на текста. При грешка или непълни факти връща text_guard.MissingFacts: в „enforce“ анализът
        се отказва (R01), в „warn“/„off“ текстът минава непроверен. В дневника влизат само типът грешка и причината, без данни."""
        try:
            return text_check.build_facts(**kwargs)
        except text_check.FactsUnavailable as exc:
            print(f"⚠️ Фактите за проверката на текста са непълни ({exc.reason})")
            return text_guard.MissingFacts(exc.reason[:60])
        except Exception as exc:
            print(f"⚠️ Фактите за проверката на текста не се построиха ({type(exc).__name__})")
            return text_guard.MissingFacts(f"build_error:{type(exc).__name__}"[:60])

    @staticmethod
    def require_text_facts(facts, *, stage: str, checks: Optional[List[Dict]] = None) -> None:
        """Отказ ПРЕДИ разход на AI заявка, когато фактите за проверка липсват (само в „enforce“)."""
        try:
            outcome = text_guard.refuse_missing_facts(facts, stage=stage)
        except text_guard.TextCheckError as failure:
            if checks is not None:
                checks.append(failure.outcome.summary())
            raise
        if outcome is not None and checks is not None:
            checks.append(outcome.summary())

    async def interpret_chart(
        self,
        natal_chart: Dict,
        transit_chart: Optional[Dict] = None,
        partner_chart: Optional[Dict] = None,
        partner_name: Optional[str] = None,
        question: str = "",
        target_date: str = "",
        language: str = "bg",
        report_type: str = "general",
        user_name: Optional[str] = None,
        gender: Optional[str] = None,
        partner_gender: Optional[str] = None,
        checks: Optional[List[Dict]] = None,
    ) -> str:
        """
        Интерпретира натална, транзитна и опционално partner карта.

        Args:
            natal_chart: Речник с данни от наталната карта
            transit_chart: Речник с данни от транзитната карта
            partner_chart: Речник с данни от partner картата (опционално, за synastry)
            partner_name: Име на партньора (опционално)
            question: Конкретен въпрос от потребителя (опционално)
            target_date: Дата на транзитната карта
            language: Език за отговора: поддържа се само "bg" (български), всеки друг отказва с ValueError
            gender / partner_gender: Известен пол (male/female); иначе езикът е неутрален
            checks: списък, който се попълва с кратко описание на проверката на текста (Фаза 10; без текст на анализа)

        Returns:
            Текстова интерпретация от AI
        """
        if language != "bg":
            # Старият свободен ход за други езици е махнат на 18.10.2026 и е архивиран (archive/legacy_free_flows_2026-10-18.txt)
            raise ValueError(f"Поддържа се само български език (language='bg'), не '{language}'.")

        # Прогнозата за период не минава оттук: тя е по карти в period_cards.py (generation.run_period)

        if partner_chart and transit_chart:
            # Анализ „за дата“ за двама: фактите са карти, написани от кода; AI само тълкува (pair_snapshot.py)
            return await pair_snapshot.interpret(
                self, natal_chart=natal_chart, partner_chart=partner_chart, transit_chart=transit_chart, user_name=user_name,
                partner_name=partner_name, question=question or "", target_date=target_date, report_type=report_type,
                checks=checks if checks is not None else [], theme=THEME_FOCUS.get(report_type, THEME_FOCUS["general"]),
                utc_today=_utc_today(), gender=gender, partner_gender=partner_gender)

        if partner_chart and not transit_chart:
            # Натален анализ за двама: фактите са карти от кода, AI само тълкува (natal_pair.py)
            return await natal_pair.interpret(
                self, natal_chart=natal_chart, partner_chart=partner_chart, user_name=user_name, partner_name=partner_name,
                question=question or "", report_type=report_type, relation=relationship.current_code.get(), checks=checks if checks is not None else [],
                theme=THEME_FOCUS.get(report_type, THEME_FOCUS["general"]), utc_today=_utc_today(), gender=gender, partner_gender=partner_gender)

        if transit_chart and not partner_chart:
            # Анализ „за дата“ за един човек: фактите са карти, написани от кода; AI само тълкува (single_snapshot.py)
            return await single_snapshot.interpret(
                self, natal_chart=natal_chart, transit_chart=transit_chart, user_name=user_name, question=question or "",
                target_date=target_date, checks=checks if checks is not None else [],
                theme=THEME_FOCUS.get(report_type, THEME_FOCUS["general"]), utc_today=_utc_today(), gender=gender)

        # Без партньор и без транзит: натален анализ за един човек, фактите са карти от кода, AI само тълкува (natal_single.py)
        return await natal_single.interpret(
            self, natal_chart=natal_chart, user_name=user_name, question=question or "", report_type=report_type,
            checks=checks if checks is not None else [], theme=THEME_FOCUS.get(report_type, THEME_FOCUS["general"]), utc_today=_utc_today(),
            gender=gender)


# Глобална инстанция за удобство (опционално)
_interpreter_instance: Optional[AIInterpreter] = None


def get_interpreter() -> AIInterpreter:
    """
    Връща глобална инстанция на AIInterpreter (singleton pattern).
    
    Returns:
        AIInterpreter инстанция
    """
    global _interpreter_instance
    
    if _interpreter_instance is None:
        _interpreter_instance = AIInterpreter()
    
    return _interpreter_instance

