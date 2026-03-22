"""
Generate a golden dataset for RAG evaluation.

Reads questions from a .txt file, extracts full PDF text,
calls Ollama to generate ground truth answers, and saves to JSON.
"""

import os
import json
import re
import sys

try:
    import fitz  # PyMuPDF
except ImportError:
    print("❌ PyMuPDF not installed. Run: pip install pymupdf")
    sys.exit(1)

try:
    from ollama import Client
except ImportError:
    print("❌ Ollama not installed. Run: pip install ollama")
    sys.exit(1)

# --- Configuration ---
PDF_DIR = r"C:\Users\uruma\Desktop\CGI\PDFs2"
QUESTIONS_FILE = r"C:\Users\uruma\Desktop\CGI\questions_all_30.txt"
OUTPUT_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", "golden_dataset.json")
OLLAMA_MODEL = os.getenv("OLLAMA_MODEL", "qwen2.5:14b")

# Max characters of PDF text to send to Ollama (0 = no limit)
MAX_PDF_CHARS = 0


def extract_pdf_text(pdf_path: str) -> str:
    """Extract full text from a PDF using PyMuPDF."""
    try:
        doc = fitz.open(pdf_path)
        text = ""
        for page in doc:
            text += page.get_text()
        doc.close()
        return text.strip()
    except Exception as e:
        print(f"  ⚠️ Could not extract text from {pdf_path}: {e}")
        return ""


def parse_questions_file(filepath: str) -> list[dict]:
    """
    Parse the questions .txt file into blocks.

    Expected format:
        File: filename.pdf
        Topic: Some Topic
        1. Question one?
        2. Question two?
        ...
    """
    blocks = []
    current = None

    with open(filepath, "r", encoding="utf-8") as f:
        lines = f.readlines()

    for line in lines:
        line = line.strip()
        if not line:
            continue

        file_match = re.match(r"^File:\s*(.+\.pdf)", line, re.IGNORECASE)
        topic_match = re.match(r"^Topic:\s*(.+)", line, re.IGNORECASE)
        question_match = re.match(r"^Q\d+\s*\([^)]*\)\s*:\s*(.+)", line)

        if file_match:
            if current:
                blocks.append(current)
            current = {"filename": file_match.group(1).strip(), "topic": "", "questions": []}
        elif topic_match and current:
            current["topic"] = topic_match.group(1).strip()
        elif question_match and current:
            current["questions"].append(question_match.group(1).strip())

    if current:
        blocks.append(current)

    return blocks


def generate_answer(client: Client, pdf_text: str, question: str) -> str:
    """Call Ollama to generate a ground truth answer from PDF text."""
    truncated = pdf_text if MAX_PDF_CHARS == 0 else pdf_text[:MAX_PDF_CHARS]

    prompt = f"""You are an expert at reading documents and answering questions accurately.

Below is the full text of a document. Read it carefully and answer the question that follows.
Base your answer ONLY on the information in the document. Be concise and accurate.

--- DOCUMENT START ---
{truncated}
--- DOCUMENT END ---

Question: {question}

Answer:"""

    response = client.chat(
        model=OLLAMA_MODEL,
        messages=[{"role": "user", "content": prompt}],
        options={"temperature": 0},
    )

    # Support both dict and Pydantic responses
    if hasattr(response, "model_dump"):
        response = response.model_dump()
    msg = response.get("message", {})
    if isinstance(msg, dict):
        return msg.get("content", "").strip()
    if hasattr(msg, "content"):
        return msg.content.strip()
    return str(msg).strip()


def main():
    os.makedirs(os.path.dirname(OUTPUT_FILE), exist_ok=True)

    print(f"📂 Reading questions from: {QUESTIONS_FILE}")
    blocks = parse_questions_file(QUESTIONS_FILE)
    print(f"✅ Parsed {len(blocks)} PDF blocks\n")

    client = Client()
    dataset = []
    total = sum(len(b["questions"]) for b in blocks)
    done = 0

    for block in blocks:
        filename = block["filename"]
        topic = block["topic"]
        pdf_path = os.path.join(PDF_DIR, filename)

        if not os.path.exists(pdf_path):
            print(f"  ⚠️ PDF not found: {pdf_path} — skipping")
            continue

        print(f"📄 [{block['filename']}] Extracting text...")
        pdf_text = extract_pdf_text(pdf_path)
        char_count = len(pdf_text)
        print(f"   {char_count:,} characters extracted")

        for i, question in enumerate(block["questions"], 1):
            done += 1
            print(f"  🔹 [{done}/{total}] Q{i}: {question[:60]}...")
            answer = generate_answer(client, pdf_text, question)
            dataset.append({
                "question": question,
                "answer": answer,
                "source_file": filename,
                "topic": topic,
            })

        print()

    with open(OUTPUT_FILE, "w", encoding="utf-8") as f:
        json.dump(dataset, f, ensure_ascii=False, indent=2)

    print(f"🎉 Done! {len(dataset)} Q&A pairs saved to:\n   {OUTPUT_FILE}")


if __name__ == "__main__":
    main()
