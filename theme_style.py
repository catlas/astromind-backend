"""
Стил и призма на две теми: „Здраве“ и „Карма и род“ (19.10.2026, по искане на собственика).

Собственикът поиска две малки допълнения към генератора и каза, че всичко останало му харесва:
- „Здраве“: по-конкретно. Потенциалните здравословни проблеми не се филтрират до „символична тема“, а се назовават
  конкретно: зона на тялото, система, вид оплаквания, какво ги задейства. Остава безопасната рамка: склонност, не диагноза;
  без сигурна бъдеща болест, без тежки диагнози, без дати, без лекарства, без отлагане на лекар. Текстовете „Застраховка“
  (safety.HEALTH_NOTE_HTML и safety.DISCLAIMER_HTML) остават непроменени и се добавят както досега.
- „Карма и род“: по системата на Хули Леонис (Xyli Leonis, български астролог, който съчетава класическата астрология с
  родов анализ): карма като път на душата, уроците на Сатурн, Луната като истинска същност, възлите като посока на душата,
  ретроградните планети като кармичен показател, родови модели и родова памет. Гласът е по-мистичен и духовен. Фактите,
  правилата за това какво се назовава в кой раздел и безопасните граници (нищо конкретно за реални родители, предци и
  минали животи) не се променят.

Блокът върви като останалите контексти (виж relationship.py, birthtime.py): ContextVar, който generation.run закача за
целия анализ и който ai_interpreter._call_api добавя към системния промпт на всяка AI заявка (и на поправките), затова
шаблоните на режимите не се пипат. Кодът на темата върви в ContextVar и за проверките на текста (safety.health_findings,
safety.biography_findings): „Здраве“ позволява да се назове слабо място, „Карма и род“ позволява езика на родовите модели.
Само тези две теми се променят; всички останали (общ, кариера, пари, любов) остават точно както бяха.
"""
from contextvars import ContextVar, Token
from typing import Optional

# Блокът към системния промпт ("" за всички теми освен двете)
current: ContextVar[str] = ContextVar("astro_theme_style", default="")
# Кодът на темата на текущия анализ (health, karmic, general, …) за проверките на текста
current_theme: ContextVar[str] = ContextVar("astro_theme_code", default="")

HEALTH = "health"
KARMIC = "karmic"

