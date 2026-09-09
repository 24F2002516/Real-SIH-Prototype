"""Generates downloadable PDF compliance reports (full tender report + single bid certificate)
using reportlab. Kept dependency-light and self-contained so it runs anywhere the rest of
the backend runs.
"""
import io
from datetime import datetime

from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.units import mm
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.platypus import (
    SimpleDocTemplate, Table, TableStyle, Paragraph, Spacer, PageBreak, HRFlowable, KeepTogether
)

# ---- Theme (matches frontend palette) ----
NAVY = colors.HexColor("#12203D")
INK = colors.HexColor("#1B2430")
SAFFRON = colors.HexColor("#B8730F")
PAPER = colors.HexColor("#F3F1EA")
BORDER = colors.HexColor("#C9CDD4")
GREEN = colors.HexColor("#1F7A4D")
RED = colors.HexColor("#A82A20")
AMBER = colors.HexColor("#96660A")
GREY = colors.HexColor("#5B6472")

STATUS_COLORS = {
    "COMPLIANT": GREEN,
    "NON_COMPLIANT": RED,
    "NEEDS_REVIEW": AMBER,
    "PENDING": GREY,
}

styles = getSampleStyleSheet()
STYLE_TITLE = ParagraphStyle("RepTitle", parent=styles["Title"], fontName="Helvetica-Bold",
                              fontSize=18, textColor=NAVY, spaceAfter=2, leading=22)
STYLE_SUBTITLE = ParagraphStyle("RepSubtitle", parent=styles["Normal"], fontName="Helvetica",
                                 fontSize=10, textColor=GREY, spaceAfter=10)
STYLE_H2 = ParagraphStyle("RepH2", parent=styles["Heading2"], fontName="Helvetica-Bold",
                           fontSize=12.5, textColor=NAVY, spaceBefore=14, spaceAfter=6)
STYLE_BODY = ParagraphStyle("RepBody", parent=styles["Normal"], fontName="Helvetica",
                             fontSize=9.5, textColor=INK, leading=13)
STYLE_SMALL = ParagraphStyle("RepSmall", parent=styles["Normal"], fontName="Helvetica",
                              fontSize=8.5, textColor=GREY, leading=11)
STYLE_CELL = ParagraphStyle("RepCell", parent=styles["Normal"], fontName="Helvetica",
                             fontSize=8.7, textColor=INK, leading=11.5)
STYLE_CELL_BOLD = ParagraphStyle("RepCellBold", parent=STYLE_CELL, fontName="Helvetica-Bold")


def _clean(text):
    """Base-14 PDF fonts (Helvetica) don't cover the Rupee sign or other exotic glyphs —
    swap them for ASCII-safe equivalents so they don't render as missing-glyph boxes."""
    if text is None:
        return ""
    return str(text).replace("\u20b9", "Rs. ")


def _status_label(status):
    return (status or "PENDING").replace("_", " ")


def _header_footer(canvas, doc, tender_label):
    canvas.saveState()
    width, height = A4
    canvas.setFillColor(NAVY)
    canvas.rect(0, height - 14 * mm, width, 14 * mm, stroke=0, fill=1)
    canvas.setFillColor(colors.white)
    canvas.setFont("Helvetica-Bold", 10.5)
    canvas.drawString(18 * mm, height - 9.5 * mm, "GeM Bid Compliance Verification")
    canvas.setFont("Helvetica", 8.5)
    canvas.setFillColor(colors.HexColor("#C9D4E8"))
    canvas.drawRightString(width - 18 * mm, height - 9.5 * mm, tender_label or "")
    canvas.setFillColor(GREY)
    canvas.setFont("Helvetica", 7.5)
    canvas.drawString(18 * mm, 10 * mm, f"Generated {datetime.now().strftime('%d %b %Y, %H:%M')}")
    canvas.drawCentredString(width / 2, 10 * mm,
                              "Auto-generated compliance report \u2014 verify critical findings manually before final award decision.")
    canvas.drawRightString(width - 18 * mm, 10 * mm, f"Page {doc.page}")
    canvas.restoreState()


