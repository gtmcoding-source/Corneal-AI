import io
import re
from xml.sax.saxutils import escape

from reportlab.lib.pagesizes import letter
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.platypus import HRFlowable, Paragraph, SimpleDocTemplate, Spacer


def _inline_markdown_to_reportlab(text):
    safe = escape(text or "")
    # Allow explicit underline tags from model output.
    safe = safe.replace("&lt;u&gt;", "<u>").replace("&lt;/u&gt;", "</u>")
    safe = re.sub(r"\*\*(.+?)\*\*", r"<b>\1</b>", safe)
    safe = re.sub(r"__(.+?)__", r"<b>\1</b>", safe)
    safe = re.sub(r"\*(.+?)\*", r"<i>\1</i>", safe)
    safe = re.sub(r"_(.+?)_", r"<i>\1</i>", safe)
    safe = re.sub(r"`(.+?)`", r"<font name='Courier'>\1</font>", safe)
    return safe


def generate_pdf(text):
    buffer = io.BytesIO()
    doc = SimpleDocTemplate(
        buffer,
        pagesize=letter,
        leftMargin=42,
        rightMargin=42,
        topMargin=40,
        bottomMargin=40,
        title="Study Notes"
    )
    styles = getSampleStyleSheet()
    body = ParagraphStyle("Body", parent=styles["BodyText"], fontSize=10.5, leading=15)
    h1 = ParagraphStyle("H1", parent=styles["Heading1"], fontSize=17, leading=22, spaceAfter=8)
    h2 = ParagraphStyle("H2", parent=styles["Heading2"], fontSize=13.5, leading=18, spaceBefore=4, spaceAfter=6)
    h3 = ParagraphStyle("H3", parent=styles["Heading3"], fontSize=11.5, leading=15, spaceBefore=3, spaceAfter=4)
    bullet = ParagraphStyle("Bullet", parent=body, leftIndent=14, firstLineIndent=-8, spaceAfter=4)

    elements = []
    lines = (text or "").splitlines()

    for raw_line in lines:
        line = (raw_line or "").rstrip()
        stripped = line.strip()

        if not stripped:
            elements.append(Spacer(1, 7))
            continue

        if stripped in {"---", "***"}:
            elements.append(HRFlowable(width="100%", thickness=0.8, color="#94a3b8", spaceBefore=5, spaceAfter=6))
            continue

        if stripped.startswith("# "):
            elements.append(Paragraph(_inline_markdown_to_reportlab(stripped[2:].strip()), h1))
            continue
        if stripped.startswith("## "):
            elements.append(Paragraph(_inline_markdown_to_reportlab(stripped[3:].strip()), h2))
            continue
        if stripped.startswith("### "):
            elements.append(Paragraph(_inline_markdown_to_reportlab(stripped[4:].strip()), h3))
            continue

        numbered = re.match(r"^(\d+)\.\s+(.+)$", stripped)
        if numbered:
            elements.append(Paragraph(f"{numbered.group(1)}. {_inline_markdown_to_reportlab(numbered.group(2))}", bullet))
            continue

        if stripped.startswith(("- ", "* ")):
            bullet_text = stripped[2:].strip()
            elements.append(Paragraph(f"&bull; {_inline_markdown_to_reportlab(bullet_text)}", bullet))
            continue

        elements.append(Paragraph(_inline_markdown_to_reportlab(stripped), body))

    doc.build(elements)
    buffer.seek(0)
    return buffer
