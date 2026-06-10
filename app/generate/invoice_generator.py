"""Synthetic invoice generator — produces 8 test PDFs into data/inputs/.

5 are machine-readable text PDFs (reportlab); 3 are image-only (rasterised via
PyMuPDF + Pillow) so pdfplumber returns no text and the vision path is forced.

Run with:  python -m app.generate.invoice_generator
"""
from __future__ import annotations

import io
from dataclasses import dataclass

import fitz  # PyMuPDF
from PIL import Image, ImageFilter
from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.units import mm
from reportlab.platypus import (
    SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle,
)

from app import config

# Vendor name → (tax_id, terms) for invoice headers. Self-contained (the v0
# vendor set is independent of the v2 Postgres seed). Unknown vendors fall back
# to a default in _build_story().
_VENDOR_META = {
    "Dell Technologies":     ("29AAAAA0001A1Z5", "Net-30"),
    "Logitech India":        ("29AAAAA0002A1Z4", "Net-30"),
    "CloudHost Solutions":   ("27AAAAA0003A1Z3", "Net-15"),
    "Acme Office Supplies":  ("06AAAAA0004A1Z2", "Net-30"),
    "FastFreight Logistics": ("24AAAAA0005A1Z1", "Net-45"),
    "Surya Stationers":      ("33AAAAA0006A1Z0", "Net-15"),
    "BlueOak Furniture":     ("29AAAAA0007A1Z9", "Net-30"),
    "Nimbus Software Labs":  ("36AAAAA0008A1Z8", "Net-30"),
    "Quanta Networks":       ("29AAAAA0009A1Z7", "Net-30"),
    "GreenLeaf Catering":    ("29AAAAA0010A1Z6", "Net-15"),
}


def inr(n: float) -> str:
    """Indian-grouped currency string, e.g. 450000 → 'INR 4,50,000.00'.

    Uses the 'INR' prefix rather than the rupee glyph — Helvetica lacks it and
    the text layer would become unreliable for pdfplumber.
    """
    neg = n < 0
    n = abs(round(n, 2))
    whole = int(n)
    frac = int(round((n - whole) * 100))
    s = str(whole)
    if len(s) > 3:
        head, tail = s[:-3], s[-3:]
        parts = []
        while len(head) > 2:
            parts.insert(0, head[-2:])
            head = head[:-2]
        if head:
            parts.insert(0, head)
        s = ",".join(parts) + "," + tail
    out = f"INR {s}.{frac:02d}"
    return ("-" + out) if neg else out


@dataclass
class LineItem:
    description: str
    quantity: int
    unit_price: float | None   # None for a bundle with no per-unit breakdown
    line_total: float


@dataclass
class InvoiceSpec:
    filename: str
    vendor_name: str
    invoice_number: str
    invoice_date: str
    po_ref: str
    line_items: list[LineItem]
    tax_treatment: str         # "separated" | "embedded" | "none"
    tax_rate: float | None
    subtotal: float
    tax_amount: float | None
    total: float
    footer_note: str | None = None
    scanned: bool = False      # rasterise to an image-only PDF when True


# --------------------------------------------------------------------------- #
# PDF rendering
# --------------------------------------------------------------------------- #
def _styles():
    ss = getSampleStyleSheet()
    ss.add(ParagraphStyle("Company", parent=ss["Title"], fontSize=20, spaceAfter=2))
    ss.add(ParagraphStyle("Small", parent=ss["Normal"], fontSize=8, textColor=colors.grey))
    ss.add(ParagraphStyle("FooterNote", parent=ss["Normal"], fontSize=9,
                          textColor=colors.HexColor("#444444"), spaceBefore=10))
    return ss


