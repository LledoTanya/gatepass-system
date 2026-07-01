"""
pdf_slip.py
-----------
Render a single gatepass as a printable PDF slip that mirrors the real
company form **CPFI-HR-SOP-F-03 — Personnel Gatepass During Working Hours**.

Digital data we have (date, employee, section/dept, purpose, and the digital
approval status) is filled in automatically. The parts of the paper form that
are still handled manually at the gate — medical recommendation, the sign-off
lines (Checked by / Approved by / Security / HRD), Time Left / Time Returned,
the waiver and the employee signature — are reproduced as blank lines so the
printout is a faithful drop-in for the paper slip.

Returns the PDF as raw bytes so FastAPI can stream it inline.
"""

import datetime as dt
import io

from reportlab.lib.units import inch
from reportlab.lib.utils import simpleSplit
from reportlab.pdfgen import canvas

# Half-letter (statement) portrait — a natural size for a gatepass slip.
PAGE_W, PAGE_H = 5.5 * inch, 8.5 * inch
MARGIN = 26

# Canonical purpose options (must match the staff form).
PURPOSES = ["Undertime", "Lunch Out", "Official Business"]

WAIVER_TEXT = (
    "I hereby waive my rights to complain and claim of responsibility & "
    "liability of the Company in the duration of my going out from the Company "
    "premises until I arrived from personal undertaking and/or while outside "
    "due to illness."
)

STATUS_COLORS = {
    "pending": (0.85, 0.55, 0.0),
    "approved": (0.0, 0.55, 0.2),
    "denied": (0.78, 0.1, 0.1),
    "completed": (0.35, 0.4, 0.5),
}


def _checkbox(c: canvas.Canvas, x: float, y: float, checked: bool, size: float = 9):
    """Draw a small square; put an X inside it when `checked`."""
    c.setLineWidth(0.8)
    c.rect(x, y, size, size, stroke=1, fill=0)
    if checked:
        c.setLineWidth(1.4)
        c.line(x + 1.5, y + 1.5, x + size - 1.5, y + size - 1.5)
        c.line(x + 1.5, y + size - 1.5, x + size - 1.5, y + 1.5)
        c.setLineWidth(0.8)


def _line(c: canvas.Canvas, x1: float, y: float, x2: float):
    c.setLineWidth(0.6)
    c.line(x1, y, x2, y)


