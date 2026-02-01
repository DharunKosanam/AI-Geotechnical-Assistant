"""
Chat endpoints for handling messages and streaming responses using Groq + RAG
"""
import asyncio
import json
from fastapi import APIRouter, HTTPException, status
from fastapi.responses import StreamingResponse
from datetime import datetime
from typing import List

from models import ChatRequest, ChatResponse, RAGChatRequest, RAGChatResponse
from app.core.config import USER_ID
from app.core.database import conversations_collection, files_collection
from app.services.llm_service import get_llm, generate_answer_with_groq
from app.services.rag_service import query_with_context, query_vector_store

router = APIRouter(tags=["chat"])


@router.post("/chat", response_model=RAGChatResponse)
async def chat_with_rag(request: RAGChatRequest):
    """
    Main RAG endpoint: Query the vector store, retrieve context, and generate an answer.
    
    This endpoint:
    1. Queries the vector store for relevant document chunks
    2. Formats the context for the LLM
    3. Generates an answer using Groq with RAG context
    4. Returns the answer with source citations
    
    Args:
        request: RAGChatRequest containing query and optional conversation history
        
    Returns:
        RAGChatResponse with answer and list of source filenames
    """
    try:
        print(f"📨 Received query: {request.query}")
        
        # Step 1: Query vector store for relevant chunks
        print("🔍 Querying vector store...")
        chunks = await query_vector_store(request.query, top_k=5)
        print(f"   Found {len(chunks)} relevant chunks")
        
        # Step 2: Format context and extract sources
        if chunks and len(chunks) > 0:
            # Format chunks into a single context string
            context = "\n\n".join([
                f"[Source: {chunk['filename']}]\n{chunk['text']}"
                for chunk in chunks
            ])
            
            # Extract unique source filenames
            sources = list(set([chunk['filename'] for chunk in chunks]))
            print(f"   Sources: {', '.join(sources)}")
        else:
            # No chunks found - use empty context
            context = ""
            sources = []
            print("   ⚠️  No relevant chunks found in vector store")
        
        # Step 3: Generate answer with Groq using RAG context
        print("🤖 Generating answer with Groq...")
        answer = await generate_answer_with_groq(
            query=request.query,
            context=context,
            history=request.history
        )
        print(f"   ✓ Generated answer ({len(answer)} chars)")
        
        # Step 4: Return response
        return RAGChatResponse(
            answer=answer,
            sources=sources
        )
        
    except Exception as error:
        print(f"❌ Error in chat endpoint: {error}")
        import traceback
        traceback.print_exc()
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to generate answer: {str(error)}"
        )


# In-memory storage for thread messages (can be moved to MongoDB for persistence)
_thread_messages = {}


async def get_thread_messages(thread_id: str):
    """Get messages for a thread"""
    if thread_id not in _thread_messages:
        _thread_messages[thread_id] = []
    return _thread_messages[thread_id]


async def add_message_to_thread(thread_id: str, role: str, content: str):
    """Add a message to a thread"""
    if thread_id not in _thread_messages:
        _thread_messages[thread_id] = []
    
    message = {
        "id": f"msg_{len(_thread_messages[thread_id])}",
        "role": role,
        "content": content,
        "created_at": datetime.now().isoformat()
    }
    _thread_messages[thread_id].append(message)
    return message


async def save_conversation_to_db(thread_id: str):
    """Helper function to save/update conversation in MongoDB"""
    try:
        existing_conversation = await conversations_collection.find_one({
            "userId": USER_ID,
            "threadId": thread_id
        })
        
        if not existing_conversation:
            conversation_doc = {
                "userId": USER_ID,
                "threadId": thread_id,
                "name": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                "isGroup": False,
                "createdAt": datetime.now(),
                "updatedAt": datetime.now()
            }
            await conversations_collection.insert_one(conversation_doc)
            print(f"✅ Created new conversation in MongoDB: {thread_id}")
        else:
            await conversations_collection.update_one(
                {"userId": USER_ID, "threadId": thread_id},
                {"$set": {"updatedAt": datetime.now()}}
            )
            print(f"✅ Updated conversation timestamp in MongoDB: {thread_id}")
            
    except Exception as db_error:
        print(f"⚠️  MongoDB error (non-fatal): {db_error}")