def _build_story(spec: InvoiceSpec):
    ss = _styles()
    tax_id, terms = _VENDOR_META.get(spec.vendor_name, ("", "Net-30"))
    story = []

    story.append(Paragraph(spec.vendor_name, ss["Company"]))
    story.append(Paragraph(f"GSTIN: {tax_id} &nbsp;&nbsp; Terms: {terms}", ss["Small"]))
    story.append(Spacer(1, 8 * mm))
    story.append(Paragraph("<b>TAX INVOICE</b>", ss["Heading2"]))

    meta = [
        ["Invoice Number:", spec.invoice_number, "Invoice Date:", spec.invoice_date],
        ["PO Reference:", spec.po_ref, "Currency:", "INR"],
    ]
    meta_tbl = Table(meta, colWidths=[32 * mm, 55 * mm, 28 * mm, 45 * mm])
    meta_tbl.setStyle(TableStyle([
        ("FONTSIZE", (0, 0), (-1, -1), 9),
        ("TEXTCOLOR", (0, 0), (0, -1), colors.grey),
        ("TEXTCOLOR", (2, 0), (2, -1), colors.grey),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
    ]))
    story.append(meta_tbl)
    story.append(Spacer(1, 6 * mm))

    rows = [["#", "Description", "Qty", "Unit Price", "Line Total"]]
    for i, li in enumerate(spec.line_items, 1):
        unit = "" if li.unit_price is None else inr(li.unit_price)
        rows.append([str(i), li.description, str(li.quantity), unit, inr(li.line_total)])
    item_tbl = Table(rows, colWidths=[10 * mm, 80 * mm, 16 * mm, 35 * mm, 35 * mm])
    item_tbl.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#1f3a5f")),
        ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
        ("FONTSIZE", (0, 0), (-1, -1), 9),
        ("ALIGN", (2, 0), (-1, -1), "RIGHT"),
        ("ALIGN", (0, 0), (0, -1), "CENTER"),
        ("GRID", (0, 0), (-1, -1), 0.4, colors.HexColor("#cccccc")),
        ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#f4f6f9")]),
        ("TOPPADDING", (0, 0), (-1, -1), 5),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
    ]))
    story.append(item_tbl)
    story.append(Spacer(1, 5 * mm))

    totals = []
    if spec.tax_treatment == "separated":
        totals.append(["Subtotal (pre-tax):", inr(spec.subtotal)])
        totals.append([f"GST @ {spec.tax_rate:g}%:", inr(spec.tax_amount)])
        totals.append(["Total:", inr(spec.total)])
    else:
        totals.append(["Total:", inr(spec.total)])
    totals_tbl = Table(totals, colWidths=[45 * mm, 40 * mm], hAlign="RIGHT")
    totals_tbl.setStyle(TableStyle([
        ("FONTSIZE", (0, 0), (-1, -1), 10),
        ("ALIGN", (1, 0), (1, -1), "RIGHT"),
        ("TOPPADDING", (0, 0), (-1, -1), 3),
        ("LINEABOVE", (0, -1), (-1, -1), 0.8, colors.black),
        ("FONTNAME", (0, -1), (-1, -1), "Helvetica-Bold"),
    ]))
    story.append(totals_tbl)

    if spec.footer_note:
        story.append(Paragraph(spec.footer_note, ss["FooterNote"]))

    story.append(Spacer(1, 10 * mm))
    story.append(Paragraph("Thank you for your business.", ss["Small"]))
    return story


def _render_pdf_bytes(spec: InvoiceSpec) -> bytes:
    buf = io.BytesIO()
    doc = SimpleDocTemplate(
        buf, pagesize=A4,
        leftMargin=18 * mm, rightMargin=18 * mm,
        topMargin=18 * mm, bottomMargin=18 * mm,
        title=spec.invoice_number,
    )
    doc.build(_build_story(spec))
    return buf.getvalue()


def _rasterise_to_image_pdf(pdf_bytes: bytes) -> bytes:
    """Rasterise a text PDF into an image-only PDF with no text layer.

    Applies a light blur and slight skew to simulate scan artefacts.
    """
    src = fitz.open(stream=pdf_bytes, filetype="pdf")
    out = fitz.open()
    zoom = config.RASTER_DPI / 72.0
    mat = fitz.Matrix(zoom, zoom)
    for page in src:
        pix = page.get_pixmap(matrix=mat)
        img = Image.frombytes("RGB", (pix.width, pix.height), pix.samples)
        img = img.rotate(-0.7, expand=True, fillcolor=(255, 255, 255))
        img = img.filter(ImageFilter.GaussianBlur(0.6))
        img_buf = io.BytesIO()
        img.save(img_buf, format="JPEG", quality=80)
        rect = fitz.Rect(0, 0, img.width / zoom, img.height / zoom)
        new_page = out.new_page(width=rect.width, height=rect.height)
        new_page.insert_image(rect, stream=img_buf.getvalue())
    return out.tobytes()


def write_invoice(spec: InvoiceSpec) -> str:
    config.INPUTS_DIR.mkdir(parents=True, exist_ok=True)
    pdf_bytes = _render_pdf_bytes(spec)
    if spec.scanned:
        pdf_bytes = _rasterise_to_image_pdf(pdf_bytes)
    path = config.INPUTS_DIR / spec.filename
    path.write_bytes(pdf_bytes)
    return str(path)


# --------------------------------------------------------------------------- #
# Invoice specs
# --------------------------------------------------------------------------- #
def _items(*triples) -> list[LineItem]:
    return [LineItem(d, q, u, q * u) for (d, q, u) in triples]


def _separated(subtotal: float, rate: float = 18):
    tax = round(subtotal * rate / 100, 2)
    return tax, round(subtotal + tax, 2)


