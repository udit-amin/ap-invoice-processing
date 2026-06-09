"""Extraction orchestrator — PDF path in, structured JSON out.

Pipeline:
    detect_type -> (text | images) -> Claude -> parse -> normalise to schema.

Always returns the full schema with every key present (missing = null). On any
failure (no API key, API error, unparseable response) it returns a structured
error object rather than raising. The ``error`` field distinguishes success from
failure. This function is the seam later stages (matching/decisioning) plug into.
"""
from __future__ import annotations

import json

import anthropic

from src import config
from src.extract import ingest, text_extract, vision_extract, prompts

# Critical fields that always carry a confidence score (handover §5.4).
_CONF_FIELDS = ["invoice_number", "vendor_name", "po_reference", "total", "overall"]


# --------------------------------------------------------------------------- #
# Public entry point
# --------------------------------------------------------------------------- #
def extract(path) -> dict:
    """Extract structured data from one invoice PDF. Never raises."""
    try:
        source_type = ingest.detect_type(path)
    except Exception as exc:  # unreadable PDF
        return _error_result(None, f"Could not open/inspect PDF: {exc}")

    api_key = config.get_api_key()
    if not api_key:
        return _error_result(source_type, "ANTHROPIC_API_KEY not set; cannot call the model.")

    try:
        if source_type == "scanned":
            user_content = _vision_content(path)
        else:
            user_content = _text_content(path)
    except Exception as exc:
        return _error_result(source_type, f"Failed to prepare invoice content: {exc}")

    try:
        raw = _call_claude(api_key, user_content)
    except Exception as exc:
        return _error_result(source_type, f"Model call failed: {exc}")

    parsed = _parse_json(raw)
    if parsed is None:
        return _error_result(source_type, "Model did not return valid JSON.",
                             raw_excerpt=raw[:500])

    return normalize(parsed, source_type)


# --------------------------------------------------------------------------- #
# Content builders
# --------------------------------------------------------------------------- #
def _text_content(path) -> list[dict]:
    text = text_extract.get_text(path)
    return [{"type": "text", "text": prompts.TEXT_USER_PREFIX + text}]


def _vision_content(path) -> list[dict]:
    images = vision_extract.get_images(path)
    content: list[dict] = [{"type": "text", "text": prompts.VISION_USER_PREFIX}]
    for img in images:
        content.append({
            "type": "image",
            "source": {
                "type": "base64",
                "media_type": img["media_type"],
                "data": img["data"],
            },
        })
    return content


def _call_claude(api_key: str, user_content: list[dict]) -> str:
    client = anthropic.Anthropic(api_key=api_key)
    resp = client.messages.create(
        model=config.MODEL,
        max_tokens=config.MAX_TOKENS,
        # Static instructions sent as a cacheable system prompt.
        system=[{
            "type": "text",
            "text": prompts.SYSTEM_INSTRUCTIONS,
            "cache_control": {"type": "ephemeral"},
        }],
        messages=[{"role": "user", "content": user_content}],
    )
    return "".join(block.text for block in resp.content if block.type == "text")


# --------------------------------------------------------------------------- #
# Parsing & normalisation
# --------------------------------------------------------------------------- #
def _parse_json(raw: str):
    """Parse model output defensively: strip stray fences, then json.loads."""
    if not raw:
        return None
    text = raw.strip()
    if text.startswith("```"):
        # drop the opening fence (``` or ```json) and the closing fence
        text = text.split("\n", 1)[1] if "\n" in text else text
        if text.rstrip().endswith("```"):
            text = text.rstrip()[:-3]
    text = text.strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        # last resort: grab the outermost {...}
        start, end = text.find("{"), text.rfind("}")
        if start != -1 and end > start:
            try:
                return json.loads(text[start:end + 1])
            except json.JSONDecodeError:
                return None
        return None


def normalize(data: dict, source_type: str) -> dict:
    """Coerce a parsed payload into the full §5.3 shape. Missing -> null."""
    data = data if isinstance(data, dict) else {}

    line_items = []
    for li in data.get("line_items") or []:
        if not isinstance(li, dict):
            continue
        line_items.append({
            "description": li.get("description"),
            "quantity": li.get("quantity"),
            "unit_price": li.get("unit_price"),
            "line_total": li.get("line_total"),
            "is_bundle": bool(li.get("is_bundle", False)),
            "bundle_components": li.get("bundle_components") or [],
        })

    tax = data.get("tax") or {}
    conf_in = data.get("extraction_confidence") or {}
    confidence = {f: conf_in.get(f) for f in _CONF_FIELDS}

    notes = data.get("extraction_notes") or []
    if isinstance(notes, str):
        notes = [notes]

    return {
        "source_type": source_type,  # trust pipeline detection over the model
        "invoice_number": data.get("invoice_number"),
        "vendor_name": data.get("vendor_name"),
        "invoice_date": data.get("invoice_date"),
        "po_reference": data.get("po_reference"),
        "currency": data.get("currency"),
        "line_items": line_items,
        "subtotal": data.get("subtotal"),
        "tax": {
            "amount": tax.get("amount"),
            "rate_pct": tax.get("rate_pct"),
            "treatment": tax.get("treatment"),
        },
        "total": data.get("total"),
        "extraction_confidence": confidence,
        "extraction_notes": list(notes),
        "error": None,
    }


def _error_result(source_type, message: str, raw_excerpt: str | None = None) -> dict:
    """Structured failure object matching the §5.3 shape (all keys present)."""
    notes = [f"extraction failed: {message}"]
    if raw_excerpt:
        notes.append(f"raw model output (truncated): {raw_excerpt}")
    return {
        "source_type": source_type,
        "invoice_number": None,
        "vendor_name": None,
        "invoice_date": None,
        "po_reference": None,
        "currency": None,
        "line_items": [],
        "subtotal": None,
        "tax": {"amount": None, "rate_pct": None, "treatment": None},
        "total": None,
        "extraction_confidence": {f: 0.0 for f in _CONF_FIELDS},
        "extraction_notes": notes,
        "error": message,
    }


