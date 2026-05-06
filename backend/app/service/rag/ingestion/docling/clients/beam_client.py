"""
Beam Docling client helpers: endpoint call, response decoding, and layout prep.
"""

from __future__ import annotations

import base64
import json
import os
from typing import Any

import requests

from app.service.rag.ingestion.docling.config import (
    BEAM_DOCLING_CLIENT_MAX_FILE_SIZE_MB,
    BEAM_DOCLING_TIMEOUT_SECONDS,
)
from app.service.rag.ingestion.docling.models import DoclingChunkFailure
from app.service.rag.ingestion.docling.storage.local_artifacts_store import (
    stringify_endpoint_error,
)
from app.service.rag.ingestion.docling.utils import pdf_utils


def _load_docling_module_runtime() -> dict[str, Any]:
    """
    Lazy import only the Docling client-side types needed to reconstruct endpoint JSON
    and serialize markdown locally.
    """

    from docling_core.transforms.serializer.markdown import MarkdownDocSerializer
    from docling_core.types.doc import (
        DoclingDocument,
        ListItem,
        PictureItem,
        SectionHeaderItem,
        TableItem,
        TitleItem,
    )

    return {
        "MarkdownDocSerializer": MarkdownDocSerializer,
        "DoclingDocument": DoclingDocument,
        "ListItem": ListItem,
        "PictureItem": PictureItem,
        "SectionHeaderItem": SectionHeaderItem,
        "TableItem": TableItem,
        "TitleItem": TitleItem,
    }


def _load_beam_docling_config() -> dict[str, Any]:
    """
    Load required Beam Docling endpoint configuration from environment variables.
    """

    endpoint = (os.getenv("BEAM_DOCLING_ENDPOINT") or "").strip()
    token = (os.getenv("BEAM_DOCLING_ENDPOINT_TOKEN") or "").strip()
    if not endpoint:
        raise RuntimeError("BEAM_DOCLING_ENDPOINT is not configured.")
    if not token:
        raise RuntimeError("BEAM_DOCLING_ENDPOINT_TOKEN is not configured.")
    return {
        "endpoint": endpoint,
        "token": token,
        "timeout_seconds": BEAM_DOCLING_TIMEOUT_SECONDS,
        "max_file_size_mb": BEAM_DOCLING_CLIENT_MAX_FILE_SIZE_MB,
    }


def _extract_document_dump(result: dict[str, Any]) -> dict[str, Any] | None:
    """
    Read Docling document JSON from either `document_dump` or legacy conversion dump.
    """

    document_dump = result.get("document_dump")
    if isinstance(document_dump, dict):
        return document_dump

    conversion_result_dump = result.get("conversion_result_dump")
    if isinstance(conversion_result_dump, dict):
        nested_document = conversion_result_dump.get("document")
        if isinstance(nested_document, dict):
            return nested_document

    return None


def _parse_beam_response_json(response: requests.Response, raw_body: str) -> dict[str, Any]:
    """
    Parse Beam response JSON defensively to tolerate parser differences and
    occasional trailing non-JSON content in valid responses.
    """

    parse_errors: list[str] = []

    try:
        parsed = response.json()
        if isinstance(parsed, dict):
            return parsed
        parse_errors.append(
            f"response.json() returned {type(parsed).__name__}, expected object"
        )
    except Exception as exc:
        parse_errors.append(f"response.json(): {type(exc).__name__}: {exc}")

    try:
        parsed = json.loads(raw_body)
        if isinstance(parsed, dict):
            return parsed
        parse_errors.append(
            f"json.loads(raw_body) returned {type(parsed).__name__}, expected object"
        )
    except Exception as exc:
        parse_errors.append(f"json.loads(raw_body): {type(exc).__name__}: {exc}")

    trimmed = raw_body.lstrip("\ufeff\r\n\t ")
    try:
        decoder = json.JSONDecoder()
        parsed, end_idx = decoder.raw_decode(trimmed)
        trailing = trimmed[end_idx:].strip()
        if isinstance(parsed, dict):
            if trailing:
                print(
                    "[docling] Beam response contained trailing text after JSON payload; trailing bytes were ignored."
                )
            return parsed
        parse_errors.append(
            f"JSONDecoder.raw_decode returned {type(parsed).__name__}, expected object"
        )
    except Exception as exc:
        parse_errors.append(f"JSONDecoder.raw_decode(): {type(exc).__name__}: {exc}")

    body_preview = raw_body[:1000]
    raise RuntimeError(
        "Beam Docling endpoint returned non-JSON response. "
        f"status={response.status_code}, content_type={response.headers.get('Content-Type', '<empty>')!r}, "
        f"decode_errors={' | '.join(parse_errors)}, body_preview={body_preview!r}"
    )


