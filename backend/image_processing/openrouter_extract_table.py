"""
OpenRouter + Qwen3-VL (thinking) table-to-JSON extractor
- Sends a LOCAL image as base64 data URL (no hosting needed)
- Uses your SYSTEM prompt rules/schema
- Saves ONLY the model JSON output to: /output/output.json

Edit these variables:
- OPENROUTER_API_KEY
- IMAGE_PATH
"""

from __future__ import annotations

import base64
import datetime as dt
import json
import mimetypes
import os
from pathlib import Path
import traceback
from typing import Any, Dict
try:
    from dotenv import load_dotenv
except ModuleNotFoundError:
    def load_dotenv(*_args, **_kwargs):  # type: ignore[no-redef]
        return False

load_dotenv()

# =========================
# User-editable variables
# =========================
OPENROUTER_API_KEY = ""  # <-- put your key here (or load from env yourself)
IMAGE_PATH = "images\\table_screenshot.png"
MODEL = os.getenv("VISION_LM_MODEL", "qwen/qwen3-vl-30b-a3b-thinking")
MAX_TOKENS = 4000
USE_CACHED_RESULT = False

BASE_DIR = Path(__file__).resolve().parent
OUTPUT_DIR = BASE_DIR / "output"
OUT_PATH = OUTPUT_DIR / "output.json"
RAW_TEXT_OUT_PATH = OUTPUT_DIR / "output_raw.txt"
FULL_RESPONSE_OUT_PATH = OUTPUT_DIR / "openrouter_response.json"
STATUS_OUT_PATH = OUTPUT_DIR / "status.json"
ERROR_OUT_PATH = OUTPUT_DIR / "error.txt"
OPENROUTER_URL = os.getenv(
    "OPENROUTER_URL",
    "https://openrouter.ai/api/v1/chat/completions",
)


SYSTEM_PROMPT = """
You are a structured data extraction engine.

Your task is to extract a table from an input image and output ONLY valid JSON following the exact schema defined below.

Do NOT output explanations.
Do NOT output markdown.
Do NOT include comments.
Output ONLY valid JSON.

OBJECTIVE

Extract all visible table rows exactly as shown in the image and convert them into structured JSON.

Every visible row in the table must become one object inside "data".

If something is unclear, preserve the raw text exactly.

JSON SCHEMA TO FOLLOW EXACTLY

{
  "table_metadata": {
    "table_name": "string",
    "display_name": "string",
    "description": "string",
    "table_type": "relational | categorized_metrics | hierarchical_statement | unknown",
    "units_note": "string"
  },
  "columns": [
    {
      "name": "string",
      "display_name": "string",
      "data_type": "string",
      "description": "string",
      "unit": "string",
      "role": "identifier | dimension | measure | text | date"
    }
  ],
  "data": [
    {
      "row_id": "string",
      "row_type": "data | header | subtotal | total | note",
      "category": "string",
      "metric": "string",
      "level": 0,
      "parent_row_id": "string",
      "values": {},
      "raw_values": {},
      "notes": "string"
    }
  ]
}

EXTRACTION RULES

1) Table Classification

Determine "table_type":

- If table is like user records (ID, Name, Email) -> "relational"
- If table contains KPI metrics grouped by sections -> "categorized_metrics"
- If table contains hierarchical sections with totals/subtotals -> "hierarchical_statement"
- If unsure -> "unknown"

2) Column Extraction

- Extract ALL visible column headers.
- Preserve header text exactly in display_name.
- Create a normalized lowercase snake_case version for name.
- Infer data_type:
  - Integer numbers -> "integer"
  - Decimal numbers -> "number"
  - Percentages -> "string"
  - Text -> "string"
  - Dates -> "date"

- Assign role:
  - Primary identifier column -> "identifier"
  - Year columns -> "measure"
  - Metric name column -> "dimension"
  - Free text column -> "text"

3) Row Extraction

Every visible row must become one object in "data".

Rules:

- Section headers -> row_type = "header"
- Subtotals -> row_type = "subtotal"
- Totals -> row_type = "total"
- Normal rows -> row_type = "data"

For hierarchical tables:
- Top-level rows -> level = 0
- Indented rows -> level = 1 or higher
- If row belongs to section -> set parent_row_id

For categorized KPI tables:
- category = section name
- metric = row label

For relational tables:
- category = null
- metric = null

4) Value Normalization Rules

In "values":

- Remove commas from numbers
  "15,912.7" -> 15912.7

- Convert parentheses to negative numbers
  "(433.9)" -> -433.9

- Keep percentages as STRING including "%"
  "110.4%" stays "110.4%"

- Empty cells -> null

In "raw_values":
- Preserve exact original text
- Keep commas
- Keep parentheses
- Keep percent symbols

5) row_id Rules

- Must be unique
- Use snake_case derived from row label
- Examples:
  - "Revenue" -> "revenue"
  - "Long-term debt and lease liabilities" -> "long_term_debt_and_lease_liabilities"

6) Important Constraints

- Do NOT invent missing rows.
- Do NOT merge rows.
- Do NOT drop rows.
- Do NOT add extra fields.
- Use null if something is missing.
- Output MUST be valid JSON.
- No trailing commas.

OUTPUT FORMAT

Return ONLY JSON.
"""