def build_gatepass_pdf(gp: dict) -> bytes:
    """Build the slip for one gatepass dict (Gatepass.as_dict())."""
    buf = io.BytesIO()
    c = canvas.Canvas(buf, pagesize=(PAGE_W, PAGE_H))

    x0, x1 = MARGIN, PAGE_W - MARGIN
    mid = PAGE_W / 2
    cx = PAGE_W / 2

    # Date comes from when the request was created.
    created = gp.get("created_at") or ""
    date_str = created.split(" ")[0] if created else dt.date.today().isoformat()

    # Departure time recorded by the guard when they click Out.
    # Left blank on the slip until that action is taken.
    left_at = gp.get("left_at") or ""
    time_left = left_at.split(" ")[1][:5] if " " in left_at else ""

    # Actual return time recorded by the guard (returned_at); blank until logged.
    returned_at = gp.get("returned_at") or ""
    time_returned = returned_at.split(" ")[1][:5] if " " in returned_at else ""

    # return_time is stored as the actual time string or None; as_dict() serialises
    # None as "—" (a display sentinel). Strip it so the PDF shows a real value or blank.
    raw_return = gp.get("return_time") or ""
    return_time_display = raw_return if raw_return and raw_return != "—" else ""

    purpose = gp.get("purpose", "")
    is_sick = purpose == "Sickness / Illness"
    # "Official Business" and true "Others" both use the notes field for a specify text.
    # Show notes in the Others/Specify line for any purpose that has notes attached.
    notes = gp.get("notes") or ""
    is_other = (purpose not in PURPOSES) and not is_sick
    # For Official Business, tick its checkbox but also print the notes as specify text.
    other_text = notes if notes else ""
    diagnosis = gp.get("diagnosis") or ""
    recommendation = gp.get("recommendation") or ""

    y = PAGE_H - 34

    # --- Header ----------------------------------------------------------- #
    c.setFont("Helvetica-Bold", 13)
    c.drawCentredString(cx, y, "CENTURY PACIFIC FOOD, INC.")
    y -= 15
    c.setFont("Helvetica-Bold", 9.5)
    c.drawCentredString(cx, y, "PERSONNEL GATEPASS DURING WORKING HOURS")
    y -= 5
    _line(c, x0, y, x1)
    y -= 15

    c.setFont("Helvetica", 9)
    c.drawString(x0, y, "DATE:")
    c.setFont("Helvetica-Bold", 9)
    c.drawString(x0 + 30, y, date_str)
    _line(c, x0 + 28, y - 2, mid - 10)
    y -= 18

    # --- Employee --------------------------------------------------------- #
    c.setFont("Helvetica", 9)
    c.drawString(x0, y, "NAME OF EMPLOYEE:")
    c.setFont("Helvetica-Bold", 9)
    c.drawString(x0 + 108, y, gp.get("name", ""))
    _line(c, x0 + 104, y - 2, x1)
    y -= 18
    c.setFont("Helvetica", 9)
    c.drawString(x0, y, "SECTION / DEPT.:")
    c.setFont("Helvetica-Bold", 9)
    c.drawString(x0 + 90, y, gp.get("department", ""))
    _line(c, x0 + 86, y - 2, x1)
    y -= 16

    _line(c, x0, y, x1)
    y -= 14

    # --- Purpose ---------------------------------------------------------- #
    c.setFont("Helvetica", 7.5)
    c.drawString(x0, y, "PLEASE ALLOW SUBJECT EMPLOYEE TO GO OUT OF COMPANY PREMISES")
    y -= 18

    c.setFont("Helvetica-Bold", 9)
    c.drawString(x0, y, "PURPOSE")
    boxx = x0 + 52
    c.setFont("Helvetica", 8.5)
    for label in PURPOSES:
        _checkbox(c, boxx, y - 1, checked=(purpose == label))
        c.drawString(boxx + 13, y, label)
        boxx += 13 + c.stringWidth(label, "Helvetica", 8.5) + 16
    y -= 18
    _checkbox(c, x0 + 52, y - 1, checked=is_other)
    c.drawString(x0 + 65, y, "Others (Specify):")
    c.setFont("Helvetica-Bold", 8.5)
    c.drawString(x0 + 150, y, other_text)
    _line(c, x0 + 146, y - 2, x1)
    y -= 14

    # Expected return time (extra digital data, printed as a helper)
    c.setFont("Helvetica-Oblique", 7.5)
    c.drawString(x0, y, f"Expected return time (per request): {return_time_display}")
    y -= 14
    _line(c, x0, y, x1)
    y -= 14

    # --- Sickness / illness ----------------------------------------------- #
    c.setFont("Helvetica-Bold", 8.5)
    c.drawString(x0, y, "DUE TO SICKNESS OR ILLNESS:")
    y -= 14
    c.setFont("Helvetica", 8)
    c.drawString(x0, y, "Medical Diagnosis")
    c.drawString(mid + 6, y, "Recommendation")
    y -= 14

    recs = ["Go Home / Advise to Rest", "Check-up / Re-check",
            "Referred to Hospital / Outside Clinic"]
    # When the request was raised as a sickness/illness pass, the diagnosis and
    # recommendation captured digitally are printed; otherwise blank lines.
    diag_lines = simpleSplit(diagnosis, "Helvetica-Bold", 8, (mid - 10) - x0) if diagnosis else []
    diag_y = y
    for i in range(3):
        _line(c, x0, diag_y - 2, mid - 10)
        if i < len(diag_lines):
            c.setFont("Helvetica-Bold", 8)
            c.drawString(x0 + 2, diag_y, diag_lines[i])
        ticked = is_sick and (recommendation == recs[i])
        _checkbox(c, mid + 6, diag_y - 1, checked=ticked)
        c.setFont("Helvetica-Bold" if ticked else "Helvetica", 7.5)
        c.drawString(mid + 20, diag_y, recs[i])
        diag_y -= 16
    y = diag_y - 4
    _line(c, x0, y, x1)
    y -= 16

    # --- Sign-off with positioned digital stamps --------------------------- #
    # Left: Medical Clinic Staff | Right: Dept. Head / Supervisor
    nurse_status = gp.get("nurse_status")
    admin_status = gp.get("status", "pending")

    # Nurse digital stamp (left side, above "Checked by")
    if nurse_status == "approved":
        col = STATUS_COLORS.get("approved", (0, 0, 0))
        c.setStrokeColorRGB(*col); c.setLineWidth(1.0)
        c.rect(x0, y - 4, mid - x0 - 16, 16, stroke=1, fill=0)
        c.setFillColorRGB(*col); c.setFont("Helvetica-Bold", 8)
        nurse_by = gp.get("nurse_decided_by") or ""
        c.drawString(x0 + 4, y, f"CLEARED: {nurse_by}")
        c.setFillColorRGB(0, 0, 0); c.setStrokeColorRGB(0, 0, 0)
    elif nurse_status == "denied":
        col = STATUS_COLORS.get("denied", (0, 0, 0))
        c.setStrokeColorRGB(*col); c.setLineWidth(1.0)
        c.rect(x0, y - 4, mid - x0 - 16, 16, stroke=1, fill=0)
        c.setFillColorRGB(*col); c.setFont("Helvetica-Bold", 8)
        c.drawString(x0 + 4, y, "DENIED BY CLINIC")
        c.setFillColorRGB(0, 0, 0); c.setStrokeColorRGB(0, 0, 0)

    # Admin digital stamp (right side, above "Approved by")
    if admin_status in ("approved", "completed"):
        col = STATUS_COLORS.get("approved", (0, 0, 0))
        c.setStrokeColorRGB(*col); c.setLineWidth(1.0)
        c.rect(mid + 6, y - 4, x1 - mid - 6, 16, stroke=1, fill=0)
        c.setFillColorRGB(*col); c.setFont("Helvetica-Bold", 8)
        admin_by = gp.get("decided_by") or ""
        c.drawString(mid + 10, y, f"APPROVED: {admin_by}")
        c.setFillColorRGB(0, 0, 0); c.setStrokeColorRGB(0, 0, 0)
    elif admin_status == "denied" and not nurse_status:
        col = STATUS_COLORS.get("denied", (0, 0, 0))
        c.setStrokeColorRGB(*col); c.setLineWidth(1.0)
        c.rect(mid + 6, y - 4, x1 - mid - 6, 16, stroke=1, fill=0)
        c.setFillColorRGB(*col); c.setFont("Helvetica-Bold", 8)
        c.drawString(mid + 10, y, "DENIED")
        c.setFillColorRGB(0, 0, 0); c.setStrokeColorRGB(0, 0, 0)
    y -= 22

    c.setFont("Helvetica", 8)
    _line(c, x0, y, mid - 14)
    _line(c, mid + 6, y, x1)
    y -= 10
    c.drawString(x0, y, "Checked by: Medical Clinic Staff")
    c.drawString(mid + 6, y, "Approved by: Dept. Head / Supervisor")
    y -= 18

    # --- Times + Security/HRD (manual) ------------------------------------ #
    c.setFont("Helvetica", 8)
    c.drawString(x0, y, "Time Left:")
    _line(c, x0 + 48, y - 2, mid - 14)
    c.setFont("Helvetica-Oblique", 7)
    c.drawString(x0 + 52, y + 1, time_left)
    c.setFont("Helvetica", 8)
    c.drawString(mid + 6, y, "Time Returned:")
    _line(c, mid + 66, y - 2, x1)
    if time_returned:
        c.setFont("Helvetica-Bold", 8)
        c.drawString(mid + 70, y + 1, time_returned)
    y -= 18
    c.drawString(x0, y, "HRD:")
    _line(c, x0 + 26, y - 2, mid - 14)
    c.drawString(mid + 6, y, "SECURITY:")
    _line(c, mid + 52, y - 2, x1)
    y -= 18
    _line(c, x0, y, x1)
    y -= 16

    # --- Waiver ----------------------------------------------------------- #
    c.setFont("Helvetica-Bold", 8.5)
    c.rect(cx - 32, y - 3, 64, 14, stroke=1, fill=0)
    c.drawCentredString(cx, y, "WAIVER")
    y -= 18
    c.setFont("Helvetica", 7)
    for ln in simpleSplit(WAIVER_TEXT, "Helvetica", 7, x1 - x0):
        c.drawString(x0, y, ln)
        y -= 10
    y -= 16

    # --- Footer ----------------------------------------------------------- #
    c.setFont("Helvetica-Oblique", 6.5)
    c.setFillColorRGB(0.45, 0.45, 0.45)
    c.drawCentredString(
        cx, MARGIN - 6,
        f"Gatepass #{gp.get('id', '')} · generated "
        f"{dt.datetime.now().strftime('%Y-%m-%d %H:%M')} · Gatepass Control",
    )

    c.showPage()
    c.save()
    buf.seek(0)
    return buf.getvalue()


