"""Legacy text-polishing helpers for child chunks before embedding.

This normalizes chunk text shape without changing chunk boundaries.
"""

import re
from typing import Any


def polish_chunks(chunks: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """
    Clean and normalize text chunks before sending them for embedding.

    Each chunk produced by the chunker may contain inconsistent whitespace,
    bullet points, or awkward line breaks. This function standardizes formatting
    to improve embedding quality and ensure consistent text structure.

    Args:
        chunks (List[Dict[str, Any]]):
            A list of dictionaries containing "content" keys.

    Returns:
        List[Dict[str, Any]]:
            The same list of dictionaries with polished "content" values.
            We are only modifying the "content" field in each dictionary.
    """
    for chunk in chunks:
        text = chunk["content"]

        # --- Step 1: Normalize whitespace and line breaks ---
        # Remove excessive spaces, tabs, or newlines so all spacing becomes single spaces.
        text = re.sub(r"\s+", " ", text.strip())

        # --- Step 2: Remove leading punctuation ---
        # Fixes the issue where chunks start with ". " due to bad splitting.
        # We remove any leading periods, commas, or semicolons so the text starts with a word.
        text = re.sub(r"^[.,;:]\s*", "", text)

        # --- Step 3: Fix spacing before punctuation ---
        # Example: "Hello , world !" -> "Hello, world!"
        text = re.sub(r"\s+([.,!?;:])", r"\1", text)

        # --- Step 4: Ensure first character is capitalized ---
        # Helps maintain cleaner sentence casing for readability and consistency.
        if text and not text[0].isupper():
            text = text[0].upper() + text[1:]

        # --- Step 5: Replace bullet symbols or stray artifacts ---
        # Handle real bullet symbols and common mojibake variants from extractor output.
        text = re.sub(r"(?:\u2022|\u00e2\u20ac\u00a2)\s*", "- ", text)

        # Amend the chunk text.
        chunk["content"] = text

    return chunks