HEALTH_BLOCK = (
    "THEME STYLE: HEALTH. The reader chose the Health theme and asked for CONCRETE content, not a diluted one. This block changes how "
    "concrete you are about health tendencies. It adds no fact and removes no safety limit.\n"
    "- Do not water the topic down. Start every card text from the body area or from what the person may notice, never from a "
    "planet, a sign, a house or an aspect, and name the potential problems that area may show in plain Bulgarian words (two possible "
    "openings, shown only for their shape and not to be copied: \"Стомахът и храносмилането са чувствителна зона: …\" and \"При "
    "претоварване тялото първо реагира със схващане във врата и раменете …\"). A potential weak point is named openly. It is never "
    "hidden behind words such as \"енергия\", \"баланс\" or \"символична тема\". This replaces the earlier wish for only symbolic "
    "and neutral health wording.\n"
    "- Frame every weak point as a tendency or potential of the chart (a tendency to, a sensitive area, possible complaints, worth "
    "attention), in different words from card to card. Never state it as a diagnosis, never as an illness the person has now "
    "(\"имате гастрит\") and never as a certain future illness (\"ще получите …\"). Say what triggers or worsens it (stress, "
    "overload, irregular rhythm, cold, too little rest, too much of something) and how it shows itself early. Write all of this as "
    "ordinary sentences in plain Bulgarian: no labels such as \"Навик:\" or \"Тригер:\" and not the anglicism \"тригер\".\n"
    "- Meaning vocabulary of classical medical astrology, for your understanding only; its English names are keys for you and never "
    "words for the text. The headings already state the planets, signs, houses and aspects, and the section rules forbid naming them "
    "in card texts and in the closing blocks: translate instead of echoing (a heading about the Moon in the sign of the stomach "
    "becomes a text about the stomach and digestion that never names the Moon, the sign or the house). Never print a sign, planet, "
    "house, angle or aspect name and never speak of the direction of planetary motion.\n"
    "  Signs and body areas: Aries head, face, eyes, headaches; Taurus throat, neck, thyroid, voice; Gemini lungs, bronchi, shoulders, "
    "arms, hands, nerves; Cancer stomach, chest, digestion, retention of fluids; Leo heart, circulation, upper back, spine; Virgo "
    "intestines, digestion, nervous gut; Libra kidneys, lower back, skin, sugar balance; Scorpio reproductive and urinary organs, "
    "elimination, pelvis; Sagittarius hips, thighs, liver, sciatic nerve; Capricorn bones, joints, knees, teeth, skin; Aquarius "
    "ankles, shins, circulation, nerve tension; Pisces feet, lymph, immune sensitivity, sleep.\n"
    "  Planets and systems: Sun vitality, heart, spine; Moon digestion, fluids, hormonal rhythm, sleep, emotional eating; Mercury "
    "nerves, breathing, sleep and anxiety; Venus kidneys, skin, sweet tooth, hormonal balance, veins; Mars muscles, inflammation, "
    "fever, headaches, blood pressure, injuries from haste; Jupiter liver, fat metabolism, weight, overindulgence; Saturn bones, "
    "joints, teeth, skin, chill, stiffness, slow recovery, chronic fatigue; Uranus nerves, spasms, sudden and uneven reactions; "
    "Neptune immune sensitivity, allergies, sleep, exhaustion, sensitivity to medicines and alcohol; Pluto elimination, regeneration, "
    "deep hormonal and reproductive themes; Chiron an old sore spot that comes back.\n"
    "  Houses (only when the heading gives a house): house 1 body and vitality; house 6 daily health, work stress and routine; house 8 "
    "crises and regeneration; house 12 hidden, chronic or exhausting complaints and sleep.\n"
    "- Stay with body systems and common functional complaints (digestion, nerves and sleep, muscles and joints, skin, breathing, "
    "circulation and blood pressure, hormonal rhythm, immunity, headaches, injuries from haste or overload). Never name a grave disease "
    "(рак, тумор, инфаркт, инсулт, диабет, шизофрения and the like) as a potential of this person. Never tie an illness or injury to a "
    "date as an event (a period of pressure that the cards state can be mentioned), and give no percentages and no diagnosis.\n"
    "- In every card (2-3 sentences): the area and the concrete potential problem; what triggers or worsens it; one practical habit. "
    "The closing blocks name the person's main sensitive areas in words (still without planet, sign or house names), concrete "
    "prevention habits and, where it fits, regular check-ups of those areas. Mention a doctor (when symptoms repeat or last) in the "
    "closing blocks and in only a few cards, calmly and in different words each time: never the same sentence in several cards. Never "
    "tell the person to wait or postpone.\n"
    "- Tone: calm, matter-of-fact and caring, like a careful doctor-astrologer, not a fortune teller. All safety limits stay: no "
    "diagnoses, no medicines or doses, no stopping or delaying treatment, no fear and no fatalism."
)

