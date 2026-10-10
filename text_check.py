"""
Проверка на готовия AI текст срещу фактите (Фаза 10). Без AI и без мрежа: чисти функции върху текст и факти, затова се
тества офлайн, включително върху 36-те запазени текста от одита.

Какво се проверява (само твърдения, които се разпознават еднозначно; всичко неясно минава):
- планета в дом (натален, наслагване, транзитна планета в натален дом), знак и градус на планета;
- аспект между две планети: съществува ли в данните и с този вид (съвпад, секстил, квадратура, тригон, опозиция),
  с посочени собственици, ако се разпознават; „точен“ само при аспект с точен момент; орбис, който съвпада с данните;
- куспида и управител на дом, брой ретроградни планети;
- за период и за дата: дата и час на станция, влизане в знак, новолуние, пълнолуние, затъмнение и на аспект;
- вътрешни етикети, имена на полета, проценти за вероятност.

Резултатът е списък от нарушения (Violation). Тежките (hard) означават, че твърдението противоречи на данните: текстът
се връща на поправка и ако пак има такива, отчетът не се записва. Меките (soft) се поправят или чистят, но не го спират.
Етикетите от подканата се махат автоматично (clean_labels).
"""
import re
from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Dict, Iterable, List, Optional, Sequence, Set, Tuple

# --------------------------------------------------------------------------------------------------------------
# Речник
# --------------------------------------------------------------------------------------------------------------
PLANET_FORMS = {
    "Sun": r"Слънц(?:е|ето)|Sun",
    "Moon": r"Лун(?:а|ата)|Moon",
    "Mercury": r"Меркури(?:й|я|ят)|Mercury",
    "Venus": r"Венер(?:а|ата)|Venus",
    "Mars": r"Марс(?:а|ът)?|Mars",
    "Jupiter": r"Юпитер(?:а|ът)?|Jupiter",
    "Saturn": r"Сатурн(?:а|ът)?|Saturn",
    "Uranus": r"Уран(?:а|ът)?|Uranus",
    "Neptune": r"Нептун(?:а|ът)?|Neptune",
    "Pluto": r"Плутон(?:а|ът)?|Pluto",
    "Chiron": r"Хирон(?:а|ът)?|Chiron",
    "Node": r"(?i:(?:възходящ\w*|северн\w*|лунн\w*)\s+(?:лунен\s+)?възел\w*|възел\w*)|Node",
}
SIGN_FORMS = {
    "Aries": r"Овен(?:а)?", "Taurus": r"Телец(?:а)?", "Gemini": r"Близнаци(?:те)?", "Cancer": r"Рак(?:а)?",
    "Leo": r"Лъв(?:а)?", "Virgo": r"Дева", "Libra": r"Везни", "Scorpio": r"Скорпион(?:а)?|Скорпио",
    "Sagittarius": r"Стрелец(?:а)?", "Capricorn": r"Козирог(?:а)?", "Aquarius": r"Водолей(?:я)?", "Pisces": r"Риби",
}
ASPECT_FORMS = {
    "conjunction": r"съвпад(?:ът|а|ение|ения)?|конюнкци(?:я|ята)|съединение",
    "sextile": r"секстил(?:ът|а)?",
    "square": r"квадратур(?:а|ата|и|ите)|квадрат(?:ът|а|ен|на|но)?",
    "trine": r"тригон(?:ът|а)?",
    "opposition": r"опозици(?:я|ята|и)",
}
ASPECT_BG = {"conjunction": "съвпад", "sextile": "секстил", "square": "квадратура", "trine": "тригон", "opposition": "опозиция"}
PLANET_BG = {"Sun": "Слънце", "Moon": "Луна", "Mercury": "Меркурий", "Venus": "Венера", "Mars": "Марс", "Jupiter": "Юпитер",
             "Saturn": "Сатурн", "Uranus": "Уран", "Neptune": "Нептун", "Pluto": "Плутон", "Chiron": "Хирон",
             "Node": "Възходящ възел", "ASC": "Асцендент", "MC": "MC"}
SIGN_BG = {"Aries": "Овен", "Taurus": "Телец", "Gemini": "Близнаци", "Cancer": "Рак", "Leo": "Лъв", "Virgo": "Дева",
           "Libra": "Везни", "Scorpio": "Скорпион", "Sagittarius": "Стрелец", "Capricorn": "Козирог", "Aquarius": "Водолей",
           "Pisces": "Риби"}
MONTHS = {"януари": 1, "февруари": 2, "март": 3, "април": 4, "май": 5, "юни": 6, "юли": 7, "август": 8,
          "септември": 9, "октомври": 10, "ноември": 11, "декември": 12}
HOUSE_WORDS = {"първ": 1, "втор": 2, "трет": 3, "четвърт": 4, "пет": 5, "шест": 6, "седм": 7, "осм": 8, "девет": 9,
               "десет": 10, "единадесет": 11, "дванадесет": 12}

_BOUND = r"(?<![\w])(?:%s)(?![\w])"
PLANET_RE = {k: re.compile(_BOUND % v) for k, v in PLANET_FORMS.items()}
SIGN_RE = {k: re.compile(_BOUND % v) for k, v in SIGN_FORMS.items()}
ASPECT_RE = {k: re.compile(r"(?<![\w])(?:%s)(?![\w])" % v, re.IGNORECASE) for k, v in ASPECT_FORMS.items()}
ANGLE_RE = {"ASC": re.compile(_BOUND % r"Асцендент(?:а|ът)?|ASC"), "MC": re.compile(_BOUND % r"MC|Среда на небето")}
HOUSE_DIGIT_RE = re.compile(r"(?<![\w.,])(\d{1,2})\s*[-–]?\s*(?:ви|ри|ти|ми|и|а|о|ма|ва|ра|та|во|ро|то|мо|ия|ият|ата|ото)?\s*(?:-?\s*)?(?:дом|дома|домът|домове|къща|къщата)(?![\w])", re.IGNORECASE)
HOUSE_WORD_RE = re.compile(r"(?<![\w])(първ|втор|трет|четвърт|пет|шест|седм|осм|девет|десет|единадесет|дванадесет)\w*\s+(?:дом|дома|домът|къща|къщата)(?![\w])", re.IGNORECASE)
DEGREE_RE = re.compile(r"(\d{1,2})\s*°\s*(?:(\d{1,2})\s*[′'’]?)?")
TIME_RE = re.compile(r"(?<![\d:])([01]?\d|2[0-3]):([0-5]\d)(?![\d:])")
DATE_TEXT_RE = re.compile(r"(?<![\w.])(\d{1,2})(?:\s*[-–]\s*(\d{1,2}))?\s+(януари|февруари|март|април|май|юни|юли|август|септември|октомври|ноември|декември)(?:\s+(\d{4}))?(?![\w])", re.IGNORECASE)
DATE_NUMRANGE_RE = re.compile(r"(?<![\w.:])(\d{1,2})\s*[-–]\s*(\d{1,2})\.(\d{1,2})(?:\.(\d{4}))?(?![\w.:°])")
DATE_NUM_RE = re.compile(r"(?<![\w.:])(\d{1,2})\.(\d{1,2})(?:\.(\d{4}))?(?![\w.:°])")
DATE_ISO_RE = re.compile(r"(?<![\w])(\d{4})-(\d{2})-(\d{2})(?![\w])")
ORB_RE = re.compile(r"(?:орб(?:ис)?\w*)\s*[~≈:]?\s*(\d+(?:[.,]\d+)?)\s*°|\(\s*(\d+[.,]\d+)\s*°\s*\)", re.IGNORECASE)
EXACT_WORD_RE = re.compile(r"(?<![\w])(?:точен|точна|точни|точният|точната|точния|точните|точното|идеален|идеална|exact)(?![\w])|"
                           r"(?<![\w])точно(?=\s+(?:на\s+|в\s+)?\d|\s+(?:съвпад\w*|момент\w*|попадени\w*))", re.IGNORECASE)
ORDINAL_DAY_RE = re.compile(r"(?<![\w.])на\s+(\d{1,2})\s*-\s*(?:ти|ви|ри|ми|во|то|и)(?![\w])(?!\s*(?:дом|къщ))", re.IGNORECASE)
HEDGE_RE = re.compile(r"(?<![\w])(?:ако|когато|би|биха|например|евентуално|ще е ли|дали)(?![\w])", re.IGNORECASE)
TRANSIT_WORD_RE = re.compile(r"(?<![\w])транзит\w*", re.IGNORECASE)
NATAL_WORD_RE = re.compile(r"(?<![\w])натал\w*", re.IGNORECASE)
_NUM = r"(\d+|една|един|две|два|три|четири|пет|шест|седем|осем|девет)"
RETRO_COUNT_RES = [
    re.compile(rf"(?<![\w]){_NUM}\s+ретроградн\w+\s+(?:планет\w+|тел\w+)", re.IGNORECASE),                    # „четири ретроградни планети“
    re.compile(rf"(?<![\w]){_NUM}\s+(?:от\s+)?(?:планет\w+|тел\w+)\s+(?:са|е|бяха)\s+ретроградн\w+", re.IGNORECASE),  # „четири планети са ретроградни“
    re.compile(rf"ретроградн\w+\s+(?:са|е)\s+{_NUM}\s+(?:от\s+)?(?:планет\w+|тел\w+)", re.IGNORECASE),                  # „ретроградни са четири планети“
]
NUMBER_WORDS = {"една": 1, "един": 1, "две": 2, "два": 2, "три": 3, "четири": 4, "пет": 5, "шест": 6, "седем": 7, "осем": 8, "девет": 9}

USER_CUE_RE = re.compile(r"(?<![\w-])(?:твоя|твоята|твоето|твоите|твой|твоят|ваш\w*|ти|теб|вие)(?![\w])", re.IGNORECASE)
_PARTNER_WORD = r"партньор(?:ът|а|ка|ката|ски|ския|ският|ската|ското|ските)?"
PARTNER_CUE_RE = re.compile(rf"(?<![\w])(?:{_PARTNER_WORD}|негов\w*|нейн\w*)(?![\w])", re.IGNORECASE)

_FIELD_NAMES = (r"house_impact|exact_this_month|state_in_month|turns_back|closest_in_period|active_from|active_to|"
                r"exact_outside_period|transit_planet_natal_house|natal_planet_natal_house|window_clipped|natal_position|"
                r"eclipse_kind|factpack|timeline_events|first_person|second_person|angle_deg|formatted_pos|CALCULATED")
_CAPS_WORD = r"[A-Z][A-Z'’]*"
_CAPS_KEY = r"(?:PLANETS|HOUSES|ASPECTS|SECTION|MANDATORY|REQUIRED|TIMELINE|EVENTS|NATAL|TRANSIT|CHART)"
_HEADER_LEAK = rf"(?:{_CAPS_WORD}\s+){{0,6}}{_CAPS_KEY}(?:\s+{_CAPS_WORD}){{0,6}}(?:\s*\(CALCULATED\))?"
_LEAK_ANY = rf"(?:{_HEADER_LEAK}|(?<![\w])(?:{_FIELD_NAMES})(?![\w]))"
# Вътрешни имена на полета и заглавия на секции от подканата, които не бива да стигат до клиента
ENGLISH_LEAK_RE = re.compile(rf"(?<![\w])(?:{_HEADER_LEAK}|(?:{_FIELD_NAMES}))(?![\w])")
LABEL_RES = [
    # Само точните служебни форми от старите подканки; обикновеният български („задължителна част“, „само при нужда“) не се пипа
    re.compile(r"[ \t]*[(\[]\s*задължителн\w+(?:\s+(?:секция|раздел|част))?\s*[)\]]", re.IGNORECASE),
    re.compile(r"[ \t]*(?<![\w])задължителна\s+секция(?![\w])", re.IGNORECASE),
    re.compile(r"[ \t]*[(\[]\s*само при\s+(?:Марс|Уран)(?:\s*(?:/|или)\s*(?:Марс|Уран))?\s*[)\]]", re.IGNORECASE),
    re.compile(r"[ \t]*[(\[]\s*(?:MANDATORY|REQUIRED)(?: SECTION)?\s*[)\]]", re.IGNORECASE),
    re.compile(r"[ \t]*(?<![\w])(?:MANDATORY|REQUIRED) SECTION\s*:?", re.IGNORECASE),
]
PERCENT_RE = re.compile(r"(?<![\w.])\d{1,3}\s*(?:%|на сто|процента?)", re.IGNORECASE)


