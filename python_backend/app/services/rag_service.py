"""
RAG (Retrieval-Augmented Generation) service for querying vector store
"""
from typing import List, Dict, Any
import io
from datetime import datetime
import fitz  # PyMuPDF - better text extraction than pypdf
from fastembed import TextEmbedding
from app.core.database import files_collection
from app.core.config import USER_ID


# Initialize embedding model lazily to avoid blocking on import
_embedding_model = None

def get_embedding_model():
    """Get or initialize the embedding model (lazy loading)"""
    global _embedding_model
    if _embedding_model is None:
        print("[LOADING] Initializing embedding model (BAAI/bge-small-en-v1.5)...")
        _embedding_model = TextEmbedding(model_name="BAAI/bge-small-en-v1.5")
        print("[OK] Embedding model loaded successfully")
    return _embedding_model


async def _search_by_category(
    query_vector: List[float], 
    category: str, 
    limit: int
) -> List[Dict[str, Any]]:
    """
    Helper function to perform vector search filtered by category.
    
    NOTE: We search more results and filter client-side because MongoDB Atlas
    requires 'category' to be added to vector index as filterable field.
    This is a fallback approach that works without index changes.
    
    Args:
        query_vector: The embedding vector for the query
        category: Category to filter by ("user_upload" or "knowledge_base")
        limit: Maximum number of results to return
        
    Returns:
        List of relevant text chunks from the specified category
    """
    # Search more candidates since we'll filter client-side
    search_limit = limit * 20  # Get 20x more to ensure we find enough after filtering
    
    pipeline = [
        {
            "$vectorSearch": {
                "index": "vector_index",
                "path": "embedding",
                "queryVector": query_vector,
                "numCandidates": search_limit * 2,
                "limit": search_limit
                # NOTE: Filter removed - requires index update in MongoDB Atlas
            }
        },
        {
            "$match": {
                "category": category,  # Filter by category AFTER vector search
                "userId": USER_ID
            }
        },
        {
            "$limit": limit  # Limit to requested number after filtering
        },
        {
            "$project": {
                "_id": 1,
                "text": 1,
                "filename": 1,
                "category": 1,
                "metadata": 1,
                "score": {"$meta": "vectorSearchScore"}
            }
        }
    ]
    
    results = []
    async for doc in files_collection.aggregate(pipeline):
        results.append({
            "id": str(doc.get("_id")),
            "text": doc.get("text", ""),
            "filename": doc.get("filename", "unknown"),
            "category": doc.get("category", "unknown"),
            "metadata": doc.get("metadata", {}),
            "score": doc.get("score", 0.0)
        })
    
    return results[:limit]  # Ensure we return exactly 'limit' results


async def query_vector_store(query: str, top_k: int = 5) -> List[Dict[str, Any]]:
    """
    Query the MongoDB Atlas Vector Store with PRIORITIZED search.
    
    Search Strategy:
    1. Search user_upload files FIRST (top 5 results)
    2. Search knowledge_base files SECOND (top 3 results)
    3. Combine with user uploads prioritized
    
    This ensures user-uploaded files are ALWAYS prioritized over the large
    knowledge base, so user-specific content isn't crowded out.
    
    Args:
        query: The search query text
        top_k: Total number of results (split between categories)
        
    Returns:
        List of relevant text chunks with user uploads first
    """
    print(f"[SEARCH] Prioritized search for: {query[:50]}...")
    
    # Generate embedding for the query using FastEmbed (384 dimensions)
    model = get_embedding_model()
    query_embeddings = list(model.embed([query]))
    query_vector = query_embeddings[0].tolist()
    
    # STEP 1: Search user uploads FIRST (prioritized)
    print("[SEARCH] Step 1: Searching user uploads...")
    user_results = await _search_by_category(query_vector, "user_upload", limit=5)
    print(f"   Found {len(user_results)} chunks from user uploads")
    if user_results:
        user_files = list(set([r['filename'] for r in user_results]))
        print(f"   Files: {', '.join(user_files[:3])}")
    
    # STEP 2: Search knowledge base SECOND (supplementary)
    print("[SEARCH] Step 2: Searching knowledge base...")
    kb_results = await _search_by_category(query_vector, "knowledge_base", limit=3)
    print(f"   Found {len(kb_results)} chunks from knowledge base")
    if kb_results:
        kb_files = list(set([r['filename'] for r in kb_results]))
        print(f"   Files: {', '.join(kb_files[:3])}")
    
    # STEP 3: Combine results (user uploads FIRST)
    combined_results = user_results + kb_results
    
    print(f"[SEARCH] Combined total: {len(combined_results)} chunks")
    print(f"   Priority: {len(user_results)} user + {len(kb_results)} knowledge base")
    
    return combined_results


