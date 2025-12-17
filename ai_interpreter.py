"""
AI Интерпретатор за астрологични карти
Използва OpenAI GPT-4o за анализ и интерпретация
"""

import os
import json
from typing import Dict, Optional, List, Tuple
from collections import defaultdict
from openai import AsyncOpenAI
from dotenv import load_dotenv
from engine import AstrologyEngine

# Зареждане на environment променливи
load_dotenv()

# Шаблони за различни типове доклади
PROMPT_TEMPLATES = {
    "general": """
        You are an expert astrologer. Provide a balanced analysis covering personality, emotional needs, and major strengths. 
        Keep it holistic and helpful.
    """,
    "health": """
        You are an Expert Medical Astrologer and Holistic Health Coach.
        FOCUS: Physical and mental health, vitality, chronic conditions, immunity, and preventive care.
        - Analyze 6th House (daily routine, chronic issues, health service) and Saturn (bones, teeth, immune system).
        - Analyze 1st House (physical vitality, constitution) and Mars (energy levels, inflammation, injury risks).
        - Analyze Moon (mental state, emotions, sleep) and Mercury (nervous system, anxiety).
        - Analyze Sun (overall vitality, heart health).
        TONE: Caring, practical, preventive, empowering.
        Do NOT diagnose or predict diseases. Focus on patterns, vitality, and holistic well-being.
    """,
    "karmic": """
        You are an Expert in Karmic Astrology, Family Constellations, and Regression Therapy.
        FOCUS: Soul lessons, ancestral patterns, and karmic healing.
        - Analyze Moon (maternal lineage, Inner Child, emotional safety) and Saturn (paternal lineage, karmic debts, authority).
        - Analyze Retrograde planets as "Karmic Returns" – unfinished business from the past.
        - Analyze Pluto for deep subconscious transformation of family DNA.
        GOAL: Uncover soul lessons and ancestral patterns, not predict external events.
        TONE: Therapeutic, deep, empathetic, spiritual.
    """,
    "career": """
        You are a Vocational Astrologer and Career Counselor.
        FOCUS: Professional life, Vocation, and Public Status.
        - Analyze MC (Midheaven) & 10th House (Career).
        - Analyze 6th House (Daily work/Habits) and 2nd House (Income/Assets).
        - Analyze Saturn (Ambition/Discipline) and Mercury/Mars (Skills/Drive).
        GOAL: Suggest suitable career paths and professional development strategies.
        TONE: Strategic, practical, motivating.
    """,
    "love": """
        You are a Relationship Astrologer specializing in Synastry and Emotional needs.
        FOCUS: Love, Romance, and Partnerships.
        - Analyze Venus (Love language/Values) and Mars (Passion/Drive).
        - Analyze 7th House (Marriage/Partners) and 5th House (Romance/Dating).
        - Analyze the Moon (Emotional needs in intimacy).
        GOAL: Describe relationship patterns, needs, and the ideal partner archetype.
        TONE: Romantic, insightful, sensitive.
    """,
    "money": """
        You are a Financial Astrologer and Wealth Coach.
        FOCUS: Financial potential, resources, and material success.
        - Analyze 2nd House (Personal Assets) and 8th House (Shared Resources).
        - Analyze Jupiter (Abundance/Luck) and Venus (Attraction).
        - Analyze Saturn (Financial blockages or discipline).
        GOAL: Identify wealth potential and strategies for financial growth.
        TONE: Pragmatic, resource-focused, empowering.
    """
}

