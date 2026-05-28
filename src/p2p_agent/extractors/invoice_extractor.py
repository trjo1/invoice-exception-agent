"""Invoice extractor — pulls structured fields from a PDF.

Strategy: read PDF text via pypdf, send to the LLM with the
`invoice_extraction` prompt, parse JSON into an `InvoiceExtraction` pydantic
instance. One retry with a stricter reminder if the first parse fails.

Reads only the text layer. Scanned / image-only PDFs are out of scope for
v1 — when those land (e.g., from a design partner), add a vision-capable
model path here.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
from pathlib import Path
from typing import Any

from pydantic import ValidationError
from pypdf import PdfReader

from p2p_agent.llm.client import ModelClient
from p2p_agent.llm.json_utils import extract_json_from_response
from p2p_agent.llm.prompts import load_prompt
from p2p_agent.models.extraction import (
    HeaderFieldsExtraction,
    InvoiceExtraction,
    LineItemExtraction,
    TaxLineExtraction,
)

logger = logging.getLogger(__name__)


class ExtractorError(Exception):
    """Raised when PDF text or LLM output cannot be turned into an InvoiceExtraction."""


SYSTEM_PROMPT = load_prompt("invoice_extraction")


# --- Content-hashed extraction cache --------------------------------------
#
# Extract is by far the slowest node (6-130s on OpenRouter, depending on the
# day). For demos especially, the same sample PDF gets uploaded repeatedly —
# rehearsal, live run, post-meeting replay. Caching the parsed JSON keyed on
# the PDF's SHA256 turns the 2nd-Nth identical upload into a memory lookup.
#
# Cache scope: process-local. Survives within one server run; reset on
# restart. SHA256 of the raw PDF bytes is the cache key.
#
# Disable with `EXTRACTION_CACHE=0` for measurement/eval runs that need
# a real LLM call every time.

_EXTRACTION_CACHE: dict[str, InvoiceExtraction] = {}


def _cache_enabled() -> bool:
    return os.environ.get("EXTRACTION_CACHE", "1") != "0"


def _pdf_sha256(pdf_path: Path) -> str:
    h = hashlib.sha256()
    with pdf_path.open("rb") as f:
        for chunk in iter(lambda: f.read(64 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def clear_extraction_cache() -> None:
    """Drop every cached extraction. Used by tests + `make clean`."""
    _EXTRACTION_CACHE.clear()


def extraction_cache_stats() -> dict[str, int]:
    """Surface size of the in-process cache for the demo / Stage 9 view."""
    return {"entries": len(_EXTRACTION_CACHE)}


def _read_pdf_text(pdf_path: Path) -> str:
    """Extract concatenated text from every page of the PDF."""
    try:
        reader = PdfReader(str(pdf_path))
    except Exception as e:  # noqa: BLE001
        raise ExtractorError(f"Failed to open PDF {pdf_path}: {e}") from e

    pages: list[str] = []
    for page in reader.pages:
        try:
            pages.append(page.extract_text() or "")
        except Exception as e:  # noqa: BLE001
            raise ExtractorError(f"Failed to extract text from {pdf_path}: {e}") from e

    text = "\n\n--- PAGE BREAK ---\n\n".join(p for p in pages if p)
    if not text.strip():
        raise ExtractorError(
            f"PDF {pdf_path.name} has no extractable text layer. "
            "Likely a scanned / image-only PDF — vision model path not yet built.",
        )
    return text


def _build_user_message(pdf_text: str, source_hint: str | None) -> str:
    header = "Extract the fields from this invoice PDF. The text below was extracted from the PDF text layer."
    if source_hint:
        header += f" Source: {source_hint}."
    return header + "\n\n```\n" + pdf_text.strip() + "\n```"


def _coerce_extraction(raw: Any) -> InvoiceExtraction:
    if not isinstance(raw, dict):
        raise ExtractorError(f"Expected JSON object, got {type(raw).__name__}")

    # Best-effort cleanup of common output quirks before validation.
    header = raw.get("header_fields") or {}
    if not isinstance(header, dict):
        header = {}

    line_items = raw.get("line_items") or []
    if not isinstance(line_items, list):
        line_items = []

    tax_lines = raw.get("tax") or []
    if not isinstance(tax_lines, list):
        tax_lines = []

    field_confidence = raw.get("field_confidence") or {}
    if not isinstance(field_confidence, dict):
        field_confidence = {}
    field_confidence = {
        str(k): float(v) for k, v in field_confidence.items()
        if isinstance(v, (int, float))
    }

    payload = {
        "invoice_id": str(raw.get("invoice_id") or ""),
        "po_reference": str(raw.get("po_reference") or ""),
        "invoice_date": str(raw.get("invoice_date") or ""),
        "currency": str(raw.get("currency") or ""),
        "payment_terms": str(raw.get("payment_terms") or ""),
        "header_fields": {
            "vendor_name": str(header.get("vendor_name") or ""),
            "vendor_address": str(header.get("vendor_address") or ""),
            "vendor_tax_id": str(header.get("vendor_tax_id") or ""),
            "buyer_name": str(header.get("buyer_name") or ""),
            "buyer_address": str(header.get("buyer_address") or ""),
            "buyer_po_contact": str(header.get("buyer_po_contact") or ""),
        },
        "line_items": [_coerce_line_item(li, i + 1) for i, li in enumerate(line_items)],
        "subtotal": float(raw.get("subtotal") or 0.0),
        "tax": [_coerce_tax_line(t) for t in tax_lines if isinstance(t, dict)],
        "total": float(raw.get("total") or 0.0),
        "field_confidence": field_confidence,
    }
    try:
        return InvoiceExtraction.model_validate(payload)
    except ValidationError as e:
        raise ExtractorError(f"Model output failed pydantic validation: {e}") from e


def _coerce_line_item(li: dict, default_line_no: int) -> dict:
    return {
        "line_no": int(li.get("line_no") or default_line_no),
        "sku": str(li.get("sku") or ""),
        "description": str(li.get("description") or ""),
        "quantity": float(li.get("quantity") or 0.0),
        "unit_price": float(li.get("unit_price") or 0.0),
        "line_total": float(li.get("line_total") or 0.0),
    }


def _coerce_tax_line(t: dict) -> dict:
    return {
        "jurisdiction": str(t.get("jurisdiction") or ""),
        "rate": float(t.get("rate") or 0.0),
        "amount": float(t.get("amount") or 0.0),
    }


async def extract_invoice(
    *,
    pdf_path: Path,
    client: ModelClient | None = None,
    case_id: str | None = None,
) -> InvoiceExtraction:
    """Extract structured fields from one invoice PDF.

    Returns a validated `InvoiceExtraction`. Raises `ExtractorError` if the PDF
    has no text layer or the model output can't be parsed after one retry.
    """
    client = client or ModelClient()

    # Content-hashed cache: same PDF bytes → same extraction. Skips the 6-130s
    # LLM call entirely. Critical for demos where the same sample is run
    # multiple times within one server lifetime.
    pdf_hash: str | None = None
    if _cache_enabled():
        try:
            pdf_hash = _pdf_sha256(pdf_path)
        except OSError:
            pdf_hash = None
        if pdf_hash is not None:
            cached = _EXTRACTION_CACHE.get(pdf_hash)
            if cached is not None:
                logger.info(
                    "extraction_cache_hit",
                    extra={"case_id": case_id, "pdf_hash": pdf_hash[:12]},
                )
                # Return a deep-copy via pydantic so callers can mutate freely.
                return cached.model_copy(deep=True)

    pdf_text = _read_pdf_text(pdf_path)
    user_msg = _build_user_message(pdf_text, source_hint=pdf_path.name)

    messages = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": user_msg},
    ]

    result = await client.complete(
        task="invoice_extraction",
        messages=messages,
        temperature=0.0,
        max_tokens=2500,
        case_id=case_id,
    )

    try:
        parsed = extract_json_from_response(result.output_text)
        extraction = _coerce_extraction(parsed)
        if pdf_hash is not None:
            _EXTRACTION_CACHE[pdf_hash] = extraction
        return extraction
    except ValueError as first_err:
        # Only retry on JSON parse failure. Schema validation errors
        # (ExtractorError) bubble — re-asking rarely fixes them and adds
        # 30-130s of pure latency.
        retry_messages = [
            *messages,
            {"role": "assistant", "content": result.output_text},
            {
                "role": "user",
                "content": (
                    "Your previous response could not be parsed. Reply with ONLY a "
                    "single JSON object wrapped in a ```json code block, matching "
                    "the schema in the system prompt exactly. No prose outside the "
                    "JSON."
                ),
            },
        ]
        retry = await client.complete(
            task="invoice_extraction",
            messages=retry_messages,
            temperature=0.0,
            max_tokens=2500,
            case_id=f"{case_id}::retry" if case_id else None,
        )
        try:
            parsed = extract_json_from_response(retry.output_text)
        except ValueError as e:
            raise ExtractorError(
                f"Model output unparseable on retry. First error: {first_err}",
            ) from e
        extraction = _coerce_extraction(parsed)
        if pdf_hash is not None:
            _EXTRACTION_CACHE[pdf_hash] = extraction
        return extraction
