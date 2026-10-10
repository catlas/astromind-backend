# -*- coding: utf-8 -*-
"""
DOCX на отчет (Фаза 13). Строи се само от запазения отчет (виж report_export.py): същият текст, същите карти и дата като на
екрана. Без нова AI генерация и без такса. Страница A4, шрифт 11 pt (Arial поддържа кирилица), заглавия, съдържание,
номера на страниците; без домове и Асцендент, когато часът на раждане е неизвестен.
"""
import re
from datetime import datetime
from io import BytesIO
from typing import Dict, List, Optional

from docx import Document
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.oxml import OxmlElement
from docx.oxml.ns import qn
from docx.shared import Cm, Pt, RGBColor

import report_text

PURPLE = RGBColor(0x5B, 0x21, 0xB6)
LIGHT_PURPLE = RGBColor(0x7C, 0x3A, 0xED)
GREY = RGBColor(0x66, 0x66, 0x66)
BODY_PT = 11

PLANET_NAMES = {
    "Sun": "Слънце", "Moon": "Луна", "Mercury": "Меркурий", "Venus": "Венера", "Mars": "Марс", "Jupiter": "Юпитер",
    "Saturn": "Сатурн", "Uranus": "Уран", "Neptune": "Нептун", "Pluto": "Плутон", "Node": "Възходящ възел",
    "Chiron": "Хирон", "ASC": "Асцендент", "MC": "MC",
}
SIGN_NAMES = {
    "Aries": "Овен", "Taurus": "Телец", "Gemini": "Близнаци", "Cancer": "Рак", "Leo": "Лъв", "Virgo": "Дева",
    "Libra": "Везни", "Scorpio": "Скорпион", "Sagittarius": "Стрелец", "Capricorn": "Козирог", "Aquarius": "Водолей",
    "Pisces": "Риби",
}
HOUSE_SUFFIX = {1: "ви", 2: "ри", 3: "ти", 4: "ти", 5: "ти", 6: "ти", 7: "ми", 8: "ми", 9: "ти", 10: "ти", 11: "и", 12: "ти"}
ASPECT_NAMES = {"conjunction": "съвпад", "sextile": "секстил", "square": "квадратура", "trine": "тригон",
                "opposition": "опозиция"}


def _sign_bg(text: Optional[str]) -> str:
    out = text or ""
    for english, bulgarian in SIGN_NAMES.items():
        out = re.sub(english, bulgarian, out, flags=re.IGNORECASE)
    return out