SEMANTIC_SUMMARY_PROMPT_TEMPLATE = """
You are an expert document analyst. 
Generate a concise "Semantic Proxy Summary" for a table extracted from a document. 

Inputs you will receive: 
1. An IMAGE — this image IS the table. 
2. Document context appearing immediately before the table, enclosed within: <CONTEXT BEFORE THE TABLE> ... </CONTEXT BEFORE THE TABLE> 
3. Document context appearing immediately after the table, enclosed within: <CONTEXT AFTER THE TABLE> ... </CONTEXT AFTER THE TABLE> 

Requirements: 
- The summary MUST begin with: "The table represents ..." 
- Write 3–5 sentences only. - Maximum 500 characters total (including spaces). 
- Explain the role and significance of the table relative to the surrounding context. 
- Reference the key metrics, comparisons, or results emphasized in the context. 
- Use the same technical terminology appearing in the provided context. 
- Highlight specific data points, trends, rows, or columns discussed by the author rather than describing layout. 
- Avoid generic phrases like “this table shows rows and columns.” 
- Output ONLY the summary text with no extra commentary.

<CONTEXT BEFORE THE TABLE>
{context_before}
</CONTEXT BEFORE THE TABLE>

<CONTEXT AFTER THE TABLE>
{context_after}
</CONTEXT AFTER THE TABLE>
"""

# =========================
# Helpers
# =========================
def encode_image_to_data_url(image_path: Path) -> str:
    """
    Encode an image file into a data URL:
      data:<mime>;base64,<...>
    """
    if not image_path.exists():
        raise FileNotFoundError(f"Image not found: {image_path}")

    mime, _ = mimetypes.guess_type(str(image_path))
    if not mime:
        # reasonable fallback if extension is unknown
        mime = "application/octet-stream"

    raw = image_path.read_bytes()
    b64 = base64.b64encode(raw).decode("utf-8")
    return f"data:{mime};base64,{b64}"


def write_text_file(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp_path = path.with_suffix(path.suffix + ".tmp")
    with open(temp_path, "w", encoding="utf-8") as file:
        file.write(content)
    temp_path.replace(path)


def write_json_file(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp_path = path.with_suffix(path.suffix + ".tmp")
    with open(temp_path, "w", encoding="utf-8") as file:
        json.dump(payload, file, indent=2, ensure_ascii=False)
    temp_path.replace(path)


def extract_json_from_content(content: str) -> Any:
    text = content.strip()
    if text.startswith("```"):
        lines = text.splitlines()
        if lines and lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].strip() == "```":
            lines = lines[:-1]
        text = "\n".join(lines).strip()

    try:
        return json.loads(text)
    except json.JSONDecodeError:
        start = text.find("{")
        end = text.rfind("}")
        if start != -1 and end != -1 and end > start:
            return json.loads(text[start : end + 1])
        raise


def call_openrouter(data_url: str, api_key: str) -> Dict[str, Any]:
    try:
        import requests
    except ModuleNotFoundError as e:
        raise RuntimeError(
            "Missing dependency 'requests'. Install it with: pip install requests"
        ) from e

    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }

    token_candidates = [MAX_TOKENS, 1000, 512, 256]
    seen = set()
    deduped_candidates = [t for t in token_candidates if not (t in seen or seen.add(t))]

    last_status_code = None
    last_resp_json: Dict[str, Any] | None = None

    for token_limit in deduped_candidates:
        payload: Dict[str, Any] = {
            "model": MODEL,
            "temperature": 0,
            "max_tokens": token_limit,
            "max_completion_tokens": token_limit,
            "provider" : {
                "only": ["alibaba"]
            },
            "messages": [
                {"role": "system", "content": SYSTEM_PROMPT},
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": "Extract the table from this image and output ONLY valid JSON."},
                        {"type": "image_url", "image_url": {"url": data_url}},
                    ],
                },
            ],
        }

        resp = requests.post(OPENROUTER_URL, headers=headers, json=payload, timeout=500)
        try:
            resp_json = resp.json()
        except Exception as e:
            raise RuntimeError(f"Non-JSON response. HTTP {resp.status_code}. Body:\n{resp.text}") from e

        if resp.status_code < 400:
            return resp_json

        last_status_code = resp.status_code
        last_resp_json = resp_json

        if resp.status_code != 402:
            break

    raise RuntimeError(f"HTTP {last_status_code}\n{json.dumps(last_resp_json, indent=2)}")


