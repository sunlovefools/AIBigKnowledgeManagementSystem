import string
from typing import List, Set, Dict

def normalize_text(text: str) -> str:
    """
    Normalizes text by lowercasing, removing punctuation, and extra whitespace.

    Example:
    " Hello, World! " -> "hello world"
    """
    if not text: return ""
    text = text.lower()
    text = text.translate(str.maketrans("", "", string.punctuation))
    return " ".join(text.split())

def get_tokens(text: str) -> Set[str]:
    """
    Tokenizes normalized text into a set of unique tokens.

    Example:
    "Hello world hello" -> {"hello", "world"}
    """
    return set(normalize_text(text).split())

def is_match(retrieved_doc: str, golden_contexts: List[str], threshold: float = 0.8) -> bool:
    """
    Golden Inclusion Strategy (Recall):
    Returns True if enough golden tokens are found inside the retrieved doc.
    """
    if not golden_contexts or not retrieved_doc: return False
    
    r_norm = normalize_text(retrieved_doc)
    # 1. Fast substring check
    for gold in golden_contexts:
        if normalize_text(gold) in r_norm: 
            print("is_match: ✅ Substring match found.")
            return True

    # 2. Token Inclusion check
    r_tokens = get_tokens(retrieved_doc)
    if not r_tokens: return False

    # There may be multiple golden contexts; check each one
    for gold in golden_contexts:
        g_tokens = get_tokens(gold)
        if not g_tokens: continue
        
        min_len = min(len(g_tokens), len(r_tokens))
        if min_len == 0: continue

        intersection = g_tokens.intersection(r_tokens)
        score = len(intersection) / min_len
        
        if score >= threshold:
            print("is_match: ✅ Token inclusion match found.")
            return True

    return False

def calculate_hit_rate(retrieved_docs: List[str], golden_contexts: List[str]) -> float:
    """
    Calculates Hit Score for a SINGLE query.
    Returns 1.0 if any retrieved document matches any golden context, else 0.0.
    
    Args:
        retrieved_docs: List of strings (the retrieved chunks).
        golden_contexts: List of strings (the ground truth facts).
    """
    if not retrieved_docs:
        return 0.0

    # Check if ANY document in the list is a match
    if any(is_match(doc, golden_contexts) for doc in retrieved_docs):
        return 1.0
    
    return 0.0

def calculate_mrr(retrieved_docs: List[str], golden_contexts: List[str]) -> float:
    """
    Calculates Reciprocal Rank for a SINGLE query.
    Returns (1 / Rank) of the first relevant document.
    """
    if not retrieved_docs:
        return 0.0

    for rank, doc in enumerate(retrieved_docs):
        if is_match(doc, golden_contexts):
            # Rank is 1-based (1, 2, 3...)
            return 1.0 / (rank + 1)
            
    return 0.0