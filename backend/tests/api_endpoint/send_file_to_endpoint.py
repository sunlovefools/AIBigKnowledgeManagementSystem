"""
Manual test helper for the Docling preview endpoint:
    POST /ingest/webhook/preview

Edit the configuration variables below, then run:
    python backend/tests/api_endpoint/send_file_to_endpoint.py
"""

import base64
import json
from pathlib import Path

import requests

# ---------------------------
# Edit these before running
# ---------------------------
PDF_FILE_PATH = r"C:\Users\Yoong Shen\Desktop\Docling_test\input\chart_document.pdf"
PREVIEW_ENDPOINT_URL = "http://127.0.0.1:8000/ingest/webhook/preview"
REQUEST_TIMEOUT_SECONDS = 300
SAVE_RESPONSE_PATH = None  # Example: r"backend\tests\api_endpoint\last_response.json"


def build_payload(file_path: Path) -> dict:
    data = file_path.read_bytes()
    return {
        "fileName": file_path.name,
        # Preview endpoint is PDF-only, so always send application/pdf.
        "contentType": "application/pdf",
        "data": base64.b64encode(data).decode("utf-8"),
    }


def main() -> int:
    file_path = Path(PDF_FILE_PATH)
    if not file_path.exists() or not file_path.is_file():
        print("File not found. Update PDF_FILE_PATH in this script.")
        print(f"Current value: {file_path}")
        return 1

    payload = build_payload(file_path)

    print(f"Sending: {file_path}")
    print(f"Endpoint: {PREVIEW_ENDPOINT_URL}")
    print(f"Content-Type: {payload['contentType']}")

    try:
        response = requests.post(
            PREVIEW_ENDPOINT_URL,
            json=payload,
            timeout=REQUEST_TIMEOUT_SECONDS,
        )
    except requests.RequestException as exc:
        print(f"Request failed: {exc}")
        return 2

    print(f"HTTP {response.status_code}")

    # Try JSON first because FastAPI routes typically return JSON responses.
    try:
        response_body = response.json()
        pretty = json.dumps(response_body, indent=2, ensure_ascii=False)
        print(pretty)
        output_text = pretty
    except ValueError:
        print(response.text)
        output_text = response.text

    if SAVE_RESPONSE_PATH:
        out_path = Path(SAVE_RESPONSE_PATH)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(output_text, encoding="utf-8")
        print(f"Saved response to: {out_path}")

    return 0 if response.ok else 3


if __name__ == "__main__":
    raise SystemExit(main())
