"""
Текстът на отчета като блокове (Фаза 13): един разбор за износа в DOCX и Markdown.

Запазеният текст е смес: Markdown от AI (`##`, `**`, `-`) и малко HTML от приложението (`<h2>` за месеците, `<p>`). Тук и
двете стават едни и същи блокове, без да се изпълнява и без да се пази HTML: непознатите тагове се махат, а текстът им остава.
Екранът (src/utils/reportText.js) прави същото разбиране на текста, затова екран и износ показват един и същ отчет.
"""
import html
import re
from dataclasses import dataclass, field
from typing import List, Tuple

HEADING_TAG = re.compile(r"<h([1-6])[^>]*>(.*?)</h\1\s*>", re.IGNORECASE | re.DOTALL)
RULE = re.compile(r"^\s*(?:[-*_]{3,}|[━─═]{3,})\s*$")
BULLET = re.compile(r"^\s*[-*•]\s+(.*)$")
ORDERED = re.compile(r"^\s*\d{1,3}[.)]\s+(.*)$")
HEADING = re.compile(r"^\s*(#{1,6})\s+(.*?)\s*#*\s*$")


@dataclass
class Block:
    kind: str                      # h | p | ul | ol | hr
    level: int = 0                 # ниво на заглавие (1-6)
    items: List[str] = field(default_factory=list)   # текст с **полу-удебелено** и *курсив* (за p и h: един елемент)


def normalize_markup(text: str) -> str:
    """HTML от приложението и AI → Markdown. Непознатите тагове се махат, текстът им остава."""
    s = (text or "").replace("\r\n", "\n").replace("\r", "\n")
    s = HEADING_TAG.sub(lambda m: f"\n\n{'#' * int(m.group(1))} {m.group(2).strip()}\n\n", s)
    s = re.sub(r"<br\s*/?>", "\n", s, flags=re.IGNORECASE)
    s = re.sub(r"</?p[^>]*>", "\n\n", s, flags=re.IGNORECASE)
    s = re.sub(r"<li[^>]*>", "\n- ", s, flags=re.IGNORECASE)
    s = re.sub(r"</li\s*>", "", s, flags=re.IGNORECASE)
    s = re.sub(r"</?(?:ul|ol|div|section|article|blockquote)[^>]*>", "\n", s, flags=re.IGNORECASE)
    s = re.sub(r"</?(?:strong|b)(?:\s[^>]*)?>", "**", s, flags=re.IGNORECASE)
    s = re.sub(r"</?(?:em|i)(?:\s[^>]*)?>", "*", s, flags=re.IGNORECASE)
    s = re.sub(r"</?[a-zA-Z][^>]*>", "", s)           # всеки друг таг се маха (без изпълнение, без запазване)
    return html.unescape(s)


def parse(text: str) -> List[Block]:
    blocks: List[Block] = []
    paragraph: List[str] = []

    def flush():
        if paragraph:
            blocks.append(Block("p", items=[" ".join(paragraph)]))
            paragraph.clear()

    for raw in normalize_markup(text).split("\n"):
        line = raw.rstrip()
        if not line.strip():
            flush()
            continue
        if RULE.match(line):
            flush()
            blocks.append(Block("hr"))
            continue
        heading = HEADING.match(line)
        if heading:
            flush()
            blocks.append(Block("h", level=len(heading.group(1)), items=[heading.group(2).strip()]))
            continue
        bullet, ordered = BULLET.match(line), ORDERED.match(line)
        if bullet or ordered:
            flush()
            kind, body = ("ul", bullet.group(1)) if bullet else ("ol", ordered.group(1))
            if blocks and blocks[-1].kind == kind:
                blocks[-1].items.append(body.strip())
            else:
                blocks.append(Block(kind, items=[body.strip()]))
            continue
        paragraph.append(line.strip())
    flush()
    return blocks


_INLINE = re.compile(r"(\*\*(?P<b>.+?)\*\*|(?<![\w*])\*(?P<i>[^*\s][^*]*?)\*(?![\w*])|(?<![\w])_(?P<u>[^_\s][^_]*?)_(?![\w]))", re.DOTALL)


def inline_runs(text: str) -> List[Tuple[str, bool, bool]]:
    """„**a** b *c*“ → [(a, True, False), (" b ", False, False), (c, False, True)]."""
    runs: List[Tuple[str, bool, bool]] = []
    position = 0
    for match in _INLINE.finditer(text):
        if match.start() > position:
            runs.append((text[position:match.start()], False, False))
        if match.group("b") is not None:
            runs.append((match.group("b"), True, False))
        else:
            runs.append((match.group("i") or match.group("u"), False, True))
        position = match.end()
    if position < len(text):
        runs.append((text[position:], False, False))
    return runs or [("", False, False)]


def plain(text: str) -> str:
    """Текст без знаците за форматиране."""
    return "".join(run for run, _, _ in inline_runs(text))


def to_markdown(text: str) -> str:
    """Чист Markdown от запазения текст: заглавия с #, списъци, разделители, абзаци през празен ред."""
    out: List[str] = []
    for block in parse(text):
        if block.kind == "h":
            out.append(f"{'#' * block.level} {block.items[0]}")
        elif block.kind == "p":
            out.append(block.items[0])
        elif block.kind == "ul":
            out.append("\n".join(f"- {item}" for item in block.items))
        elif block.kind == "ol":
            out.append("\n".join(f"{n}. {item}" for n, item in enumerate(block.items, 1)))
        else:
            out.append("---")
    return "\n\n".join(out) + ("\n" if out else "")


def split_sections(text: str) -> List[Tuple[str, str]]:
    """Отчет за период: [(заглавие на месеца или прегледа, текст)]. Обикновен отчет: един раздел без заглавие."""
    parts = re.split(r"(?im)^\s*<h2[^>]*>(.*?)</h2\s*>\s*$", (text or "").replace("\r\n", "\n"))
    if len(parts) < 3:
        return [("", text or "")]
    sections = []
    lead = parts[0].strip()
    if lead:
        sections.append(("", lead))
    for index in range(1, len(parts), 2):
        sections.append((html.unescape(re.sub(r"<[^>]+>", "", parts[index])).strip(), parts[index + 1].strip()))
    return sections
