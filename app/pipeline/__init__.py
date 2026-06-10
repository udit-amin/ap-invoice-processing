"""Pipeline package: the end-to-end orchestrator plus its HTTP router.

`process_invoice` is re-exported here so callers can keep using
`from app.pipeline import process_invoice` (or `from app import pipeline;
pipeline.process_invoice(...)`) after the v3 restructure moved the orchestrator
into this package.
"""
from app.pipeline.orchestrator import process_invoice

__all__ = ["process_invoice"]
