from pathlib import Path
from datetime import datetime
from typing import Dict, List
import os

from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import getSampleStyleSheet
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.platypus import SimpleDocTemplate, Table, TableStyle, Paragraph, Spacer, Image

BASE_DIR = Path(os.getenv("DENT_AI_BASE", Path(__file__).resolve().parent))
STORAGE_DIR = Path(os.getenv("PLAN_STORAGE_DIR", BASE_DIR / "storage"))
STORAGE_DIR.mkdir(parents=True, exist_ok=True)

LOGO_PATH = Path(os.getenv("PLAN_LOGO_PATH", BASE_DIR / "logo_CS_vertical_blue.png"))
LOGO_MAX_WIDTH = 120
LOGO_MAX_HEIGHT = 60

FONT_CANDIDATES = [
    (
        Path(os.getenv("FONT_TIMES_REG", r"C:\Windows\Fonts\times.ttf")),
        Path(os.getenv("FONT_TIMES_BOLD", r"C:\Windows\Fonts\timesbd.ttf")),
        Path(os.getenv("FONT_TIMES_ITALIC", r"C:\Windows\Fonts\timesi.ttf")),
        Path(os.getenv("FONT_TIMES_BOLDITALIC", r"C:\Windows\Fonts\timesbi.ttf")),
    ),
    (
        Path(os.getenv("FONT_DEJAVU_REG", BASE_DIR / "fonts" / "DejaVuSans.ttf")),
        Path(os.getenv("FONT_DEJAVU_BOLD", BASE_DIR / "fonts" / "DejaVuSans-Bold.ttf")),
        Path(os.getenv("FONT_DEJAVU_ITALIC", BASE_DIR / "fonts" / "DejaVuSans-Oblique.ttf")),
        Path(os.getenv("FONT_DEJAVU_BOLDITALIC", BASE_DIR / "fonts" / "DejaVuSans-BoldOblique.ttf")),
    ),
    (
        Path(os.getenv("FONT_ARIALUNI_REG", r"C:\Windows\Fonts\arialuni.ttf")),
        Path(os.getenv("FONT_ARIALUNI_BOLD", r"C:\Windows\Fonts\arialuni.ttf")),
        Path(os.getenv("FONT_ARIALUNI_ITALIC", r"C:\Windows\Fonts\arialuni.ttf")),
        Path(os.getenv("FONT_ARIALUNI_BOLDITALIC", r"C:\Windows\Fonts\arialuni.ttf")),
    ),
    (
        Path(os.getenv("FONT_ARIAL_REG", r"C:\Windows\Fonts\arial.ttf")),
        Path(os.getenv("FONT_ARIAL_BOLD", r"C:\Windows\Fonts\arialbd.ttf")),
        Path(os.getenv("FONT_ARIAL_ITALIC", r"C:\Windows\Fonts\ariali.ttf")),
        Path(os.getenv("FONT_ARIAL_BOLDITALIC", r"C:\Windows\Fonts\arialbi.ttf")),
    ),
]
FONT_FAMILY_NAME = "ClinicFont"


def _register_font_family() -> str:
    registered = set(pdfmetrics.getRegisteredFontNames())
    if FONT_FAMILY_NAME in registered:
        return FONT_FAMILY_NAME

    for regular, bold, italic, bold_italic in FONT_CANDIDATES:
        if not regular.exists():
            continue

        pdfmetrics.registerFont(TTFont(FONT_FAMILY_NAME, str(regular)))

        if bold.exists():
            pdfmetrics.registerFont(TTFont(f"{FONT_FAMILY_NAME}-Bold", str(bold)))
        if italic.exists():
            pdfmetrics.registerFont(TTFont(f"{FONT_FAMILY_NAME}-Italic", str(italic)))
        if bold_italic.exists():
            pdfmetrics.registerFont(TTFont(f"{FONT_FAMILY_NAME}-BoldItalic", str(bold_italic)))

        pdfmetrics.registerFontFamily(
            FONT_FAMILY_NAME,
            normal=FONT_FAMILY_NAME,
            bold=f"{FONT_FAMILY_NAME}-Bold" if bold.exists() else FONT_FAMILY_NAME,
            italic=f"{FONT_FAMILY_NAME}-Italic" if italic.exists() else FONT_FAMILY_NAME,
            boldItalic=f"{FONT_FAMILY_NAME}-BoldItalic" if bold_italic.exists() else FONT_FAMILY_NAME,
        )
        return FONT_FAMILY_NAME

    return "Helvetica"