def _call_beam_docling_endpoint(pdf_bytes: bytes, file_name: str) -> dict[str, Any]:
    """
    Call the Beam-hosted Docling endpoint and return parsed JSON response.
    """

    config = _load_beam_docling_config()
    encoded_pdf = base64.b64encode(pdf_bytes).decode("ascii")

    payload = {
        "filename": file_name,
        "file_b64": encoded_pdf,
        "include_conversion_dump": False,
        "include_document_dump": True,
        "include_item_dump": False,
        "max_file_size_mb": config["max_file_size_mb"],
    }
    headers = {
        "Authorization": f"Bearer {config['token']}",
        "Content-Type": "application/json",
        "Accept": "application/json",
    }

    print("Sending PDF document to Beam Docling endpoint for conversion...")
    try:
        response = requests.post(
            config["endpoint"],
            json=payload,
            headers=headers,
            timeout=config["timeout_seconds"],
        )
    except requests.RequestException as exc:
        raise RuntimeError(f"Beam Docling endpoint request failed: {exc}") from exc

    raw_body = response.text or ""
    if not raw_body.strip():
        raise RuntimeError("Beam Docling endpoint returned empty response body.")

    if not response.ok:
        body_preview = raw_body[:1000]
        raise RuntimeError(
            "Beam Docling endpoint returned HTTP error: "
            f"status={response.status_code}, body_preview={body_preview!r}"
        )

    result = _parse_beam_response_json(response, raw_body)

    if result.get("ok") is False:
        error_code = result.get("error_code") or "UNKNOWN"
        error_message = result.get("error_message") or "Beam endpoint error"
        raise RuntimeError(
            "Beam Docling endpoint returned error response: "
            f"code={error_code}, message={error_message}"
        )

    document_dump = _extract_document_dump(result)
    if not isinstance(document_dump, dict):
        raise RuntimeError(
            "Beam Docling endpoint response missing document_dump (and legacy conversion_result_dump.document fallback)."
        )

    print("Successfully received response from Beam Docling endpoint.")
    return result


def _ordered_items_by_seq(ordered_items: Any) -> dict[int, dict[str, Any]]:
    """
    Build a sequence-indexed lookup map for endpoint ordered items.
    """

    mapped: dict[int, dict[str, Any]] = {}
    if not isinstance(ordered_items, list):
        return mapped

    for item in ordered_items:
        if not isinstance(item, dict):
            continue
        seq = item.get("seq")
        if isinstance(seq, int):
            mapped[seq] = item
    return mapped


def build_beam_layout(
    *,
    pdf_bytes: bytes,
    file_name: str,
    warnings: list[str],
    partial_failures: list[DoclingChunkFailure],
) -> dict[str, Any]:
    """
    Build normalized layout items and runtime classes from Beam endpoint output.
    """

    endpoint_result = _call_beam_docling_endpoint(pdf_bytes, file_name)
    runtime = _load_docling_module_runtime()

    for note in endpoint_result.get("server_notes") or []:
        if note:
            warnings.append(f"Beam: {note}")

    endpoint_status = str(endpoint_result.get("status") or "")
    endpoint_errors = endpoint_result.get("errors") or []
    if endpoint_status == "partial_success" and endpoint_errors:
        partial_failures.append(
            DoclingChunkFailure(
                page_range="full-document",
                errors=[stringify_endpoint_error(err) for err in endpoint_errors],
            )
        )

    doc_dump = _extract_document_dump(endpoint_result)
    if not isinstance(doc_dump, dict):
        raise RuntimeError(
            "Beam Docling endpoint response missing document_dump (and legacy conversion_result_dump.document fallback)."
        )

    doc = runtime["DoclingDocument"].model_validate(doc_dump)
    serializer = runtime["MarkdownDocSerializer"](doc=doc)
    ordered_by_seq = _ordered_items_by_seq(endpoint_result.get("ordered_items"))

    items: list[dict[str, Any]] = []
    for seq, (element, _level) in enumerate(doc.iterate_items()):
        endpoint_item = ordered_by_seq.get(seq, {})
        page_no = (
            endpoint_item.get("page_no")
            if isinstance(endpoint_item, dict)
            and isinstance(endpoint_item.get("page_no"), int)
            else pdf_utils.extract_page_no(element)
        )
        num_rows, num_cols = (
            pdf_utils.coerce_endpoint_table_shape(endpoint_item)
            if isinstance(endpoint_item, dict)
            else (None, None)
        )
        items.append(
            {
                "element": element,
                "serializer": serializer,
                "page_no": page_no,
                "endpoint_item": endpoint_item,
                "num_rows": num_rows,
                "num_cols": num_cols,
            }
        )

    return {
        "items": items,
        "picture_item_cls": runtime["PictureItem"],
        "table_item_cls": runtime["TableItem"],
        "list_item_cls": runtime.get("ListItem"),
        "section_header_item_cls": runtime.get("SectionHeaderItem"),
        "title_item_cls": runtime.get("TitleItem"),
        "converted_chunks": 1,
    }

