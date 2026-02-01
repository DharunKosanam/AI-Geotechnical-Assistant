"""
RAG (Retrieval-Augmented Generation) service for querying vector store
"""
from typing import List, Dict, Any
import io
from datetime import datetime
from pypdf import PdfReader
from fastembed import TextEmbedding
from app.core.database import files_collection
from app.core.config import USER_ID


# Initialize FastEmbed model for 384-dimension embeddings
embedding_model = TextEmbedding(model_name="BAAI/bge-small-en-v1.5")


async def query_vector_store(query: str, top_k: int = 5) -> List[Dict[str, Any]]:
    """
    Query the MongoDB Atlas Vector Store for relevant documents.
    
    Args:
        query: The search query text
        top_k: Number of top results to return (default: 5)
        
    Returns:
        List of relevant text chunks with their metadata
    """
    # Generate embedding for the query using FastEmbed (384 dimensions)
    query_embeddings = list(embedding_model.embed([query]))
    query_vector = query_embeddings[0].tolist()
    
    # Perform MongoDB Vector Search using aggregate pipeline
    pipeline = [
        {
            "$vectorSearch": {
                "index": "vector_index",
                "path": "embedding",
                "queryVector": query_vector,
                "numCandidates": top_k * 10,  # Search more candidates for better results
                "limit": top_k
            }
        },
        {
            "$project": {
                "_id": 1,
                "text": 1,
                "filename": 1,
                "metadata": 1,
                "score": {"$meta": "vectorSearchScore"}
            }
        }
    ]
    
    # Execute the aggregation pipeline
    results = []
    async for doc in files_collection.aggregate(pipeline):
        results.append({
            "id": str(doc.get("_id")),
            "text": doc.get("text", ""),
            "filename": doc.get("filename", "unknown"),
            "metadata": doc.get("metadata", {}),
            "score": doc.get("score", 0.0)
        })
    
    return results


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
    Extract text from PDF file content.
    
    Args:
        file_content: PDF file content as bytes
        
    Returns:
        Extracted text from all pages
    """
    try:
        pdf_file = io.BytesIO(file_content)
        pdf_reader = PdfReader(pdf_file)
        
        text = ""
        for page in pdf_reader.pages:
            text += page.extract_text() + "\n"
        
        return text.strip()
    except Exception as e:
        print(f"❌ Error extracting text from PDF: {e}")
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


async def ingest_document(filename: str, file_content: bytes) -> Dict[str, Any]:
    """
    Ingest a PDF document: extract text, chunk, embed, and store in MongoDB.
    
    Args:
        filename: Name of the file
        file_content: File content as bytes
        
    Returns:
        Dictionary with ingestion results
        
    Raises:
        ValueError: If file is not a PDF or processing fails
    """
    # Validate it's a PDF
    if not filename.lower().endswith('.pdf'):
        raise ValueError("Only PDF files are supported. Please upload a PDF file.")
    
    print(f"📄 Ingesting document: {filename}")
    
    # Step 1: Extract text from PDF
    print("  1️⃣ Extracting text from PDF...")
    try:
        text = extract_text_from_pdf(file_content)
        print(f"     ✓ Extracted {len(text)} characters")
    except Exception as e:
        print(f"     ❌ Text extraction failed: {e}")
        raise
    
    if not text or len(text.strip()) < 10:
        raise ValueError("PDF appears to be empty or contains no extractable text")
    
    # Step 2: Chunk the text
    print("  2️⃣ Chunking text...")
    chunks = chunk_text(text, chunk_size=500, overlap=50)
    print(f"     ✓ Created {len(chunks)} chunks")
    
    if not chunks:
        raise ValueError("No text chunks could be created from the PDF")
    
    # Step 3: Generate embeddings for each chunk
    print("  3️⃣ Generating embeddings...")
    embeddings_list = list(embedding_model.embed(chunks))
    embeddings = [emb.tolist() for emb in embeddings_list]
    print(f"     ✓ Generated {len(embeddings)} embeddings (384-dim)")
    
    # Step 4: Create document objects
    print("  4️⃣ Creating document objects...")
    documents = []
    for i, (chunk, embedding) in enumerate(zip(chunks, embeddings)):
        doc = {
            "text": chunk,
            "filename": filename,
            "source": filename,
            "embedding": embedding,
            "userId": USER_ID,
            "category": "user_upload",
            "chunkIndex": i,
            "totalChunks": len(chunks),
            "metadata": {
                "chunkSize": len(chunk),
                "chunkIndex": i,
                "totalChunks": len(chunks),
                "originalFilename": filename
            },
            "createdAt": datetime.now()
        }
        documents.append(doc)
    
    print(f"     ✓ Prepared {len(documents)} documents")
    
    # Step 5: Insert into MongoDB
    print("  5️⃣ Inserting into MongoDB...")
    result = await files_collection.insert_many(documents)
    print(f"     ✓ Inserted {len(result.inserted_ids)} documents")
    
    print(f"✅ Document ingestion complete: {filename}")
    
    return {
        "filename": filename,
        "chunks_created": len(chunks),
        "total_characters": len(text),
        "documents_inserted": len(result.inserted_ids),
        "status": "success"
    }

