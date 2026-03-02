from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer
from reportlab.lib.styles import getSampleStyleSheet
import io

def generate_pdf(text):
    buffer = io.BytesIO()
    doc = SimpleDocTemplate(buffer)
    styles = getSampleStyleSheet()
    
    # Convert newline to PDF line breaks
    formatted_text = text.replace('\n', '<br/>')
    
    elements = []
    # Title
    elements.append(Paragraph("Cornell Notes", styles["Title"]))
    elements.append(Spacer(1, 12))
    
    # Body Content
    elements.append(Paragraph(formatted_text, styles["BodyText"]))

    doc.build(elements)
    buffer.seek(0)
    
    return buffer