def call_openrouter_custom_messages(
    *,
    api_key: str,
    messages: list[Dict[str, Any]],
    max_tokens: int = MAX_TOKENS,
) -> Dict[str, Any]:
    try:
        import requests
    except ModuleNotFoundError as e:
        raise RuntimeError(
            "Missing dependency 'requests'. Install it with: pip install requests"
        ) from e

    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }

    token_candidates = [max_tokens, 1000, 512, 256]
    seen = set()
    deduped_candidates = [t for t in token_candidates if not (t in seen or seen.add(t))]

    last_status_code = None
    last_resp_json: Dict[str, Any] | None = None

    for token_limit in deduped_candidates:
        payload: Dict[str, Any] = {
            "model": MODEL,
            "temperature": 0,
            "max_tokens": token_limit,
            "max_completion_tokens": token_limit,
            "provider": {
                "only": ["alibaba"]
            },
            "messages": messages,
        }

        resp = requests.post(OPENROUTER_URL, headers=headers, json=payload, timeout=500)
        try:
            resp_json = resp.json()
        except Exception as e:
            raise RuntimeError(f"Non-JSON response. HTTP {resp.status_code}. Body:\n{resp.text}") from e

        if resp.status_code < 400:
            return resp_json

        last_status_code = resp.status_code
        last_resp_json = resp_json

        if resp.status_code != 402:
            break

    raise RuntimeError(f"HTTP {last_status_code}\n{json.dumps(last_resp_json, indent=2)}")


def get_message_content(resp_json: Dict[str, Any]) -> str:
    try:
        return resp_json["choices"][0]["message"]["content"]
    except Exception:
        raise RuntimeError(f"Unexpected response shape:\n{json.dumps(resp_json, indent=2)}")


def extract_table_json_from_image(
    image_path: Path | str,
    *,
    api_key: str | None = None,
    output_dir: Path | str | None = None,
    save_artifacts: bool = True,
) -> Dict[str, Any]:
    """Extract one table image into structured JSON using OpenRouter.

    This uses the same model, prompt, and response parsing flow as the CLI `main()`.
    """
    image_path_obj: Path = Path(image_path)
    active_output_dir: Path = Path(output_dir) if output_dir is not None else OUTPUT_DIR

    raw_text_out_path: Path = active_output_dir / "output_raw.txt"
    full_response_out_path: Path = active_output_dir / "openrouter_response.json"
    status_out_path: Path = active_output_dir / "status.json"
    error_out_path: Path = active_output_dir / "error.txt"
    out_path: Path = active_output_dir / "output.json"

    resolved_api_key: str | None = api_key or os.getenv("OPENROUTER_API_KEY")
    if not resolved_api_key:
        raise RuntimeError("OPENROUTER_API_KEY not found in environment variables. Set it or put it in the script.")

    content: str | None = None
    active_output_dir.mkdir(parents=True, exist_ok=True)

    try:
        data_url = encode_image_to_data_url(image_path_obj)
        resp_json = call_openrouter(data_url, resolved_api_key)
        content = get_message_content(resp_json)

        parsed: Dict[str, Any] = extract_json_from_content(content)

        if save_artifacts:
            write_json_file(full_response_out_path, resp_json)
            write_text_file(raw_text_out_path, content)
            write_json_file(out_path, parsed)

            status_payload: Dict[str, Any] = {
                "ok": True,
                "timestamp": dt.datetime.now().isoformat(),
                "source": "openrouter",
                "image_path": str(image_path_obj),
                "output_json": str(out_path),
                "raw_text": str(raw_text_out_path),
                "full_response": str(full_response_out_path),
            }
            write_json_file(status_out_path, status_payload)

        return parsed
    except Exception as e:
        if save_artifacts:
            error_text = f"{type(e).__name__}: {e}\n\n{traceback.format_exc()}"
            write_text_file(error_out_path, error_text)

            if content is not None:
                write_text_file(raw_text_out_path, content)

            fallback_output: Dict[str, Any] = {
                "ok": False,
                "error": str(e),
                "error_file": str(error_out_path),
                "raw_text_file": str(raw_text_out_path),
                "timestamp": dt.datetime.now().isoformat(),
            }
            write_json_file(out_path, fallback_output)
            write_json_file(status_out_path, fallback_output)

        raise