# Dynamic Forecast Templates (Time-Based Analysis)
DYNAMIC_PROMPT_TEMPLATES = {
    "career": """
        You are a Career Strategist and Professional Astrologer.
        MODE: Time-Based Career Forecast.
        FOCUS: Professional advancement, work opportunities, and career timing.
        
        **CRITICAL:** The user's 10th House (Career) is ruled by **{house_10_ruler}**.
        Focus on transits to **Natal {house_10_ruler}** for job changes and success.
        
        - IGNORE romantic aspects unless they directly affect work performance or decisions.
        - Prioritize transits to Natal {house_10_ruler} (the career ruler) above all other indicators.
        - Also analyze Saturn (responsibility, career structure) and Jupiter (opportunities, expansion).
        - Look for transits affecting MC (Midheaven) for major career shifts.
        - Consider the 6th House (daily work) and 2nd House (income/assets) as secondary indicators.
        
        STRUCTURE your monthly analysis:
        1. Strategic Goals for this Month
        2. Critical Dates (when to act or avoid decisions)
        3. Advice (specific actions to take)
        TONE: Strategic, practical, motivating.
    """,
    "love": """
        You are a Relationship Coach and Astrological Timing Specialist.
        MODE: Time-Based Relationship Forecast.
        FOCUS: Romantic timing, relationship dynamics, and partnership opportunities.
        
        **CRITICAL:** The user's 7th House (Relationships) is ruled by **{house_7_ruler}**.
        Focus on transits to **Natal {house_7_ruler}** and Venus for relationship timing.
        
        - Prioritize transits to Natal {house_7_ruler} (the partnership ruler) as the primary indicator.
        - Also focus on Venus (love, attraction), Moon (emotional needs), Mars (passion, drive).
        - Consider the 5th House (romance, dating) as a secondary indicator.
        - If Partner data is present, analyze the INTERACTION intensity for this specific month.
        - Look for periods of harmony (trines, sextiles) vs. tension (squares, oppositions).
        
        STRUCTURE your monthly analysis:
        1. Romantic Atmosphere for this Month
        2. Key Dates (best times for romance, talks, or intimacy)
        3. Warnings (periods to be cautious or patient)
        TONE: Romantic, insightful, sensitive.
    """,
    "health": """
        You are an Expert Medical Astrologer.
        
        **CRITICAL RULES for HEALTH ANALYSIS:**
        
        1. **FOCUS ON THE 6TH HOUSE RULER:**
           - **CRITICAL:** The user's 6th House (Health) is ruled by **{house_6_ruler}**.
           - You MUST prioritize transits to **Natal {house_6_ruler}** as the main health indicator.
           - Analyze transits to Natal {house_6_ruler} FIRST before any other health indicators.
        
        2. **PLANET ARCHETYPES (Do not mix them up) - Interpret based on the ruler:**
           - If {house_6_ruler} is Saturn -> bones, teeth, chronic issues, fatigue, depletion, skin, "cold" diseases.
           - If {house_6_ruler} is Mars -> inflammation, fevers, cuts, acute infections, surgery, adrenaline.
           - If {house_6_ruler} is Uranus -> sudden stress, nervous system, accidents, surgeries, spikes (blood pressure).
           - If {house_6_ruler} is Neptune -> allergies, poisoning, difficult diagnosis, lymphatic system.
           - If {house_6_ruler} is Pluto -> deep psychological transformation, hormonal cycles, regeneration. NOT usually "flu" or "broken leg".
           - If {house_6_ruler} is Mercury -> nervous system, anxiety, communication issues affecting health.
           - If {house_6_ruler} is Moon -> emotional health, digestive system, immunity.
           - If {house_6_ruler} is Sun -> overall vitality, heart health, energy levels.
           - If {house_6_ruler} is Venus -> throat, kidneys, hormonal balance.
           - If {house_6_ruler} is Jupiter -> liver, pancreas, overindulgence issues.
        
        3. **NATAL CONTEXT (The "Natal Echo"):**
           - If the user has a hard aspect natally (e.g., Sun Square Pluto), a similar transit is NOT a crisis; it is a familiar energy pattern. Do not overdramatize it.
           - Focus on *new* influences that disrupt the equilibrium.
        
        4. **RESPONSE STRUCTURE:**
           - **Physical Body (6th House & {house_6_ruler}):** Specific physical risks based on the ruler {house_6_ruler}.
           - **Vitality (Sun/Mars):** Energy levels.
           - **Psyche (Moon/Pluto):** Emotional background.
           - **Alerts:** Only mention surgeries/accidents if MARS or URANUS are involved in hard aspects.
        
        MODE: Time-Based Health Forecast.
        If the user asks a specific health question (e.g., "Will I get pregnant?" or "Will my surgery go well?"), prioritize that in the specific answer section.
        TONE: Caring, practical, preventive, empowering.
    """,
    "karmic": """
        You are an Expert in Karmic Astrology, Family Constellations, and Regression Therapy.
        
        Your goal is NOT to predict external events, but to uncover **Soul Lessons** and **Ancestral Patterns**.
        
        **CORE PHILOSOPHY (The Lens):**
        
        1. **THE MOON = THE MOTHER:** Represents emotional safety, the maternal lineage, childhood trauma, and the "Inner Child". Aspects to the Moon trigger mother-wounds or healing of the feminine line.
        
        2. **SATURN = THE FATHER:** Represents the law, authority, the paternal lineage, karmic debts, and maturity. Aspects to Saturn trigger father-wounds or the need to take responsibility.
        
        3. **RETROGRADE PLANETS:** These are CRITICAL. Treat them as "Karmic Returns" – unfinished business from the past or past lives coming back for review.
        
        4. **PLUTO:** Represents the deep subconscious and transformation of the family DNA.
        
        **INTERPRETATION RULES:**
        
        - If you see a **Saturn** aspect, talk about "Limits," "Responsibility," and "The Father's lesson."
        - If you see a **Moon** aspect, talk about "Safety," "Nurturing," and "The Mother's model."
        - If you see a **Retrograde**, interpret it as "A second chance to fix a past mistake."
        - **Tone:** Therapeutic, deep, empathetic, spiritual. Avoid mundane topics like "salary" or "office politics" unless they are karmic tests.
        
        **OUTPUT STRUCTURE (Per Month):**
        
        1. **The Karmic Theme:** What is the soul trying to learn this month?
        2. **Ancestral Echoes (Moon/Saturn):** Which family patterns are active? (e.g., "Healing the relationship with the mother figure").
        3. **Retrograde Review:** Specific internal work required.
        4. **Healing Practice:** A short psychological or spiritual advice (e.g., "Forgiveness ritual," "Inner child dialogue").
        
        If the user asks a specific question, answer it through the lens of Karma (e.g., "Why is this happening to me? -> Because you are repeating a family cycle").
        
        MODE: Time-Based Karmic Forecast.
        TONE: Therapeutic, deep, empathetic, spiritual.
    """,
    "money": """
        You are a Financial Astrologer and Wealth Timing Specialist.
        MODE: Time-Based Financial Forecast.
        FOCUS: Financial opportunities, investment timing, and material resources.
        
        **CRITICAL:** The user's 2nd House (Money) is ruled by **{house_2_ruler}**.
        Focus on transits to **Natal {house_2_ruler}** and Jupiter for financial timing.
        
        - Prioritize transits to Natal {house_2_ruler} (the money ruler) as the primary indicator.
        - Also analyze Jupiter (expansion, opportunities), Saturn (discipline, structure), Venus (value, attraction).
        - Consider the 8th House (shared resources, investments) as a secondary indicator.
        - Look for favorable periods for financial decisions vs. caution periods.
        
        STRUCTURE your monthly analysis:
        1. Financial Climate for this Month
        2. Opportunity Dates (when to make financial moves)
        3. Caution Periods (when to avoid major financial decisions)
        TONE: Pragmatic, resource-focused, empowering.
    """,
    "general": """
        You are an Expert Predictive Astrologer.
        MODE: Time-Based General Forecast.
        FOCUS: Holistic life overview covering all major areas.
        - Balance attention across career, relationships, personal growth, and material resources.
        - Look for major themes and patterns.
        STRUCTURE your monthly analysis:
        1. Major Themes for this Month
        2. Key Dates (important periods to note)
        3. Overall Advice (what to focus on or be cautious about)
        TONE: Balanced, insightful, helpful.
    """
}


