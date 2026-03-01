# One-shot local endpoint -> serialize -> output/local_endpoint.md
from pathlib import Path
import base64
import json
import math
import time

import pypdfium2 as pdfium
import requests
from uuid6 import uuid6
from docling_core.transforms.serializer.markdown import MarkdownDocSerializer
from docling_core.types.doc import DoclingDocument, PictureItem, TableItem

import os
from dotenv import load_dotenv

load_dotenv()

BEAM_DOCLING_ENDPOINT = (os.getenv("BEAM_DOCLING_ENDPOINT") or "").strip()
BEAM_DOCLING_ENDPOINT_SECRET_TOKEN = (os.getenv("BEAM_DOCLING_ENDPOINT_TOKEN") or "").strip()
if not BEAM_DOCLING_ENDPOINT:
    raise RuntimeError("BEAM_DOCLING_ENDPOINT is not set in .env")
if not BEAM_DOCLING_ENDPOINT_SECRET_TOKEN:
    raise RuntimeError("BEAM_DOCLING_ENDPOINT_TOKEN is not set in .env")

print(f"Beam endpoint URL: {BEAM_DOCLING_ENDPOINT}")
CLIENT_IMAGE_SCALE = 2.5  # render scale for local cropping from PDF
SERIALIZER_IMAGE_PLACEHOLDER = "<!-- image -->"

pdf_path = Path(input_pdf) if "input_pdf" in globals() else Path("input\A_Hierarchical_Shifted-Window_Time_Series_Transformer_for_Stock_Market_Index_Price_Forecasting.pdf")
output_dir = Path("output")
output_dir.mkdir(parents=True, exist_ok=True)

if not pdf_path.exists():
    raise FileNotFoundError(
        f"Input PDF not found at {pdf_path}. Define input_pdf first or edit pdf_path in this cell."
    )

payload = {
    "filename": pdf_path.name,
    "file_b64": base64.b64encode(pdf_path.read_bytes()).decode("ascii"),
    "include_conversion_dump": False,
    "include_document_dump": True,
    "include_item_dump": False,
    "max_file_size_mb": 25,
}

headers = {
    "Authorization": f"Bearer {BEAM_DOCLING_ENDPOINT_SECRET_TOKEN}",
    "Content-Type": "application/json",
    "Accept": "application/json",
}

request_started = time.perf_counter()
response = requests.post(BEAM_DOCLING_ENDPOINT, json=payload, headers=headers, timeout=600)
endpoint_seconds = time.perf_counter() - request_started
print(f"Endpoint request time: {endpoint_seconds:.2f} seconds")

print(response)

# To raise for non-2xx status codes and catch issues early before trying to parse JSON
response.raise_for_status()
raw_body = response.text

# Check if the response body is empty before trying to parse JSON
if not raw_body.strip():
    raise RuntimeError(
        "Endpoint returned an empty response body (expected JSON). "
        f"url={BEAM_DOCLING_ENDPOINT}, status={response.status_code}, content_type={response.headers.get('Content-Type', '<empty>')!r}. "
        "This usually means you are hitting the wrong/stale Beam URL or the handler is not being invoked."
    )
try:
    beam_endpoint_result = response.json()
except requests.JSONDecodeError:
    try:
        beam_endpoint_result = json.loads(raw_body)
    except json.JSONDecodeError as exc:
        body_preview = raw_body[:1000]
        raise RuntimeError(
            "Endpoint returned non-JSON response. "
            f"url={BEAM_DOCLING_ENDPOINT}, status={response.status_code}, content_type={response.headers.get('Content-Type', '<empty>')!r}, "
            f"body_preview={body_preview!r}"
        ) from exc

if not beam_endpoint_result.get("ok"):
    raise RuntimeError(
        f"Endpoint returned non-ok response: {beam_endpoint_result.get('status')} / {beam_endpoint_result.get('error_code')} / {beam_endpoint_result.get('error_message')}"
    )

doc_dump = beam_endpoint_result.get("document_dump")
if not isinstance(doc_dump, dict):
    conversion_result_dump = beam_endpoint_result.get("conversion_result_dump")
    if isinstance(conversion_result_dump, dict):
        doc_dump = conversion_result_dump.get("document")
if not isinstance(doc_dump, dict):
    raise RuntimeError(
        "Missing document payload in endpoint response (checked document_dump and conversion_result_dump.document)."
    )

serialize_started = time.perf_counter()
doc = DoclingDocument.model_validate(doc_dump)
serializer = MarkdownDocSerializer(doc=doc)
ordered_items = beam_endpoint_result.get("ordered_items", [])
ordered_by_seq = {
    item.get("seq"): item
    for item in ordered_items
    if isinstance(item, dict) and isinstance(item.get("seq"), int)
}

pdf_doc = pdfium.PdfDocument(str(pdf_path))
_render_cache = {}

def _get_page_render(page_no: int):
    page_index = int(page_no) - 1
    if page_index < 0 or page_index >= len(pdf_doc):
        return None
    cached = _render_cache.get(page_index)
    if cached is not None:
        return cached

    page = pdf_doc[page_index]
    page_w_pt, page_h_pt = page.get_size()
    pil_img = page.render(scale=CLIENT_IMAGE_SCALE).to_pil()
    page.close()

    cached = {
        "img": pil_img,
        "page_w_pt": float(page_w_pt),
        "page_h_pt": float(page_h_pt),
    }
    _render_cache[page_index] = cached
    return cached

