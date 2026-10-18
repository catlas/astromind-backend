"""
Анализ по карти с факти: общата машина за режимите, в които фактите се пишат от кода, а AI само тълкува
(решение на собственика, 12.10.2026; допълнено с условията от независимия преглед).

Режимите (pair_snapshot.py, single_snapshot.py) дават карти, подкани и заглавия. Машината прави едно и също за всички:
- всяка карта е факт, написан от кода (притежател, натална или транзитна планета, вид аспект, орбис, посока), със собствен
  идентификатор. AI пише само тълкуването под нея;
- всеки блок се проверява отделно (text_guard, най-много 2 поправки). Проверката НЕ е отслабена, а е по-тясна: картата се
  проверява срещу собствения си аспект, а обобщението, съветите, отговорът и заключението, и целият сглобен текст, само срещу
  аспектите на ВКЛЮЧЕНИТЕ карти. Аспект от пропусната карта в тях е нарушение дори да е фактически верен (геометрията не
  доказва, че тълкуването му е проверено);
- провалена карта се възстановява: нова заявка само за нея, после същата проверка. Ако пак не мине, тя се пропуска;
- ключова карта (най-тесните аспекти на всеки човек и най-тясното взаимодействие) не може да се пропусне: резултатът се отказва;
- при пропусната карта затварящите блокове се съставят наново САМО от проверените карти (частичен анализ, не се таксува);
- ако се провали затварящ блок при цели карти, той се съставя наново от тях (резервен анализ). Резервният има същия минимален
  стандарт като пълния: всички карти, отговор на въпроса и заключение; затова се таксува както пълния;
- накрая целият текст минава проверката още веднъж: факти, пълнота и противоречия. Минимум: анализ за всеки човек, отговор,
  който покрива въпроса, и заключение. Иначе резултатът се отказва. При неизвестен час не се допълва с несъществуващи карти.
"""
import dataclasses
import re
from dataclasses import dataclass, field
from typing import Callable, Dict, List, Optional, Tuple

import ai_budget
import factpack
import report_guard
import text_check
import text_guard
from text_check import Violation

MIN_KEPT_PER_PERSON = 2                           # най-малко проверени транзитни карти за човек (или всички, ако е имал по-малко)
MAX_FAILED_CARDS = 4                              # повече провалени карти значи, че AI е объркан: отказ без още заявки
KEY_CARDS_PER_PERSON = 2                          # най-тесните аспекти на човека са ключови
MIN_ANSWER_CHARS = 200                            # по-кратък отговор не покрива въпроса
COMPOSITION = ("SUMMARY", "ADVICE", "ANSWER", "CONCLUSION")
# Таг в началото на ред: [[A1]], **[[A1]]**, ### [[A1]], [A1] или [[A1]]: текст на същия ред (моделите го пишат по различен начин)
TAG_RE = re.compile(r"^[ \t*#>_-]*\[{1,2}\s*([A-Za-z][A-Za-z0-9_]*)\s*\]{1,2}[ \t*:_.-]*", re.MULTILINE)
_QUESTION_STOP = {"какво", "какви", "какъв", "каква", "кога", "където", "къде", "които", "който", "която", "трябва", "защо", "когато",
                  "можем", "можете", "може", "нашата", "нашия", "нашите", "нещо", "както", "после", "преди", "между", "около"}


# Бележка в началото на резервен или частичен анализ (виж pair_snapshot.py)
QUALITY_NOTES = {
    "fallback": "**Резервен анализ.** Обобщението и съветите са съставени наново само от проверените блокове. Всички карти са запазени.",
    "partial": ("**Частичен анализ.** Една или повече карти не минаха проверката и са пропуснати; обобщението и съветите са съставени "
                "само от проверените. Този анализ не се таксува."),
}


@dataclass
class Card:
    id: str                    # A1, B3, S2
    group: str                 # A | B | S
    heading: str               # написан от кода: факти без AI
    row: Dict = field(default_factory=dict)      # редът във формата на Facts: kind, owner|o1/o2, p1, p2, aspect, orb
    key: bool = False
    removed: bool = False
    text: str = ""             # проверено тълкуване на AI
    score: Optional[float] = None   # по-малко е по-важно: орбис при транзитите, орбис минус тежест на темата/отношенията при натала


