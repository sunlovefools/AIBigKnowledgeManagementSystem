import re

def polish_chunks(chunks):
    """
    Clean and normalize text chunks before sending them for embedding.

    Each chunk produced by the chunker may contain inconsistent whitespace,
    bullet points, or awkward line breaks. This function standardizes formatting
    to improve embedding quality and ensure consistent text structure.

    Args:
        chunks (List[Dict[str, str]]): 
            A list of dictionaries containing "index" and "text" keys.

    Returns:
        {index: int, text: str}:
            A list of polished chunks with cleaned text.
    """
    polished = []
    for chunk in chunks:
        text = chunk["text"]

        # --- Step 1: Normalize whitespace and line breaks ---
        # Remove excessive spaces, tabs, or newlines so all spacing becomes single spaces.
        text = re.sub(r"\s+", " ", text.strip())

        # --- Step 2: Fix spacing before punctuation ---
        # Example: "Hello , world !" → "Hello, world!"
        text = re.sub(r"\s+([.,!?;:])", r"\1", text)

        # --- Step 3: Ensure first character is capitalized ---
        # Helps maintain cleaner sentence casing for readability and consistency.
        if text and not text[0].isupper():
            text = text[0].upper() + text[1:]

        # --- Step 4: Replace bullet symbols or stray artifacts ---
        # Converts common bullet characters (•) to a plain dash ("-") for uniformity.
        text = re.sub(r"•\s*", "- ", text)

        # Add cleaned result to the output list
        polished.append({
            "index": chunk["index"],
            "text": text
        })

    return polished