# ---------------------------------------------------------------------------
# Report summary PDF  (dept head / OIC — monthly summary by employee)
# ---------------------------------------------------------------------------
def build_report_pdf(dept: str, month: str, data: list) -> bytes:
    """
    data: list of { name, employee_id, months: { "YYYY-MM": { total, purposes } } }
    Renders a printable summary table.
    """
    from reportlab.lib.pagesizes import letter
    from reportlab.platypus import SimpleDocTemplate, Table, TableStyle, Paragraph, Spacer
    from reportlab.lib.styles import getSampleStyleSheet
    from reportlab.lib import colors

    buf = io.BytesIO()
    doc = SimpleDocTemplate(buf, pagesize=letter,
                            leftMargin=36, rightMargin=36,
                            topMargin=40, bottomMargin=40)
    styles = getSampleStyleSheet()
    elements = []

    # Title
    title_style = styles["Heading2"]
    elements.append(Paragraph(f"CENTURY PACIFIC FOOD, INC.", styles["Heading1"]))
    elements.append(Paragraph(f"Gatepass Summary Report — {dept}", styles["Heading2"]))
    elements.append(Paragraph(f"Period: {month}", styles["Normal"]))
    elements.append(Spacer(1, 12))

    if not data:
        elements.append(Paragraph("No records for this period.", styles["Normal"]))
    else:
        # Collect all months that appear
        all_months: set = set()
        for e in data:
            all_months.update(e.get("months", {}).keys())
        all_months_sorted = sorted(all_months)

        # Build header
        header = ["Employee", "Employee ID"] + all_months_sorted + ["Total"]

        # Build rows
        rows = [header]
        for e in data:
            total = 0
            cells = []
            for m in all_months_sorted:
                cnt = (e.get("months", {}).get(m) or {}).get("total", 0)
                total += cnt
                cells.append(str(cnt) if cnt else "—")
            rows.append([e.get("name", ""), e.get("employee_id", "")] + cells + [str(total)])

        # Column widths: name=130, eid=70, each month=50, total=40
        col_widths = [130, 70] + [50] * len(all_months_sorted) + [40]
        # Clamp total width to available page width (letter - margins)
        max_w = letter[0] - 72
        total_w = sum(col_widths)
        if total_w > max_w:
            scale = max_w / total_w
            col_widths = [w * scale for w in col_widths]

        t = Table(rows, colWidths=col_widths, repeatRows=1)
        t.setStyle(TableStyle([
            ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#2b8a99")),
            ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
            ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
            ("FONTSIZE", (0, 0), (-1, 0), 8),
            ("FONTNAME", (0, 1), (-1, -1), "Helvetica"),
            ("FONTSIZE", (0, 1), (-1, -1), 8),
            ("FONTNAME", (-1, 1), (-1, -1), "Helvetica-Bold"),  # totals col bold
            ("ALIGN", (2, 0), (-1, -1), "CENTER"),
            ("ALIGN", (0, 0), (1, -1), "LEFT"),
            ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#f1f5f9")]),
            ("GRID", (0, 0), (-1, -1), 0.4, colors.HexColor("#cbd5e1")),
            ("TOPPADDING", (0, 0), (-1, -1), 4),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
            ("LEFTPADDING", (0, 0), (-1, -1), 5),
        ]))
        elements.append(t)

    elements.append(Spacer(1, 16))
    elements.append(Paragraph(
        f"Generated {dt.datetime.now().strftime('%Y-%m-%d %H:%M')} · Gatepass Control",
        styles["Italic"]
    ))

    doc.build(elements)
    buf.seek(0)
    return buf.getvalue()