@router.post("/stream")
async def send_chat_message_stream(request: ChatRequest):
    """
    Send a message and get streaming response from Groq with RAG context.
    """
    try:
        thread_id = request.threadId
        content = request.content
        
        # Validate inputs
        if not content or not content.strip():
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Content is required"
            )
            
        if not thread_id or not thread_id.strip():
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Thread ID is required"
            )
        
        print(f"📨 Received message for thread: {thread_id}")
        
        # Add user message to thread
        await add_message_to_thread(thread_id, "user", content)
        
        # Save conversation async
        asyncio.create_task(save_conversation_to_db(thread_id))
        
        # Generate streaming response
        async def generate_response_stream():
            try:
                # Perform RAG search to get relevant context
                print("🔍 Performing vector search...")
                rag_result = await query_with_context(content, top_k=5)
                
                # Get previous messages for context
                messages = await get_thread_messages(thread_id)
                
                # Build conversation history (last 5 messages)
                conversation_history = ""
                for msg in messages[-5:]:
                    conversation_history += f"{msg['role'].upper()}: {msg['content']}\n"
                
                # Build the prompt with RAG context
                system_prompt = """You are an expert AI assistant specializing in geotechnical engineering and soil mechanics. 
Use the provided context from documents to answer questions accurately. 
If the context doesn't contain relevant information, use your knowledge but mention this.
Always cite sources when using information from the provided documents."""
                
                context_section = ""
                if rag_result['context']:
                    context_section = f"\n\nRELEVANT DOCUMENTS:\n{rag_result['context']}\n"
                    print(f"✅ Found {rag_result['num_results']} relevant documents")
                else:
                    print("⚠️  No relevant documents found in vector store")
                
                full_prompt = f"""{system_prompt}

CONVERSATION HISTORY:
{conversation_history}
{context_section}

USER QUESTION: {content}

ASSISTANT:"""
                
                # Initialize Groq LLM
                llm = get_llm()
                
                # Stream the response
                print("🤖 Generating response with Groq...")
                
                # Start SSE stream
                yield "event: thread.message.delta\n".encode()
                initial_data = {"delta": {"content": [{"type": "text", "text": {"value": ""}}]}}
                yield f"data: {json.dumps(initial_data)}\n\n".encode()
                
                # Get streaming response from Groq
                full_response = ""
                response_stream = await llm.astream_complete(full_prompt)
                
                async for chunk in response_stream:
                    if chunk.text:
                        full_response += chunk.text
                        # Format as OpenAI-style SSE event
                        event_data = {
                            "delta": {
                                "content": [{
                                    "type": "text",
                                    "text": {"value": chunk.text}
                                }]
                            }
                        }
                        yield f"event: thread.message.delta\n".encode()
                        yield f"data: {json.dumps(event_data)}\n\n".encode()
                
                # Add assistant response to thread
                await add_message_to_thread(thread_id, "assistant", full_response)
                
                # Send completion event
                yield f"event: thread.message.completed\n".encode()
                yield f"data: {json.dumps({'status': 'completed'})}\n\n".encode()
                
                print(f"✅ Response completed ({len(full_response)} chars)")
                
            except Exception as error:
                print(f"❌ Stream error: {error}")
                error_event = {
                    "error": {
                        "message": str(error),
                        "type": "server_error"
                    }
                }
                yield f"event: error\n".encode()
                yield f"data: {json.dumps(error_event)}\n\n".encode()
        
        return StreamingResponse(
            generate_response_stream(),
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache",
                "Connection": "keep-alive",
                "X-Accel-Buffering": "no",
            }
        )
                
    except HTTPException:
        raise
    except Exception as error:
        print(f"❌ Unexpected error: {error}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Internal server error: {str(error)}"
        )