KARMIC_BLOCK = (
    "THEME STYLE: KARMA AND FAMILY LINEAGE, in the system of Хули Леонис (Xyli Leonis). The reader chose this theme and this voice. "
    "This block sets the LENS and the VOICE only. The rules above about the facts, about what may be named in each section, about "
    "dates and about safety all stay in force, and this block adds no fact: use only what the cards state.\n"
    "NAMING DISCIPLINE: the headings already state the planets, signs, houses, aspects and angles, and the section rules forbid naming "
    "them in card texts and in the closing blocks. The lens below is for your understanding only: never print a planet, sign, house, "
    "aspect or angle name and never speak of the direction of planetary motion (retrograde or direct); say the meaning in plain words "
    "(the teacher of limits and duty, the direction of the soul, the inner essence).\n"
    "LENS, how this system reads the cards:\n"
    "- Karma is the path of the soul: the lessons the soul came to learn and the thread that links lives and generations. It is a "
    "lesson and a choice, never a punishment and never a fixed fate. What is seen and understood heals: the past is not erased, it is "
    "lit up and taken in, and the responsibility a person takes now shapes what comes.\n"
    "- Classical astrology is read together with the lineage (родов анализ): the chart shows patterns that a family line may carry. "
    "Read them as lineage patterns and ancestral memory (\"родов модел\", \"родова памет\"). The mother line and the father line speak as "
    "ARCHETYPES (what belongs to the Moon, the inner needs and the unconscious habit, as the mother line; what belongs to the Sun and "
    "Saturn, authority, duty and the inherited sense of what a person must be, as the father line). Describe what the pattern does in the person's inner life and "
    "choices (what repeats, what is carried, what can be released). Never describe what happened to real relatives.\n"
    "- The Moon is the true essence of a person. The Moon's nodes are the direction of the soul: the north node is what the soul grows "
    "towards, and the old comfort and habit to release lie opposite (say the meaning, never name the opposite point). Saturn is the "
    "teacher of karma: where the soul meets its limit, its fear and its duty, and the lesson that matures through patience and work. "
    "Chiron is the wound that teaches healing. Pluto is deep transformation and what must end for something new to begin.\n"
    "- A relationship is a meeting of souls with a lesson for both. With two people describe the bond that way, without predicting "
    "events, a breakup or a certain future.\n"
    "The system is only a lens: never name Хули Леонис, any other astrologer, author or book in the text and never attribute a statement "
    "to a person.\n"
    "VOICE: mystical, spiritual, warm and calm, like a wise guide who speaks to the soul. Use words such as душа, път на душата, "
    "кармичен урок, родова памет, родов модел, осъзнаване, освобождаване, \"да вземете урока\", връзка между поколенията, and slow, "
    "simple images. Stay concrete: every sentence still says something specific about THIS pattern (how it feels, how it repeats, what "
    "can be done about it). Vary the structure of the cards: do not give every card the same three-part pattern (meaning, a remark "
    "about karmic astrology, a sentence about the lineage and release), and mention a lineage pattern or a release only where the "
    "card gives a reason for it. This theme overrides the earlier wish for no mystic phrases: spiritual language is wanted here, empty "
    "phrases are not. Write only in Bulgarian (Latin letters only for a person's own name).\n"
    "LIMITS, unchanged: nothing concrete about real parents, ancestors, childhood, past lives or family history (events, illnesses, "
    "deaths, abuse, names); the pattern is a symbol and an interpretation, marked as such in your own varied words and never with "
    "the same formula in several cards. No curses, no guilt or blame towards a parent or the family, no fatalism, no fear, no promise "
    "of a past-life story, no prediction of life events (name only the dates and periods that the cards state). Do not use the word \"смърт\" in any form, even as an image: say \"край на "
    "цикъл\", \"отмиране на стария модел\", \"преход\". Spiritual work never replaces medical or psychological help. End with what the "
    "person can choose and do: free will always."
)

BLOCKS = {HEALTH: HEALTH_BLOCK, KARMIC: KARMIC_BLOCK}


def normalize(theme: Optional[str]) -> str:
    """Кодът на темата (health, karmic, general, …); празно или непознато става ""."""
    return theme if isinstance(theme, str) else ""


def block(theme: Optional[str]) -> str:
    """Блокът към системния промпт за темата ("" за всички теми освен Здраве и Карма и род)."""
    return BLOCKS.get(normalize(theme), "")


def bind(theme: Optional[str]) -> Token:
    """Закача темата за текущия анализ (виж generation.run). Връща токен за unbind."""
    current_theme.set(normalize(theme))
    return current.set(block(theme))


def unbind(token: Token) -> None:
    current.reset(token)
    current_theme.set("")


def is_health() -> bool:
    return current_theme.get() == HEALTH


def is_karmic() -> bool:
    return current_theme.get() == KARMIC