async def query_with_context(query: str, top_k: int = 5) -> Dict[str, Any]:
    """
    Query the vector store and return results with context.
    
    Args:
        query: The search query text
        top_k: Number of top results to return (default: 5)
        
    Returns:
        Dictionary containing the query, results, and formatted context
    """
    results = await query_vector_store(query, top_k)
    
    # Format context for LLM
    context = "\n\n".join([
        f"[Source: {r['filename']}]\n{r['text']}"
        for r in results
    ])
    
    return {
        "query": query,
        "results": results,
        "context": context,
        "num_results": len(results)
    }


def extract_text_from_pdf(file_content: bytes) -> str:
    """
    Extract text from PDF file content using PyMuPDF (better extraction quality).
    
    Args:
        file_content: PDF file content as bytes
        
    Returns:
        Extracted text from all pages
    """
    try:
        # Open PDF from bytes using PyMuPDF
        doc = fitz.open(stream=file_content, filetype="pdf")
        
        text = ""
        empty_pages = []
        
        # Extract text from each page
        for page_num in range(len(doc)):
            page = doc[page_num]
            page_text = page.get_text()
            
            # Check if page is empty or image-based
            if not page_text or len(page_text.strip()) == 0:
                empty_pages.append(page_num + 1)  # 1-indexed for user display
                print(f"      [WARNING] Page {page_num + 1} is empty or image-based (no extractable text)")
            else:
                text += page_text + "\n"
        
        # Close the document
        doc.close()
        
        # Show summary if there were empty pages
        if empty_pages:
            if len(empty_pages) == len(doc):
                print(f"      [ERROR] All {len(doc)} pages are empty or image-based!")
                print(f"      This PDF may contain only images or scanned documents.")
            else:
                print(f"      [INFO] {len(empty_pages)} out of {len(doc)} pages were empty/image-based: {empty_pages[:10]}")
        
        return text.strip()
    except Exception as e:
        print(f"[ERROR] Error extracting text from PDF with PyMuPDF: {e}")
        raise ValueError(f"Failed to extract text from PDF: {str(e)}")


def chunk_text(text: str, chunk_size: int = 500, overlap: int = 50) -> List[str]:
    """
    Split text into chunks with overlap.
    
    Args:
        text: Text to chunk
        chunk_size: Target size of each chunk in characters
        overlap: Number of characters to overlap between chunks
        
    Returns:
        List of text chunks
    """
    if not text or len(text.strip()) == 0:
        return []
    
    chunks = []
    start = 0
    text_length = len(text)
    
    while start < text_length:
        # Get chunk
        end = start + chunk_size
        chunk = text[start:end]
        
        # Try to break at sentence boundary if possible
        if end < text_length:
            # Look for sentence endings
            last_period = chunk.rfind('.')
            last_newline = chunk.rfind('\n')
            break_point = max(last_period, last_newline)
            
            if break_point > chunk_size * 0.5:  # Only break if we're past halfway
                chunk = text[start:start + break_point + 1]
                end = start + break_point + 1
        
        chunks.append(chunk.strip())
        
        # Move start position with overlap
        start = end - overlap
        
        # Avoid infinite loop
        if start <= end - chunk_size + overlap:
            start = end
    
    return [c for c in chunks if c]  # Filter out empty chunks


