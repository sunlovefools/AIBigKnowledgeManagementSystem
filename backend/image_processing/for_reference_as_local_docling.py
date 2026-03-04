from docling.datamodel.base_models import ConversionStatus
from docling_core.transforms.serializer.markdown import MarkdownDocSerializer
from uuid6 import uuid6
import time

def convert_chunk(start_page: int, end_page: int):
    return converter.convert(
        str(input_pdf),
        raises_on_error=False,
        page_range=(start_page, end_page),
    )

markdown_chunks = []
chunk_failures = []
converted_chunks = 0
current_start = 1
table_counter = 1
picture_counter = 1

while True:
    current_end = current_start + CHUNK_SIZE - 1
    print(f"Converting pages {current_start}-{current_end} ...")


    # Implement a timer here if desired to monitor per-chunk conversion time
    start = time.time()
    result = convert_chunk(current_start, current_end)
    end = time.time()
    print(f"Chunk conversion time for pages {current_start}-{current_end}: {end - start:.2f} seconds")

    if result.status in {ConversionStatus.FAILURE, ConversionStatus.SKIPPED}:
        break

    doc_filename = input_pdf.stem

    chunk_markdown_parts = []
    serializer = MarkdownDocSerializer(doc=result.document)

    for element, _ in result.document.iterate_items():
        if isinstance(element, PictureItem):
            print(element)
            element_image_filename = (
                output_dir / f"{doc_filename}-picture-{picture_counter}.png"
            )
            with element_image_filename.open("wb") as fp:
                element.get_image(result.document).save(fp, "PNG")
            picture_counter += 1

        if isinstance(element, TableItem):
            element_data = element.data
            if element_data.num_rows == 0 or element_data.num_cols == 0:
                table_image_name = (
                    f"{doc_filename}-table-{table_counter}-{uuid6()}.png"
                )
                element_image_filename = output_dir / table_image_name
                with element_image_filename.open("wb") as fp:
                    element.get_image(result.document).save(fp, "PNG")

                chunk_markdown_parts.extend(
                    [
                        "> **Table (image)**: Structure extraction failed (rows/cols = 0).",
                        f"> ![{table_image_name}]({table_image_name})",
                        "",
                    ]
                )
                table_counter += 1
                continue
            table_counter += 1

        serialized_text = serializer.serialize(item=element).text.strip()
        if serialized_text:
            chunk_markdown_parts.append(serialized_text)

    converted_chunks += 1
    chunk_markdown = "\n\n".join(chunk_markdown_parts).strip()
    if chunk_markdown:
        markdown_chunks.append(chunk_markdown)

    if result.status == ConversionStatus.PARTIAL_SUCCESS and result.errors:
        chunk_failures.append(
            {
                "range": f"{current_start}-{current_end}",
                "errors": [str(err) for err in result.errors],
            }
        )

    current_start += CHUNK_SIZE

if not markdown_chunks:
    raise RuntimeError(
        "No pages converted successfully. Try CHUNK_SIZE=4 for very constrained environments."
    )

markdown_text = "\n\n".join(markdown_chunks)
print(f"Converted chunk passes: {converted_chunks}")
if chunk_failures:
    print(f"Warning: {len(chunk_failures)} chunk(s) had partial errors.")