# --------------------------------------------------------------------------------------------------------------
# Факти
# --------------------------------------------------------------------------------------------------------------
@dataclass
class Facts:
    """Фактите, срещу които се проверява текстът. Строят се със същите функции като данните в подканата."""
    mode: str                                                    # natal | snapshot | period | overview
    names: Dict[str, str] = field(default_factory=dict)          # "user" / "partner" -> показвано име
    natal: Dict[str, Dict[str, Dict]] = field(default_factory=dict)      # човек -> планета -> {sign, deg, min, house}
    cusps: Dict[str, Dict[int, Dict]] = field(default_factory=dict)      # човек -> дом -> {sign, deg, min, ruler}
    retro_count: Dict[str, int] = field(default_factory=dict)            # "user" / "partner" / "sky" -> брой
    overlays: Dict[Tuple[str, str], Dict[str, int]] = field(default_factory=dict)   # (чия планета, чий дом) -> планета -> дом
    natal_aspects: Dict[str, List[Dict]] = field(default_factory=dict)   # човек -> [{p1, p2, aspect, orb}]
    synastry: List[Dict] = field(default_factory=list)                   # [{o1, p1, aspect, o2, p2, orb}]
    transit_houses: Dict[str, Dict[str, int]] = field(default_factory=dict)     # човек -> транзитна планета -> натален дом
    transit_aspects: Dict[str, List[Dict]] = field(default_factory=dict)        # човек -> [{p1 (транзитна), p2 (натална), aspect, orb}]
    sky: Dict[str, Dict] = field(default_factory=dict)                   # транзитно небе: планета -> {sign, deg, min}
    no_houses: Set[str] = field(default_factory=set)                     # хора без известен час: няма домове, Асцендент и MC
    alt_signs: Dict[Tuple[str, str], List[str]] = field(default_factory=dict)   # (човек, тяло) -> възможни знаци при неизвестен час
    snapshot_date: Optional[str] = None                                  # "YYYY-MM-DD" (анализ за дата)
    snapshot_time: Optional[str] = None                                  # "HH:MM"
    calendar: Optional[List[Dict]] = None                                # редовете на календара (public)
    period: Optional[Tuple[str, str]] = None                             # (начало, край) на периода
    report_date: Optional[str] = None


@dataclass
class Violation:
    code: str            # house, sign, aspect, orb, exact, cusp, ruler, retro_count, date, time, event, label, english, percent
    severity: str        # hard | soft
    excerpt: str         # откъс от текста (до 160 знака)
    detail: str          # какво не е наред и какво е вярното (за поправката)


def _split_sign_pos(formatted: str) -> Dict:
    """"Capricorn 21°53'" -> {sign, deg, min}."""
    m = re.match(r"\s*([A-Za-z]+)\s+(\d{1,2})°(\d{1,2})", formatted or "")
    if not m:
        return {}
    return {"sign": m.group(1), "deg": int(m.group(2)), "min": int(m.group(3))}


def planets_view(view_planets: Dict[str, Dict]) -> Dict[str, Dict]:
    out = {}
    for name, p in (view_planets or {}).items():
        pos = _split_sign_pos(p.get("formatted_pos", ""))
        if pos:
            out[name] = {**pos, "house": p.get("house")}
    return out


def cusps_view(houses: Dict[str, Dict]) -> Dict[int, Dict]:
    out = {}
    for key, row in (houses or {}).items():
        pos = _split_sign_pos(row.get("cusp", ""))
        if pos and key.startswith("House"):
            out[int(key[5:])] = {**pos, "ruler": row.get("ruler")}
    return out


# --------------------------------------------------------------------------------------------------------------
# Нормализиране и разделяне на изречения
# --------------------------------------------------------------------------------------------------------------
def normalize(text: str) -> str:
    t = re.sub(r"<\s*br\s*/?>|</p>|</li>|</h\d>|</div>|</tr>|</ul>|</ol>", "\n", text or "", flags=re.IGNORECASE)
    t = re.sub(r"<[^>]+>", " ", t)
    t = (t.replace(" ", " ").replace("’", "'").replace("‘", "'").replace("′", "'").replace("ʼ", "'")
          .replace("″", '"').replace("”", '"').replace("“", '"'))
    t = re.sub(r"[*_`#]+", "", t)
    t = re.sub(r"^\s*:?-{2,}[-|:\s]*$", "", t, flags=re.MULTILINE)          # разделител на таблица
    t = re.sub(r"^\s*[-•▪]\s+", "", t, flags=re.MULTILINE)                  # водещи тирета на списък
    t = t.replace("|", " · ")
    t = re.sub(r"(?<=\d{4})\s*г\.", "", t)                                 # „23 ноември 2026 г.“: точката не е край на изречение
    t = re.sub(r"[ \t]+", " ", t)
    return t


_SPLIT_RE = re.compile(r"(?<=[.!?])\s+(?=[А-ЯA-Z„\"«(\d])|\n+|;\s+|\s[—–]\s")


MAX_SEGMENT = 1500          # по-дълго „изречение“ е таблица без край или зациклил текст: режи се, за да не става обработката квадратична


def _cap_segment(raw: str) -> List[str]:
    """Реже твърде дълго изречение по запетая или интервал на парчета до MAX_SEGMENT знака."""
    pieces: List[str] = []
    while len(raw) > MAX_SEGMENT:
        cut = max(raw.rfind(", ", 0, MAX_SEGMENT), raw.rfind(": ", 0, MAX_SEGMENT))
        if cut < MAX_SEGMENT // 2:
            cut = raw.rfind(" ", 0, MAX_SEGMENT)
        if cut <= 0:
            cut = MAX_SEGMENT
        pieces.append(raw[:cut + 1].strip())
        raw = raw[cut + 1:].strip()
    if raw:
        pieces.append(raw)
    return pieces


def segments(text: str) -> List[str]:
    parts = []
    for raw in _SPLIT_RE.split(normalize(text)):
        raw = (raw or "").strip()
        if len(raw) > 2:
            parts.extend(_cap_segment(raw))
    return parts


# --------------------------------------------------------------------------------------------------------------
# Споменавания в едно изречение
# --------------------------------------------------------------------------------------------------------------
@dataclass
class Mention:
    kind: str            # planet | sign | aspect | house | degree
    key: object
    start: int
    end: int


def find_mentions(seg: str) -> List[Mention]:
    out: List[Mention] = []
    for key, rx in PLANET_RE.items():
        out += [Mention("planet", key, m.start(), m.end()) for m in rx.finditer(seg)]
    for key, rx in ANGLE_RE.items():
        out += [Mention("planet", key, m.start(), m.end()) for m in rx.finditer(seg)]
    for key, rx in SIGN_RE.items():
        out += [Mention("sign", key, m.start(), m.end()) for m in rx.finditer(seg)]
    for key, rx in ASPECT_RE.items():
        out += [Mention("aspect", key, m.start(), m.end()) for m in rx.finditer(seg)]
    for m in HOUSE_DIGIT_RE.finditer(seg):
        n = int(m.group(1))
        if 1 <= n <= 12:
            out.append(Mention("house", n, m.start(), m.end()))
    for m in HOUSE_WORD_RE.finditer(seg):
        out.append(Mention("house", HOUSE_WORDS[m.group(1).lower()], m.start(), m.end()))
    for m in DEGREE_RE.finditer(seg):
        out.append(Mention("degree", (int(m.group(1)), int(m.group(2)) if m.group(2) else None), m.start(), m.end()))
    # планета без излишно припокриване: „Възходящ възел“ изяжда „възел“
    out.sort(key=lambda x: (x.start, -(x.end - x.start)))
    cleaned: List[Mention] = []
    for m in out:
        if cleaned and m.kind == cleaned[-1].kind == "planet" and m.start < cleaned[-1].end:
            continue
        cleaned.append(m)
    return cleaned


_FILLER = {"е", "са", "бе", "бяха", "в", "във", "и", "или", "на", "се", "по", "с", "със", "към", "от", "има", "съдържа",
           "попада", "попадат", "пада", "падат", "намира", "намират", "стои", "стоят",
           "разположен", "разположена", "разположени", "разположено", "транзитно", "транзитен", "транзитна",
           "транзитни", "транзитния", "транзитният", "транзитната", "транзитното", "транзитните",
           "натален", "наталният", "натална", "наталната", "натално", "наталното", "наталния", "натални", "наталните",
           "твоя", "твоят", "твоята", "твоето", "твоите", "твой", "негов", "неговия", "неговият", "неговата",
           "неговото", "неговите", "нейния", "нейната", "нейното", "нейните", "партньорския", "партньорската",
           "партньорското", "партньорските", "партньорът", "партньора", "ваш", "вашия", "вашата", "вашето", "вашите",
           "си", "ти", "му", "ѝ", "я", "го", "също", "точно", "вече", "още", "само", "едновременно", "именно",
           "доминира", "акцентира", "попаднал", "попаднала", "попаднали"}


def _tokens(gap: str) -> List[str]:
    """Думите на междината, без скоби със съдържание, градуси, знаци и пунктуация."""
    g = re.sub(r"\([^)]*\)", " ", gap)
    g = DEGREE_RE.sub(" ", g)
    for rx in SIGN_RE.values():
        g = rx.sub(" ", g)
    g = re.sub(r"[^\w]+", " ", g, flags=re.UNICODE)
    return [w for w in g.lower().split() if w]


def gap_ok(gap: str, extra: Iterable[str] = ()) -> bool:
    allowed = _FILLER | set(extra)
    return all(w in allowed or w.startswith(("ваш", "негов", "нейн", "партньор", "твой", "твоя", "твоит", "транзит", "натал"))
               for w in _tokens(gap))


@dataclass
class Group:
    planets: List[Mention]
    start: int
    end: int

    @property
    def keys(self) -> List[str]:
        return [p.key for p in self.planets]


def _parens_are_positions(gap: str) -> bool:
    """Скобите в междината носят само позиция на планета (знак, градуси, „ретроградна“), а не дата, дом, име или забележка."""
    for inner in re.findall(r"\(([^)]*)\)", gap):
        rest = DEGREE_RE.sub(" ", inner)
        for rx in SIGN_RE.values():
            rest = rx.sub(" ", rest)
        rest = re.sub(r"ретроград\w*|директ\w*|℞|\bR\b", " ", rest)
        if re.search(r"\w", rest):
            return False
    return True


def planet_groups(seg: str, mentions: List[Mention]) -> List[Group]:
    """Планети, изброени една до друга ("Венера, Марс и Нептун", "Меркурий–Плутон", "Слънце (Везни 15°) и Луна")."""
    groups: List[Group] = []
    aspects = [x for x in mentions if x.kind == "aspect"]
    for m in (x for x in mentions if x.kind == "planet"):
        if groups:
            prev = groups[-1].planets[-1]
            gap = seg[groups[-1].end:m.start]
            joined = re.fullmatch(r"\s*(?:[,–-]|\s+и\s+|\s+или\s+)?\s*", gap) is not None
            if not joined and re.search(r"[,–-]|\bи\b|\bили\b", gap):
                stripped = re.sub(r"\([^)]*\)", " ", gap)
                stripped = DEGREE_RE.sub(" ", stripped)
                for rx in SIGN_RE.values():
                    stripped = rx.sub(" ", stripped)
                stripped = re.sub(r"[,–-]|\bи\b|\bили\b", " ", stripped)
                joined = (gap_ok(stripped) and not re.search(r"[.+·/|]", stripped) and _parens_are_positions(gap))
            if joined and re.search(r"[,и]|\bили\b", gap):
                # „Нептун квадрат Нептун, Марс квадрат Сатурн“: обектът на един аспект и подлогът на следващия са различни изречения
                after_prev = any(0 <= prev.start - a.end <= 16 and gap_ok(seg[a.end:prev.start]) for a in aspects)
                before_new = any(0 <= a.start - m.end <= 3 for a in aspects)
                if after_prev and before_new:
                    joined = False
            if joined:
                groups[-1].planets.append(m)
                groups[-1].end = m.end
                continue
        groups.append(Group([m], m.start, m.end))
    return groups


# --------------------------------------------------------------------------------------------------------------
# Собственик на твърдението (чия е планетата, чий е домът)
# --------------------------------------------------------------------------------------------------------------
def _name_re(name: str) -> Optional[re.Pattern]:
    name = (name or "").strip()
    if len(name) < 3:
        return None
    return re.compile(r"(?<![\w])" + re.escape(name) + r"\w{0,2}(?![\w])", re.IGNORECASE)


def _owner_cues(facts: Facts) -> Dict[str, List[re.Pattern]]:
    cues = {"user": [USER_CUE_RE], "partner": [PARTNER_CUE_RE]}
    for key in ("user", "partner"):
        rx = _name_re(facts.names.get(key, ""))
        if rx:
            cues[key].append(rx)
    return cues