def _crop_image_from_endpoint_item(endpoint_item: dict):
    bbox = endpoint_item.get("bbox")
    page_no = endpoint_item.get("page_no")
    if not isinstance(bbox, dict) or not isinstance(page_no, int):
        return None

    try:
        l = float(bbox["l"])
        t = float(bbox["t"])
        r = float(bbox["r"])
        b = float(bbox["b"])
    except Exception:
        return None

    page_info = _get_page_render(page_no)
    if page_info is None:
        return None

    page_img = page_info["img"]
    page_h_pt = page_info["page_h_pt"]
    img_w_px, img_h_px = page_img.size
    coord_origin = str(bbox.get("coord_origin", "TOPLEFT")).upper()

    x1 = l * CLIENT_IMAGE_SCALE
    x2 = r * CLIENT_IMAGE_SCALE

    if coord_origin == "BOTTOMLEFT":
        y1 = (page_h_pt - t) * CLIENT_IMAGE_SCALE
        y2 = (page_h_pt - b) * CLIENT_IMAGE_SCALE
    else:
        y1 = t * CLIENT_IMAGE_SCALE
        y2 = b * CLIENT_IMAGE_SCALE

    left = max(0, min(img_w_px, int(math.floor(min(x1, x2)))))
    right = max(0, min(img_w_px, int(math.ceil(max(x1, x2)))))
    top = max(0, min(img_h_px, int(math.floor(min(y1, y2)))))
    bottom = max(0, min(img_h_px, int(math.ceil(max(y1, y2)))))

    if right <= left or bottom <= top:
        return None

    return page_img.crop((left, top, right, bottom))

def _image_marker(image_uuid: str) -> str:
    return f"<Image: {image_uuid}>"

def _replace_or_prefix_image_placeholder(serialized_text: str, image_uuid: str) -> str:
    marker = _image_marker(image_uuid)
    if not serialized_text:
        return marker
    if SERIALIZER_IMAGE_PLACEHOLDER in serialized_text:
        return serialized_text.replace(SERIALIZER_IMAGE_PLACEHOLDER, marker)
    return f"{marker}\n\n{serialized_text}"

doc_filename = pdf_path.stem
picture_counter = 1
table_counter = 1
markdown_parts = []
saved_image_count = 0

try:
    for seq, (element, _) in enumerate(doc.iterate_items()):
        endpoint_item = ordered_by_seq.get(seq, {})
        serialized_text = serializer.serialize(item=element).text.strip()

        if isinstance(element, PictureItem):
            image_uuid = str(uuid6())
            cropped_img = _crop_image_from_endpoint_item(endpoint_item)
            if cropped_img is not None:
                picture_name = f"{doc_filename}-picture-{picture_counter}-{image_uuid}.png"
                cropped_img.save(output_dir / picture_name, "PNG")
                picture_counter += 1
                saved_image_count += 1
                serialized_text = _replace_or_prefix_image_placeholder(serialized_text, image_uuid)
            elif serialized_text and SERIALIZER_IMAGE_PLACEHOLDER in serialized_text:
                serialized_text = serialized_text.replace(
                    SERIALIZER_IMAGE_PLACEHOLDER,
                    "<Image: crop_failed>",
                )

        if isinstance(element, TableItem):
            table_info = endpoint_item.get("table_info") or {}
            element_data = getattr(element, "data", None)
            num_rows = getattr(element_data, "num_rows", None)
            num_cols = getattr(element_data, "num_cols", None)
            if num_rows is None:
                num_rows = table_info.get("num_rows")
            if num_cols is None:
                num_cols = table_info.get("num_cols")

            if num_rows == 0 or num_cols == 0:
                table_image_uuid = str(uuid6())
                table_image_name = f"{doc_filename}-table-{table_counter}-{table_image_uuid}.png"
                cropped_img = _crop_image_from_endpoint_item(endpoint_item)
                if cropped_img is not None:
                    cropped_img.save(output_dir / table_image_name, "PNG")
                    saved_image_count += 1
                    markdown_parts.extend([
                        "> **Table (image)**: Structure extraction failed (rows/cols = 0).",
                        f"> {_image_marker(table_image_uuid)}",
                        "",
                    ])
                else:
                    markdown_parts.extend([
                        "> **Table (image)**: Structure extraction failed (rows/cols = 0).",
                        "> (Local crop failed: missing/invalid bbox or page_no.)",
                        "",
                    ])
                table_counter += 1
                continue
            table_counter += 1

        if serialized_text:
            markdown_parts.append(serialized_text)
finally:
    pdf_doc.close()

markdown_text = "\n\n".join(markdown_parts).strip()
if not markdown_text:
    raise RuntimeError("No markdown text serialized from endpoint response.")

markdown_path = output_dir / "local_endpoint.md"
markdown_path.write_text(markdown_text, encoding="utf-8")
serialize_seconds = time.perf_counter() - serialize_started
total_seconds = endpoint_seconds + serialize_seconds

print(f"Serialized markdown items: {len(markdown_parts)}")
print(f"Saved cropped images (UUID filenames): {saved_image_count}")
print(f"Serialization + local crop + write time: {serialize_seconds:.2f} seconds")
print(f"Approx total (endpoint + serialize): {total_seconds:.2f} seconds")
print(f"Markdown written to: {markdown_path}")
print(f"Endpoint include_item_images meta: {beam_endpoint_result.get('meta', {}).get('include_item_images')}")
if beam_endpoint_result.get("errors"):
    print(f"Endpoint reported errors: {len(beam_endpoint_result.get('errors', []))}")
