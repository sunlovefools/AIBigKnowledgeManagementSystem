from typing import List, Tuple
from langchain_text_splitters import RecursiveCharacterTextSplitter
from langchain_core.documents import Document
from pydantic import BaseModel, Field
from uuid import uuid4 

# Define Schema for Parent and Child chunks
class ParentChunkModel(BaseModel):
    """Schema for the large, context-rich Parent Documents (Document Store)."""
    # Uses Field(..., alias="_id") and by_alias=True config for AstraDB compatibility
    # The MongoDB driver prefers '_id', but we use 'parent_id' internally.
    db_id: str = Field(..., alias="_id")
    content: str                    # The large text segment (full context for LLM)
    document_name: str
    
    class Config:
        populate_by_name = True     # Allows initialization by field alias (_id)
        
class ChildChunkModel(BaseModel):
    """Schema for the small, embedded Child Chunks (Vector Store)."""
    index: int                       # Global sequential index
    text: str                        # The small text segment (embedded for vector search)
    parent_id: str                   # Foreign key linking back to the ParentChunkModel._id
    file_name: str                   # Original file name

# --- Helper function ---
def _create_initial_child_chunks(text: str, file_name: str, chunk_size: int) -> List[Document]:
    """
    Step 1: Raw Splitting.
    Splits text into raw child chunks using LangChain's recursive splitter.
    """
    splitter = RecursiveCharacterTextSplitter(
        chunk_size=chunk_size,
        chunk_overlap=int(chunk_size * 0.1),  # 10% overlap
        separators=["\n\n", "\n", ".", " ", ""]
    )
    base_doc = Document(page_content=text, metadata={"document_name": file_name})
    return splitter.split_documents([base_doc])

def _merge_small_chunks(docs: List[Document], min_chars: int) -> List[Document]:
    """
    Step 2: Merging of Small Child Chunks.
    Consolidates tiny 'orphan' chunks (like headers or stray lines) into subsequent chunks 
    to ensure every vector has sufficient semantic meaning.
    """
    merged_docs = []
    buffer_text = ""

    for i, doc in enumerate(docs):
        content = doc.page_content.strip()

        # Prepend any buffered text from the previous iteration
        if buffer_text:
            content = f"{buffer_text}\n{content}"
            buffer_text = ""

        # Check if the current chunk is too small (and not the last one)
        is_too_small = len(content) < min_chars
        is_not_last = i < len(docs) - 1

        if is_too_small and is_not_last:
            # Buffer it to merge with the next chunk
            buffer_text = content
        else:
            # Valid chunk; save it
            doc.page_content = content
            merged_docs.append(doc)

    # Edge Case: If buffer remains after the loop, append to the very last doc
    if buffer_text and merged_docs:
        merged_docs[-1].page_content += f"\n{buffer_text}"

    return merged_docs

def _group_children_into_parents(
    child_docs: List[Document], 
    target_parent_size: int, 
    min_parent_size: int
) -> List[List[Document]]:
    """
    Step 3: Aggregation.
    Groups child chunks into lists (Parents) based on the target character limit.
    Also handles merging the final group if it is too small.
    """
    parent_groups = []
    current_group = []
    current_length = 0

    for child in child_docs:
        child_len = len(child.page_content)

        # Check if adding this child exceeds the parent target
        if current_length + child_len > target_parent_size:
            if current_group:
                parent_groups.append(current_group)
            
            # Reset for new group
            current_group = [child]
            current_length = child_len
        else:
            current_group.append(child)
            current_length += child_len

    # Append any remaining chunks
    if current_group:
        parent_groups.append(current_group)

    # Merge the last group if it's too weak (too small)
    if len(parent_groups) > 1:
        last_group = parent_groups[-1]
        last_group_len = sum(len(d.page_content) for d in last_group)
        
        if last_group_len < min_parent_size:
            previous_group = parent_groups[-2]
            previous_group.extend(last_group)
            parent_groups.pop() # Remove the now-merged last group

    return parent_groups

# ================================================================
# Main Function to Split Parent and Child Chunks
# ================================================================

def split_parent_child_chunks(
    text: str,
    file_name: str, 
    parent_target_chars: int = 1800,
    child_max_chars: int = 600,
    min_parent_chars: int = 900,
    min_child_chars: int = 50
) -> Tuple[List[ParentChunkModel], List[ChildChunkModel]]:
    """
    Splits text using a 'Bottom-Up' strategy: first creating atomic Child Chunks (vectors), 
    then aggregating them into larger Parent Chunks (context windows).

    Sequence:
    1. Splits text into atomic Child Chunks (vectors).
    2. Merge small/broken child chunks.
    3. Aggregates children into larger Parent Contexts.
    4. Converts everything into Pydantic Parent and Child models.

    Args:
        text (str): The raw text content.
        file_name (str): Original filename for metadata.
        parent_target_chars (int): Ideal size for the Context Window (Parent).
        child_max_chars (int): Hard limit for Vector chunks (Child).
        min_parent_chars (int): If the final parent chunk is smaller than this, 
                              it will be merged into the previous one.
        min_child_chars (int): Minimum size for child chunks; smaller ones 
                               will be merged with the next chunk.

    Returns:
        Tuple containing a list of ParentChunkModels and a list of ChildChunkModels.
    """
    if not text.strip():
        return [], []
    
    # 1. Generate and Merge Child Chunks
    raw_children_docs = _create_initial_child_chunks(text, file_name, child_max_chars)
    valid_children_docs = _merge_small_chunks(raw_children_docs, min_child_chars)

    # 2. Group Children into Parents Chunks
    parent_groups = _group_children_into_parents(
        valid_children_docs,
        parent_target_chars,
        min_parent_chars
    )

    # 3. Model Conversion
    final_parent_chunks = []
    final_child_chunks = []
    child_global_index = 0

    for group in parent_groups:
        # Assign a unique ID to the Parent (To link Children later)
        parent_id = str(uuid4())

        # Join children with newlines to reconstruct the Parent Text
        parent_content = " ".join([doc.page_content for doc in group])

        # A. Create Parent Model (The Context)
        final_parent_chunks.append(
            ParentChunkModel(
                db_id=parent_id,
                content=parent_content,
                document_name=file_name,
            )
        )

        # B. Create Child Models (The Vectors)
        # Each child stores the 'parent_id' so the LLM can find the full context later
        for child_doc in group:
            final_child_chunks.append(
                ChildChunkModel(
                    index=child_global_index,
                    text=child_doc.page_content.strip(),
                    parent_id=parent_id, 
                    file_name=file_name,
                )
            )
            child_global_index += 1
            
    return final_parent_chunks, final_child_chunks