_POSSESSIVE_GAP = {"има", "съдържа", "притежава", "си"}


def cue_before(seg: str, pos: int, facts: Facts, window: int = 30) -> Optional[str]:
    """Кой човек се подразбира от думата точно пред позицията (най-много две думи между): user | partner | None."""
    chunk = seg[max(0, pos - window):pos]
    best: Tuple[int, Optional[str]] = (-1, None)
    for owner, rxs in _owner_cues(facts).items():
        if owner not in facts.names:
            continue                                  # „неговия“ при един човек е самият човек, не партньор
        for rx in rxs:
            for m in rx.finditer(chunk):
                if m.end() >= best[0]:
                    best = (m.end(), owner)
    if best[1] is None:
        return None
    rest = chunk[best[0]:]
    if re.search(r"[^\w\s'-]", rest):
        return None                                   # „(Потребител) + Сатурн“: собственикът е на предишното твърдение
    if any(w not in _POSSESSIVE_GAP and not w.startswith(("транзит", "натал")) for w in _tokens(rest)):
        return None                                   # „дом на Иван и Нептун“: „и“ го прекъсва
    return best[1]


def cue_after(seg: str, pos: int, facts: Facts, window: int = 22) -> Optional[str]:
    """„... на Иван“, „... на партньора“, „... на теб“ веднага след позицията."""
    chunk = seg[pos:pos + window]
    m = re.match(r"\s*(?:(?:на|у|за)\s+(\w+)|\(\s*(\w+)\s*\))", chunk)
    if not m:
        return None
    word = m.group(1) or m.group(2)
    for owner in ("user", "partner"):
        rx = _name_re(facts.names.get(owner, ""))
        if rx and rx.fullmatch(word):
            return owner
    if "partner" in facts.names and re.fullmatch(rf"{_PARTNER_WORD}|негов\w*|нейн\w*", word, re.IGNORECASE):
        return "partner"
    if re.fullmatch(r"теб|вас|ти|ви|мен", word, re.IGNORECASE):
        return "user"
    return None


def kind_cue(seg: str, pos: int, mentions: Optional[List[Mention]] = None) -> Optional[str]:
    """„транзитен ...“ или „натален ...“ непосредствено пред позицията (не отвъд друга планета: „транзитният Уран тригон Меркурий“)."""
    floor = max(0, pos - 24)
    if mentions:
        floor = max([floor] + [m.end for m in mentions if m.kind in ("planet", "aspect") and m.end <= pos])
    chunk = seg[floor:pos]
    t, n = list(TRANSIT_WORD_RE.finditer(chunk)), list(NATAL_WORD_RE.finditer(chunk))
    if t and (not n or t[-1].end() > n[-1].end()):
        return "transit"
    if n:
        return "natal"
    return None


def _group_owner(seg: str, group: Group, facts: Facts) -> Optional[str]:
    return cue_before(seg, group.start, facts) or cue_after(seg, group.end, facts)


# --------------------------------------------------------------------------------------------------------------
# Прегледи на фактите
# --------------------------------------------------------------------------------------------------------------
def house_rows(facts: Facts) -> List[Tuple[str, str, str, str, int]]:
    """(чия планета, чий дом, вид, планета, дом); „sky“ е транзитното небе."""
    rows = []
    for owner, planets in facts.natal.items():
        for planet, d in planets.items():
            if d.get("house"):
                rows.append((owner, owner, "natal", planet, int(d["house"])))
    for (po, ho), mapping in facts.overlays.items():
        for planet, house in mapping.items():
            rows.append((po, ho, "overlay", planet, int(house)))
    for owner, mapping in facts.transit_houses.items():
        for planet, house in mapping.items():
            rows.append(("sky", owner, "transit", planet, int(house)))
    return rows


def sign_rows(facts: Facts) -> List[Tuple[str, str, str, str, Optional[int], Optional[int]]]:
    """(чия, вид, планета, знак, градус, минути)."""
    rows = []
    for owner, planets in facts.natal.items():
        for planet, d in planets.items():
            rows.append((owner, "natal", planet, d["sign"], d.get("deg"), d.get("min")))
    for (owner, planet), signs in facts.alt_signs.items():          # неизвестен час: всеки от възможните знаци е верен
        for sign in signs:
            rows.append((owner, "natal", planet, sign, None, None))
    for planet, d in facts.sky.items():
        rows.append(("sky", "transit", planet, d["sign"], d.get("deg"), d.get("min")))
    return rows


def _who(facts: Facts, key: str) -> str:
    if key == "sky":
        return "небето в момента"
    return facts.names.get(key, key)


def _pl(planet: str) -> str:
    return PLANET_BG.get(planet, planet)


# --------------------------------------------------------------------------------------------------------------
# Дом
# --------------------------------------------------------------------------------------------------------------
def _short(seg: str, start: int = 0, end: Optional[int] = None, limit: int = 160) -> str:
    start = max(0, start)
    if start > 0 and not seg[start - 1].isspace():
        space = seg.rfind(" ", 0, start)
        start = space + 1 if space != -1 else 0
    chunk = seg[start:end].strip()
    return chunk if len(chunk) <= limit else chunk[:limit - 1] + "…"


def _connector_gap(gap: str) -> bool:
    """Планети, изброени една след друга: запетая, „и“, тире; скоби със знак и градуси между тях са позволени."""
    stripped = re.sub(r"\([^)]*\)", " ", gap)
    stripped = DEGREE_RE.sub(" ", stripped)
    for rx in SIGN_RE.values():
        stripped = rx.sub(" ", stripped)
    if re.search(r"[+·/|.!?;:]", stripped) or not _parens_are_positions(gap):
        return False
    stripped = re.sub(r"[,–-]|\bи\b|\bили\b", " ", stripped)
    return gap_ok(stripped) and (bool(re.search(r"[,–-]|\bи\b|\bили\b", gap)) or not gap.strip())


def _degrees_for(seg: str, mentions: List[Mention], sign: Mention, last_sign: Mention, floor: int) -> List[Mention]:
    """Градусите на знака: „Везни 15°09'“, „15°09' Везни“ или списък „Лъв 5°56' и 20°52'“."""
    before = next((d for d in mentions if d.kind == "degree" and d.start >= floor and 0 <= sign.start - d.end <= 2
                   and not seg[d.end:sign.start].strip()), None)
    if before:
        return [before]
    out: List[Mention] = []
    pos = last_sign.end
    for d in sorted((x for x in mentions if x.kind == "degree" and x.start >= last_sign.end), key=lambda x: x.start):
        gap = seg[pos:d.start]
        if not out and re.fullmatch(r"\s*\(?\s*,?\s*", gap):
            out.append(d)
        elif out and re.fullmatch(r"\s*(?:,|и|–|-|/)\s*", gap):
            out.append(d)
        else:
            break
        pos = d.end
    return out


def placement_claims(seg: str, mentions: List[Mention], groups: List[Group], facts: Facts) -> Tuple[List[Dict], List[Dict]]:
    """Последователно свързване на планети със знак и дом. Планетите се трупат, докато не ги „затвори“ знак или дом;
    следващата планета започва ново изброяване. Така „Слънце (Везни 15°) и Луна (Дева 17°) са в 9-ти дом“ дава Слънце във
    Везни, Луна в Дева и двете в 9-ти дом, а не Слънце и Луна в Дева."""
    house_list: List[Dict] = []
    sign_list: List[Dict] = []
    signs = [m for m in mentions if m.kind == "sign"]
    consumed_signs: Set[int] = set()
    consumed_planets: Set[int] = set()
    # знакът непосредствено пред планетата: „Стрелец Асцендент“
    for sm in signs:
        nxt = next((p for p in mentions if p.kind == "planet" and 0 <= p.start - sm.end <= 2), None)
        if nxt and not re.search(r"(?:в|във)\s*$", seg[max(0, sm.start - 4):sm.start]):
            sign_list.append({"planets": [nxt.key], "sign": sm.key, "deg": None, "po": cue_before(seg, sm.start, facts),
                              "kind": kind_cue(seg, sm.start, mentions), "excerpt": _short(seg, max(0, sm.start - 6), nxt.end + 4)})
            consumed_signs.add(sm.start)
            consumed_planets.add(nxt.start)
    pending: List[Mention] = []
    claimed = False
    last_end = 0
    used_houses: Set[int] = set()
    for m in sorted((x for x in mentions if x.kind in ("planet", "sign", "house")), key=lambda x: x.start):
        if m.kind == "planet":
            if m.start in consumed_planets:
                continue
            if pending and not claimed and _connector_gap(seg[last_end:m.start]):
                pending.append(m)
            else:
                pending, claimed = [m], False
            last_end = m.end
            continue
        if not pending:
            continue
        gap = _strip_names(seg[last_end:m.start], facts)            # „Слънцето на Иван е във вашия 8-ми дом“
        if m.kind == "sign":
            if m.start in consumed_signs:
                continue
            if len(gap) > 24 or not gap_ok(gap) or HEDGE_RE.search(seg[max(0, pending[0].start - 40):m.end]):
                continue
            chain = [m]
            while True:
                nxt_sign = next((x for x in signs if x.start > chain[-1].end and x.start - chain[-1].end <= 4
                                 and re.fullmatch(r"\s*(?:[–,/-]|и)\s*", seg[chain[-1].end:x.start])), None)
                if not nxt_sign:
                    break
                chain.append(nxt_sign)
            for c_sign in chain:
                consumed_signs.add(c_sign.start)
            names = [p for p in pending if p.key not in ("ASC", "MC") or "(" not in gap]
            if len(chain) > 1:
                if len(chain) != len(names):
                    continue
                pairs = list(zip(names, chain))
            else:
                pairs = [(p, m) for p in names]
            degs = _degrees_for(seg, mentions, chain[0], chain[-1], last_end) if len(chain) == 1 else []
            if len(names) > 1:
                degs = degs if len(degs) == len(names) else []        # градусите се присвояват само ако са по един на планета
            else:
                degs = degs[:1]
            for idx, (planet, sign_m) in enumerate(pairs):
                deg = degs[idx] if degs else None
                sign_list.append({"planets": [planet.key], "sign": sign_m.key, "deg": deg.key if deg else None,
                                  "po": cue_before(seg, pending[0].start, facts) or cue_after(seg, pending[-1].end, facts),
                                  "kind": kind_cue(seg, pending[0].start, mentions),
                                  "excerpt": _short(seg, max(0, pending[0].start - 10),
                                                    min(len(seg), (deg.end if deg else sign_m.end) + 6))})
            last_end = max([chain[-1].end] + [d.end for d in degs])
            claimed = True
            continue
        # дом
        if len(gap) > 70 or not gap_ok(gap):
            continue
        if re.search(r"управител|управлява|куспид|начало на|започва", seg[max(0, m.start - 28):m.start], re.IGNORECASE):
            continue
        if HEDGE_RE.search(seg[max(0, pending[0].start - 40):m.end]):
            continue
        used_houses.add(m.start)
        house_list.append({"planets": [p.key for p in pending], "house": m.key,
                           "po": cue_before(seg, pending[0].start, facts) or cue_after(seg, pending[-1].end, facts),
                           "ho": cue_before(seg, m.start, facts, window=22) or cue_after(seg, m.end, facts),
                           "kind": kind_cue(seg, pending[0].start, mentions),
                           "excerpt": _short(seg, max(0, pending[0].start - 12), min(len(seg), m.end + 14))})
        last_end = m.end
        claimed = True
    # „В 8-ми дом са Марс и Юпитер“: домът е преди планетите
    for h in (x for x in mentions if x.kind == "house" and x.start not in used_houses):
        after = [g for g in groups if g.start >= h.end]
        if not after:
            continue
        g = after[0]
        gap = seg[h.end:g.start]
        if len(gap) <= 40 and gap_ok(gap, {"има", "съдържа"}) and re.search(r"(?:^|\s)(?:е|са|има|съдържа|попада\w*|стои\w*|намира\w*)(?:\s|$)", gap) \
                and not HEDGE_RE.search(seg[max(0, h.start - 40):g.end]) \
                and not re.search(r"управител|управлява|куспид|начало на|започва", seg[max(0, h.start - 28):h.start], re.IGNORECASE):
            house_list.append({"planets": g.keys, "house": h.key, "po": _group_owner(seg, g, facts),
                               "ho": cue_before(seg, h.start, facts, window=22) or cue_after(seg, h.end, facts),
                               "kind": kind_cue(seg, g.start, mentions), "excerpt": _short(seg, max(0, h.start - 12), min(len(seg), g.end + 14))})
    return house_list, sign_list