# ---------------------------------------------------------------------------
# Archive PDF  (guard — full flat list of archived records)
# ---------------------------------------------------------------------------
def build_archive_pdf(rows: list) -> bytes:
    """
    rows: list of Gatepass.as_dict()
    Renders a flat printable table of all archived gatepass records.
    """
    from reportlab.lib.pagesizes import letter, landscape
    from reportlab.platypus import SimpleDocTemplate, Table, TableStyle, Paragraph, Spacer
    from reportlab.lib.styles import getSampleStyleSheet
    from reportlab.lib import colors

    buf = io.BytesIO()
    page = landscape(letter)
    doc = SimpleDocTemplate(buf, pagesize=page,
                            leftMargin=36, rightMargin=36,
                            topMargin=36, bottomMargin=36)
    styles = getSampleStyleSheet()
    elements = []

    elements.append(Paragraph("CENTURY PACIFIC FOOD, INC.", styles["Heading1"]))
    elements.append(Paragraph("Gatepass Archive", styles["Heading2"]))
    elements.append(Paragraph(
        f"Generated {dt.datetime.now().strftime('%Y-%m-%d %H:%M')} · {len(rows)} records",
        styles["Normal"]
    ))
    elements.append(Spacer(1, 12))

    def purpose_label(g):
        if g.get("purpose") == "Sickness / Illness":
            rec = g.get("recommendation") or ""
            return f"Sickness{' — ' + rec if rec else ''}"
        return g.get("purpose", "")

    if not rows:
        elements.append(Paragraph("No archived records.", styles["Normal"]))
    else:
        header = ["Name", "Department", "Purpose", "Status",
                  "Requested", "Time Left", "Time Returned", "Decided By"]
        table_rows = [header]
        for g in rows:
            table_rows.append([
                g.get("name", ""),
                g.get("department", ""),
                purpose_label(g),
                g.get("status", "").capitalize(),
                (g.get("created_at") or "")[:16],
                (g.get("left_at") or "")[-8:-3] if g.get("left_at") else "—",
                (g.get("returned_at") or "")[-8:-3] if g.get("returned_at") else "—",
                g.get("decided_by_name") or g.get("decided_by") or "—",
            ])

        # Available width in landscape letter minus margins
        available = page[0] - 72
        col_widths = [110, 90, 110, 60, 90, 55, 70, 80]
        tw = sum(col_widths)
        if tw > available:
            scale = available / tw
            col_widths = [w * scale for w in col_widths]

        t = Table(table_rows, colWidths=col_widths, repeatRows=1)
        t.setStyle(TableStyle([
            ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#2b8a99")),
            ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
            ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
            ("FONTSIZE", (0, 0), (-1, 0), 8),
            ("FONTNAME", (0, 1), (-1, -1), "Helvetica"),
            ("FONTSIZE", (0, 1), (-1, -1), 7.5),
            ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#f1f5f9")]),
            ("GRID", (0, 0), (-1, -1), 0.4, colors.HexColor("#cbd5e1")),
            ("TOPPADDING", (0, 0), (-1, -1), 3),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
            ("LEFTPADDING", (0, 0), (-1, -1), 4),
        ]))
        elements.append(t)

    doc.build(elements)
    buf.seek(0)
    return buf.getvalue()
