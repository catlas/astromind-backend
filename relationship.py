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
current_code: ContextVar[str] = ContextVar("astro_relationship_code", default="")


def normalize(code: Optional[str]) -> str:
    """Известен код или "general"."""
    return code if code in CONTEXTS else DEFAULT


def is_valid(code: Optional[str]) -> bool:
    return code is None or code == "" or code in CONTEXTS


PARENT_ROLES = ("user", "partner")          # кой от двамата е родителят при „Родител и дете“; без избор ролите не се гадаят

MINOR_RULE = (
    "The child is a MINOR (under 18). Focus on support, communication, safety and healthy independence. Never use a romantic or "
    "sexual frame, never describe the child's own future romantic or sexual relationships, and make no claims about upbringing, "
    "diagnoses or the child's health."
)


def block(code: Optional[str], parent: Optional[str] = None, names: Optional[Tuple[str, str]] = None, minor: bool = False) -> str:
    """Текстът към системния промпт за този контекст. parent е „user“ или „partner“ (само при „Родител и дете“), names са
    показваните имена на първия и втория човек, minor казва, че детето е непълнолетно."""
    key = normalize(code)
    label, rules = CONTEXTS[key]
    text = f"RELATIONSHIP CONTEXT (chosen by the user): {label}.\n{rules}\n{COMMON_RULES}"
    if key == "parent_child" and parent in PARENT_ROLES:
        first, second = names or ("the first person", "the second person")
        parent_name, child_name = (first, second) if parent == "user" else (second, first)
        text += (f"\nROLES (chosen by the user): {parent_name} is the parent and {child_name} is the child. Use these roles exactly; "
                 f"do not reverse them.")
        if minor:
            text += "\n" + MINOR_RULE
    return text


def bind(code: Optional[str], parent: Optional[str] = None, names: Optional[Tuple[str, str]] = None, minor: bool = False) -> Token:
    """Закача контекста за текущата задача (виж generation.run). Връща токен за unbind."""
    current_code.set(normalize(code))
    return current.set(block(code, parent, names, minor))


def unbind(token: Token) -> None:
    current.reset(token)
    current_code.set("")


# Заглавия и тон в шаблоните на темите, написани за романтична връзка („Romantic Atmosphere“, „sexual chemistry“): при друг
# контекст се подменят, за да съответстват на избраните отношения (R08, регресионен одит 11.10.2026). Фактите не се пипат.
# Шаблоните на темите са архивирани (archive/legacy_*.txt, 17–18.10.2026): подканите по карти не носят романтични заглавия,
# а adapt остава предпазна мрежа за всяка подкана, която минава през ai_interpreter._call_api.
_ATMOSPHERE = {"friend": "Atmosphere of the friendship this month", "family": "Atmosphere of closeness in the family this month",
               "parent_child": "Atmosphere of care and support this month", "work": "Atmosphere of the collaboration this month",
               "general": "Atmosphere of the connection this month"}
_NEUTRAL = (
    ("Romantic Atmosphere for this Month", None),
    ("TONE: Romantic, insightful, sensitive.", "TONE: Warm, insightful, sensitive."),
    ("FOCUS: Romantic timing, relationship dynamics, and partnership opportunities.",
     "FOCUS: Timing and dynamics of the connection between the two people."),
    ("(best times for romance, talks, or intimacy)", "(best times for closeness, talks, or joint plans)"),
    ("- Consider the 5th House (romance, dating) as a secondary indicator.", "- Consider the 5th House (shared joy, creativity) as a secondary indicator."),
    ("Venus-Mars (romantic/sexual chemistry)", "Venus-Mars (warmth, shared activity, friction)"),
    ("Focus: Romantic compatibility, emotional connection, sexual chemistry, long-term partnership potential",
     "Focus: Emotional connection, trust, shared joy, long-term bond"),
    ("Questions to answer: Emotional needs compatibility? Sexual chemistry? Fun and romance? Long-term potential?",
     "Questions to answer: Emotional needs compatibility? Trust? Shared joy? Long-term bond?"),
    ("Priority Houses: 5th (romance/fun), 7th (committed partnership), 8th (intimacy/sexuality)",
     "Priority Houses: 5th (shared joy), 7th (close one-to-one bond), 8th (deep trust and shared resources)"),
)


def adapt(text: str) -> str:
    """Подменя романтичните заглавия и тон в промпта според избрания контекст; при романтична връзка или без контекст не пипа нищо."""
    code = current_code.get()
    if not text or not code or code == "romantic":
        return text
    for old, new in _NEUTRAL:
        if old in text:
            text = text.replace(old, new if new is not None else _ATMOSPHERE.get(code, _ATMOSPHERE["general"]))
    return text