class DOCXGenerator:
    """Данни (виж report_export.docx_data): title, kind, created, people, relationship, period, charts, sections."""

    def generate_docx(self, data: Dict) -> bytes:
        doc = Document()
        self._setup(doc)
        self._cover(doc, data)
        for chart in data.get("charts", []):
            if chart.get("chart"):
                doc.add_page_break()
                self._chart_summary(doc, chart)
        sections = [s for s in data.get("sections", []) if (s.get("text") or "").strip()]
        for section in sections:
            doc.add_page_break()
            self._section(doc, section)
        self._footer(doc)
        buffer = BytesIO()
        doc.save(buffer)
        return buffer.getvalue()

    # ------------------------------------------------------------------ страница и стилове
    def _setup(self, doc) -> None:
        section = doc.sections[0]
        section.page_width, section.page_height = Cm(21.0), Cm(29.7)            # A4
        section.left_margin = section.right_margin = Cm(2.2)
        section.top_margin, section.bottom_margin = Cm(2.2), Cm(2.0)
        normal = doc.styles["Normal"]
        normal.font.name = "Arial"
        normal.font.size = Pt(BODY_PT)
        normal.element.rPr.rFonts.set(qn("w:cs"), "Arial")
        normal.element.rPr.rFonts.set(qn("w:eastAsia"), "Arial")
        normal.paragraph_format.space_after = Pt(6)
        normal.paragraph_format.line_spacing = 1.25
        for name, size, color in (("Title", 26, PURPLE), ("Heading 1", 18, PURPLE), ("Heading 2", 14, LIGHT_PURPLE),
                                  ("Heading 3", 12, LIGHT_PURPLE)):
            style = doc.styles[name]
            style.font.name = "Arial"
            style.font.size = Pt(size)
            style.font.bold = True
            style.font.color.rgb = color
            style.element.rPr.rFonts.set(qn("w:cs"), "Arial")
            style.element.rPr.rFonts.set(qn("w:eastAsia"), "Arial")
            style.element.rPr.rFonts.set(qn("w:ascii"), "Arial")
            style.element.rPr.rFonts.set(qn("w:hAnsi"), "Arial")
            for theme in ("w:asciiTheme", "w:hAnsiTheme", "w:eastAsiaTheme", "w:cstheme"):    # темата иначе слага друг шрифт
                style.element.rPr.rFonts.attrib.pop(qn(theme), None)
            style.paragraph_format.space_before = Pt(12 if name != "Title" else 0)
            style.paragraph_format.space_after = Pt(6)
            style.paragraph_format.keep_with_next = True

    # ------------------------------------------------------------------ корица
    def _cover(self, doc, data: Dict) -> None:
        title = doc.add_heading("Астрологичен доклад", 0)
        title.alignment = WD_ALIGN_PARAGRAPH.CENTER
        label = doc.add_paragraph()
        label.alignment = WD_ALIGN_PARAGRAPH.CENTER
        run = label.add_run(str(data.get("title") or ""))
        run.font.size = Pt(15)
        run.bold = True
        for person in data.get("people", []):
            block = doc.add_paragraph()
            block.alignment = WD_ALIGN_PARAGRAPH.CENTER
            block.add_run(str(person.get("name") or "")).bold = True
            for line in (person.get("birth"), person.get("place")):
                if line:
                    block.add_run("\n" + str(line))
        facts = [("Вид анализ", data.get("kind")), ("Отношения", data.get("relationship")), ("Период", data.get("period")),
                 ("Създаден на", data.get("created"))]
        facts = [(name, value) for name, value in facts if value]
        if facts:
            table = doc.add_table(rows=len(facts), cols=2)
            table.style = "Table Grid"
            for row, (name, value) in zip(table.rows, facts):
                row.cells[0].text = name
                row.cells[1].text = str(value)
                for paragraph in row.cells[0].paragraphs:
                    for r in paragraph.runs:
                        r.bold = True
        titles = [s["title"] for s in data.get("sections", []) if s.get("title") and (s.get("text") or "").strip()]
        if len(titles) > 1:
            doc.add_heading("Съдържание", level=2)
            for number, name in enumerate(titles, 1):
                doc.add_paragraph(f"{number}. {name}").paragraph_format.space_after = Pt(2)

    # ------------------------------------------------------------------ карта
    def _chart_summary(self, doc, chart_data: Dict) -> None:
        chart = chart_data["chart"]
        aspects = chart_data.get("aspects") or []
        known = chart.get("time_known") is not False
        ranges = chart.get("sign_ranges") or {}
        doc.add_heading(chart_data.get("title") or "Обобщена информация за картата", level=1)
        if not known:
            doc.add_paragraph("Часът на раждане е неизвестен: няма Асцендент, MC и домове. Планетите са за 12:00 местно "
                              "време на датата на раждане; Луната и всяко тяло, което сменя знак през деня, са с възможните знаци.")
        doc.add_heading("Планетарни позиции", level=2)
        rows = []
        for name, planet in (chart.get("planets") or {}).items():
            if not known and name in ranges and (name == "Moon" or len(ranges[name].get("signs", [])) > 1):
                signs = [SIGN_NAMES.get(s, s) for s in ranges[name].get("signs", [])]
                rows.append((PLANET_NAMES.get(name, name), " или ".join(signs) + " (според часа)" if len(signs) > 1
                             else f"{signs[0]} (градусът не е известен)"))
            elif planet and planet.get("formatted_pos"):
                rows.append((PLANET_NAMES.get(name, name), _sign_bg(planet["formatted_pos"])))
        angles = chart.get("angles") or {}
        if known and angles.get("Ascendant") is not None:
            rows.append(("Асцендент", _sign_bg(angles.get("Ascendant_formatted", ""))))
        if known and angles.get("MC") is not None:
            rows.append(("MC", _sign_bg(angles.get("MC_formatted", ""))))
        self._two_column(doc, rows)
        if known:
            doc.add_heading("Домове", level=2)
            by_house: Dict[int, List[str]] = {}
            for name, planet in (chart.get("planets") or {}).items():
                if planet and planet.get("longitude") is not None and planet.get("house"):
                    by_house.setdefault(int(planet["house"]), []).append(PLANET_NAMES.get(name, name))
            self._two_column(doc, [(f"{n}-{HOUSE_SUFFIX[n]} дом", ", ".join(by_house.get(n, [])) or "празен")
                                   for n in range(1, 13)])
        if aspects:
            doc.add_heading("Аспекти", level=2)
            self._two_column(doc, [(f"{PLANET_NAMES.get(a['planet1'], a['planet1'])} – {PLANET_NAMES.get(a['planet2'], a['planet2'])}",
                                    f"{ASPECT_NAMES.get(a['aspect'], a['aspect'])} (орбис {a['orb']:.2f}°)") for a in aspects])

    def _two_column(self, doc, rows) -> None:
        if not rows:
            return
        table = doc.add_table(rows=len(rows), cols=2)
        table.style = "Table Grid"
        for row, (left, right) in zip(table.rows, rows):
            row.cells[0].text = str(left)
            row.cells[1].text = str(right)
            for paragraph in row.cells[0].paragraphs + row.cells[1].paragraphs:
                paragraph.paragraph_format.space_after = Pt(0)
                for r in paragraph.runs:
                    r.font.size = Pt(10)

    # ------------------------------------------------------------------ текст
    def _section(self, doc, section: Dict) -> None:
        if section.get("title"):
            doc.add_heading(section["title"], level=1)
        for block in report_text.parse(section.get("text") or ""):
            if block.kind == "h":
                doc.add_heading(report_text.plain(block.items[0]), level=min(3, max(2, block.level)))
            elif block.kind == "p":
                self._runs(doc.add_paragraph(), block.items[0])
            elif block.kind == "ul":
                for item in block.items:
                    self._runs(doc.add_paragraph(style="List Bullet"), item)
            elif block.kind == "ol":
                # Номерата са част от текста: стилът „List Number“ продължава номерацията през всички списъци на документа
                for number, item in enumerate(block.items, 1):
                    paragraph = doc.add_paragraph()
                    paragraph.paragraph_format.left_indent = Cm(0.9)
                    paragraph.paragraph_format.first_line_indent = Cm(-0.6)
                    paragraph.paragraph_format.space_after = Pt(3)
                    paragraph.add_run(f"{number}. ")
                    self._runs(paragraph, item)
            else:
                rule = doc.add_paragraph()
                rule.paragraph_format.space_after = Pt(4)
                border = OxmlElement("w:pBdr")
                line = OxmlElement("w:bottom")
                for key, value in (("w:val", "single"), ("w:sz", "6"), ("w:space", "1"), ("w:color", "AAAAAA")):
                    line.set(qn(key), value)
                border.append(line)
                rule._p.get_or_add_pPr().append(border)

    @staticmethod
    def _runs(paragraph, text: str) -> None:
        for chunk, bold, italic in report_text.inline_runs(text):
            run = paragraph.add_run(chunk)
            run.bold = bold or None
            run.italic = italic or None

    # ------------------------------------------------------------------ долен колонтитул с номер на страницата
    def _footer(self, doc) -> None:
        doc.styles["Footer"].font.size = Pt(9)
        footer = doc.sections[0].footer.paragraphs[0]
        footer.alignment = WD_ALIGN_PARAGRAPH.CENTER
        run = footer.add_run(f"AstroMind · {datetime.now().strftime('%d.%m.%Y')} · Само за занимателна цел · стр. ")
        run.font.size = Pt(9)
        run.font.color.rgb = GREY
        page = footer.add_run()
        page.font.size = Pt(9)
        page.font.color.rgb = GREY
        for kind, text in (("begin", None), (None, "PAGE"), ("end", None)):
            if kind:
                element = OxmlElement("w:fldChar")
                element.set(qn("w:fldCharType"), kind)
            else:
                element = OxmlElement("w:instrText")
                element.set(qn("xml:space"), "preserve")
                element.text = text
            page._r.append(element)