@dataclass
class Context:
    natal_chart: Dict
    partner_chart: Optional[Dict]
    transit_chart: Dict
    user_name: Optional[str]
    partner_name: Optional[str]
    first: str
    second: str
    question: str
    target_date: str
    theme: str
    utc_today: str
    gender: Optional[str] = None
    partner_gender: Optional[str] = None
    report_type: str = "general"
    relation: str = ""                              # код на отношенията (romantic, friend, family, parent_child, work, general) или ""


class CardMode:
    """Какво е различно за режима. Подкласовете дават карти, редове за домове, подкани и заглавия."""
    name = ""
    stage = ""                                     # префикс на етапите в дневниците (pair, single)
    person_groups: Tuple[str, ...] = ("A",)        # групите, които се броят за „анализ за всеки човек“
    groups: Tuple[str, ...] = ("A", "B", "S")      # групите карти в реда на показване
    key_per_group: Dict[str, int] = {"A": 2, "B": 2, "S": 1}     # колко карти от групата са ключови (най-важните по score)
    strict_cards = False                           # в тълкуването на карта: без други планети, аспекти, знаци и домове
    closing_names_forbidden = False                # в затварящите блокове: без планети, аспекти, знаци и домове (само смисъл)
    advice_title = "Какво да имате предвид в този ден"
    facts_stage = "snapshot"                       # етапът в дневниците при липсващи факти (натални режими: „natal“)

    def build(self, ctx: Context) -> Tuple[List[Card], Dict[str, str]]:
        raise NotImplementedError

    def group_title(self, group: str, ctx: Context) -> str:
        raise NotImplementedError

    def system_prompt(self, ctx: Context, cards: List[Card], answer_tag: str) -> str:
        raise NotImplementedError

    def compose_system(self, ctx: Context, answer_tag: str) -> str:
        raise NotImplementedError

    def user_prompt(self, interp, ctx: Context, cards: List[Card], lines: Dict[str, str]) -> str:
        raise NotImplementedError

    def facts_kwargs(self, ctx: Context) -> Dict:
        raise NotImplementedError

    def owner_of(self, group: str, ctx: Context) -> str:
        return ctx.first if group == "A" else ctx.second

    def restore_system(self, ctx: Context) -> str:
        """Подканата за възстановяване на една карта (нова заявка само за нея)."""
        return RESTORE_SYSTEM.format(theme=ctx.theme)

    def card_check(self, card: Card):
        """extra_check за тълкуването на карта. Режимите могат да добавят свои правила към общите (card_scope)."""
        return card_scope(card, self.strict_cards)

    def closing_check(self, text: str) -> List[Violation]:
        """Правилата за затварящите блокове (обобщение, съвети, отговор, заключение), освен покритието на въпроса."""
        return closing_scope(text) if self.closing_names_forbidden else []


def parse_sections(text: str, known=None) -> Dict[str, str]:
    """Секциите на отговора по таговете [[A1]], [[SUMMARY]]… Текст преди първия таг се пренебрегва; тагове извън known (ако е дадено)
    не отварят секция. Текстът на секцията е останалото от реда на тага и следващите редове до следващия таг."""
    found = [m for m in TAG_RE.finditer(text or "") if known is None or m.group(1).upper() in known]
    out: Dict[str, str] = {}
    for i, m in enumerate(found):
        end = found[i + 1].start() if i + 1 < len(found) else len(text)
        body = text[m.end():end].strip()
        tag = m.group(1).upper()
        if body and tag not in out:
            out[tag] = body
    return out


def _shape(raw: str) -> str:
    """Формата на неправилен отговор за журнала: брой тагове и първите знаци, без текст на анализа."""
    first = (raw or "").strip().splitlines()[0][:50] if (raw or "").strip() else ""
    return f"len={len(raw or '')} tags={len(TAG_RE.findall(raw or ''))} brackets={(raw or '').count('[[')} first={first!r}"


