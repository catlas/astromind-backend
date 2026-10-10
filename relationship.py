"""
Контекст на отношенията между двамата души в анализ за двама (Фаза 12).

Вторият човек не е автоматично романтичен партньор. Потребителят избира какви са отношенията им, а изборът важи за целия
анализ: единичен, за дата и за период (всеки месец). Контекстът се добавя към системния промпт през ContextVar, както
паметта (виж memory.py), затова шаблоните на темите не се променят. Без избор или при непозната стойност се ползва
"общо взаимодействие": типът на отношенията не се гадае от пола или от групата на профила.

Контекстът никога не променя изчислените факти (позиции, домове, аспекти, дати). Той казва само как да се назоват и
тълкуват.
"""
from contextvars import ContextVar, Token
from typing import Dict, Optional, Tuple

DEFAULT = "general"

# код -> (име за потребителя, указания към AI)
CONTEXTS: Dict[str, Tuple[str, str]] = {
    "general": (
        "Общо взаимодействие",
        "The user did not say what kind of relationship connects these two people. Describe the connection neutrally, as "
        "an interaction between two people. Do not assume romance, attraction, sex, marriage, family ties or work ties.",
    ),
    "romantic": (
        "Романтична връзка",
        "The two people are in a romantic relationship (or are exploring one). Romantic, emotional and practical themes of "
        "a couple are in scope. Keep the style direct and concrete.",
    ),
    "friend": (
        "Приятелство",
        "The two people are friends. Describe trust, shared interests, communication, loyalty and how they support or "
        "irritate each other. Do NOT describe romance, attraction, sexuality or marriage. Read Venus, Mars and "
        "5th/7th/8th-house contacts as warmth, shared activity, rivalry or friction between friends.",
    ),
    "family": (
        "Семейство",
        "The two people are relatives (family members who are not described as parent and child). Describe shared roots, "
        "habits, duties, loyalty and recurring family patterns between them. Do NOT describe romance, attraction or "
        "sexuality. Do not invent facts about their family history.",
    ),
    "parent_child": (
        "Родител и дете",
        "One of the two people is the parent and the other the child. The order is not stated: do not guess who is the "
        "parent from the chart or from the order of the names. Describe care, guidance, authority, independence and how "
        "each can support the other. Keep the tone respectful, never romantic or sexual. No diagnoses and no claims about "
        "upbringing or the child's health. If a person may be a minor, focus on support and communication.",
    ),
    "work": (
        "Работа",
        "The two people work together (colleagues, business partners, or manager and employee; do not guess the roles). "
        "Describe communication, collaboration, decision-making, responsibilities and boundaries. Do NOT describe romance, "
        "attraction or sexuality. Do not invent facts about the company or the job.",
    ),
}

LABELS: Dict[str, str] = {code: label for code, (label, _) in CONTEXTS.items()}

COMMON_RULES = (
    "Rules for this relationship context: it applies to the whole analysis, including every month of a forecast, and it "
    "never changes any calculated fact. Name the people by their names or as 'the other person'. Unless the context is "
    "romantic, do not use words that name another kind of relationship (for example partner, lover, spouse, "
    "boyfriend, girlfriend). If the theme is Love, read it as emotional closeness and mutual care that fits this "
    "relationship. Write in Bulgarian."
)

current: ContextVar[str] = ContextVar("astro_relationship_context", default="")


def normalize(code: Optional[str]) -> str:
    """Известен код или "general"."""
    return code if code in CONTEXTS else DEFAULT


def is_valid(code: Optional[str]) -> bool:
    return code is None or code == "" or code in CONTEXTS


def block(code: Optional[str]) -> str:
    """Текстът към системния промпт за този контекст."""
    key = normalize(code)
    label, rules = CONTEXTS[key]
    return f"RELATIONSHIP CONTEXT (chosen by the user): {label}.\n{rules}\n{COMMON_RULES}"


def bind(code: Optional[str]) -> Token:
    """Закача контекста за текущата задача (виж generation.run). Връща токен за unbind."""
    return current.set(block(code))


def unbind(token: Token) -> None:
    current.reset(token)