def _requirements_table(tender):
    header = ["Requirement", "Check type", "Weight", "Mandatory"]
    rows = [[Paragraph(f"<b>{h}</b>", STYLE_CELL_BOLD) for h in header]]
    for r in tender.requirements:
        rows.append([
            Paragraph(_clean(r.name), STYLE_CELL),
            Paragraph(r.rule_type.replace("_", " ").title(), STYLE_CELL),
            Paragraph(str(r.weight), STYLE_CELL),
            Paragraph("Yes" if r.is_mandatory else "No", STYLE_CELL),
        ])
    t = Table(rows, colWidths=[75 * mm, 40 * mm, 20 * mm, 25 * mm], repeatRows=1)
    t.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), NAVY),
        ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
        ("LINEBELOW", (0, 0), (-1, 0), 0.6, NAVY),
        ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, PAPER]),
        ("LINEBELOW", (0, 1), (-1, -1), 0.4, BORDER),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("TOPPADDING", (0, 0), (-1, -1), 5),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
        ("LEFTPADDING", (0, 0), (-1, -1), 6),
    ]))
    return t


def _summary_table(bids):
    total = len(bids)
    compliant = sum(1 for b in bids if b.status == "COMPLIANT")
    non_compliant = sum(1 for b in bids if b.status == "NON_COMPLIANT")
    needs_review = sum(1 for b in bids if b.status == "NEEDS_REVIEW")
    scored = [b.compliance_score for b in bids if b.compliance_score is not None]
    avg = round(sum(scored) / len(scored), 1) if scored else 0.0

    stats = [
        ("Bids received", str(total)),
        ("Compliant", str(compliant)),
        ("Non-compliant", str(non_compliant)),
        ("Needs review", str(needs_review)),
        ("Average score", f"{avg}%"),
    ]
    header = [Paragraph(f"<b>{k}</b>", STYLE_CELL_BOLD) for k, _ in stats]
    values = [Paragraph(v, ParagraphStyle("val", parent=STYLE_CELL, fontSize=13, textColor=NAVY,
                                           fontName="Helvetica-Bold")) for _, v in stats]
    t = Table([header, values], colWidths=[36 * mm] * 5)
    t.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, -1), PAPER),
        ("BOX", (0, 0), (-1, -1), 0.6, BORDER),
        ("INNERGRID", (0, 0), (-1, -1), 0.4, BORDER),
        ("ALIGN", (0, 0), (-1, -1), "CENTER"),
        ("TOPPADDING", (0, 0), (-1, -1), 6),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
    ]))
    return t


def _bid_ranking_table(bids):
    header = ["Rank", "Vendor", "Score", "Status", "Docs"]
    rows = [[Paragraph(f"<b>{h}</b>", STYLE_CELL_BOLD) for h in header]]
    for i, b in enumerate(bids, start=1):
        score = f"{b.compliance_score:.1f}%" if b.compliance_score is not None else "\u2014"
        color = STATUS_COLORS.get(b.status, GREY)
        status_style = ParagraphStyle("st", parent=STYLE_CELL, textColor=color, fontName="Helvetica-Bold")
        rows.append([
            Paragraph(str(i), STYLE_CELL),
            Paragraph(_clean(b.vendor_name) or "\u2014", STYLE_CELL),
            Paragraph(score, STYLE_CELL_BOLD),
            Paragraph(_status_label(b.status), status_style),
            Paragraph(str(len(b.documents)), STYLE_CELL),
        ])
    t = Table(rows, colWidths=[14 * mm, 70 * mm, 22 * mm, 34 * mm, 20 * mm], repeatRows=1)
    t.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), NAVY),
        ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
        ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, PAPER]),
        ("LINEBELOW", (0, 1), (-1, -1), 0.4, BORDER),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("TOPPADDING", (0, 0), (-1, -1), 5),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
        ("LEFTPADDING", (0, 0), (-1, -1), 6),
    ]))
    return t