class AIInterpreter:
    """Клас за AI интерпретация на астрологични карти"""
    
    def __init__(self, api_key: Optional[str] = None):
        """
        Инициализация на AI интерпретатора.
        
        Args:
            api_key: OpenAI API ключ (ако не е предоставен, чете от environment)
        """
        self.api_key = api_key or os.getenv("OPENAI_API_KEY")
        
        if not self.api_key:
            raise ValueError(
                "OPENAI_API_KEY не е намерен. Моля задайте го в .env файл или като environment променлива."
            )
        
        # Initialize with extended timeout for chunked requests
        self.client = AsyncOpenAI(
            api_key=self.api_key,
            timeout=120.0  # 120 seconds timeout for chunked monthly requests
        )
        
        # Initialize engine for house ruler calculations
        self.engine = AstrologyEngine()
    
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
            "   - \"Ingress\" -> \"Навлизане\" / \"Ингрес\"\n\n"
            "3. **Terminology:** Use professional Bulgarian astrological terminology.\n\n"
            "4. **Tone:** Professional, empathetic, and grammatically correct in Bulgarian.\n"
        )
    
    def _calculate_health_ruler(self, natal_chart: Dict) -> Tuple[Optional[str], Optional[str]]:
        """
        Изчислява 6th house sign и ruler за health анализ.
        
        Args:
            natal_chart: Натална карта речник с houses данни
            
        Returns:
            Tuple от (6th_house_sign, health_ruler) или (None, None) ако не е намерено
        """
        try:
            houses = natal_chart.get("houses", {})
            house_6_cusp = houses.get("House6")
            
            if house_6_cusp is None:
                return (None, None)
            
            # Използваме engine за изчисляване на sign и ruler
            sign, ruler = self.engine.get_house_ruler_from_cusp(house_6_cusp)
            return (sign, ruler)
        except Exception as e:
            print(f"Грешка при изчисляване на health ruler: {e}")
            return (None, None)
    
    def _build_dynamic_system_prompt(
        self, 
        report_type: str, 
        language: str,
        natal_chart: Dict,
        partner_chart: Optional[Dict] = None,
        user_display_name: str = "User",
        partner_display_name: str = "Partner",
        has_partner: bool = False,
        user_question: Optional[str] = None,
        house_rulers: Optional[Dict[str, str]] = None,
        partner_house_rulers: Optional[Dict[str, str]] = None
    ) -> str:
        """
        Build system prompt for dynamic (monthly chunked) forecasts.
        
        Args:
            report_type: Type of report (career, love, health, money, general)
            language: Language for response
            natal_chart: User's natal chart data
            partner_chart: Optional partner's natal chart data
            user_display_name: Display name for user
            partner_display_name: Display name for partner
            has_partner: Whether partner chart is present
            user_question: Optional user question that must be answered in each monthly chunk
            
        Returns:
            Formatted system prompt string
        """
        # Calculate house rulers if not provided
        if house_rulers is None:
            houses = natal_chart.get("houses", {})
            house_rulers = self.engine.get_house_rulers(houses) if houses else {}
        
        base_persona = DYNAMIC_PROMPT_TEMPLATES.get(report_type, DYNAMIC_PROMPT_TEMPLATES["general"])
        
        # Build global house rulers context block (applies to ALL report types)
        house_rulers_context = ""
        if house_rulers:
            house_rulers_context = (
                f"\n\n*** ASTROLOGICAL CONTEXT (APPLIES TO ALL SECTIONS) ***\n"
                f"- **Health Ruler (6th House):** {house_rulers.get('house_6_ruler', 'unknown')} (Prioritize for health questions)\n"
                f"- **Career Ruler (10th House):** {house_rulers.get('house_10_ruler', 'unknown')}\n"
                f"- **Money Ruler (2nd House):** {house_rulers.get('house_2_ruler', 'unknown')}\n"
                f"- **Love Ruler (7th House):** {house_rulers.get('house_7_ruler', 'unknown')}\n\n"
                f"If the user asks a specific question, USE THESE RULERS to answer accurately.\n"
            )
        
        # Build partner house rulers context block (for couples analysis)
        partner_rulers_context = ""
        if partner_house_rulers:
            partner_rulers_context = (
                f"\n\n*** PARTNER CONTEXT (Use when analyzing events with target='Partner') ***\n"
                f"- **Partner's Health Ruler (6th House):** {partner_house_rulers.get('house_6_ruler', 'unknown')}\n"
                f"- **Partner's Career Ruler (10th House):** {partner_house_rulers.get('house_10_ruler', 'unknown')}\n"
                f"- **Partner's Money Ruler (2nd House):** {partner_house_rulers.get('house_2_ruler', 'unknown')}\n"
                f"- **Partner's Love Ruler (7th House):** {partner_house_rulers.get('house_7_ruler', 'unknown')}\n\n"
                f"**INSTRUCTION FOR COUPLES:**\n"
                f"When comparing, look for CONFLICTS or SYNERGY between the User's rulers and the Partner's rulers.\n"
                f"Example: If User's Career Ruler is blocked but Partner's Money Ruler is active -> \"One earns while the other struggles.\"\n"
            )
        
        # Inject house rulers into the prompt based on report type
        if house_rulers:
            if report_type == "health":
                house_6_ruler = house_rulers.get("house_6_ruler", "unknown")
                base_persona = base_persona.replace("{house_6_ruler}", house_6_ruler)
            elif report_type == "career":
                house_10_ruler = house_rulers.get("house_10_ruler", "unknown")
                base_persona = base_persona.replace("{house_10_ruler}", house_10_ruler)
            elif report_type == "love":
                house_7_ruler = house_rulers.get("house_7_ruler", "unknown")
                base_persona = base_persona.replace("{house_7_ruler}", house_7_ruler)
            elif report_type == "money":
                house_2_ruler = house_rulers.get("house_2_ruler", "unknown")
                base_persona = base_persona.replace("{house_2_ruler}", house_2_ruler)
        else:
            # Fallback if house_rulers is None or empty
            base_persona = base_persona.replace("{house_6_ruler}", "unknown")
            base_persona = base_persona.replace("{house_10_ruler}", "unknown")
            base_persona = base_persona.replace("{house_7_ruler}", "unknown")
            base_persona = base_persona.replace("{house_2_ruler}", "unknown")
        
        # Build context based on whether partner is present
        if has_partner and partner_chart:
            context = (
                f"\nCONTEXT: You are analyzing a TIMELINE for TWO people ({user_display_name} and {partner_display_name}). "
                f"For each month, analyze how the astrological events affect BOTH individuals and their relationship interaction. "
                f"Focus on how their simultaneous transits create harmony or tension."
            )
        else:
            context = (
                f"\nCONTEXT: You are analyzing a TIMELINE for {user_display_name}. "
                f"For each month, focus specifically on how the astrological events relate to the report type ({report_type})."
            )
        
        # Add common rules
        common_rules = (
            f"\n\nCRITICAL RULES:\n"
            f"- You are an interpreter of RIGOROUS, PRE-CALCULATED ASTROLOGICAL EVENTS. Do NOT guess or invent aspects or events.\n"
            f"- The JSON 'timeline_events' already contains the EXACT aspect name, angle and orb (e.g. 'aspect': 'Trine', 'angle_deg': 120, 'orb': 0.2).\n"
            f"- If an event says 'aspect': 'Trine', you MUST treat it as a harmonious/flowing aspect. Do NOT reinterpret it as tense or difficult.\n"
            f"- If an event says 'aspect': 'Square' or 'Opposition', you MUST treat it as tense/challenging. Do NOT reinterpret it as easy or harmonious.\n"
            f"- NEVER change the aspect classification. The Python backend is the ONLY SOURCE OF TRUTH for aspect types.\n"
            f"- Do NOT calculate new aspects from planet positions. ONLY interpret the aspects explicitly listed in the events.\n"
            f"- Pay special attention to events with type 'INGRESS' (planets entering new signs). Use them to describe changes in the background atmosphere and overall themes.\n"
            f"- Always use the 'formatted_pos' field for planetary positions. Do NOT calculate from raw longitude.\n"
            f"- For angles (Ascendant, MC): Use 'Ascendant_formatted' and 'MC_formatted' fields.\n"
            f"- Map transit planets to NATAL houses (not transit houses).\n"
            f"- Focus on SPECIFIC dates within the month provided.\n"
        )
        
        # Add mandatory question answer section if user_question exists
        question_instruction = ""
        if user_question and user_question.strip():
            if language == "bg":
                question_instruction = (
                    f"\n\nIMPORTANT: Потребителят е задал КОНКРЕТЕН ВЪПРОС: \"{user_question}\".\n\n"
                    f"Трябва ДА добавиш задължителна финална секция в края на всеки месечен анализ със заглавие:\n"
                    f"\"### Отговор на вашия въпрос: {user_question}\"\n\n"
                    f"В тази секция:\n"
                    f"1. Синтезирай месечните събития специфично, за да отговориш на този въпрос.\n"
                    f"2. Оцени вероятността събитието да се случи ПРЕЗ ТОЗИ МЕСЕЦ на базата на аспектите (напр. \"Висока вероятност поради Jupiter\", или \"Мало вероятно поради Saturn блокира\").\n"
                    f"3. Бъди директен и конкретен. НЕ бъди неясен или уклончив.\n"
                )
            else:
                question_instruction = (
                    f"\n\nIMPORTANT: The user has asked a SPECIFIC QUESTION: \"{user_question}\".\n\n"
                    f"You MUST add a final section at the end of your response for this month titled:\n"
                    f"\"### Answer to your question: {user_question}\"\n\n"
                    f"In this section:\n"
                    f"1. Synthesize the monthly events specifically to answer this question.\n"
                    f"2. Assess the probability of the event happening THIS month based on the aspects (e.g., \"High probability due to Jupiter\", or \"Unlikely due to Saturn blocking\").\n"
                    f"3. Be direct and specific. Do NOT be vague.\n"
                )
        
        # Add strict Bulgarian language rules at the end
        language_rules = self._get_bulgarian_language_rules()
        
        return f"{base_persona}{house_rulers_context}{partner_rulers_context}{context}{common_rules}{question_instruction}{language_rules}"
    
    async def _process_monthly_chunk(
        self,
        month: str,
        monthly_events: List[Dict],
        report_type: str,
        language: str,
        natal_chart: Dict,
        partner_chart: Optional[Dict],
        user_display_name: str,
        partner_display_name: str,
        question: str,
        has_partner: bool
    ) -> str:
        """
        Process a single month's events and generate AI interpretation.
        
        Returns:
            Monthly forecast text or error message
        """
        # Ensure has_partner is properly set (defensive check)
        has_partner_flag = bool(has_partner and partner_chart is not None)
        
        try:
            # Calculate house rulers for the natal chart
            houses = natal_chart.get("houses", {})
            house_rulers = self.engine.get_house_rulers(houses) if houses else {}
            
            # Calculate house rulers for partner chart if present
            partner_house_rulers = None
            if partner_chart:
                partner_houses = partner_chart.get("houses", {})
                partner_house_rulers = self.engine.get_house_rulers(partner_houses) if partner_houses else {}
            
            # Build system prompt
            system_prompt = self._build_dynamic_system_prompt(
                report_type=report_type,
                language=language,
                natal_chart=natal_chart,
                partner_chart=partner_chart,
                user_display_name=user_display_name,
                partner_display_name=partner_display_name,
                has_partner=has_partner_flag,
                user_question=question,
                house_rulers=house_rulers,
                partner_house_rulers=partner_house_rulers
            )
            
            # Build user prompt with monthly events
            monthly_events_json = json.dumps(monthly_events, indent=2, ensure_ascii=False)
            
            user_prompt = f"PERIOD: {month}\n"
            user_prompt += f"FOCUS: {report_type.upper()}\n\n"
            
            if has_partner_flag:
                natal_json = json.dumps(natal_chart, indent=2, ensure_ascii=False)
                partner_json = json.dumps(partner_chart, indent=2, ensure_ascii=False)
                user_prompt += f"--- {user_display_name.upper()} NATAL CHART ---\n{natal_json}\n\n"
                user_prompt += f"--- {partner_display_name.upper()} NATAL CHART ---\n{partner_json}\n\n"
            else:
                natal_json = json.dumps(natal_chart, indent=2, ensure_ascii=False)
                user_prompt += f"--- NATAL CHART ---\n{natal_json}\n\n"
            
            user_prompt += f"--- TIMELINE EVENTS FOR {month} ---\n{monthly_events_json}\n\n"
            
            if question:
                user_prompt += f"User Question: {question}\n\n"
            
            user_prompt += f"Provide a detailed forecast for {month}, focusing on {report_type} themes."
            
            # Call OpenAI API
            response = await self.client.chat.completions.create(
                model="gpt-4o",
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt}
                ],
                temperature=0.7,
                max_tokens=2000  # Enough for detailed monthly analysis
            )
            
            content = response.choices[0].message.content
            return content.strip() if content else ""
            
        except Exception as e:
            error_msg = str(e)
            # Avoid exposing internal variable names in error messages
            return f"*Грешка при генериране на прогноза за {month}: {error_msg}*"
    
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
        timeline_events: Optional[List[Dict]] = None
    ) -> str:
        """
        Интерпретира натална, транзитна и опционално partner карта с помощта на GPT-4o.
        
        Args:
            natal_chart: Речник с данни от наталната карта
            transit_chart: Речник с данни от транзитната карта
            partner_chart: Речник с данни от partner картата (опционално, за synastry)
            partner_name: Име на партньора (опционално)
            question: Конкретен въпрос от потребителя (опционално)
            target_date: Дата на транзитната карта
            language: Език за отговора (по подразбиране "bg" за български)
        
        Returns:
            Текстова интерпретация от AI
        """
        # Определяне на имената (използва се в различни режими)
        user_display_name = user_name if user_name else "User"
        partner_display_name = partner_name if partner_name else "Partner"
        
        # PRIORITY 1: DYNAMIC RELATIONSHIP FORECAST (timeline_events AND partner_chart) - Monthly Chunking
        if timeline_events and partner_chart:
            # Group events by month
            events_by_month = defaultdict(list)
            for event in timeline_events:
                month_key = event['date'][:7]  # "YYYY-MM"
                events_by_month[month_key].append(event)
            
            # Sort months
            sorted_months = sorted(events_by_month.keys())
            
            if not sorted_months:
                return "Няма събития за анализиране в избрания период."
            
            # Build header
            start_date_str = sorted_months[0]
            end_date_str = sorted_months[-1]
            
            # Format month names for display (Bulgarian)
            month_names = {
                "01": "Януари", "02": "Февруари", "03": "Март", "04": "Април",
                "05": "Май", "06": "Юни", "07": "Юли", "08": "Август",
                "09": "Септември", "10": "Октомври", "11": "Ноември", "12": "Декември"
            }
            
            full_report = f"# Прогноза за Връзка ({month_names.get(start_date_str[5:7], start_date_str[5:7])} {start_date_str[:4]} - {month_names.get(end_date_str[5:7], end_date_str[5:7])} {end_date_str[:4]})\n\n"
            
            if question:
                full_report += f"**Въпрос:** {question}\n\n"
            
            full_report += f"**Анализ за {user_display_name} и {partner_display_name}**\n\n---\n\n"
            
            # Process each month
            for idx, month in enumerate(sorted_months):
                monthly_events = events_by_month[month]
                
                monthly_text = await self._process_monthly_chunk(
                    month=month,
                    monthly_events=monthly_events,
                    report_type=report_type,
                    language=language,
                    natal_chart=natal_chart,
                    partner_chart=partner_chart,
                    user_display_name=user_display_name,
                    partner_display_name=partner_display_name,
                    question=question,  # Include question in ALL chunks so each month answers it
                    has_partner=True
                )
                
                # Format month for display
                month_display = f"{month_names.get(month[5:7], month[5:7])} {month[:4]}"
                full_report += f"\n\n## Прогноза за {month_display}\n\n{monthly_text}\n\n---\n"
            
            return full_report
        
        elif timeline_events:
            # PRIORITY 2: DYNAMIC PERSONAL FORECAST MODE (Monthly Chunking)
            # Group events by month
            events_by_month = defaultdict(list)
            for event in timeline_events:
                month_key = event['date'][:7]  # "YYYY-MM"
                events_by_month[month_key].append(event)
            
            # Sort months
            sorted_months = sorted(events_by_month.keys())
            
            if not sorted_months:
                return "Няма събития за анализиране в избрания период."
            
            # Build header
            start_date_str = sorted_months[0]
            end_date_str = sorted_months[-1]
            
            # Format month names for display (Bulgarian)
            month_names = {
                "01": "Януари", "02": "Февруари", "03": "Март", "04": "Април",
                "05": "Май", "06": "Юни", "07": "Юли", "08": "Август",
                "09": "Септември", "10": "Октомври", "11": "Ноември", "12": "Декември"
            }
            
            full_report = f"# Астрологична Прогноза ({month_names.get(start_date_str[5:7], start_date_str[5:7])} {start_date_str[:4]} - {month_names.get(end_date_str[5:7], end_date_str[5:7])} {end_date_str[:4]})\n\n"
            
            if question:
                full_report += f"**Въпрос:** {question}\n\n"
            
            full_report += "---\n\n"
            
            # Process each month
            for idx, month in enumerate(sorted_months):
                monthly_events = events_by_month[month]
                
                monthly_text = await self._process_monthly_chunk(
                    month=month,
                    monthly_events=monthly_events,
                    report_type=report_type,
                    language=language,
                    natal_chart=natal_chart,
                    partner_chart=None,
                    user_display_name=user_display_name,
                    partner_display_name=partner_display_name,
                    question=question,  # Include question in ALL chunks so each month answers it
                    has_partner=False
                )
                
                # Format month for display
                month_display = f"{month_names.get(month[5:7], month[5:7])} {month[:4]}"
                full_report += f"\n\n## Прогноза за {month_display}\n\n{monthly_text}\n\n---\n"
            
            return full_report
        
        elif partner_chart and transit_chart:
            # PRIORITY 3: RELATIONSHIP TRANSIT FORECAST (Snapshot - Single Date)
            base_persona = PROMPT_TEMPLATES.get(report_type, PROMPT_TEMPLATES["general"])
            
            system_prompt = (
                f"MODE: RELATIONSHIP TRANSIT FORECAST (Snapshot)\n"
                f"You are an Expert Predictive Astrologer specializing in Relationship Timing.\n"
                f"You have the Natal Charts of {user_display_name} and {partner_display_name}, and the TRANSIT CHART for the specific moment: {target_date}.\n\n"
                f"YOUR TASK:\n\n"
                f"1. **Analyze Current Transits to {user_display_name}:**\n"
                f"   - Which User planets are being triggered right now?\n"
                f"   - What aspects are forming between transit planets and User's natal planets?\n"
                f"   - Which User houses are being activated?\n"
                f"   - What is the emotional/psychological state of {user_display_name} on this date?\n\n"
                f"2. **Analyze Current Transits to {partner_display_name}:**\n"
                f"   - Which Partner planets are being triggered right now?\n"
                f"   - What aspects are forming between transit planets and Partner's natal planets?\n"
                f"   - Which Partner houses are being activated?\n"
                f"   - What is the emotional/psychological state of {partner_display_name} on this date?\n\n"
                f"3. **SYNTHESIS (The Most Important Part):**\n"
                f"   - How do these simultaneous astrological weathers interact?\n"
                f"   - Example: '{user_display_name} is under Saturn pressure (stressed, feeling restricted), while {partner_display_name} is having a Jupiter return (happy, expansive). {partner_display_name} needs to be patient with {user_display_name} today.'\n"
                f"   - Example: 'Both {user_display_name} and {partner_display_name} have Mars transits (conflict energy). High risk of arguments. Recommendation: Avoid important decisions or sensitive topics on this date.'\n"
                f"   - Example: '{user_display_name} has Venus trine (harmony, romance), while {partner_display_name} has Neptune square (confusion, unclear communication). {user_display_name} may feel romantic, but {partner_display_name} may be unclear about intentions. Patience and clarity needed.'\n\n"
                f"4. **Practical Recommendations:**\n"
                f"   - Is this a good date for romance, serious talks, or shared activities?\n"
                f"   - What should the couple focus on or avoid on this specific date?\n"
                f"   - Are there opportunities for growth or intimacy?\n"
                f"   - Are there warning signs of conflict or miscommunication?\n\n"
                f"5. **Use Natal Context:** Reference both natal charts to explain WHY these specific transits matter for THIS relationship.\n\n"
                f"CRITICAL RULES:\n"
                f"- Map transit planets to NATAL houses (not transit houses). Use {user_display_name}'s natal house cusps for User transits, and {partner_display_name}'s natal house cusps for Partner transits.\n"
                f"- Always use the 'formatted_pos' field for planetary positions. Do NOT calculate from raw longitude.\n"
                f"- For angles (Ascendant, MC): Use 'Ascendant_formatted' and 'MC_formatted' fields.\n"
                f"- Focus on the SPECIFIC DATE provided ({target_date}). This is a snapshot analysis, not a timeline.\n"
                f"- Do NOT perform general synastry analysis (inter-aspects between natal charts) unless relevant to understanding the transit interactions.\n"
            )
            
            # Add strict Bulgarian language rules
            system_prompt += self._get_bulgarian_language_rules()
            
            # Форматиране на данните като JSON за user_prompt
            natal_json = json.dumps(natal_chart, indent=2, ensure_ascii=False)
            partner_json = json.dumps(partner_chart, indent=2, ensure_ascii=False)
            
            # За транзитната карта, извличаме само планетите (без къщите)
            transit_planets_only = {
                "planets": transit_chart.get("planets", {}),
                "datetime_utc": transit_chart.get("datetime_utc", ""),
                "julian_day": transit_chart.get("julian_day", 0),
                "timezone": transit_chart.get("timezone", ""),
                "datetime_local": transit_chart.get("datetime_local", "")
            }
            transit_json = json.dumps(transit_planets_only, indent=2, ensure_ascii=False)
            
            user_prompt = f"User Question: {question if question else 'Provide a relationship forecast for this specific date.'}\n\n"
            user_prompt += f"--- {user_display_name.upper()} NATAL CHART ---\n"
            user_prompt += "CRITICAL: Use these House Cusps for ALL house overlay calculations for User's transits.\n"
            user_prompt += "CRITICAL: Use the 'formatted_pos' field for each planet's position. Do NOT calculate from 'longitude'.\n"
            user_prompt += f"{natal_json}\n\n"
            user_prompt += f"--- {partner_display_name.upper()} NATAL CHART ---\n"
            user_prompt += "CRITICAL: Use these House Cusps for ALL house overlay calculations for Partner's transits.\n"
            user_prompt += "CRITICAL: Use the 'formatted_pos' field for each planet's position. Do NOT calculate from 'longitude'.\n"
            user_prompt += f"{partner_json}\n\n"
            user_prompt += f"--- TRANSIT PLANETARY POSITIONS (Date: {target_date}) ---\n"
            user_prompt += "IMPORTANT: Ignore any house cusps in this data. Map these planets to the NATAL houses above.\n"
            user_prompt += "CRITICAL: Use the 'formatted_pos' field for each planet's position. Do NOT calculate from 'longitude'.\n"
            user_prompt += f"{transit_json}\n\n"
            user_prompt += (
                f"Analyze how the current transits on {target_date} affect {user_display_name} and {partner_display_name} individually, "
                f"and then synthesize how these simultaneous astrological energies interact as a couple. "
                f"Provide practical recommendations for this specific date."
            )
        
        elif partner_chart:
            # PRIORITY 4: STATIC SYNASTRY MODE
            base_persona = PROMPT_TEMPLATES.get(report_type, PROMPT_TEMPLATES["general"])
            context_instruction = "\nCONTEXT: SYNASTRY MODE. Apply the persona above to the RELATIONSHIP dynamics between User and Partner."
            system_prompt = f"{base_persona}\n{context_instruction}\n\n"
        
            # Add Synastry rules
            system_prompt += (
                "SYNASTRY RULES:\n\n"
                "1. INTER-ASPECTS (Planet to Planet):\n"
                "   - Compare User's Planets vs. Partner's Planets.\n"
                "   - Prioritize 'Personal Planets' (Sun, Moon, Mercury, Venus, Mars).\n"
                "   - Look for Sun/Moon conjunctions (Soulmate indicator).\n"
                "   - Look for Mars/Venus aspects (Sexual chemistry).\n"
                "   - Calculate aspects: Conjunctions (0°), Trines (120°), Sextiles (60°), Squares (90°), Oppositions (180°).\n"
                "   - Use orb tolerance: Conjunctions/Oppositions (8°), Trines/Squares (6°), Sextiles (4°).\n\n"
                "2. HOUSE OVERLAYS (CRITICAL):\n"
                "   - Take Partner's Planets and place them into USER'S Natal Houses.\n"
                "   - Use User's Natal House cusps to determine where Partner's planets fall.\n\n"
                "   HOUSE OVERLAY CALCULATION RULE:\n"
                "   To determine which USER HOUSE a Partner's planet falls into, compare the degrees mathematically:\n\n"
                "   Step 1: Look at the User's House Cusps (e.g., House 1: Cancer 14°, House 2: Leo 5°).\n"
                "   Step 2: Look at the Partner's Planet (e.g., Sun: Cancer 22°).\n"
                "   Step 3: Logic:\n"
                "     - If Partner Planet is in the SAME SIGN as House X Cusp AND Degree is GREATER or EQUAL, it is in House X.\n"
                "     - If Partner Planet is in the SAME SIGN as House X Cusp AND Degree is LESS, check the previous house.\n"
                "     - If Partner Planet is in a DIFFERENT SIGN, find the house whose cusp sign matches the planet's sign.\n\n"
                "   Examples:\n"
                "   - User ASC (House 1) is Cancer 14°. Partner Sun is Cancer 22°. Since 22 > 14 (same sign), Sun is in House 1.\n"
                "   - User ASC is Cancer 14°. Partner Sun is Cancer 5°. Since 5 < 14 (same sign), Sun is in House 12.\n"
                "   - User House 7 cusp is Libra 10°. Partner Venus is Libra 15°. Since 15 > 10 (same sign), Venus is in House 7.\n\n"
                "   Key houses: 1st (Identity), 4th (Home/Emotional Security), 5th (Romance), 7th (Partnership), 10th (Career/Public Image).\n\n"
            )
            
            # Общи инструкции
            system_prompt += (
                "CRITICAL: Position Formatting Rules\n"
                "- Each planet in the JSON has a 'formatted_pos' field (e.g., 'Aries 23°02'').\n"
                "- ALWAYS use the 'formatted_pos' string provided in the JSON for your analysis.\n"
                "- Do NOT attempt to calculate degrees from the raw 'longitude' float.\n"
                "- Do NOT guess or estimate positions. Use the exact 'formatted_pos' value.\n"
                "- The 'formatted_pos' is pre-calculated and accurate - trust it completely.\n\n"
                "- For angles (Ascendant, MC): Use 'Ascendant_formatted' and 'MC_formatted' fields.\n"
                "- Do NOT calculate Ascendant or MC signs from raw longitude values.\n"
                "- The formatted angles are in the 'angles' object: angles.Ascendant_formatted and angles.MC_formatted.\n\n"
                "- Do NOT guess positions. Use the provided JSON data precisely.\n"
                "- Focus on what is ACTUALLY happening based on the data, not general interpretations.\n"
            )
            
            # Add strict Bulgarian language rules
            system_prompt += self._get_bulgarian_language_rules()
            
            # Форматиране на данните като JSON за user_prompt
            natal_json = json.dumps(natal_chart, indent=2, ensure_ascii=False)
            partner_json = json.dumps(partner_chart, indent=2, ensure_ascii=False)
            
            user_prompt = f"User Question: {question if question else 'General analysis'}\n\n"
            user_prompt += f"--- {user_display_name.upper()} NATAL CHART ---\n"
            user_prompt += "CRITICAL: Use these House Cusps for ALL house overlay calculations.\n"
            user_prompt += "CRITICAL: Use the 'formatted_pos' field for each planet's position. Do NOT calculate from 'longitude'.\n"
            user_prompt += f"{natal_json}\n\n"
            user_prompt += f"--- {partner_display_name.upper()} NATAL CHART ---\n"
            user_prompt += "CRITICAL: Use the 'formatted_pos' field for each planet's position. Do NOT calculate from 'longitude'.\n"
            user_prompt += f"IMPORTANT: For house overlays, place {partner_display_name}'s planets into {user_display_name}'s houses (use {user_display_name}'s house cusps above).\n"
            user_prompt += f"{partner_json}\n\n"
            user_prompt += (
                f"Please provide a comprehensive SYNASTRY analysis covering:\n\n"
                f"1. INTER-ASPECTS:\n"
                f"   - Compare {user_display_name}'s planets with {partner_display_name}'s planets.\n"
                f"   - Focus on Personal Planets (Sun, Moon, Mercury, Venus, Mars).\n"
                f"   - Identify Sun/Moon conjunctions (soulmate potential).\n"
                f"   - Identify Mars/Venus aspects (sexual chemistry).\n"
                f"   - List all significant aspects with exact degrees.\n\n"
                f"2. HOUSE OVERLAYS:\n"
                f"   - Place {partner_display_name}'s planets into {user_display_name}'s houses using mathematical degree comparison.\n"
                f"   - CALCULATION RULE: If {partner_display_name}'s planet is in the SAME SIGN as {user_display_name}'s House X cusp AND degree is GREATER or EQUAL, it is in House X.\n"
                f"   - Example: {user_display_name}'s ASC (House 1) is Cancer 14°. {partner_display_name}'s Sun is Cancer 22°. Since 22 > 14 (same sign), Sun is in House 1.\n"
                f"   - Example: {user_display_name}'s ASC is Cancer 14°. {partner_display_name}'s Sun is Cancer 5°. Since 5 < 14 (same sign), Sun is in House 12.\n"
                f"   - How does {partner_display_name} impact {user_display_name}'s life goals (10th house) and emotional security (4th house)?\n"
                f"   - Analyze 1st house (identity), 5th house (romance), 7th house (partnership), 8th house (intimacy).\n\n"
                f"3. RELATIONSHIP AREAS:\n"
                f"   - Emotional connection (Moon aspects, 4th house overlays)\n"
                f"   - Communication (Mercury aspects, 3rd house overlays)\n"
                f"   - Sexual chemistry (Mars/Venus aspects, 5th/8th house overlays)\n"
                f"   - Long-term potential (Saturn aspects, 7th/10th house overlays)\n\n"
                f"4. Use ONLY the 'formatted_pos' values provided. Do NOT calculate from raw longitude.\n"
                f"5. Do NOT predict the future or mention transits."
            )
        
        else:
            # PRIORITY 5: DEFAULT - NATAL/TRANSIT ANALYSIS
            base_persona = PROMPT_TEMPLATES.get(report_type, PROMPT_TEMPLATES["general"])
            
            # Add Context (Natal, Transit, or Synastry)
            if transit_chart:
                context_instruction = "\nCONTEXT: FORECAST/TRANSIT MODE. Apply the persona above to the CURRENT TRANSITS. How do these transits affect the specific topic (Career/Love/Psyche/Money)?"
            elif partner_chart:
                context_instruction = "\nCONTEXT: SYNASTRY MODE. Apply the persona above to the RELATIONSHIP dynamics between User and Partner."
            else:
                context_instruction = "\nCONTEXT: NATAL CHART ONLY. Analyze the birth potential regarding this specific topic."
            
            # Build base system prompt
            system_prompt = f"{base_persona}\n{context_instruction}\n\n"
            
            # Add Synastry rules if partner chart exists
            if partner_chart:
                system_prompt += (
                    "SYNASTRY RULES:\n\n"
                    "1. INTER-ASPECTS (Planet to Planet):\n"
                    "   - Compare User's Planets vs. Partner's Planets.\n"
                    "   - Prioritize 'Personal Planets' (Sun, Moon, Mercury, Venus, Mars).\n"
                    "   - Look for Sun/Moon conjunctions (Soulmate indicator).\n"
                    "   - Look for Mars/Venus aspects (Sexual chemistry).\n"
                    "   - Calculate aspects: Conjunctions (0°), Trines (120°), Sextiles (60°), Squares (90°), Oppositions (180°).\n"
                    "   - Use orb tolerance: Conjunctions/Oppositions (8°), Trines/Squares (6°), Sextiles (4°).\n\n"
                    "2. HOUSE OVERLAYS (CRITICAL):\n"
                    "   - Take Partner's Planets and place them into USER'S Natal Houses.\n"
                    "   - Use User's Natal House cusps to determine where Partner's planets fall.\n\n"
                    "   HOUSE OVERLAY CALCULATION RULE:\n"
                    "   To determine which USER HOUSE a Partner's planet falls into, compare the degrees mathematically:\n\n"
                    "   Step 1: Look at the User's House Cusps (e.g., House 1: Cancer 14°, House 2: Leo 5°).\n"
                    "   Step 2: Look at the Partner's Planet (e.g., Sun: Cancer 22°).\n"
                    "   Step 3: Logic:\n"
                    "     - If Partner Planet is in the SAME SIGN as House X Cusp AND Degree is GREATER or EQUAL, it is in House X.\n"
                    "     - If Partner Planet is in the SAME SIGN as House X Cusp AND Degree is LESS, check the previous house.\n"
                    "     - If Partner Planet is in a DIFFERENT SIGN, find the house whose cusp sign matches the planet's sign.\n\n"
                    "   Examples:\n"
                    "   - User ASC (House 1) is Cancer 14°. Partner Sun is Cancer 22°. Since 22 > 14 (same sign), Sun is in House 1.\n"
                    "   - User ASC is Cancer 14°. Partner Sun is Cancer 5°. Since 5 < 14 (same sign), Sun is in House 12.\n"
                    "   - User House 7 cusp is Libra 10°. Partner Venus is Libra 15°. Since 15 > 10 (same sign), Venus is in House 7.\n\n"
                    "   Key houses: 1st (Identity), 4th (Home/Emotional Security), 5th (Romance), 7th (Partnership), 10th (Career/Public Image).\n\n"
                )
            
            # Add Transit rules if transit chart exists
            if transit_chart:
                system_prompt += (
                    "TRANSIT ANALYSIS RULES:\n"
                    "1. NATAL CHART - The user's birth potential, showing their inherent nature and life patterns.\n"
                    "2. TRANSIT CHART - The sky at the moment of the question/future date, showing current planetary influences.\n\n"
                    "CRITICAL RULE FOR TRANSITS:\n"
                    "When analyzing the 'Transit Chart', IGNORE the 'houses' data inside the Transit Chart JSON.\n"
                    "You MUST map the 'planets' from the Transit Chart into the 'houses' of the NATAL CHART.\n\n"
                    "Example:\n"
                    "- If Natal ASC is Cancer (House 1 starts in Cancer).\n"
                    "- And Transit Sun is in Sagittarius.\n"
                    "- Then Transit Sun is in the Natal 6th House (NOT the 3rd).\n"
                    "- ALWAYS calculate the house position of transit planets based on the NATAL house cusps provided.\n\n"
                    "Key Analysis Points:\n"
                    "- Look for Transiting Jupiter/Saturn impacting Natal MC (Career) or Ascendant (Identity).\n"
                    "- Identify exact aspects (Conjunctions, Trines, Squares, Oppositions) between Transit and Natal planets.\n"
                    "- Calculate orb tolerance: Conjunctions/Squares/Oppositions (8°), Trines/Sextiles (6°).\n"
                    "- Map transit planets to NATAL houses, not transit houses.\n"
                    "- Be specific about the DATE provided in the transit chart.\n\n"
                )
            
            # Общи инструкции
            system_prompt += (
                "CRITICAL: Position Formatting Rules\n"
                "- Each planet in the JSON has a 'formatted_pos' field (e.g., 'Aries 23°02'').\n"
                "- ALWAYS use the 'formatted_pos' string provided in the JSON for your analysis.\n"
                "- Do NOT attempt to calculate degrees from the raw 'longitude' float.\n"
                "- Do NOT guess or estimate positions. Use the exact 'formatted_pos' value.\n"
                "- The 'formatted_pos' is pre-calculated and accurate - trust it completely.\n\n"
                "- For angles (Ascendant, MC): Use 'Ascendant_formatted' and 'MC_formatted' fields.\n"
                "- Do NOT calculate Ascendant or MC signs from raw longitude values.\n"
                "- The formatted angles are in the 'angles' object: angles.Ascendant_formatted and angles.MC_formatted.\n\n"
                "- Do NOT guess positions. Use the provided JSON data precisely.\n"
                "- Focus on what is ACTUALLY happening based on the data, not general interpretations.\n"
            )
            
            # Add strict Bulgarian language rules
            system_prompt += self._get_bulgarian_language_rules()
            
            # Форматиране на данните като JSON за user_prompt
            natal_json = json.dumps(natal_chart, indent=2, ensure_ascii=False)
            
            user_prompt = f"User Question: {question if question else 'General analysis'}\n\n"
            user_prompt += f"--- {user_display_name.upper()} NATAL CHART ---\n"
            user_prompt += "CRITICAL: Use these House Cusps for ALL house overlay calculations.\n"
            user_prompt += "CRITICAL: Use the 'formatted_pos' field for each planet's position. Do NOT calculate from 'longitude'.\n"
            user_prompt += f"{natal_json}\n\n"
            
            if partner_chart:
                partner_json = json.dumps(partner_chart, indent=2, ensure_ascii=False)
                user_prompt += f"--- {partner_display_name.upper()} NATAL CHART ---\n"
                user_prompt += "CRITICAL: Use the 'formatted_pos' field for each planet's position. Do NOT calculate from 'longitude'.\n"
                user_prompt += f"IMPORTANT: For house overlays, place {partner_display_name}'s planets into {user_display_name}'s houses (use {user_display_name}'s house cusps above).\n"
                user_prompt += f"{partner_json}\n\n"
            
            # Условно добавяне на транзитни данни
            if transit_chart is not None:
                # За транзитната карта, извличаме само планетите (без къщите)
                transit_planets_only = {
                    "planets": transit_chart.get("planets", {}),
                    "datetime_utc": transit_chart.get("datetime_utc", ""),
                    "julian_day": transit_chart.get("julian_day", 0),
                    "timezone": transit_chart.get("timezone", ""),
                    "datetime_local": transit_chart.get("datetime_local", "")
                }
                transit_json = json.dumps(transit_planets_only, indent=2, ensure_ascii=False)
                
                user_prompt += f"--- TRANSIT PLANETARY POSITIONS (Date: {target_date}) ---\n"
                user_prompt += "IMPORTANT: Ignore any house cusps in this data. Map these planets to the NATAL houses above.\n"
                user_prompt += "CRITICAL: Use the 'formatted_pos' field for each planet's position. Do NOT calculate from 'longitude'.\n"
                user_prompt += f"{transit_json}\n\n"
            
            # Условни инструкции базирани на режима
            if transit_chart is None:
                if partner_chart:
                    user_prompt += (
                        f"Please provide a comprehensive SYNASTRY analysis covering:\n\n"
                        f"1. INTER-ASPECTS:\n"
                        f"   - Compare {user_display_name}'s planets with {partner_display_name}'s planets.\n"
                        f"   - Focus on Personal Planets (Sun, Moon, Mercury, Venus, Mars).\n"
                        f"   - Identify Sun/Moon conjunctions (soulmate potential).\n"
                        f"   - Identify Mars/Venus aspects (sexual chemistry).\n"
                        f"   - List all significant aspects with exact degrees.\n\n"
                        f"2. HOUSE OVERLAYS:\n"
                        f"   - Place {partner_display_name}'s planets into {user_display_name}'s houses using mathematical degree comparison.\n"
                        f"   - CALCULATION RULE: If {partner_display_name}'s planet is in the SAME SIGN as {user_display_name}'s House X cusp AND degree is GREATER or EQUAL, it is in House X.\n"
                        f"   - Example: {user_display_name}'s ASC (House 1) is Cancer 14°. {partner_display_name}'s Sun is Cancer 22°. Since 22 > 14 (same sign), Sun is in House 1.\n"
                        f"   - Example: {user_display_name}'s ASC is Cancer 14°. {partner_display_name}'s Sun is Cancer 5°. Since 5 < 14 (same sign), Sun is in House 12.\n"
                        f"   - How does {partner_display_name} impact {user_display_name}'s life goals (10th house) and emotional security (4th house)?\n"
                        f"   - Analyze 1st house (identity), 5th house (romance), 7th house (partnership), 8th house (intimacy).\n\n"
                        f"3. RELATIONSHIP AREAS:\n"
                        f"   - Emotional connection (Moon aspects, 4th house overlays)\n"
                        f"   - Communication (Mercury aspects, 3rd house overlays)\n"
                        f"   - Sexual chemistry (Mars/Venus aspects, 5th/8th house overlays)\n"
                        f"   - Long-term potential (Saturn aspects, 7th/10th house overlays)\n\n"
                        f"4. Use ONLY the 'formatted_pos' values provided. Do NOT calculate from raw longitude.\n"
                        f"5. Do NOT predict the future or mention transits."
                    )
                else:
                    user_prompt += (
                        "Please provide a comprehensive NATAL CHART analysis:\n"
                        "1. Analyze personality traits based on planetary positions and signs.\n"
                        "2. Identify life themes and karmic patterns.\n"
                        "3. Explain strengths and challenges from aspects.\n"
                        "4. Describe house placements and their meanings.\n"
                        "5. Focus on psychological patterns and inner motivations.\n"
                        "6. Do NOT predict the future or mention transits.\n"
                        "7. Focus on the person's inherent nature and potential."
                    )
            else:
                if partner_chart:
                    user_prompt += (
                        f"Please provide a comprehensive SYNASTRY + FORECAST analysis:\n\n"
                        f"1. SYNASTRY (Compatibility):\n"
                        f"   - Compare {user_display_name}'s planets with {partner_display_name}'s planets.\n"
                        f"   - Focus on Personal Planets (Sun, Moon, Mercury, Venus, Mars).\n"
                        f"   - Identify Sun/Moon conjunctions (soulmate potential).\n"
                        f"   - Identify Mars/Venus aspects (sexual chemistry).\n"
                        f"   - House overlays: Use mathematical degree comparison.\n"
                        f"     * If {partner_display_name}'s planet is in the SAME SIGN as {user_display_name}'s House X cusp AND degree is GREATER or EQUAL, it is in House X.\n"
                        f"     * Example: {user_display_name}'s ASC (House 1) is Cancer 14°. {partner_display_name}'s Sun is Cancer 22°. Since 22 > 14 (same sign), Sun is in House 1.\n"
                        f"     * Example: {user_display_name}'s ASC is Cancer 14°. {partner_display_name}'s Sun is Cancer 5°. Since 5 < 14 (same sign), Sun is in House 12.\n"
                        f"   - How does {partner_display_name} impact {user_display_name}'s life goals (10th house) and emotional security (4th house)?\n\n"
                        f"2. RELATIONSHIP FORECAST (with Transits):\n"
                        f"   - Analyze transits to BOTH charts ({user_display_name} and {partner_display_name}).\n"
                        f"   - Will they stay together? Is there a crisis or opportunity?\n"
                        f"   - Map transit planets to {user_display_name}'s Natal Houses (not transit houses).\n"
                        f"   - Look for transiting planets activating relationship houses (7th house) or Venus/Mars.\n"
                        f"   - Identify periods of harmony or tension.\n\n"
                        f"3. RELATIONSHIP AREAS:\n"
                        f"   - Emotional connection (Moon aspects, 4th house overlays)\n"
                        f"   - Communication (Mercury aspects, 3rd house overlays)\n"
                        f"   - Sexual chemistry (Mars/Venus aspects, 5th/8th house overlays)\n"
                        f"   - Long-term potential (Saturn aspects, 7th/10th house overlays)\n\n"
                        f"4. Use ONLY the 'formatted_pos' values provided. Do NOT calculate from raw longitude.\n"
                        f"5. Be specific about dates, degrees, and aspects.\n"
                        f"6. Focus on practical implications for the relationship."
                    )
                else:
                    user_prompt += (
                        "Please provide a comprehensive FORECAST analysis:\n"
                        "1. Compare each transit planet's position to the natal chart.\n"
                        "2. Identify significant aspects between transit and natal planets.\n"
                        "3. Map transit planets to NATAL houses (not transit houses).\n"
                        "4. Analyze potential for meeting a new partner (5th/7th house transits) if relevant.\n"
                        "5. Explain what these transits mean for the person at this specific date.\n"
                        "6. Be specific about dates, degrees, and aspects.\n"
                        "7. Focus on practical implications and timing."
                    )
        
        # Добавяне на инструкция за езика
        if language == "bg":
            user_prompt += "\n\nМоля отговори на български език."
        elif language == "en":
            user_prompt += "\n\nPlease respond in English."
        
        try:
            # Извикване на OpenAI API
            response = await self.client.chat.completions.create(
                model="gpt-4o",
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt}
                ],
                temperature=0.7,
                max_tokens=3000  # Увеличено за по-подробен анализ
            )
            
            # Извличане на отговора
            content = response.choices[0].message.content
            interpretation = content.strip() if content else ""
            
            return interpretation
            
        except Exception as e:
            raise RuntimeError(f"Грешка при комуникация с OpenAI API: {e}")


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