_PARTIAL_SKY_MODES = ("period", "overview")


def _house_row_fits(r: Tuple[str, str, str, str, int], c: Dict) -> bool:
    """Редът от фактите отговаря на вида и собственика в твърдението (домът още не се сравнява).
    Транзитната планета няма собственик: „Иван има Марс в 8-ми дом“ е транзитният Марс в дома на Иван."""
    if r[2] == "transit":
        if c["kind"] == "natal":
            return False
        owner = c["ho"] or c["po"]
        return owner is None or r[1] == owner
    if c["kind"] == "transit":
        return False
    return (not c["po"] or r[0] == c["po"]) and (not c["ho"] or r[1] == c["ho"])


def check_houses(seg: str, mentions: List[Mention], groups: List[Group], facts: Facts) -> List[Violation]:
    rows = house_rows(facts)
    out = []
    for c in placement_claims(seg, mentions, groups, facts)[0]:
        if facts.mode in _PARTIAL_SKY_MODES and c["kind"] != "natal":
            continue                          # в периода няма пълно транзитно небе: „Марс в Лъв (8-ми дом)“ може да е транзитен
        for planet in c["planets"]:
            if planet in ("ASC", "MC"):
                continue
            candidates = [r for r in rows if r[3] == planet]
            if not candidates:
                continue                                                # няма такъв факт: не се твърди нищо
            if any(_house_row_fits(r, c) and r[4] == c["house"] for r in candidates):
                continue
            scope = [r for r in candidates if _house_row_fits(r, c)] or candidates
            facts_text = "; ".join(f"{_pl(planet)} на {_who(facts, r[0])} е в {r[4]}-ти дом на {_who(facts, r[1])}"
                                   if r[0] != "sky" else f"транзитният {_pl(planet)} е в {r[4]}-ти дом на {_who(facts, r[1])}"
                                   for r in scope[:4])
            out.append(Violation("house", "hard", c["excerpt"],
                                 f"{_pl(planet)} не е в {c['house']}-ти дом по данните. Вярно: {facts_text}."))
    return out


# --------------------------------------------------------------------------------------------------------------
# Знак и градус
# --------------------------------------------------------------------------------------------------------------
def check_signs(seg: str, mentions: List[Mention], groups: List[Group], facts: Facts) -> List[Violation]:
    rows = sign_rows(facts)
    out = []
    for c in placement_claims(seg, mentions, groups, facts)[1]:
        if facts.mode in _PARTIAL_SKY_MODES and c["kind"] != "natal":
            continue
        for planet in c["planets"]:
            candidates = [r for r in rows if r[2] == planet]
            if not candidates:
                continue
            def fits(r) -> bool:
                if c["kind"] == "transit" and r[1] != "transit":
                    return False
                if c["kind"] == "natal" and r[1] == "transit":
                    return False
                return r[1] == "transit" or not c["po"] or r[0] == c["po"]

            def matches(r) -> bool:
                if not fits(r) or r[3] != c["sign"]:
                    return False
                if c["deg"] and r[4] is not None:
                    d, m = c["deg"]
                    if d != r[4] or (m is not None and r[5] is not None and abs(m - r[5]) > 1):
                        return False
                return True
            if any(matches(r) for r in candidates):
                continue
            scope = [r for r in candidates if fits(r)] or candidates
            facts_text = "; ".join(f"{_pl(planet)} на {_who(facts, r[0])}: {SIGN_BG.get(r[3], r[3])} {r[4]}°{r[5]:02d}'"
                                   if r[4] is not None else f"{_pl(planet)} на {_who(facts, r[0])}: {SIGN_BG.get(r[3], r[3])}"
                                   for r in scope[:4])
            out.append(Violation("sign", "hard", c["excerpt"], f"Знакът или градусът на {_pl(planet)} не съвпада с данните. Вярно: {facts_text}."))
    return out


# --------------------------------------------------------------------------------------------------------------
# Аспекти
# --------------------------------------------------------------------------------------------------------------
_ROW_KIND = {"natal": "натален аспект", "synastry": "аспект между двамата", "transit": "транзит към натал", "calendar": "аспект от календара"}


def aspect_rows(facts: Facts) -> List[Dict]:
    """Всички аспекти, които са в данните: натални, синастрични, транзит към натал и от календара на периода."""
    rows: List[Dict] = []
    for owner, items in facts.natal_aspects.items():
        for a in items:
            rows.append({"kind": "natal", "o1": owner, "o2": owner, "p1": a["p1"], "p2": a["p2"], "aspect": a["aspect"],
                         "orb": a.get("orb")})
    by_name = {v: k for k, v in facts.names.items()}
    for a in facts.synastry:
        rows.append({"kind": "synastry", "o1": by_name.get(a["o1"], a["o1"]), "o2": by_name.get(a["o2"], a["o2"]),
                     "p1": a["p1"], "p2": a["p2"], "aspect": a["aspect"], "orb": a.get("orb")})
    for owner, items in facts.transit_aspects.items():
        for a in items:
            rows.append({"kind": "transit", "o1": "sky", "o2": owner, "p1": a["p1"], "p2": a["p2"], "aspect": a["aspect"],
                         "orb": a.get("orb")})
    for e in facts.calendar or []:
        if e.get("type") == "TRANSIT":
            owner = "user" if e["target"] == "User" else "partner"
            rows.append({"kind": "calendar", "o1": "sky", "o2": owner, "p1": e["planet"], "p2": e["natal_planet"],
                         "aspect": e["aspect"].lower(), "orb": None, "row": e})
    return rows


def _strip_names(text: str, facts: Facts) -> str:
    for key in ("user", "partner"):
        rx = _name_re(facts.names.get(key, ""))
        if rx:
            text = rx.sub(" ", text)
    return text


def _aspect_pairs(mentions: List[Mention], a: Mention, groups: List[Group], seg: str,
                  facts: Facts) -> List[Tuple[Mention, Mention]]:
    """Двойките планети, за които се отнася думата за аспект:
    „Сатурн в тригон с Марс и Слънце“ -> (Сатурн, Марс), (Сатурн, Слънце); „тригонът Юпитер–Плутон“ -> (Юпитер, Плутон);
    „Венера и Сатурн в съвпад“ -> (Венера, Сатурн); „Твоя Венера опозиция Иван Марс“ -> (Венера, Марс)."""
    gb = next((g for g in reversed(groups) if 0 <= a.start - g.end <= 16 and not re.search(r"[+·/|,]|\b(?:и|или)\b", seg[g.end:a.start])
               and gap_ok(_strip_names(seg[g.end:a.start], facts), {"образува", "образуват", "прави", "правят"})), None)
    ga = next((g for g in groups if 0 <= g.start - a.end <= 16 and not re.search(r"[+·/|]", seg[a.end:g.start])
               and gap_ok(_strip_names(seg[a.end:g.start], facts), {"между", "спрямо", "образува", "образуват"})), None)
    if gb and any(q is not a and q.kind == "aspect" and 0 <= gb.start - q.end <= 16
                  and not any(x.kind == "planet" and q.end <= x.start < gb.start for x in mentions) for q in mentions):
        gb = None                                  # „квадрат към Уран и Нептун на Иван в квадрат към Нептун“: групата е обект на предишния аспект
    if gb and ga:
        if len(gb.planets) == 1:
            return [(gb.planets[0], o) for o in ga.planets]
        if len(ga.planets) == 1:
            return [(sb, ga.planets[0]) for sb in gb.planets]
        return []
    if ga and len(ga.planets) >= 2 and not re.search(r"(?<![\w])(?:към|с|със|на|спрямо)(?![\w])", seg[a.end:ga.start]):
        return [(ga.planets[0], ga.planets[1])]                    # „тригонът Юпитер–Плутон“, не „квадратури към Луната и Сатурн“
    if ga and len(ga.planets) == 1 and not gb:
        # „Съвпадът на Луната с Юпитер“
        g2 = next((g for g in groups if 0 <= g.start - ga.end <= 8 and g is not ga and not re.search(r"[+·/|]", seg[ga.end:g.start])
                   and gap_ok(_strip_names(seg[ga.end:g.start], facts), {"спрямо"})), None)
        if g2:
            return [(ga.planets[0], g2.planets[0])]
    if gb and len(gb.planets) >= 2:
        return [(gb.planets[-2], gb.planets[-1])]
    # по-свободно: една планета малко преди и една малко след, без друга дума за аспект между тях
    before = [p for g in groups for p in g.planets if p.end <= a.start and a.start - p.end <= 60
              and not re.search(r"[.!?]", seg[p.end:a.start])]
    after = [p for g in groups for p in g.planets if p.start >= a.end and p.start - a.end <= 60
             and not re.search(r"[.!?]", seg[a.end:p.start])]
    if before and after and not any(m.kind == "aspect" and m is not a and before[-1].end <= m.start < after[0].start for m in mentions):
        return [(before[-1], after[0])]
    return []


def aspect_claims(seg: str, mentions: List[Mention], groups: List[Group], facts: Facts) -> List[Dict]:
    aspects = [m for m in mentions if m.kind == "aspect"]
    orbs = [(m.start(), float((m.group(1) or m.group(2)).replace(",", "."))) for m in ORB_RE.finditer(seg)]
    exact_for: Dict[int, List[Tuple[int, int]]] = {}        # дума за аспект -> думите „точен“, които ѝ принадлежат (най-близката)
    for e in EXACT_WORD_RE.finditer(seg):
        if not aspects:
            break
        near = min(range(len(aspects)), key=lambda j: min(abs(e.start() - aspects[j].end), abs(aspects[j].start - e.end())))
        if min(abs(e.start() - aspects[near].end), abs(aspects[near].start - e.end())) <= 40:
            exact_for.setdefault(near, []).append((e.start(), e.end()))
    claims: List[Dict] = []
    for i, a in enumerate(aspects):
        if HEDGE_RE.search(seg[max(0, a.start - 50):a.end + 50]):
            continue
        if any(o is not a and re.fullmatch(r"\s*/\s*", seg[min(a.end, o.end):max(a.start, o.start)]) and (o.end <= a.start or o.start >= a.end)
               for o in aspects):
            continue                                              # „тригон/секстил“: авторът не е избрал един аспект
        pairs = _aspect_pairs(mentions, a, groups, seg, facts)
        if not pairs:
            continue
        # общо подлежащо: „Марс в квадрат с Луната и в опозиция с Юпитер“: Луната е допълнение на първия аспект
        shared = False
        if claims:
            last = claims[-1]
            if len(pairs) == 1 and pairs[0][0].start == last["_p2"].start and last["_a"].end <= pairs[0][0].start:
                pairs = [(last["_p1"], pairs[0][1])]
                shared = True
        twin = next((o for j, o in enumerate(aspects) if j != i and o.key != a.key and abs(o.start - a.start) <= 22
                     and not any(x.kind == "planet" and min(a.end, o.end) <= x.start < max(a.start, o.start) for x in mentions)
                     and not re.search(r"[,.;]|\b(?:и|или|но|а|с|към|на)\b", seg[min(a.end, o.end):max(a.start, o.start)])), None)
        later_starts = [min(q.start, a.start) for q in aspects[i + 1:]]
        for k, (p1, p2) in enumerate(pairs):
            lo, hi = (min(p2.start, a.start) if shared else min(p1.start, a.start)), max(p2.end, a.end)
            limit = min([hi + 70] + later_starts) if later_starts else hi + 70
            found = [v for pos, v in orbs if hi <= pos < limit]
            if not found:
                found = [v for pos, v in orbs if max(p.end for p in (p2,) + tuple(x for x, _ in pairs)) <= pos < limit]
            orb = found[k] if len(found) == len(pairs) else (found[0] if len(pairs) == 1 and found else None)
            claims.append({
                "p1": p1.key, "p2": p2.key, "aspect": a.key, "twin": twin.key if twin else None,
                "o1": cue_before(seg, p1.start, facts) or cue_after(seg, p1.end, facts),
                "o2": cue_before(seg, p2.start, facts) or cue_after(seg, p2.end, facts),
                "k1": kind_cue(seg, p1.start, mentions), "k2": kind_cue(seg, p2.start, mentions),
                "orb": orb,
                "exact_word": bool(exact_for.get(i)), "exact_spans": exact_for.get(i, []),
                "excerpt": _short(seg, max(0, lo - 12), min(len(seg), hi + 30)),
                "span": (lo, hi), "_p1": p1, "_p2": p2, "_a": a,
            })
    return claims


