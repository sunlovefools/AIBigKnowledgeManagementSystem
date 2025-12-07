from typing import List, Dict, Any, Tuple
# 💡 NEW IMPORTS: LangChain splitting and UUID for unique IDs
from langchain_text_splitters import RecursiveCharacterTextSplitter
from langchain_core.documents import Document
from pydantic import BaseModel, Field
from uuid import uuid4 

# Define Schema for Parent and Child chunks
class ParentChunkModel(BaseModel):
    """Schema for the large, context-rich Parent Documents (Document Store)."""
    # Uses Field(..., alias="_id") and by_alias=True config for AstraDB compatibility
    # The MongoDB driver prefers '_id', but we use 'parent_id' internally.
    _id: str = Field(..., alias="_id")
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

# ================================================================
# Parent-Child Splitting Logic (Used by Ingestion Route)
# ================================================================

def split_parent_child_chunks(
    text: str,
    file_name: str, 
    parent_max_chars: int = 1500,
    child_max_chars: int = 600
) -> Tuple[List[ParentChunkModel], List[ChildChunkModel]]:
    """
    Splits text into separate Parent (Context) and Child (Vector) chunks using LangChain RecursiveCharacterTextSplitter.

    Parent Chunks are larger segments of text while Child Chunks are smaller segments derived from each Parent Chunk.
    A metadata field `parent_id` is included in each Child Chunk to link it back to its Parent Chunk such that
    during query time, relevant Parent Chunks can be retrieved based on Child Chunk matches.

    Args:
        text (str): The raw, extracted text content of the document.
        file_name (str): The name of the original document (e.g., 'sample.pdf').
        parent_max_chars (int, optional): The maximum size for large **Parent Chunks** (Context Store). Defaults to 1500.
        child_max_chars (int, optional): The maximum size for small **Child Chunks** (Vector Store). Defaults to 600.

    Returns:
        Tuple[List[ParentChunkModel], List[ChildChunkModel]]: A tuple containing:
            - Index 0: `final_parent_chunks` (List of ParentChunkModel)
            - Index 1: `final_child_chunks` (List of ChildChunkModel)

    Notes:
        - Uses **LangChain's RecursiveCharacterTextSplitter** for semantically aware splitting.
        - The `child_max_chars` limits chunk size to ensure compatibility with embedding model input limits and database byte constraints.
        - Each child chunk will have a 10% overlap with adjacent chunks to preserve context.
    """
    if not text.strip():
        return [], []

    # 1. Define Splitters for both parent and child splitters
    # Purpose: Maximize context for the LLM during answer generation.
    parent_splitter = RecursiveCharacterTextSplitter(
        chunk_size=parent_max_chars,
        chunk_overlap=0,
        separators=["\n\n", "\n", " ", ""]
    )
    
    # Purpose: Maximize search precision and fit within the embedding model's optimal input size.
    child_splitter = RecursiveCharacterTextSplitter(
        chunk_size=child_max_chars, 
        chunk_overlap=int(child_max_chars * 0.1), # 10% overlap helps capture context around boundaries
        separators=["\n\n", "\n", " ", ""]
    )
    
    # 2. Create base document for the splitter to process (Document object from LangChain)
    base_doc = Document(page_content=text, metadata={"document_name": file_name})
    
    # 3. Split the document into parent chunks (Return List [Document])
    parent_docs = parent_splitter.split_documents([base_doc])
    
    final_parent_chunks = []
    final_child_chunks = []
    child_global_index = 0

    # 4. For each Parent Document, create Child Documents
    for parent_doc in parent_docs:
        # Generate a unique ID for this parent chunk
        parent_id = str(uuid4())
        
        # Format the Parent Chunk for insertion into the AstraDB Document Store (Parent_Store)
        final_parent_chunks.append({
            "_id": parent_id,
            "content": parent_doc.page_content.strip(),
            "document_name": parent_doc.metadata.get("document_name"),
        })
        
        # 5. Split Parent Document into Child Documents (Return List [Document])
        child_docs = child_splitter.split_documents([parent_doc])
        
        for child_doc in child_docs:
            # Format the Child Chunk for insertion into the AstraDB Vector Store (Vector_Store)
            final_child_chunks.append({
                "index": child_global_index,
                "text": child_doc.page_content.strip(),
                "parent_id": parent_id, # Links back to Parent Chunk
                "file_name": file_name,
            })
            child_global_index += 1
            
    return final_parent_chunks, final_child_chunks