def _build_styles():
    font_name = _register_font_family()

    styles = getSampleStyleSheet()
    header_style = styles['Heading2'].clone('ClinicHeading')
    body_style = styles['BodyText'].clone('ClinicBody')

    header_style.fontName = font_name
    body_style.fontName = font_name
    header_style.leading = 18
    body_style.leading = 12

    bold_name = f"{font_name}-Bold"
    if bold_name not in pdfmetrics.getRegisteredFontNames():
        bold_name = font_name

    return font_name, bold_name, header_style, body_style


def generate_pdf(plan: Dict, doctor: str, patient: str, card: str, full_doctor_title: str | None = None) -> Path:
    font_name, bold_font_name, header_style, body_style = _build_styles()

    items: List[Dict] = plan.get("items", [])
    total: float = float(plan.get("total", 0.0))

    filename = f"plan_{patient.replace(' ', '_')}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.pdf"
    output_path = STORAGE_DIR / filename

    doc = SimpleDocTemplate(
        str(output_path),
        pagesize=A4,
        rightMargin=36,
        leftMargin=36,
        topMargin=36,
        bottomMargin=36,
    )
    elements = []

    if LOGO_PATH.exists():
        logo = Image(str(LOGO_PATH))
        logo._restrictSize(LOGO_MAX_WIDTH, LOGO_MAX_HEIGHT)
        elements.append(logo)
        elements.append(Spacer(1, 8))

    elements.append(Paragraph("План лечения", header_style))
    elements.append(Spacer(1, 12))
    doctor_line = full_doctor_title or doctor
    elements.append(Paragraph(f"<b>Врач:</b> {doctor_line}", body_style))
    elements.append(Paragraph(f"<b>Пациент:</b> {patient}", body_style))
    elements.append(Paragraph(f"<b>Номер карты:</b> {card}", body_style))
    elements.append(Paragraph(f"<b>Дата:</b> {datetime.now().strftime('%d.%m.%Y %H:%M')}", body_style))
    elements.append(Spacer(1, 12))

    def cell(text: str) -> Paragraph:
        return Paragraph(str(text), body_style)

    table_data = [[
        cell("Код"),
        cell("Наименование"),
        cell("Раздел"),
        cell("Цена"),
        cell("Кол-во"),
        cell("Сумма"),
    ]]

    for item in items:
        table_data.append([
            cell(item.get("code", "")),
            cell(item.get("display_name", "")),
            cell(item.get("section", "")),
            cell(f"{float(item.get('base_price', 0)):.2f}"),
            cell(str(int(item.get("count", 1)))),
            cell(f"{float(item.get('sum', 0)):.2f}"),
        ])

    table = Table(table_data, repeatRows=1, colWidths=[60, 170, 120, 60, 50, 60])
    table_style = TableStyle([
        ('FONTNAME', (0, 0), (-1, -1), font_name),
        ('FONTNAME', (0, 0), (-1, 0), bold_font_name),
        ('BACKGROUND', (0, 0), (-1, 0), colors.lightgrey),
        ('TEXTCOLOR', (0, 0), (-1, 0), colors.black),
        ('ALIGN', (3, 1), (-1, -1), 'RIGHT'),
        ('ALIGN', (0, 0), (-1, 0), 'CENTER'),
        ('FONTSIZE', (0, 0), (-1, -1), 9),
        ('BOTTOMPADDING', (0, 0), (-1, 0), 8),
        ('BACKGROUND', (0, 1), (-1, -1), colors.whitesmoke),
        ('GRID', (0, 0), (-1, -1), 0.5, colors.grey),
    ])
    table.setStyle(table_style)
    elements.append(table)

    elements.append(Spacer(1, 12))
    elements.append(Paragraph(f"<b>Итого:</b> {total:.2f} ₽", body_style))

    doc.build(elements)
    return output_path
