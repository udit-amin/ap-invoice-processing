# AP Invoice Processing — Extraction Module

Turn a supplier invoice PDF into clean, confidence-scored JSON. This is the
extraction layer of a larger AP automation pipeline — it handles both
machine-readable and image-only (scanned) PDFs, itemised and bundled line items,
and separated or embedded tax.

## What's here

```
src/
  extract/
    api.py                FastAPI service for the extraction module
  config.py               Model string, thresholds, paths
  db/
    schema.sql            SQLite table definitions
    seed.py               Populate vendors, POs, policy_config
  extract/
    ingest.py             Detect text vs image-only PDF
    text_extract.py       pdfplumber path
    vision_extract.py     PyMuPDF → base64 path
    prompts.py            Cached system instructions for Claude
    extractor.py          Orchestrator — PDF in, JSON out
  generate/
    invoice_generator.py  Synthetic test PDFs (5 text + 3 image-only)
tests/
  test_extraction.py      Schema + behaviour assertions across all 8 inputs
```

## Setup

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
```

Provide an Anthropic API key (read from the environment or an untracked `.env`
file that is gitignored):

```bash
export ANTHROPIC_API_KEY=sk-ant-...
# or:  echo 'ANTHROPIC_API_KEY=sk-ant-...' > .env
```

Seed the database and generate the test invoices:

```bash
python -m src.db.seed
python -m src.generate.invoice_generator
```

## Running the API

```bash
uvicorn src.extract.api:app --reload
```

- **Interactive docs:** http://localhost:8000/docs
- **Health check:** `GET /health`
- **Extract an invoice:** `POST /extract` with a PDF as `multipart/form-data`

```bash
curl -s -X POST http://localhost:8000/extract \
  -F "file=@data/inputs/edge_bundled.pdf" | python3 -m json.tool

# To start the server:
uvicorn src.extract.api:app --reload
```

Every successful extraction is logged to the `run_log` table in `data/invoices.db`.

## Extraction pipeline

```
PDF
  → ingest.detect_type()        "text" | "scanned"
  → text_extract.get_text()     machine-readable path (pdfplumber)
    or vision_extract.get_images()  image-only path (PyMuPDF → base64)
  → extractor.extract()         Claude call → parse → normalise
  → JSON (always same shape, error field distinguishes success/failure)
```

Two core challenges it handles:

- **Bundled lines** — a single line at one combined price with no per-component
  breakdown is emitted as `is_bundle: true` with `unit_price: null`. Prices are
  never invented.
- **Embedded tax** — tax-inclusive prices with no tax line are detected, the
  implied tax is back-calculated from the stated rate, and confidence is lowered
  to flag that the value was inferred.

## Output schema (always this shape, missing values are `null`)

```json
{
  "source_type": "text | scanned",
  "invoice_number": "INV-2026-0042",
  "vendor_name": "Dell Technologies",
  "invoice_date": "2026-05-14",
  "po_reference": "PO-4421",
  "currency": "INR",
  "line_items": [
    {
      "description": "Laptop + headphone bundle",
      "quantity": 5,
      "unit_price": null,
      "line_total": 450000,
      "is_bundle": true,
      "bundle_components": ["Laptop", "Headphones"]
    }
  ],
  "subtotal": 450000,
  "tax": { "amount": 81000, "rate_pct": 18, "treatment": "separated" },
  "total": 531000,
  "extraction_confidence": {
    "invoice_number": 0.98, "vendor_name": 0.96,
    "po_reference": 0.91, "total": 0.94, "overall": 0.90
  },
  "extraction_notes": ["Line 1 is bundled; could not separate unit prices."],
  "error": null
}
```

`CONFIDENCE_THRESHOLD` (0.80) is surfaced in API responses. The routing logic
that acts on it (escalate to human review) is a later module.

## Test invoices

| File | Vendor | PO | Format |
|------|--------|----|--------|
| `normal_01.pdf` | Logitech India | PO-4422 | Text — two itemised lines |
| `normal_02.pdf` | CloudHost Solutions | PO-4423 | **Image-only** — single line |
| `normal_03.pdf` | Acme Office Supplies | PO-4424 | Text — many small lines |
| `normal_04.pdf` | BlueOak Furniture | PO-4426 | **Image-only** — round amounts |
| `normal_05.pdf` | Nimbus Software Labs | PO-4427 | Text — matches PO exactly |
| `edge_scanned.pdf` | FastFreight Logistics | PO-4425 | **Image-only** — lower confidence |
| `edge_bundled.pdf` | Dell Technologies | PO-4421 | Text — bundled line, no unit price |
| `edge_embedded_tax.pdf` | Surya Stationers | PO-4429 | Text — tax-inclusive, back-calculated |

## Tests

```bash
pytest tests/test_extraction.py -v
```

Live-model tests skip cleanly when `ANTHROPIC_API_KEY` is not set. The API
tests (health, validation, schema shape) always run via FastAPI's test client.

## Stack

Python 3.11+ · FastAPI + uvicorn · anthropic SDK (Sonnet, prompt caching) ·
SQLite (stdlib) · pdfplumber · PyMuPDF + Pillow · reportlab · pytest + httpx