def restrict(facts, cards: List[Card]):
    """Фактите само с аспектите на дадените карти: всичко останало (натални аспекти, непоказани транзитни и синастрични) отпада.
    Така аспект извън включените карти е нарушение в обобщението, дори да е фактически верен. Без Facts се връща както е."""
    if not isinstance(facts, text_check.Facts):
        return facts
    transit: Dict[str, List[Dict]] = {}
    synastry: List[Dict] = []
    natal: Dict[str, List[Dict]] = {}
    for c in cards:
        row = c.row
        if row.get("kind") == "natal":
            natal.setdefault(row["owner"], []).append({k: row[k] for k in ("p1", "p2", "aspect", "orb")})
        elif row.get("kind") == "transit":
            transit.setdefault(row["owner"], []).append({k: row[k] for k in ("p1", "p2", "aspect", "orb")})
        elif row.get("kind") == "synastry":
            synastry.append({k: row[k] for k in ("o1", "p1", "aspect", "o2", "p2", "orb")})
    return dataclasses.replace(facts, natal_aspects=natal, synastry=synastry, transit_aspects=transit)


def question_coverage(question: str):
    """extra_check за отговора: достатъчно дълъг и свързан с въпроса (споделя основа на дума с него). Не е семантична проверка:
    хваща празния и несвързания отговор; смисълът му се проверява от фактите и от преглед на готовите текстове."""
    stems = {w[:5] for w in re.findall(r"[а-яА-Я]{5,}", (question or "").lower()) if w not in _QUESTION_STOP}

    def check(text: str) -> List[Violation]:
        plain = re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", text or "")).strip()
        out: List[Violation] = []
        if len(plain) < MIN_ANSWER_CHARS:
            out.append(Violation("complete", "hard", plain[:80], f"Отговорът е твърде кратък за въпроса: напиши поне {MIN_ANSWER_CHARS} знака, "
                                 "конкретно за зададения въпрос, само с фактите от проверените карти."))
        elif stems and not any(s in plain.lower() for s in stems):
            out.append(Violation("complete", "hard", plain[:80], "Отговорът не се отнася до зададения въпрос: отговори директно на него, "
                                 "като назовеш темата му със свои думи и я свържеш с проверените карти."))
        return out
    return check


def card_scope(card: Card, strict: bool = False):
    """extra_check за тълкуването на карта: само нейните планети и нейният аспект. Други планети и аспекти в блока на картата
    са нарушение (подканата ги забранява). Така „проверен“ блок не може да носи непроверен факт, дори планетите му да не се срещат
    в останалите аспекти на данните. При strict (натални режими) в блока няма и знаци, домове и градуси: те са в заглавието."""
    own_planets = {card.row.get(k) for k in ("p1", "p2", "planet") if card.row.get(k)}
    own_aspect = card.row.get("aspect")

    def check(text: str) -> List[Violation]:
        out: List[Violation] = []
        for key, rx in text_check.PLANET_RE.items():
            m = rx.search(text or "")
            if m and key not in own_planets:
                out.append(Violation("aspect", "hard", text[max(0, m.start() - 30):m.end() + 30],
                                     f"В тълкуването на тази карта не споменавай {text_check.PLANET_BG.get(key, key)}: картата е само за "
                                     f"{' и '.join(text_check.PLANET_BG.get(p, str(p)) for p in sorted(own_planets, key=str))}. "
                                     "Не назовавай други планети и аспекти, а опиши какво означава картата със свои думи."))
        for key, rx in text_check.ASPECT_RE.items():
            m = rx.search(text or "")
            if m and key != own_aspect:
                out.append(Violation("aspect", "hard", text[max(0, m.start() - 30):m.end() + 30],
                                     f"В тълкуването на тази карта не споменавай {text_check.ASPECT_BG.get(key, key)}: "
                                     + (f"картата е {text_check.ASPECT_BG.get(own_aspect, own_aspect)}. Не назовавай други аспекти." if own_aspect
                                        else "картата не е аспект. Не назовавай аспекти.")))
        if strict:
            out.extend(_names_in(text, "в тълкуването на карта", sign_house_only=True))
        return out
    return check