async def delete_document(filename: str) -> Dict[str, Any]:
    """
    Delete all vector chunks associated with a filename from MongoDB.
    
    Args:
        filename: Name of the file to delete
        
    Returns:
        Dictionary with deletion results
    """
    try:
        print(f"[DELETE] Deleting document: {filename}")
        print(f"[DELETE] Searching for filename: {repr(filename)}")
        
        # First, check what we have in the database
        sample = await files_collection.find_one({"userId": USER_ID})
        if sample:
            print(f"[DELETE] Sample filename in DB: {repr(sample.get('filename'))}")
        
        # Delete all chunks with matching filename or source
        result = await files_collection.delete_many({
            "$or": [
                {"filename": filename},
                {"source": filename}
            ],
            "userId": USER_ID
        })
        
        deleted_count = result.deleted_count
        print(f"[OK] Deleted {deleted_count} chunks for file: {filename}")
        
        return {
            "filename": filename,
            "deleted_count": deleted_count,
            "status": "success"
        }
    except Exception as e:
        print(f"[ERROR] Failed to delete document {filename}: {e}")
        raise ValueError(f"Failed to delete document: {str(e)}")


async def ingest_document(filename: str, file_content: bytes, category: str = "user_upload") -> Dict[str, Any]:
    """
    Ingest a PDF document: extract text, chunk, embed, and store in MongoDB.
    
    Args:
        filename: Name of the file
        file_content: File content as bytes
        category: Category of the file ("user_upload" or "knowledge_base")
        
    Returns:
        Dictionary with ingestion results
        
    Raises:
        ValueError: If file is not a PDF or processing fails
    """
    # Validate it's a PDF
    if not filename.lower().endswith('.pdf'):
        raise ValueError("Only PDF files are supported. Please upload a PDF file.")
    
    print(f"[FILE] Ingesting document: {filename}")
    
    # Step 1: Extract text from PDF
    print("  1. Extracting text from PDF...")
    try:
        text = extract_text_from_pdf(file_content)
        print(f"      Extracted {len(text)} characters")
    except Exception as e:
        print(f"     [ERROR] Text extraction failed: {e}")
        raise
    
    if not text or len(text.strip()) < 10:
        raise ValueError("PDF appears to be empty or contains no extractable text")
    
    # Step 2: Chunk the text
    print("  2. Chunking text...")
    chunks = chunk_text(text, chunk_size=500, overlap=50)
    print(f"      Created {len(chunks)} chunks")
    
    if not chunks:
        raise ValueError("No text chunks could be created from the PDF")
    
    # Step 3: Generate embeddings for each chunk
    print("  3. Generating embeddings...")
    model = get_embedding_model()
    embeddings_list = list(model.embed(chunks))
    embeddings = [emb.tolist() for emb in embeddings_list]
    print(f"      Generated {len(embeddings)} embeddings (384-dim)")
    
    # Step 4: Create document objects
    print("  4. Creating document objects...")
    documents = []
    for i, (chunk, embedding) in enumerate(zip(chunks, embeddings)):
        doc = {
            "text": chunk,
            "filename": filename,
            "source": filename,
            "embedding": embedding,
            "userId": USER_ID,
            "category": category,  # Use the category parameter
            "chunkIndex": i,
            "totalChunks": len(chunks),
            "metadata": {
                "chunkSize": len(chunk),
                "chunkIndex": i,
                "totalChunks": len(chunks),
                "originalFilename": filename,
                "category": category  # Also include in metadata for querying
            },
            "createdAt": datetime.now()
        }
        documents.append(doc)
    
    print(f"      Prepared {len(documents)} documents")
    
    # Step 5: Insert into MongoDB
    print("  5. Inserting into MongoDB...")
    result = await files_collection.insert_many(documents)
    print(f"      Inserted {len(result.inserted_ids)} documents")
    
    print(f"[OK] Document ingestion complete: {filename}")
    
    return {
        "filename": filename,
        "chunks_created": len(chunks),
        "total_characters": len(text),
        "documents_inserted": len(result.inserted_ids),
        "status": "success"
    }