def _bid_breakdown_block(bid, index):
    score = f"{bid.compliance_score:.1f}%" if bid.compliance_score is not None else "\u2014"
    color = STATUS_COLORS.get(bid.status, GREY)

    title = Paragraph(f"{index}. {_clean(bid.vendor_name) or 'Unnamed vendor'}", STYLE_H2)
    meta = Paragraph(
        f"Email: {_clean(bid.vendor_email) or '\u2014'} &nbsp;&nbsp;|&nbsp;&nbsp; "
        f"Submitted: {bid.submitted_at.strftime('%d %b %Y, %H:%M') if bid.submitted_at else '\u2014'} &nbsp;&nbsp;|&nbsp;&nbsp; "
        f"Documents: {len(bid.documents)}",
        STYLE_SMALL,
    )
    score_style = ParagraphStyle("score", parent=STYLE_BODY, fontName="Helvetica-Bold",
                                  fontSize=12, textColor=color)
    score_line = Paragraph(
        f"Compliance score: {score} &nbsp;\u2014&nbsp; {_status_label(bid.status)}",
        score_style,
    )

    rows = [[Paragraph("<b>Requirement</b>", STYLE_CELL_BOLD), Paragraph("<b>Result</b>", STYLE_CELL_BOLD),
             Paragraph("<b>Detail</b>", STYLE_CELL_BOLD)]]
    for r in bid.results:
        result_color = GREEN if r.passed else RED
        result_style = ParagraphStyle("res", parent=STYLE_CELL, textColor=result_color, fontName="Helvetica-Bold")
        req_label = _clean(r.requirement_name) + (" (mandatory)" if r.is_mandatory else "")
        rows.append([
            Paragraph(req_label, STYLE_CELL),
            Paragraph("Pass" if r.passed else "Fail", result_style),
            Paragraph(_clean(r.detail) or "", STYLE_CELL),
        ])
    breakdown = Table(rows, colWidths=[62 * mm, 20 * mm, 78 * mm], repeatRows=1)
    breakdown.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#DCE3EC")),
        ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, PAPER]),
        ("LINEBELOW", (0, 1), (-1, -1), 0.4, BORDER),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("TOPPADDING", (0, 0), (-1, -1), 4),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
        ("LEFTPADDING", (0, 0), (-1, -1), 6),
    ]))

    docs_line = None
    if bid.documents:
        doc_bits = [_clean(d.filename) for d in bid.documents]
        docs_line = Paragraph("Submitted documents: " + ", ".join(doc_bits), STYLE_SMALL)

    block = [title, meta, score_line, Spacer(1, 4), breakdown]
    if docs_line:
        block += [Spacer(1, 3), docs_line]
    block.append(Spacer(1, 10))
    return KeepTogether(block)


def generate_tender_report(tender, bids):
    buf = io.BytesIO()
    doc = SimpleDocTemplate(buf, pagesize=A4,
                             leftMargin=18 * mm, rightMargin=18 * mm,
                             topMargin=22 * mm, bottomMargin=18 * mm)

    tender_label = tender.gem_bid_number or ""
    story = []

    story.append(Paragraph("Bid Compliance Verification Report", STYLE_TITLE))
    story.append(Paragraph(_clean(tender.title), ParagraphStyle("sub", parent=STYLE_SUBTITLE, fontSize=11,
                                                          textColor=INK, fontName="Helvetica-Bold")))
    story.append(Paragraph(
        f"{_clean(tender.gem_bid_number)} &nbsp;\u00b7&nbsp; {_clean(tender.department) or 'Department not specified'}",
        STYLE_SUBTITLE))
    story.append(HRFlowable(width="100%", thickness=0.8, color=SAFFRON, spaceAfter=8))

    if tender.description:
        story.append(Paragraph(_clean(tender.description), STYLE_BODY))

    story.append(Paragraph("Summary", STYLE_H2))
    story.append(_summary_table(bids))

    story.append(Paragraph("Compliance Requirements", STYLE_H2))
    story.append(_requirements_table(tender))

    story.append(Paragraph("Bid Ranking", STYLE_H2))
    if bids:
        story.append(_bid_ranking_table(bids))
    else:
        story.append(Paragraph("No bids have been submitted against this tender yet.", STYLE_BODY))

    if bids:
        story.append(PageBreak())
        story.append(Paragraph("Detailed Rule-by-Rule Breakdown", STYLE_H2))
        for i, bid in enumerate(bids, start=1):
            story.append(_bid_breakdown_block(bid, i))

    doc.build(story, onFirstPage=lambda c, d: _header_footer(c, d, tender_label),
              onLaterPages=lambda c, d: _header_footer(c, d, tender_label))
    return buf.getvalue()


def generate_bid_report(bid):
    buf = io.BytesIO()
    doc = SimpleDocTemplate(buf, pagesize=A4,
                             leftMargin=18 * mm, rightMargin=18 * mm,
                             topMargin=22 * mm, bottomMargin=18 * mm)
    tender = bid.tender
    tender_label = tender.gem_bid_number or ""

    story = [
        Paragraph("Bid Compliance Certificate", STYLE_TITLE),
        Paragraph(f"{_clean(tender.title)} &nbsp;\u00b7&nbsp; {_clean(tender.gem_bid_number)}", STYLE_SUBTITLE),
        HRFlowable(width="100%", thickness=0.8, color=SAFFRON, spaceAfter=8),
        _bid_breakdown_block(bid, 1),
    ]

    doc.build(story, onFirstPage=lambda c, d: _header_footer(c, d, tender_label),
              onLaterPages=lambda c, d: _header_footer(c, d, tender_label))
    return buf.getvalue()