def _names_in(text: str, where: str, sign_house_only: bool = False) -> List[Violation]:
    """Знаци, домове (и планети, ако не е sign_house_only) в блок, където фактите са в заглавията: нарушение с поправка „със свои думи“."""
    out: List[Violation] = []
    patterns = [(rx, "знак") for rx in text_check.SIGN_RE.values()] + [(text_check.HOUSE_DIGIT_RE, "дом"), (text_check.HOUSE_WORD_RE, "дом")]
    if not sign_house_only:
        patterns += [(rx, "планета") for rx in text_check.PLANET_RE.values()] + [(rx, "аспект") for rx in text_check.ASPECT_RE.values()]
    for rx, what in patterns:
        m = rx.search(text or "")
        if m:
            out.append(Violation("aspect", "hard", text[max(0, m.start() - 30):m.end() + 30],
                                 f"{where.capitalize()} не споменавай {what}: фактите са в заглавията на картите. Опиши само смисъла със свои думи."))
    return out


def closing_scope(text: str) -> List[Violation]:
    """extra_check за затварящите блокове в режимите, където картите са размяна на домове и позиции: без планети, аспекти, знаци и домове."""
    return _names_in(text, "в обобщението, съветите, отговора и заключението")


def _fail(stage: str, code: str, detail: str, text: str = "") -> text_guard.TextCheckError:
    outcome = text_guard.GuardOutcome(stage=stage, mode=text_guard.MODE_ENFORCE, text=text,
                                      after=[Violation(code, "hard", "", detail)], rejected=True, reason="violations_remain")
    return text_guard.TextCheckError(stage, outcome)


async def _check_block(interp, facts, stage: str, text: str, checks: List[Dict], extra=None,
                       details: Optional[List[str]] = None) -> Tuple[Optional[str], str]:
    """Проверява един блок (най-много 2 поправки). Връща (проверен текст или None, кодовете на нарушенията при провал). При провал
    описанията на тежките нарушения на последната версия се добавят в details (ако е даден списък): за нов опит със знанието какво е грешно."""
    text = TAG_RE.sub("", text or "").strip()                    # забъркан таг в текста на блока не е част от анализа
    try:
        return await interp.guard_text(text, facts, stage=stage, checks=checks, extra_check=extra), ""
    except text_guard.TextCheckError as failure:
        if details is not None:
            details.extend(v.detail for v in failure.outcome.after if v.severity == "hard")
        return None, failure.outcome.codes() or "check"


def _answer_tag(question: str) -> str:
    return "[[ANSWER]]  a direct answer to the user's question in 3-5 sentences, based only on the cards.\n" if question else ""


def mark_key_cards(cards: List[Card], mode: "CardMode") -> None:
    """Ключови са най-важните карти на групата по score (mode.key_per_group): ако една от тях не мине проверката, анализът се отказва."""
    for group, count in mode.key_per_group.items():
        pool = [c for c in cards if c.group == group]
        pool.sort(key=lambda c: c.score if c.score is not None else float(c.row.get("orb") or 0))
        for card in pool[:count]:
            card.key = True


def minimum_met(cards: List[Card], original: Dict[str, int], person_groups: Tuple[str, ...] = ("A", "B")) -> Optional[str]:
    """None, ако за всеки човек са запазени поне MIN_KEPT_PER_PERSON проверени карти (или всички, ако е имал по-малко)."""
    for group in person_groups:
        kept = sum(1 for c in cards if c.group == group and not c.removed)
        need = min(MIN_KEPT_PER_PERSON, original.get(group, 0))
        if kept < need:
            return f"За {'първия' if group == 'A' else 'втория'} човек са проверени само {kept} от {original.get(group, 0)} карти."
    return None


async def _check_composition(interp, facts, sections: Dict[str, str], question: str, checks: List[Dict], stage_prefix: str,
                             closing: Optional[Callable[[str], List[Violation]]] = None) -> Dict[str, str]:
    needed = [t for t in COMPOSITION if t != "ANSWER" or question]
    missing = [t for t in needed if not sections.get(t)]
    if missing:
        raise _fail(f"{stage_prefix}:format", "complete", "Липсват задължителни секции: " + ", ".join(missing))
    out: Dict[str, str] = {}
    for tag in needed:
        coverage = question_coverage(question) if tag == "ANSWER" else None
        if closing is not None:
            extra = (lambda t, c=coverage: closing(t) + (c(t) if c else []))
        else:
            extra = coverage
        checked, codes = await _check_block(interp, facts, f"{stage_prefix}:{tag}", sections[tag], checks, extra)
        if checked is None:
            raise _fail(f"{stage_prefix}:{tag}", codes.split(",")[0] or "check", f"Блокът {tag} не мина проверката ({codes}).")
        out[tag] = checked
    return out