def _exact_has_date(seg: str, facts: Facts, spans: List[Tuple[int, int]]) -> bool:
    """„точен на 12 януари“: думата има дата след себе си (до 24 знака) или непосредствено преди себе си. Датата се сверява в check_dates."""
    items = parse_dates(seg, facts)
    for e0, e1 in spans:
        nxt = next((x for x in items if x.start >= e1), None)
        prv = next((x for x in reversed(items) if x.end <= e0), None)
        if nxt is not None and nxt.start - e1 <= 24 and not re.search(r"[.!?;]", seg[e1:nxt.start]):
            return True
        if prv is not None and e0 - prv.end <= 8:
            return True
    return False


def check_aspects(seg: str, mentions: List[Mention], groups: List[Group], facts: Facts) -> List[Violation]:
    rows = aspect_rows(facts)
    if not rows:
        return []
    out = []
    for c in aspect_claims(seg, mentions, groups, facts):
        pair = sorted([c["p1"], c["p2"]])
        same_pair = [r for r in rows if sorted([r["p1"], r["p2"]]) == pair]
        if c["twin"]:
            if c["aspect"] > c["twin"]:
                continue
            out.append(Violation("aspect", "hard", c["excerpt"],
                                 f"За двойката {_pl(c['p1'])} и {_pl(c['p2'])} са употребени две различни думи за аспект "
                                 f"({ASPECT_BG[c['aspect']]} и {ASPECT_BG[c['twin']]}); трябва да е един вид, както е в данните."))
            continue
        if not same_pair:
            if any(r["p1"] in pair or r["p2"] in pair for r in rows):
                out.append(Violation("aspect", "hard", c["excerpt"],
                                     f"Между {_pl(c['p1'])} и {_pl(c['p2'])} няма аспект в данните ({ASPECT_BG[c['aspect']]} не съществува за тази двойка)."))
            continue
        typed = [r for r in same_pair if r["aspect"] == c["aspect"]]
        if not typed:
            have = sorted({ASPECT_BG[r["aspect"]] for r in same_pair})
            out.append(Violation("aspect", "hard", c["excerpt"],
                                 f"Между {_pl(c['p1'])} и {_pl(c['p2'])} в данните е {', '.join(have)}, не {ASPECT_BG[c['aspect']]}."))
            continue
        # собственици и вид (транзит/натал), когато се разпознават
        def owners_ok(r) -> bool:
            for planet, owner, kind in ((c["p1"], c["o1"], c["k1"]), (c["p2"], c["o2"], c["k2"])):
                if owner is None and kind is None:
                    continue
                options = {(r["p1"], r["o1"]), (r["p2"], r["o2"])}
                ok = False
                for p, o in options:
                    if p != planet:
                        continue
                    if kind == "transit" and o != "sky":
                        continue
                    if kind == "natal" and o == "sky":
                        continue
                    if owner is not None and o != owner and o != "sky":
                        continue                                  # „Марс на Иван“ в периода е транзитният Марс към Иван
                    ok = True
                if not ok:
                    return False
            return True
        typed_owned = [r for r in typed if owners_ok(r)]
        if not typed_owned:
            have = "; ".join(f"{_pl(r['p1'])} ({_who(facts, r['o1'])}) – {_pl(r['p2'])} ({_who(facts, r['o2'])}), {_ROW_KIND.get(r['kind'], '')}"
                             for r in typed[:4])
            out.append(Violation("aspect", "hard", c["excerpt"],
                                 f"{ASPECT_BG[c['aspect']].capitalize()} между {_pl(c['p1'])} и {_pl(c['p2'])} в тази връзка (чии планети, транзитна или "
                                 f"натална) не е в данните. {ASPECT_BG[c['aspect']].capitalize()} има само: {have}."))
            continue
        if c["orb"] is not None:
            orb_rows = ([r for r in typed_owned if r["kind"] == "calendar"] or typed_owned) if facts.mode in _PARTIAL_SKY_MODES else typed_owned
            orbs = [r["orb"] for r in orb_rows if r.get("orb") is not None]
            if orbs and min(abs(o - c["orb"]) for o in orbs) > 0.15:
                out.append(Violation("orb", "hard", c["excerpt"],
                                     f"Орбисът не съвпада с данните за {_pl(c['p1'])} – {_pl(c['p2'])} ({ASPECT_BG[c['aspect']]}). "
                                     f"Вярно: {', '.join(f'{o:.2f}°' for o in orbs[:3])}."))
            elif not orbs and any(r["kind"] == "calendar" for r in orb_rows):
                out.append(Violation("orb", "soft", c["excerpt"], "За аспектите в периода няма орбис в данните: не посочвай орбис."))
        if c["exact_word"] and not _exact_has_date(seg, facts, c["exact_spans"]):
            calendar_rows = [r for r in typed_owned if r["kind"] == "calendar"]
            if calendar_rows and not any(r["row"].get("exact") for r in calendar_rows):
                out.append(Violation("exact", "hard", c["excerpt"],
                                     f"Аспектът {_pl(c['p1'])} – {_pl(c['p2'])} ({ASPECT_BG[c['aspect']]}) няма точен момент в периода: "
                                     f"описвай го като активен или приближаващ се, не като точен."))
    return out


# --------------------------------------------------------------------------------------------------------------
# Куспида, управител, брой ретроградни
# --------------------------------------------------------------------------------------------------------------
CUSP_RE = re.compile(r"куспид\w*\s*(?:на\s+)?(\d{1,2})\s*[-–]?\s*(?:ви|ри|ти|ми|и|ия|ият)?\s*(?:дом)?\s*[:\-–(]?\s*(?:е\s+)?(?:в|във)?\s*", re.IGNORECASE)
_RULER_WORD_BEFORE = re.compile(r"управител\w*\s+(?:на\s+)?$", re.IGNORECASE)
_CUSP_MID = re.compile(r"\s*(?:\([^)]*\)\s*)?[,:–-]?\s*(?:(?:е|са|започва|започват|се\s+намира|попада|стои|остава)\s+)?(?:в|във)\s*", re.IGNORECASE)
_RULER_AFTER_RE = re.compile(r"(?:управител\w*\s*(?:е|са|му\s+е|ѝ\s+е)?|управляван\w*\s+(?:е\s+)?от|(?:се\s+)?управлява\w*\s+(?:се\s+)?от|владетел\w*\s*(?:е)?)\s*(?:планетата\s+)?", re.IGNORECASE)


def _next_planet(seg: str, pos: int, mentions: List[Mention], limit: int = 24) -> Optional[Mention]:
    for m in mentions:
        if m.kind == "planet" and m.start >= pos and m.start - pos <= limit:
            return m
    return None


def _next_sign(seg: str, pos: int, mentions: List[Mention], limit: int = 20) -> Optional[Mention]:
    for m in mentions:
        if m.kind == "sign" and m.start >= pos and m.start - pos <= limit:
            return m
    return None


def house_subject_claims(seg: str, mentions: List[Mention], facts: Facts) -> Tuple[List[Dict], List[Dict]]:
    """Твърдения, в които домът е подлог: „Шести дом започва в Рак“, „Върхът на 8-ми дом е в Лъв, управляван от Слънце“,
    „управител на 4-ти дом е Венера“. Връща (куспиди, управители)."""
    cusps: List[Dict] = []
    rulers: List[Dict] = []
    houses = [m for m in mentions if m.kind == "house"]
    planets = [m for m in mentions if m.kind == "planet"]
    for i, h in enumerate(houses):
        before = seg[max(0, h.start - 40):h.start]
        owner = cue_before(seg, h.start, facts, window=22) or cue_after(seg, h.end, facts)
        placed = next((p for p in reversed(planets) if p.end <= h.start and h.start - p.end <= 30
                       and gap_ok(seg[p.end:h.start], {"в", "във"})), None)      # „Слънце в 7-ми дом“: позиция на планета
        ruler_head = _RULER_WORD_BEFORE.search(before)
        if placed is None and not ruler_head:
            sign = next((x for x in mentions if x.kind == "sign" and 0 <= x.start - h.end <= 30), None)
            if sign is not None and _CUSP_MID.fullmatch(seg[h.end:sign.start]):
                cusps.append({"house": h.key, "sign": sign.key, "owner": owner,
                              "excerpt": _short(seg, max(0, h.start - 14), min(len(seg), sign.end + 12))})
        # управител след дома: „… управляван от Слънце“, „негов управител е Меркурий“
        clause_end = houses[i + 1].start if i + 1 < len(houses) else len(seg)
        clause = seg[h.end:min(clause_end, h.end + 150)]
        if placed is None or ruler_head:
            for m in _RULER_AFTER_RE.finditer(clause):
                pl = _next_planet(seg, h.end + m.end(), mentions, limit=14)
                if pl is not None and pl.start < clause_end and not HEDGE_RE.search(seg[max(0, h.start - 30):pl.end]):
                    rulers.append({"house": h.key, "planet": pl.key, "owner": owner,
                                   "excerpt": _short(seg, max(0, h.start - 14), min(len(seg), pl.end + 10))})
                    break
        if ruler_head:
            head_start = h.start - len(before) + ruler_head.start()
            after = _next_planet(seg, h.end, mentions, limit=14)
            if after is not None and gap_ok(seg[h.end:after.start], {"е", "са"}) and not HEDGE_RE.search(seg[max(0, head_start - 20):after.end]):
                rulers.append({"house": h.key, "planet": after.key, "owner": owner,         # „управител на 4-ти дом е Венера“
                               "excerpt": _short(seg, max(0, head_start - 4), min(len(seg), after.end + 8))})
            else:
                pl = next((p for p in reversed(planets) if p.end <= head_start and head_start - p.end <= 12), None)
                if pl is not None:                                                            # „Венера е управител на 4-ти дом“
                    rulers.append({"house": h.key, "planet": pl.key, "owner": owner,
                                   "excerpt": _short(seg, max(0, pl.start - 4), min(len(seg), h.end + 10))})
    return cusps, rulers


def check_cusps_rulers(seg: str, mentions: List[Mention], facts: Facts) -> List[Violation]:
    out: List[Violation] = []
    if not facts.cusps:
        return out
    cusp_claims, ruler_claims = house_subject_claims(seg, mentions, facts)
    for m in CUSP_RE.finditer(seg):                                    # „куспида 6 в Рак“ (без думата „дом“)
        house = int(m.group(1))
        sign = _next_sign(seg, m.end(), mentions, limit=8)
        if 1 <= house <= 12 and sign is not None and not any(c["house"] == house and c["sign"] == sign.key for c in cusp_claims):
            cusp_claims.append({"house": house, "sign": sign.key,
                                "owner": cue_before(seg, m.start(), facts) or cue_after(seg, sign.end, facts),
                                "excerpt": _short(seg, max(0, m.start() - 8), sign.end + 12)})
    done: Set[Tuple] = set()
    for c in ruler_claims:
        key = ("r", c["house"], c["planet"])
        if key in done or not 1 <= c["house"] <= 12:
            continue
        done.add(key)
        real = {o: facts.cusps[o][c["house"]]["ruler"] for o in facts.cusps if c["house"] in facts.cusps[o]
                and (c["owner"] is None or o == c["owner"])}
        if real and c["planet"] not in real.values():
            text = "; ".join(f"{_who(facts, o)}: {_pl(r)}" for o, r in real.items() if r)
            out.append(Violation("ruler", "hard", c["excerpt"],
                                 f"{c['house']}-ти дом не се управлява от {_pl(c['planet'])} по данните. Вярно: {text}."))
    for c in cusp_claims:
        key = ("c", c["house"], c["sign"])
        if key in done or not 1 <= c["house"] <= 12:
            continue
        done.add(key)
        real = {o: facts.cusps[o][c["house"]] for o in facts.cusps if c["house"] in facts.cusps[o]
                and (c["owner"] is None or o == c["owner"])}
        if real and c["sign"] not in {v["sign"] for v in real.values()}:
            text = "; ".join(f"{_who(facts, o)}: {SIGN_BG.get(v['sign'], v['sign'])} {v['deg']}°{v['min']:02d}'" for o, v in real.items())
            out.append(Violation("cusp", "hard", c["excerpt"],
                                 f"Куспидата на {c['house']}-ти дом не е в {SIGN_BG.get(c['sign'], c['sign'])} по данните. Вярно: {text}."))
    return out


# Изречение, което обяснява липсата на час, не е твърдение за домове (напр. „Часът не е известен, затова няма Асцендент“)
EXPLAINS_NO_TIME_RE = re.compile(
    r"(?<![\w])(?:неизвестен|неизвестна|неизвестно|непознат\w*|не е известен|не са известни|без (?:известен )?час|"
    r"липс\w+\s+час|няма\s+(?:известен\s+)?час)(?![\w])", re.IGNORECASE)