def build_semantic_summary_prompt(context_before: str, context_after: str) -> str:
    trimmed_before: str = context_before[-2000:]
    trimmed_after: str = context_after[:2000]
    return SEMANTIC_SUMMARY_PROMPT_TEMPLATE.format(
        context_before=trimmed_before,
        context_after=trimmed_after,
    )


def extract_table_semantic_summary_from_image(
    image_path: Path | str,
    *,
    context_before: str,
    context_after: str,
    api_key: str | None = None,
    output_dir: Path | str | None = None,
    save_artifacts: bool = True,
) -> str:
    image_path_obj: Path = Path(image_path)
    active_output_dir: Path = Path(output_dir) if output_dir is not None else OUTPUT_DIR

    summary_out_path: Path = active_output_dir / "semantic_summary.txt"
    raw_text_out_path: Path = active_output_dir / "semantic_raw.txt"
    full_response_out_path: Path = active_output_dir / "semantic_openrouter_response.json"
    status_out_path: Path = active_output_dir / "semantic_status.json"
    error_out_path: Path = active_output_dir / "semantic_error.txt"

    resolved_api_key: str | None = api_key or os.getenv("OPENROUTER_API_KEY")
    if not resolved_api_key:
        raise RuntimeError("OPENROUTER_API_KEY not found in environment variables. Set it or put it in the script.")

    content: str | None = None
    active_output_dir.mkdir(parents=True, exist_ok=True)

    try:
        data_url = encode_image_to_data_url(image_path_obj)
        prompt_text: str = build_semantic_summary_prompt(context_before, context_after)

        resp_json = call_openrouter_custom_messages(
            api_key=resolved_api_key,
            max_tokens=800,
            messages=[
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": prompt_text},
                        {"type": "image_url", "image_url": {"url": data_url}},
                    ],
                },
            ],
        )
        content = get_message_content(resp_json)

        summary_text: str = " ".join(content.strip().split())
        if len(summary_text) > 500:
            summary_text = summary_text[:500].rstrip()

        if save_artifacts:
            write_json_file(full_response_out_path, resp_json)
            write_text_file(raw_text_out_path, content)
            write_text_file(summary_out_path, summary_text)

            status_payload: Dict[str, Any] = {
                "ok": True,
                "timestamp": dt.datetime.now().isoformat(),
                "source": "openrouter",
                "image_path": str(image_path_obj),
                "summary_text": str(summary_out_path),
                "raw_text": str(raw_text_out_path),
                "full_response": str(full_response_out_path),
                "context_before_chars": len(context_before),
                "context_after_chars": len(context_after),
                "context_before_chars_sent": len(context_before[-2000:]),
                "context_after_chars_sent": len(context_after[:2000]),
            }
            write_json_file(status_out_path, status_payload)

        return summary_text
    except Exception as e:
        if save_artifacts:
            error_text = f"{type(e).__name__}: {e}\n\n{traceback.format_exc()}"
            write_text_file(error_out_path, error_text)

            if content is not None:
                write_text_file(raw_text_out_path, content)

            fallback_output: Dict[str, Any] = {
                "ok": False,
                "error": str(e),
                "error_file": str(error_out_path),
                "raw_text_file": str(raw_text_out_path),
                "timestamp": dt.datetime.now().isoformat(),
            }
            write_json_file(status_out_path, fallback_output)

        raise


def main() -> None:
    try:
        parsed = extract_table_json_from_image(
            image_path=Path(IMAGE_PATH),
            api_key=os.getenv("OPENROUTER_API_KEY"),
            output_dir=OUTPUT_DIR,
            save_artifacts=True,
        )
        print(f"Saved full API response to: {FULL_RESPONSE_OUT_PATH}")
        print(f"Saved raw model text to: {RAW_TEXT_OUT_PATH}")
        print(f"Saved JSON to: {OUT_PATH}")
        print("Extracted JSON preview:")
        print(json.dumps(parsed, indent=2, ensure_ascii=False)[:1200])
    except Exception:
        print(f"Failed to produce parsed JSON. Error saved to: {ERROR_OUT_PATH}")
        print(f"Fallback output saved to: {OUT_PATH}")


if __name__ == "__main__":
    main()