async def _compose(interp, mode: CardMode, ctx: Context, facts, cards: List[Card], checks: List[Dict]) -> Dict[str, str]:
    """Обобщение, съвети, отговор и заключение САМО от включените карти. Хвърля TextCheckError при провал."""
    kept = [c for c in cards if not c.removed and c.text]
    blocks = "".join(f"[{c.id}] {c.heading}\n{c.text}\n\n" for c in kept)
    system = mode.compose_system(ctx, _answer_tag(ctx.question))
    user = f"User question: {ctx.question or 'none'}\n\nVERIFIED BLOCKS:\n{blocks}Write the sections now, in Bulgarian."
    raw = await interp._call_api(system_prompt=system, user_prompt=user, max_tokens=interp.max_output_tokens)
    sections = parse_sections(raw, set(COMPOSITION))
    return await _check_composition(interp, restrict(facts, kept), sections, ctx.question, checks, f"{mode.stage}:compose",
                                    mode.closing_check if mode.closing_names_forbidden else None)


RESTORE_SYSTEM = (
    "You write ONE short interpretation for ONE pre-written fact card. Output only the interpretation text, no tags.\n"
    "THEME: {theme}\n"
    "Write 2-3 sentences about THAT card only: what it means for the person (or for the pair) on the chosen date, in plain words. "
    "The heading already names the planets, the aspect and the orb: do NOT name any planet, aspect, sign, house or degree. "
    "Start directly with the meaning. Refer to the chosen date only as \"в този ден\": never \"днес\", \"утре\" or \"вчера\". "
    "Be direct and concrete, no filler, no promises about the future. Write in Bulgarian.\n"
)


async def _restore(interp, mode: CardMode, ctx: Context, facts, card: Card, checks: List[Dict]) -> bool:
    """Възстановяване на провалена карта: нова заявка само за нея и същата проверка. Връща дали е възстановена."""
    system = mode.restore_system(ctx)
    user = (f"People: {ctx.first}" + (f" and {ctx.second}" if ctx.partner_chart else "") + (f". Chosen moment: {ctx.target_date}." if ctx.target_date else ".") + "\n"
            f"CARD {card.id}: {card.heading}\nWrite the interpretation now, in Bulgarian.")
    try:
        raw = await interp._call_api(system_prompt=system, user_prompt=user, max_tokens=800)
    except ai_budget.BudgetExceeded:
        raise                                                    # спрян бюджет не е „неуспешно възстановяване“
    except Exception:
        return False
    checked, _codes = await _check_block(interp, restrict(facts, [card]), f"{mode.stage}:{card.id}:restore", raw, checks, mode.card_check(card))
    if checked is None:
        return False
    card.text, card.removed = checked, False
    return True


def assemble(mode: CardMode, ctx: Context, cards: List[Card], lines: Dict[str, str], blocks: Dict[str, str]) -> str:
    parts: List[str] = []
    for group in mode.groups:
        shown = [c for c in cards if c.group == group and not c.removed]
        in_mode = any(c.group == group for c in cards) or group in mode.person_groups
        if not in_mode:
            continue
        if group in mode.person_groups or shown:
            parts.append(f"## {mode.group_title(group, ctx)}")
        if lines.get(group):
            parts.append(lines[group])
        parts.extend(f"### {c.heading}\n{c.text}" for c in shown)
    parts.append(f"## Обща картина\n{blocks['SUMMARY']}")
    parts.append(f"## {mode.advice_title}\n{blocks['ADVICE']}")
    if ctx.question:
        parts.append(f"## Отговор на въпроса\n{blocks['ANSWER']}")
    parts.append(f"## Заключение\n{blocks['CONCLUSION']}")
    return "\n\n".join(parts)