def check_unknown_time(seg: str, mentions: List[Mention], groups: List[Group], facts: Facts) -> List[Violation]:
    """Човек без известен час няма домове, Асцендент и MC. Твърдение за тях е измислица (hard). Когато собственикът не се
    разпознава, твърдението е нарушение само ако никой в анализа няма час; иначе минава (не може да се реши сигурно)."""
    out: List[Violation] = []
    if not facts.no_houses or EXPLAINS_NO_TIME_RE.search(seg):
        return out
    everyone = set(facts.natal) or set(facts.names)
    all_unknown = bool(everyone) and everyone <= facts.no_houses

    def unknown(owner: Optional[str]) -> bool:
        return (owner in facts.no_houses) if owner else all_unknown

    def violation(owner: Optional[str], excerpt: str) -> Violation:
        who = _who(facts, owner) if owner in facts.no_houses else "човека"
        return Violation("no_time", "hard", excerpt,
                         f"За {who} часът на раждане е неизвестен: няма домове, Асцендент и MC. Не твърдей дом, Асцендент "
                         f"или MC за {who}.")

    for c in placement_claims(seg, mentions, groups, facts)[0]:
        owner = c["ho"] or (c["po"] if c["kind"] != "transit" else None)
        if unknown(owner):
            out.append(violation(owner, c["excerpt"]))
    for m in mentions:
        if m.kind == "planet" and m.key in ("ASC", "MC"):
            owner = cue_before(seg, m.start, facts) or cue_after(seg, m.end, facts)
            if unknown(owner):
                out.append(violation(owner, _short(seg, max(0, m.start - 20), min(len(seg), m.end + 20))))
    cusp_claims, ruler_claims = house_subject_claims(seg, mentions, facts)
    for c in cusp_claims + ruler_claims:
        if unknown(c["owner"]):
            out.append(violation(c["owner"], c["excerpt"]))
    return out


def check_retro_count(seg: str, mentions: List[Mention], groups: List[Group], facts: Facts) -> List[Violation]:
    out = []
    if not facts.retro_count:
        return out
    known = sorted(set(facts.retro_count.values()))
    for m in (m for rx in RETRO_COUNT_RES for m in rx.finditer(seg)):
        word = m.group(1).lower()
        n = int(word) if word.isdigit() else NUMBER_WORDS.get(word)
        if n is None:
            continue
        text = ", ".join(f"{_who(facts, k)}: {v}" for k, v in facts.retro_count.items())
        # изброени планети след числото: „четири планети са ретроградни (Венера, Сатурн, Уран, Нептун, Плутон, Хирон)“
        listed = next((g for g in groups if 0 <= g.start - m.end() <= 80 and not re.search(r"[.!?]", seg[m.end():g.start])), None)
        if listed is not None and len(listed.planets) >= 2 and len(listed.planets) != n:
            out.append(Violation("retro_count", "hard", _short(seg, max(0, m.start() - 10), listed.end + 4),
                                 f"Броят ретроградни ({n}) не съвпада с изброените планети ({len(listed.planets)}). Вярно: {text}."))
        elif n not in known and facts.mode not in _PARTIAL_SKY_MODES:        # в периода небето няма общ брой в данните
            out.append(Violation("retro_count", "hard", _short(seg, max(0, m.start() - 10), m.end() + 10),
                                 f"Броят ретроградни планети не съвпада с данните. Вярно: {text}."))
    return out


# --------------------------------------------------------------------------------------------------------------
# Дати, часове и събития от календара
# --------------------------------------------------------------------------------------------------------------
def _years_of(facts: Facts) -> Set[int]:
    years: Set[int] = set()
    for d in (facts.snapshot_date, facts.report_date, *(facts.period or ())):
        if d:
            years.add(int(d[:4]))
    if facts.period:
        years.update(range(int(facts.period[0][:4]), int(facts.period[1][:4]) + 1))
    return years


@dataclass
class DateItem:
    """Дата или диапазон от дати в изречението."""
    dates: List[date]
    time: Optional[str]
    start: int
    end: int
    approx: bool = False
    relative: bool = False          # „след 19 ноември“, „преди 5 декември“

    @property
    def is_range(self) -> bool:
        return len(self.dates) > 1

    def covers(self, iso: str, tolerance: int = 0) -> bool:
        lo = (self.dates[0] - timedelta(days=tolerance)).isoformat()
        hi = (self.dates[-1] + timedelta(days=tolerance)).isoformat()
        return lo <= iso <= hi


def parse_dates(seg: str, facts: Facts) -> List[DateItem]:
    """Дати в изречението. „1–5 октомври“ и „30 октомври – 2 ноември“ са един диапазон. Дата без година се отнася към
    годината на периода/анализа; дата с година извън тях се пропуска (рождена дата и пр.)."""
    years = sorted(_years_of(facts))
    if not years:
        return []

    def resolve(day: int, month: int, year: Optional[int]) -> Optional[date]:
        try:
            if year is not None:
                return date(year, month, day) if year in years else None
            ref = facts.period[0] if facts.period else (facts.snapshot_date or facts.report_date)
            base_year = int(ref[:4])
            candidates = [y for y in years if y >= base_year] or years
            for y in candidates:
                d = date(y, month, day)
                if facts.period is None or facts.period[0] <= d.isoformat() <= facts.period[1] or y == candidates[-1]:
                    return d
            return date(candidates[-1], month, day)
        except ValueError:
            return None

    items: List[DateItem] = []
    taken: List[Tuple[int, int]] = []
    for m in DATE_TEXT_RE.finditer(seg):
        month = MONTHS[m.group(3).lower()]
        year = int(m.group(4)) if m.group(4) else None
        days = [int(m.group(1))] + ([int(m.group(2))] if m.group(2) else [])
        resolved = [resolve(d, month, year) for d in days]
        taken.append((m.start(), m.end()))
        if any(r is None for r in resolved) or resolved != sorted(resolved):
            continue
        time_m = TIME_RE.search(seg[m.end():m.end() + 14])
        tm = (f"{int(time_m.group(1)):02d}:{time_m.group(2)}"
              if time_m and len(days) == 1 and re.match(r"[\s,]*(?:в|от|около|ч\.?)?\s*$", seg[m.end():m.end() + time_m.start()]) else None)
        items.append(DateItem(list(resolved), tm, m.start(), m.end()))
    for m in DATE_ISO_RE.finditer(seg):
        try:
            dt = date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
        except ValueError:
            continue
        if dt.year in years:
            items.append(DateItem([dt], None, m.start(), m.end()))
            taken.append((m.start(), m.end()))
    for m in DATE_NUMRANGE_RE.finditer(seg):
        month = int(m.group(3))
        if not 1 <= month <= 12:
            continue
        year = int(m.group(4)) if m.group(4) else None
        resolved = [resolve(int(m.group(1)), month, year), resolve(int(m.group(2)), month, year)]
        taken.append((m.start(), m.end()))
        if all(resolved) and resolved[0] <= resolved[1]:
            items.append(DateItem(list(resolved), None, m.start(), m.end()))
    for m in DATE_NUM_RE.finditer(seg):
        if any(a <= m.start() < b for a, b in taken):
            continue
        day, month = int(m.group(1)), int(m.group(2))
        if not (1 <= month <= 12 and 1 <= day <= 31):
            continue
        dt = resolve(day, month, int(m.group(3)) if m.group(3) else None)
        if dt:
            items.append(DateItem([dt], None, m.start(), m.end()))
    items.sort(key=lambda x: x.start)
    merged: List[DateItem] = []
    for it in items:
        prev = merged[-1] if merged else None
        if prev and not prev.is_range and not it.is_range and prev.dates[0] <= it.dates[0] \
                and re.fullmatch(r"\s*(?:[–-]|до)\s*", seg[prev.end:it.start]):
            merged[-1] = DateItem([prev.dates[0], it.dates[0]], None, prev.start, it.end)
        else:
            merged.append(it)
    items = merged
    for m in ORDINAL_DAY_RE.finditer(seg):                      # „(1–19 октомври, точен на 13-ти)“
        before = [x for x in items if x.end <= m.start()]
        if not before:
            continue
        ref = before[-1].dates[-1]
        try:
            items.append(DateItem([date(ref.year, ref.month, int(m.group(1)))], None, m.start(), m.end()))
        except ValueError:
            continue
    items.sort(key=lambda x: x.start)
    for it in items:
        pre = seg[max(0, it.start - 16):it.start]
        it.approx = bool(re.search(r"(?:около|към|приблизително|~|≈)\s*(?:на\s*)?$", pre, re.IGNORECASE))
        it.relative = bool(re.search(r"(?:след|преди)\s*(?:около\s*)?$", pre, re.IGNORECASE))
    return items


@dataclass
class EventIndex:
    """Календарът във вид, удобен за сверяване на дати."""
    stations: List[Tuple[str, str, str, str]] = field(default_factory=list)        # (планета, посока, дата, час)
    ingresses: List[Tuple[str, str, str, str]] = field(default_factory=list)       # (планета, знак, дата, час)
    lunations: List[Tuple[str, str, str, str]] = field(default_factory=list)       # (new|full|eclipse, знак, дата, час)
    series: List[Dict] = field(default_factory=list)
    global_dates: Set[str] = field(default_factory=set)      # всички дати от календара и границите
    bound_dates: Set[str] = field(default_factory=set)       # само граници: период, начало и край на месец, дата на отчета


def build_event_index(facts: Facts) -> EventIndex:
    idx = EventIndex()
    for e in facts.calendar or []:
        t = e.get("type")
        if t == "TRANSIT":
            exact = [(x["when"][:10], x["when"][11:16]) for x in e.get("exact", [])]
            outside = [(w[:10], w[11:16]) for w in e.get("exact_outside_period", [])]
            closest = e.get("closest_in_period") or {}
            extra = {closest["when"][:10]} if closest.get("when") else set()
            idx.series.append({"owner": "user" if e["target"] == "User" else "partner", "p1": e["planet"], "p2": e["natal_planet"],
                               "aspect": e["aspect"].lower(), "exact": exact, "outside": outside,
                               "bounds": {e["active_from"], e["active_to"]} | extra, "row": e})
            idx.global_dates.update(d for d, _ in exact + outside)
            idx.global_dates.update({e["active_from"], e["active_to"]} | extra)
        else:
            d, tm = e["when"][:10], e["when"][11:16]
            idx.global_dates.add(d)
            if t == "RETROGRADE":
                idx.stations.append((e["planet"], e["direction"], d, tm))
            elif t == "INGRESS":
                idx.ingresses.append((e["planet"], e["sign"], d, tm))
            elif t in ("LUNATION", "ECLIPSE"):
                kind = "eclipse" if t == "ECLIPSE" else ("new" if e["event"].startswith("New") else "full")
                sign = next((k for k in SIGN_FORMS if e["event"].endswith(k) or f" in {k}" in e["event"]), "")
                idx.lunations.append((kind, sign, d, tm))
    if facts.period:
        start, end = facts.period
        idx.bound_dates.update({start, end})
        y, m = int(start[:4]), int(start[5:7])
        while f"{y:04d}-{m:02d}" <= end[:7]:
            idx.bound_dates.add(f"{y:04d}-{m:02d}-01")
            nxt = date(y + (m == 12), 1 if m == 12 else m + 1, 1)
            idx.bound_dates.add((nxt - timedelta(days=1)).isoformat())
            y, m = nxt.year, nxt.month
    if facts.report_date:
        idx.bound_dates.add(facts.report_date)
    idx.global_dates |= idx.bound_dates
    return idx


STATION_WORD_RE = re.compile(r"(?<![\w])(?:ретроград\w*|директ(?:е)?н\w*|станци\w*|обръща\w*\s+посока|завива\w*)", re.IGNORECASE)
INGRESS_WORD_RE = re.compile(r"(?<![\w])(?:влиза\w*|навлиза\w*|преминава\w*|влизане|навлизане|ингрес\w*|връща\w*(?:\s+се)?|се\s+връща\w*)\s+(?:отново\s+)?(?:в|във)\s+", re.IGNORECASE)
LUNATION_WORD_RE = re.compile(r"(?<![\w])(новолуни\w*|пълнолуни\w*|затъмнени\w*)", re.IGNORECASE)
_EVENT_GAP_OK = {"от", "на", "до", "в", "във", "става", "стават", "ще", "е", "са", "бъде", "започва", "започват", "завършва", "приключва",
                 "спира", "обръща", "посока", "отново", "вече", "планетата", "и", "точно", "около", "към", "ден", "часа", "ч", "ретрограден",
                 "ретроградна", "директен", "директна", "влиза", "навлиза", "се", "си", "движение", "движението", "ход", "хода", "точен",
                 "точна", "пик", "кулминация", "кулминира", "активен", "активна", "период", "орб"}
