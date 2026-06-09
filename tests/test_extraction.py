"""Extraction tests over all 8 generated invoices.

Asserts the output schema for every input plus the per-invoice behaviours:
normals extract cleanly at high confidence; image-only invoices route to the
vision path; the bundle is flagged without hallucinated prices; embedded tax is
detected, back-calculated and noted.

Live-API tests skip cleanly when ANTHROPIC_API_KEY is not set.
"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from src import config
from src.extract.api import app
from src.extract import ingest
from src.extract.extractor import extract, normalize
from src.generate.invoice_generator import build_specs, generate_all

NORMALS = [f"normal_0{i}.pdf" for i in range(1, 6)]
EDGES   = ["edge_scanned.pdf", "edge_bundled.pdf", "edge_embedded_tax.pdf"]
ALL     = NORMALS + EDGES

# Derive expected path (text vs scanned) from the generator spec — the single
# source of truth — so the test stays correct as the mix changes.
_SPECS  = {s.filename: s for s in build_specs()}
SCANNED = [f for f in ALL if _SPECS[f].scanned]
TEXT    = [f for f in ALL if not _SPECS[f].scanned]

requires_api = pytest.mark.skipif(
    config.get_api_key() is None,
    reason="ANTHROPIC_API_KEY not set — live extraction tests skipped.",
)


# --------------------------------------------------------------------------- #
# Fixtures
# --------------------------------------------------------------------------- #
@pytest.fixture(scope="session", autouse=True)
def _ensure_inputs():
    missing = [f for f in ALL if not (config.INPUTS_DIR / f).exists()]
    if missing:
        generate_all()
    yield


@pytest.fixture(scope="session")
def results():
    if config.get_api_key() is None:
        pytest.skip("ANTHROPIC_API_KEY not set — live extraction tests skipped.")
    return {f: extract(str(config.INPUTS_DIR / f)) for f in ALL}


@pytest.fixture(scope="session")
def api_client():
    return TestClient(app)


# --------------------------------------------------------------------------- #
# Non-API / unit tests (always run)
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("fname", ALL)
def test_inputs_exist(fname):
    assert (config.INPUTS_DIR / fname).exists()


def test_type_detection_routes_correctly():
    for f in TEXT:
        assert ingest.detect_type(str(config.INPUTS_DIR / f)) == "text", f
    for f in SCANNED:
        assert ingest.detect_type(str(config.INPUTS_DIR / f)) == "scanned", f


def test_normalize_fills_full_schema_from_empty():
    out = normalize({}, "text")
    _assert_schema(out)
    assert out["invoice_number"] is None
    assert out["line_items"] == []
    assert out["tax"]["treatment"] is None


# --------------------------------------------------------------------------- #
# API tests (no live model needed)
# --------------------------------------------------------------------------- #
def test_health(api_client):
    r = api_client.get("/health")
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "ok"
    assert "model" in body
    assert "api_key_set" in body


def test_extract_rejects_non_pdf(api_client):
    r = api_client.post(
        "/extract",
        files={"file": ("invoice.txt", b"not a pdf", "text/plain")},
    )
    assert r.status_code == 422


def test_extract_rejects_empty_file(api_client):
    r = api_client.post(
        "/extract",
        files={"file": ("empty.pdf", b"", "application/pdf")},
    )
    assert r.status_code == 422


@requires_api
def test_extract_endpoint_returns_schema(api_client):
    path = config.INPUTS_DIR / "normal_01.pdf"
    with open(path, "rb") as f:
        r = api_client.post("/extract", files={"file": ("normal_01.pdf", f, "application/pdf")})
    assert r.status_code == 200
    _assert_schema(r.json())


# --------------------------------------------------------------------------- #
# Live extractor tests (require API key)
# --------------------------------------------------------------------------- #
@requires_api
@pytest.mark.parametrize("fname", ALL)
def test_schema_shape_all_inputs(results, fname):
    _assert_schema(results[fname])


@requires_api
@pytest.mark.parametrize("fname", TEXT)
def test_text_invoices_use_text_path(results, fname):
    assert results[fname]["source_type"] == "text"


@requires_api
@pytest.mark.parametrize("fname", SCANNED)
def test_scanned_invoices_use_vision_path(results, fname):
    r = results[fname]
    assert r["error"] is None, r["extraction_notes"]
    assert r["source_type"] == "scanned"
    assert r["invoice_number"] is not None
    assert r["po_reference"] is not None


@requires_api
@pytest.mark.parametrize("fname", NORMALS)
def test_normals_clean_and_confident(results, fname):
    r = results[fname]
    assert r["error"] is None, r["extraction_notes"]
    assert r["tax"]["treatment"] == "separated"
    assert r["invoice_number"] is not None
    assert r["vendor_name"] is not None
    assert r["po_reference"] is not None
    assert r["total"] is not None
    assert r["extraction_confidence"]["overall"] >= config.CONFIDENCE_THRESHOLD
    assert all(not li["is_bundle"] for li in r["line_items"])


@requires_api
def test_scanned_lower_confidence_than_text(results):
    text_overall    = [results[f]["extraction_confidence"]["overall"] for f in TEXT]
    scanned_overall = [results[f]["extraction_confidence"]["overall"] for f in SCANNED]
    assert sum(scanned_overall) / len(scanned_overall) < sum(text_overall) / len(text_overall)


@requires_api
def test_edge_bundled_flagged_not_hallucinated(results):
    r = results["edge_bundled.pdf"]
    assert r["error"] is None, r["extraction_notes"]
    assert len(r["line_items"]) == 1
    li = r["line_items"][0]
    assert li["is_bundle"] is True
    assert li["unit_price"] is None
    comps = " ".join(li["bundle_components"]).lower()
    assert "laptop" in comps and "headphone" in comps
    assert any("bundle" in n.lower() for n in r["extraction_notes"])


@requires_api
def test_edge_embedded_tax_inferred_and_noted(results):
    r = results["edge_embedded_tax.pdf"]
    assert r["error"] is None, r["extraction_notes"]
    assert r["tax"]["treatment"] == "embedded"
    assert r["tax"]["amount"] is not None
    assert abs(r["tax"]["amount"] - 7200) < 50   # 47,200 − 47,200/1.18 ≈ 7,200
    assert any("tax" in n.lower() for n in r["extraction_notes"])


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #
def _assert_schema(r: dict):
    top_keys = {
        "source_type", "invoice_number", "vendor_name", "invoice_date",
        "po_reference", "currency", "line_items", "subtotal", "tax", "total",
        "extraction_confidence", "extraction_notes",
    }
    assert top_keys.issubset(r.keys()), f"missing keys: {top_keys - set(r.keys())}"
    assert isinstance(r["line_items"], list)
    for li in r["line_items"]:
        assert {"description", "quantity", "unit_price", "line_total",
                "is_bundle", "bundle_components"}.issubset(li.keys())
        assert isinstance(li["is_bundle"], bool)
        assert isinstance(li["bundle_components"], list)
    assert {"amount", "rate_pct", "treatment"}.issubset(r["tax"].keys())
    for f in ["invoice_number", "vendor_name", "po_reference", "total", "overall"]:
        assert f in r["extraction_confidence"]
    assert isinstance(r["extraction_notes"], list)