async def run(interp, mode: CardMode, ctx: Context, checks: List[Dict]) -> str:
    facts = interp.build_text_facts(**mode.facts_kwargs(ctx))
    interp.require_text_facts(facts, stage=mode.facts_stage, checks=checks)

    cards, lines = mode.build(ctx)
    original = {g: sum(1 for c in cards if c.group == g) for g in mode.groups}
    mark_key_cards(cards, mode)

    system = mode.system_prompt(ctx, cards, _answer_tag(ctx.question))
    user = mode.user_prompt(interp, ctx, cards, lines)
    known = {c.id for c in cards} | set(COMPOSITION)
    sections: Dict[str, str] = {}
    for attempt in (1, 2):                                      # неправилен формат: най-много една нова заявка
        raw = await interp._call_api(system_prompt=system, user_prompt=user, max_tokens=interp.max_output_tokens)
        sections = parse_sections(raw, known)
        if sum(1 for c in cards if sections.get(c.id)) >= len(cards) // 2 and sections.get("CONCLUSION"):
            break
        print(f"🔎 Формат на отговора (опит {attempt}): {_shape(raw)}")
    else:
        raise _fail(f"{mode.stage}:format", "complete", "Отговорът на AI не е в изискания формат.")

    # 1. Всяка карта се проверява поотделно, срещу собствения си аспект (най-много 2 поправки на карта)
    failed: Dict[str, str] = {}
    for card in cards:
        raw_text = sections.get(card.id)
        if not raw_text:
            card.removed, failed[card.id] = True, "missing"
        else:
            checked, codes = await _check_block(interp, restrict(facts, [card]), f"{mode.stage}:{card.id}", raw_text, checks, mode.card_check(card))
            if checked is None:
                card.removed, failed[card.id] = True, codes
            else:
                card.text = checked
        if len(failed) > MAX_FAILED_CARDS:
            raise _fail(f"{mode.stage}:too_many", "complete", f"{len(failed)} карти не минаха проверката: AI не следва картите.")

    # 2. Възстановяване на провалените карти (нова заявка за всяка), после същата проверка
    restored: List[str] = []
    for card in [c for c in cards if c.removed]:
        if await _restore(interp, mode, ctx, facts, card, checks):
            restored.append(card.id)
    removed = [c for c in cards if c.removed]

    # 3. Ключова карта не се пропуска: без нея анализът не е достатъчен, колкото и карти да са останали
    lost_key = [c.id for c in removed if c.key]
    if lost_key:
        raise _fail(f"{mode.stage}:key_card", "complete", "Не мина проверката ключова карта: " + ", ".join(lost_key) + ".")
    if removed:
        problem = minimum_met(cards, original, mode.person_groups)
        if problem:
            raise _fail(f"{mode.stage}:minimum", "complete", problem)

    # 4. Затварящи блокове: от всички карти (пълен) или наново само от включените (резервен или частичен)
    kept = [c for c in cards if not c.removed]
    blocks: Dict[str, str] = {}
    if not removed:
        try:
            blocks = await _check_composition(interp, restrict(facts, kept), sections, ctx.question, checks, f"{mode.stage}",
                                              mode.closing_check if mode.closing_names_forbidden else None)
        except text_guard.TextCheckError as failure:
            checks.append({"stage": f"{mode.stage}:composition", "mode": "enforce", "result": "fallback", "codes": failure.outcome.codes()[:60]})
            blocks = {}
    quality = "full"
    if not blocks:
        blocks = await _compose(interp, mode, ctx, facts, cards, checks)
        quality = "partial" if removed else "fallback"

    text = assemble(mode, ctx, cards, lines, blocks)

    # 5. Целият текст още веднъж: факти (само аспектите на включените карти), пълнота и противоречия между блоковете
    final: List[Violation] = []
    if text_guard.check_mode() == text_guard.MODE_ENFORCE and isinstance(facts, text_check.Facts):
        final = [v for v in text_check.check_text(text, restrict(facts, kept)) if v.severity == "hard"]
        final += report_guard.cross_part_violations([("overview", text)], [], facts).get("overview", [])
    if final:
        outcome = text_guard.GuardOutcome(stage=f"{mode.stage}:final", mode=text_guard.MODE_ENFORCE, text=text, after=final,
                                          rejected=True, reason="violations_remain")
        checks.append(outcome.summary())
        raise text_guard.TextCheckError(f"{mode.stage}:final", outcome)

    checks.append({"stage": mode.name, "mode": "enforce", "result": "ok" if quality == "full" else quality, "quality": quality,
                   "cards": len(cards), "kept": len(kept), "removed": ",".join(c.id for c in removed)[:60],
                   "restored": ",".join(restored)[:30], "codes": ",".join(f"{k}:{v}" for k, v in failed.items())[:60]})
    return text