_NO_EVENT_PLANETS = ("ASC", "MC", "Node", "Moon", "Chiron")


@dataclass
class Anchor:
    """Събитие в изречението, за което може да се чете дата: станция, влизане в знак, лунация или аспект."""
    kind: str                       # station | ingress | lunation | aspect
    start: int
    end: int
    planets: List[str] = field(default_factory=list)
    sign: Optional[str] = None
    lunation: Optional[str] = None
    claim: Optional[Dict] = None


def _planets_near(mentions: List[Mention], pos: int, limit: int = 45) -> List[Mention]:
    near = [m for m in mentions if m.kind == "planet" and (abs(m.start - pos) <= limit or abs(m.end - pos) <= limit)]
    return sorted(near, key=lambda m: min(abs(m.start - pos), abs(m.end - pos)))


def event_anchors(seg: str, mentions: List[Mention], groups: List[Group], facts: Facts) -> List[Anchor]:
    anchors: List[Anchor] = []
    for m in INGRESS_WORD_RE.finditer(seg):
        sign = next((x for x in mentions if x.kind == "sign" and 0 <= x.start - m.end() <= 3), None)
        near = [p for p in _planets_near(mentions, m.start()) if p.key not in _NO_EVENT_PLANETS]
        if sign and near:
            anchors.append(Anchor("ingress", m.start(), sign.end, [near[0].key], sign=sign.key))
    for m in LUNATION_WORD_RE.finditer(seg):
        word = m.group(1).lower()
        kind = "new" if word.startswith("новолуни") else ("full" if word.startswith("пълнолуни") else "eclipse")
        sign = next((x for x in mentions if x.kind == "sign" and 0 <= x.start - m.end() <= 14), None)
        anchors.append(Anchor("lunation", m.start(), sign.end if sign else m.end(), lunation=kind, sign=sign.key if sign else None))
    for m in STATION_WORD_RE.finditer(seg):
        if any(a.kind == "ingress" and a.start - 10 <= m.start() <= a.end + 10 for a in anchors):
            continue
        near = [p for p in _planets_near(mentions, m.start()) if p.key not in ("Sun",) + _NO_EVENT_PLANETS]
        if near:
            anchors.append(Anchor("station", m.start(), m.end(), [near[0].key]))
    for c in aspect_claims(seg, mentions, groups, facts):
        anchors.append(Anchor("aspect", c["span"][0], c["span"][1], claim=c))
    return anchors


def _gap(item: DateItem, a: Anchor) -> int:
    if item.end <= a.start:
        return a.start - item.end
    if a.end <= item.start:
        return item.start - a.end
    return 0


def _adjacent(seg: str, item: DateItem, a: Anchor, mentions: List[Mention]) -> bool:
    """Датата е непосредствено до събитието (станция, влизане, лунация): между тях само свързващи думи, имена и знаци."""
    lo, hi = (item.end, a.start) if item.end <= a.start else (a.end, item.start)
    gap = seg[lo:hi]
    if len(gap) > 34 or re.search(r"[.!?;)]", gap):
        return False                           # „(Новолуние) и 31 октомври (Луна в Рак)“: скобата затваря чуждото събитие
    if a.kind == "aspect":
        return True
    for m in sorted((x for x in mentions if x.kind in ("planet", "sign") and lo <= x.start and x.end <= hi), key=lambda x: -x.start):
        gap = gap[:m.start - lo] + " " + gap[m.end - lo:]                  # имената на планети и знаци не пречат: „17 октомври · Плутон става директен“
    toks = _tokens(gap)
    return all(t in _EVENT_GAP_OK or t in _FILLER or t.startswith(("ретроград", "директ", "транзит", "натал")) for t in toks)


def _bind(seg: str, item: DateItem, anchors: List[Anchor], mentions: List[Mention], items: List[DateItem],
          exact_span: Optional[Tuple[int, int]] = None) -> Optional[Anchor]:
    """Най-близкото събитие до датата; при две еднакво близки различни събития няма привързване.
    exact_span: датата е уточнена с „точен“ (позицията на думата). Тогава се привързва към аспект:
    1) „Точна квадратура Юпитер–Сатурн“: аспектът, който започва веднага след думата;
    2) „Нептун квадрат Нептун (точен на 22.11)“: най-близкият аспект преди думата (до 60 знака, без край на изречение);
    3) иначе най-близкият аспект след датата (до 90 знака)."""
    if exact_span is not None:
        e0, e1 = exact_span
        aspects = [a for a in anchors if a.kind == "aspect"]
        after_word = [a for a in aspects if 0 <= a.start - e1 <= 14 and not (e1 <= item.start <= a.start)]       # не отвъд самата дата
        if after_word:
            return min(after_word, key=lambda a: a.start - e1)
        before_word = [a for a in aspects if 0 <= e0 - a.end <= 60 and not re.search(r"[.!?;]", seg[a.end:e0])]
        if before_word:
            return max(before_word, key=lambda a: a.end)
        right = [a for a in aspects if 0 <= a.start - item.end <= 90 and not re.search(r"[.!?;]", seg[item.end:a.start])]
        left = [a for a in aspects if 0 <= item.start - a.end <= 90 and not re.search(r"[.!?;]", seg[a.end:item.start])]
        pool = right or left
        return min(pool, key=lambda a: abs(a.start - item.end)) if pool else None
    cands = []
    for a in anchors:
        g = _gap(item, a)
        if g > 70 or not _adjacent(seg, item, a, mentions):
            continue
        if any(o is not item and (item.end <= o.start < a.start or a.end <= o.end <= item.start) for o in items):
            continue                                            # друга дата е по-близо до това събитие
        cands.append((g, a))
    if not cands:
        return None
    cands.sort(key=lambda x: x[0])
    if len(cands) > 1 and cands[1][0] - cands[0][0] <= 4 and cands[1][1].kind != cands[0][1].kind:
        return None
    return cands[0][1]


def check_dates(seg: str, mentions: List[Mention], groups: List[Group], facts: Facts) -> List[Violation]:
    if facts.mode not in ("period", "snapshot", "overview"):
        return []
    items = parse_dates(seg, facts)
    if not items:
        return []
    out: List[Violation] = []
    if facts.mode == "snapshot":
        for it in items:
            for d in it.dates:
                if facts.snapshot_date and d.isoformat() != facts.snapshot_date:
                    out.append(Violation("date", "soft", _short(seg, max(0, it.start - 20), it.end + 20),
                                         f"Анализът е само за {facts.snapshot_date} {facts.snapshot_time or ''}: не посочвай други дати."))
                    break
                if it.time and facts.snapshot_time and it.time != facts.snapshot_time:
                    out.append(Violation("time", "soft", _short(seg, max(0, it.start - 20), it.end + 20),
                                         f"Часът на анализа е {facts.snapshot_time}, не {it.time}."))
        return out
    if not facts.calendar:
        return out
    idx = build_event_index(facts)
    anchors = event_anchors(seg, mentions, groups, facts)
    rows = aspect_rows(facts)
    start, end = facts.period or ("0000-00-00", "9999-99-99")
    exact_targets: Dict[int, Tuple[int, int]] = {}  # датите, които думата „точен“ уточнява: „точен на 25 ноември“, „(22 ноември, точна)“
    for m_ in EXACT_WORD_RE.finditer(seg):
        nxt = next((x for x in items if x.start >= m_.end()), None)
        prv = next((x for x in reversed(items) if x.end <= m_.start()), None)
        if nxt is not None and nxt.start - m_.end() <= 24 and not re.search(r"[.!?;]", seg[m_.end():nxt.start]):
            exact_targets[id(nxt)] = (m_.start(), m_.end())
        elif prv is not None and m_.start() - prv.end <= 8:
            exact_targets[id(prv)] = (m_.start(), m_.end())

    def in_period(iso: str) -> bool:
        return start <= iso <= end

    def mismatched(item: DateItem, real_dates: List[date], tol: int, endpoints_only: bool) -> List[str]:
        """Датите, които не съвпадат с реално събитие. Диапазонът е верен, когато покрива реална дата (влизания, лунации)
        или когато поне един негов край е реална дата (станции: „3 октомври – 14 ноември“)."""
        inside = [d for d in item.dates if in_period(d.isoformat())]
        if not inside:
            return []
        if item.is_range:
            ok = (any(abs((d - rd).days) <= tol for d in item.dates for rd in real_dates) if endpoints_only
                  else any(item.covers(rd.isoformat(), tol) for rd in real_dates))
            return [] if ok else [d.isoformat() for d in inside]
        return [d.isoformat() for d in inside if not any(abs((d - rd).days) <= tol for rd in real_dates)]

    for it in items:
        if it.relative:
            continue
        excerpt = _short(seg, max(0, it.start - 40), min(len(seg), it.end + 24))
        tol = 1 if it.approx else 0
        a = _bind(seg, it, anchors, mentions, items, exact_span=exact_targets.get(id(it)))
        if a is None:
            if not it.is_range and in_period(it.dates[0].isoformat()) and it.dates[0].isoformat() not in idx.global_dates:
                d = it.dates[0]
                out.append(Violation("date", "soft", excerpt, f"Датата {d.day} {_month_bg(d)} не е в календара: цитирай само дати от данните."))
            continue
        checked = [d.isoformat() for d in it.dates if in_period(d.isoformat())]
        if a.kind == "station":
            planets = {a.planets[0]} | {p.key for p in mentions if p.kind == "planet" and p.key not in _NO_EVENT_PLANETS + ("Sun",)
                                        and min(abs(p.start - it.start), abs(p.end - it.end)) <= 40
                                        and not re.search(r"[.!?;]", seg[min(p.end, it.start):max(p.start, it.end)] if p.end <= it.start or p.start >= it.end else "")}
            real = [(dt, t, direction, p) for p, direction, dt, t in idx.stations if p in planets]
            if not checked:
                continue
            if not real:
                out.append(Violation("event", "hard", excerpt, f"В периода няма станция на {_pl(a.planets[0])}."))
                continue
            bad = mismatched(it, [date.fromisoformat(r[0]) for r in real], tol, endpoints_only=True)
            if bad:
                real_p = [r for r in real if r[3] == a.planets[0]] or real
                out.append(Violation("event", "hard", excerpt, f"Датата на станцията на {_pl(a.planets[0])} не е вярна. Вярно: "
                                     + "; ".join(f"{_dm(r[0])} {r[1]} ({'ретрограден' if r[2] == 'retrograde' else 'директен'})" for r in real_p) + "."))
            elif it.time and not it.is_range and not any(r[0] == it.dates[0].isoformat() and r[1] == it.time for r in real):
                out.append(Violation("time", "hard", excerpt, "Часът на станцията не е вярен. Вярно: "
                                     + "; ".join(f"{_dm(r[0])} {r[1]}" for r in real if r[0] == it.dates[0].isoformat()) + "."))
        elif a.kind == "ingress":
            real = [(dt, t) for p, s, dt, t in idx.ingresses if p == a.planets[0] and s == a.sign]
            if not checked:
                continue
            if not real:
                out.append(Violation("event", "hard", excerpt, f"В периода няма влизане на {_pl(a.planets[0])} в {SIGN_BG[a.sign]}."))
                continue
            bad = mismatched(it, [date.fromisoformat(r[0]) for r in real], tol, endpoints_only=False)
            if bad:
                out.append(Violation("event", "hard", excerpt, f"Датата на влизането на {_pl(a.planets[0])} в {SIGN_BG[a.sign]} не е вярна. Вярно: "
                                     + "; ".join(f"{_dm(r[0])} {r[1]}" for r in real) + "."))
            elif it.time and not it.is_range and not any(r[0] == it.dates[0].isoformat() and r[1] == it.time for r in real):
                out.append(Violation("time", "hard", excerpt, "Часът на влизането не е верен. Вярно: "
                                     + "; ".join(f"{_dm(r[0])} {r[1]}" for r in real if r[0] == it.dates[0].isoformat()) + "."))
        elif a.kind == "lunation":
            names = {"new": "новолуние", "full": "пълнолуние", "eclipse": "затъмнение"}
            real = [(dt, t, sg) for k, sg, dt, t in idx.lunations if k == a.lunation]
            if not checked:
                continue
            if not real:
                out.append(Violation("event", "hard", excerpt, f"В периода няма {names[a.lunation]}."))
                continue
            bad = mismatched(it, [date.fromisoformat(r[0]) for r in real], tol, endpoints_only=False)
            if bad:
                out.append(Violation("event", "hard", excerpt, f"Датата на {names[a.lunation]}то не е вярна. Вярно: "
                                     + "; ".join(f"{_dm(r[0])} {r[1]} ({SIGN_BG.get(r[2], r[2])})" for r in real) + "."))
                continue
            matches = [r for r in real if it.covers(r[0], tol)]
            slash = re.match(r"\s*/\s*[А-Я]", seg[a.end:a.end + 4]) is not None            # „Телец/Близнаци“: авторът не е избрал
            if a.sign and matches and not slash and a.sign not in {m_[2] for m_ in matches if m_[2]}:
                out.append(Violation("event", "hard", excerpt, f"Знакът на {names[a.lunation]}то на {_dm(matches[0][0])} е "
                                     f"{SIGN_BG.get(matches[0][2], matches[0][2])}, не {SIGN_BG.get(a.sign, a.sign)}."))
            elif it.time and not it.is_range and matches and matches[0][1] != it.time:
                out.append(Violation("time", "hard", excerpt, f"Часът на {names[a.lunation]}то е {matches[0][1]}, не {it.time}."))
        elif a.kind == "aspect":
            c = a.claim
            matched = [r for r in rows if r["kind"] == "calendar" and sorted([r["p1"], r["p2"]]) == sorted([c["p1"], c["p2"]])
                       and r["aspect"] == c["aspect"]]
            if not matched:
                continue
            ser = [s_ for s_ in idx.series if any(s_["row"] is r["row"] for r in matched)]
            all_exact = {dt for s_ in ser for dt, _ in s_["exact"] + s_["outside"]}
            bounds = {x for s_ in ser for x in s_["bounds"]}
            lo_b, hi_b = (min(bounds), max(bounds)) if bounds else ("9999", "0000")
            if id(it) in exact_targets:
                good = any(it.covers(x, tol) for x in all_exact)
                if not good:
                    real = sorted(all_exact)
                    out.append(Violation("exact", "hard", excerpt, "Датата на точния аспект не е вярна. Вярно: "
                                         + (", ".join(_dm(x) for x in real) if real else "аспектът няма точен момент в периода") + "."))
                elif it.time and not it.is_range:
                    times = {x[1] for s_ in ser for x in s_["exact"] if x[0] == it.dates[0].isoformat()}
                    if times and it.time not in times:
                        out.append(Violation("time", "hard", excerpt, f"Часът на точния аспект е {', '.join(sorted(times))}, не {it.time}."))
            else:
                allowed = all_exact | bounds | idx.bound_dates
                if it.is_range:
                    good = (it.dates[0].isoformat() <= hi_b and it.dates[-1].isoformat() >= lo_b) or any(it.covers(x, tol) for x in all_exact)
                else:
                    iso = it.dates[0].isoformat()
                    good = iso in allowed or (it.approx and lo_b <= iso <= hi_b) or any(it.covers(x, tol) for x in all_exact)
                if not good and checked:
                    out.append(Violation("date", "hard", excerpt, f"Датата {_dm(checked[0])} не е нито точен момент, нито начало или край на активността на този аспект. "
                                         f"Вярно: точни {', '.join(_dm(x) for x in sorted(all_exact)) or 'няма'}; активен от {', '.join(_dm(x) for x in sorted(bounds))}."))
                elif good and it.time and not it.is_range:
                    times = {x[1] for s_ in ser for x in s_["exact"] if x[0] == it.dates[0].isoformat()}
                    if times and it.time not in times:
                        out.append(Violation("time", "hard", excerpt, f"Часът на точния аспект е {', '.join(sorted(times))}, не {it.time}."))
    return out