def build_specs() -> list[InvoiceSpec]:
    specs: list[InvoiceSpec] = []

    # normal_01 — Logitech / PO-4422 — text, two itemised lines
    items = _items(("Wireless Mouse M185", 50, 700), ("Wireless Keyboard K380", 30, 1500))
    sub = sum(i.line_total for i in items)
    tax, tot = _separated(sub)
    specs.append(InvoiceSpec("normal_01.pdf", "Logitech India", "INV-2026-0101",
                             "2026-05-04", "PO-4422", items, "separated", 18, sub, tax, tot))

    # normal_02 — CloudHost / PO-4423 — image-only, single line
    items = _items(("Annual Cloud Hosting — Standard Plan", 1, 240000))
    sub = sum(i.line_total for i in items)
    tax, tot = _separated(sub)
    specs.append(InvoiceSpec("normal_02.pdf", "CloudHost Solutions", "INV-2026-0102",
                             "2026-05-06", "PO-4423", items, "separated", 18, sub, tax, tot,
                             scanned=True))

    # normal_03 — Acme Office Supplies / PO-4424 — text, many small lines
    items = _items(
        ("A4 Paper Ream (500 sheets)", 20, 250),
        ("Ballpoint Pen (box of 50)", 15, 120),
        ("Heavy-duty Stapler", 10, 180),
        ("Sticky Notes Pad", 40, 60),
        ("Laser Printer Toner Cartridge", 8, 3200),
        ("Manila File Folder", 50, 45),
    )
    sub = sum(i.line_total for i in items)
    tax, tot = _separated(sub)
    specs.append(InvoiceSpec("normal_03.pdf", "Acme Office Supplies", "INV-2026-0103",
                             "2026-05-09", "PO-4424", items, "separated", 18, sub, tax, tot))

    # normal_04 — BlueOak Furniture / PO-4426 — image-only, round amounts
    items = _items(("Workstation Desk 1.4m (Oak)", 8, 40000))
    sub = sum(i.line_total for i in items)
    tax, tot = _separated(sub)
    specs.append(InvoiceSpec("normal_04.pdf", "BlueOak Furniture", "INV-2026-0104",
                             "2026-05-11", "PO-4426", items, "separated", 18, sub, tax, tot,
                             scanned=True))

    # normal_05 — Nimbus Software Labs / PO-4427 — text, matches PO exactly
    items = _items(("Nimbus Pro License (annual seat)", 20, 7500))
    sub = sum(i.line_total for i in items)
    tax, tot = _separated(sub)
    specs.append(InvoiceSpec("normal_05.pdf", "Nimbus Software Labs", "INV-2026-0105",
                             "2026-05-13", "PO-4427", items, "separated", 18, sub, tax, tot))

    # edge_scanned — FastFreight / PO-4425 — image-only, itemised, separated tax
    items = _items(
        ("Freight — Mumbai to Delhi (FTL)", 15, 4500),
        ("Freight — Delhi to Bangalore (FTL)", 10, 5200),
    )
    sub = sum(i.line_total for i in items)
    tax, tot = _separated(sub)
    specs.append(InvoiceSpec("edge_scanned.pdf", "FastFreight Logistics", "INV-2026-0201",
                             "2026-05-16", "PO-4425", items, "separated", 18, sub, tax, tot,
                             scanned=True))

    # edge_bundled — Dell / PO-4421 — bundled line, no per-component price
    items = [LineItem("Laptop + headphone bundle", 5, None, 450000)]
    sub = 450000
    tax, tot = _separated(sub)
    specs.append(InvoiceSpec("edge_bundled.pdf", "Dell Technologies", "INV-2026-0202",
                             "2026-05-14", "PO-4421", items, "separated", 18, sub, tax, tot))

    # edge_embedded_tax — Surya / PO-4429 — tax-inclusive prices, no tax line
    items = _items(
        ("Premium Hardbound Notebook A5", 80, 295),
        ("Executive Gel Pen", 100, 118),
        ("Sticky Note Cube (400 sheets)", 100, 118),
    )
    total_incl = sum(i.line_total for i in items)
    specs.append(InvoiceSpec("edge_embedded_tax.pdf", "Surya Stationers", "INV-2026-0203",
                             "2026-05-18", "PO-4429", items, "embedded", 18,
                             subtotal=total_incl, tax_amount=None, total=total_incl,
                             footer_note="<b>Note:</b> All prices are inclusive of 18% GST."))

    return specs


def generate_all() -> list[str]:
    paths = []
    for spec in build_specs():
        path = write_invoice(spec)
        kind = "image-only" if spec.scanned else "text     "
        print(f"  [{kind}] {spec.filename:24s} {spec.vendor_name}")
        paths.append(path)
    return paths


if __name__ == "__main__":
    print(f"Generating invoices into {config.INPUTS_DIR} ...")
    paths = generate_all()
    print(f"Done — {len(paths)} PDFs written.")
