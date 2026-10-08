"""Render the normative Markdown specification as exactly two A4 pages."""
from pathlib import Path
import re
from xml.sax.saxutils import escape

from reportlab.lib import colors
from reportlab.lib.enums import TA_LEFT
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle, PageBreak

ROOT = Path(__file__).resolve().parents[1]
styles = {
    'body': ParagraphStyle('body', fontName='Helvetica', fontSize=9.1, leading=11.5, spaceAfter=6),
    'title': ParagraphStyle('title', fontName='Helvetica-Bold', fontSize=16, leading=19, textColor=colors.HexColor('#113b4b'), spaceAfter=8),
    'head': ParagraphStyle('head', fontName='Helvetica-Bold', fontSize=11, leading=14, spaceBefore=5, spaceAfter=5, textColor=colors.HexColor('#113b4b')),
    'cell': ParagraphStyle('cell', fontName='Helvetica', fontSize=8.2, leading=10.5),
}


def inline(text):
    return re.sub(r'\*\*(.*?)\*\*', r'<b>\1</b>', escape(text))


def footer(canvas, document):
    canvas.setStrokeColor(colors.HexColor('#b9cbd0'))
    canvas.line(36, 31, A4[0] - 36, 31)
    canvas.setFont('Helvetica', 8)
    canvas.setFillColor(colors.HexColor('#49636a'))
    canvas.drawString(36, 20, 'BHTTP/1 | Network Architecture Project')
    canvas.drawRightString(A4[0] - 36, 20, str(document.page))


def main():
    lines = (ROOT / 'docs/spec.md').read_text().splitlines()
    story = []
    i = 0
    while i < len(lines):
        line = lines[i].strip()
        i += 1
        if not line:
            continue
        if line == '<!-- PAGE BREAK -->':
            story.append(PageBreak())
        elif line.startswith('# '):
            story.append(Paragraph(inline(line[2:]), styles['title']))
        elif line.startswith('## '):
            story.append(Paragraph(inline(line[3:]), styles['head']))
        elif line.startswith('|'):
            rows = [line]
            while i < len(lines) and lines[i].strip().startswith('|'):
                rows.append(lines[i].strip())
                i += 1
            cells = []
            for row in rows:
                if re.fullmatch(r'[|\s:-]+', row):
                    continue
                cells.append([Paragraph(inline(c.strip()), styles['cell']) for c in row.strip('|').split('|')])
            widths = [65, 85, 55, 318] if len(cells[0]) == 4 else [25, 87, 149, 25, 87, 150]
            table = Table(cells, colWidths=widths, hAlign='LEFT', repeatRows=1)
            table.setStyle(TableStyle([
                ('BACKGROUND', (0, 0), (-1, 0), colors.HexColor('#e1edf0')),
                ('GRID', (0, 0), (-1, -1), 0.4, colors.HexColor('#b9cbd0')),
                ('VALIGN', (0, 0), (-1, -1), 'TOP'),
                ('LEFTPADDING', (0, 0), (-1, -1), 5),
                ('RIGHTPADDING', (0, 0), (-1, -1), 5),
                ('TOPPADDING', (0, 0), (-1, -1), 5),
                ('BOTTOMPADDING', (0, 0), (-1, -1), 5),
            ]))
            story.extend([table, Spacer(1, 7)])
        else:
            story.append(Paragraph(inline(line), styles['body']))
    target = ROOT / 'docs/spec.pdf'
    doc = SimpleDocTemplate(str(target), pagesize=A4, leftMargin=36, rightMargin=36,
                            topMargin=35, bottomMargin=42, title='BHTTP/1 - Two-page Protocol Specification',
                            author='Suhas Kembhavi')
    doc.build(story, onFirstPage=footer, onLaterPages=footer)
    print(target)


if __name__ == '__main__':
    main()