_MONTH_NAMES = ["", "януари", "февруари", "март", "април", "май", "юни", "юли", "август", "септември", "октомври", "ноември", "декември"]


def _month_bg(d: date) -> str:
    return _MONTH_NAMES[d.month]


def _dm(iso: str) -> str:
    return f"{int(iso[8:10])} {_MONTH_NAMES[int(iso[5:7])]}"


# --------------------------------------------------------------------------------------------------------------
# Етикети, имена на полета, проценти
# --------------------------------------------------------------------------------------------------------------
def clean_labels(text: str) -> Tuple[str, int]:
    """Маха детерминирано служебните етикети и имена на полета от подканата („(ЗАДЪЛЖИТЕЛНА СЕКЦИЯ)“, „state_in_month: …“,
    „(„PARTNER PLANETS IN USER'S NATAL HOUSES")“). Не променя нищо друго в текста. Връща (текст, брой махнати места)."""
    removed = 0
    for rx in LABEL_RES:
        text, n = rx.subn("", text)
        removed += n

    def drop(m: re.Match) -> str:
        nxt = m.string[m.end():m.end() + 1]
        prev = m.string[m.start() - 1:m.start()] if m.start() else "\n"
        return " " if nxt and nxt.isalnum() and prev not in ("\n", "") else ""

    wrapped = re.compile(rf"[ \t]*\(\s*[„“\"'«`]?\s*{_LEAK_ANY}\s*[”“\"'»`]?\s*\)")
    field_value = re.compile(rf"[ \t]*[\"'„“`]*(?<![\w])(?:{_FIELD_NAMES})\s*[:=]\s*[\"'„“`]?[\w.:\-]+[\"'”“`]*")
    bare = re.compile(rf"[ \t]*[\"'„“`]*{_LEAK_ANY}[\"'”“`]*")
    for rx in (wrapped, field_value, bare):
        text, n = rx.subn(drop, text)
        removed += n
    if removed:
        text = re.sub(r"\(\s*\)|„\s*[“”]|``", "", text)
    return text, removed


def check_leaks(raw: str) -> List[Violation]:
    out = []
    for m in ENGLISH_LEAK_RE.finditer(raw):
        out.append(Violation("english", "soft", _short(raw, max(0, m.start() - 30), m.end() + 30),
                             f"Вътрешното име „{m.group(0)}“ не трябва да се вижда в текста: махни го или го кажи с обикновени думи."))
    for m in PERCENT_RE.finditer(raw):
        out.append(Violation("percent", "soft", _short(raw, max(0, m.start() - 30), m.end() + 20),
                             "Не давай проценти или числови вероятности: използвай обикновени думи (благоприятен, смесен, затруднен период)."))
    return out


# --------------------------------------------------------------------------------------------------------------
# Вход: проверка на цял текст
# --------------------------------------------------------------------------------------------------------------
def name_collides(facts: Facts) -> bool:
    """Името на човек е дума за планета или знак (Венера, Луна, Марс…): в текста не може да се различи човекът от планетата."""
    patterns = list(PLANET_FORMS.values()) + list(SIGN_FORMS.values())
    for name in facts.names.values():
        for word in re.findall(r"\w+", name or ""):
            if any(re.fullmatch(p, word, re.IGNORECASE) for p in patterns):
                return True
    return False


def check_text(text: str, facts: Facts) -> List[Violation]:
    """Всички нарушения в текста. Редът е като в текста; повторенията се махат."""
    found: List[Violation] = list(check_leaks(text or ""))
    if name_collides(facts):
        return found                          # само служебните етикети и проценти: твърденията не могат да се разпознаят сигурно
    for seg in segments(text or ""):
        mentions = find_mentions(seg)
        groups = planet_groups(seg, mentions)
        found += check_houses(seg, mentions, groups, facts)
        found += check_signs(seg, mentions, groups, facts)
        found += check_aspects(seg, mentions, groups, facts)
        found += check_cusps_rulers(seg, mentions, facts)
        found += check_unknown_time(seg, mentions, groups, facts)
        found += check_retro_count(seg, mentions, groups, facts)
        found += check_dates(seg, mentions, groups, facts)
    seen: Set[Tuple[str, str, str]] = set()
    unique = []
    for v in found:
        key = (v.code, v.excerpt, v.detail)
        if key not in seen:
            seen.add(key)
            unique.append(v)
    return unique


def hard(violations: Sequence[Violation]) -> List[Violation]:
    return [v for v in violations if v.severity == "hard"]


# --------------------------------------------------------------------------------------------------------------
# Строене на фактите от картите (същите функции като за подканата)
# --------------------------------------------------------------------------------------------------------------
def _person_facts(chart: Dict) -> Tuple[Dict, Dict, int]:
    import factpack
    view = factpack.natal_view(chart)
    natal = planets_view(view["planets"])
    angles = view.get("angles") or {}
    for key, name in (("Ascendant_formatted", "ASC"), ("MC_formatted", "MC")):
        pos = _split_sign_pos(angles.get(key, ""))
        if pos:
            natal[name] = {**pos, "house": None}
    return natal, cusps_view(view.get("houses") or {}), int(view.get("retrograde_count") or 0)


def build_facts(*, mode: str, user_name: Optional[str], natal_chart: Dict, partner_name: Optional[str] = None,
                partner_chart: Optional[Dict] = None, transit_chart: Optional[Dict] = None, calendar=None,
                target_date: Optional[str] = None, report_date: Optional[str] = None) -> Facts:
    """mode: natal | snapshot | period | overview. calendar е scanner.PeriodCalendar."""
    import factpack
    from aspects_engine import calculate_natal_aspects

    facts = Facts(mode=mode, report_date=report_date)
    facts.names["user"] = factpack.display_name(user_name, factpack.FIRST_PERSON_DEFAULT)
    people = [("user", natal_chart)]
    if partner_chart is not None:
        facts.names["partner"] = factpack.display_name(partner_name, factpack.SECOND_PERSON_DEFAULT)
        people.append(("partner", partner_chart))
    for key, chart in people:
        natal, cusps, retro = _person_facts(chart)
        facts.natal[key], facts.cusps[key], facts.retro_count[key] = natal, cusps, retro
        if chart.get("time_known") is False:
            facts.no_houses.add(key)
            for planet, info in (chart.get("sign_ranges") or {}).items():
                facts.alt_signs[(key, planet)] = list(info.get("signs") or [])
        # Без изкуствено изпразване при грешка: непълни аспекти биха дали фалшиви „няма такъв аспект“; грешката
        # стига до AIInterpreter.build_text_facts и текстът минава непроверен
        facts.natal_aspects[key] = [{"p1": a["planet1"], "p2": a["planet2"], "aspect": a["aspect"], "orb": a.get("orb")}
                                    for a in calculate_natal_aspects(chart, use_wider_orbs=False)]
    if partner_chart is not None:
        # Наслагване има само в домовете на човек с известен час
        if factpack.has_houses(natal_chart):
            facts.overlays[("partner", "user")] = factpack.overlay(natal_chart, partner_chart)
        if factpack.has_houses(partner_chart):
            facts.overlays[("user", "partner")] = factpack.overlay(partner_chart, natal_chart)
        facts.synastry = [{"o1": a["person1"], "p1": a["planet1"], "aspect": a["aspect"], "o2": a["person2"],
                           "p2": a["planet2"], "orb": a.get("orb")}
                          for a in factpack.synastry_aspects(natal_chart, partner_chart, facts.names["user"], facts.names["partner"])]
    if transit_chart is not None:
        sky_view = factpack.transit_view(transit_chart)
        facts.sky = planets_view(sky_view["planets"])
        facts.retro_count["sky"] = int(sky_view.get("retrograde_count") or 0)
        for key, chart in people:
            facts.transit_houses[key] = factpack.overlay(chart, transit_chart) if factpack.has_houses(chart) else {}
            facts.transit_aspects[key] = [{"p1": a["transit_planet"], "p2": a["natal_planet"], "aspect": a["aspect"], "orb": a.get("orb")}
                                          for a in factpack.transit_aspects(chart, transit_chart)]
    if target_date:
        m = re.search(r"(\d{4}-\d{2}-\d{2})(?:[ T](\d{2}:\d{2}))?", target_date)
        if m:
            facts.snapshot_date, facts.snapshot_time = m.group(1), m.group(2)
    if calendar is not None:
        facts.calendar = calendar.public_events()
        facts.period = (calendar.start, calendar.end)
        facts.report_date = report_date
    return facts


def violations_for_prompt(violations: Sequence[Violation], limit: int = 12) -> str:
    """Списък за заявката за поправка."""
    lines = []
    for i, v in enumerate(list(violations)[:limit], 1):
        lines.append(f"{i}. [{v.code}] Откъс: „{v.excerpt}“. Проблем: {v.detail}")
    return "\n".join(lines)
