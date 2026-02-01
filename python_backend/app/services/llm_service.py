"""
LLM service for managing Groq AI interactions
"""
import os
from typing import List, Dict, Optional
from llama_index.llms.groq import Groq
from dotenv import load_dotenv

# Load environment variables
load_dotenv()


def get_llm() -> Groq:
    """
    Initialize and return a Groq LLM instance.
    
    Returns:
        Groq: Configured Groq LLM instance
        
    Raises:
        ValueError: If GROQ_API_KEY is not set in environment variables
    """
    groq_api_key = os.getenv("GROQ_API_KEY")
    
    if not groq_api_key:
        raise ValueError(
            "GROQ_API_KEY is not set in environment variables. "
            "Please add GROQ_API_KEY to your .env file."
        )
    
    llm = Groq(
        model="qwen/qwen3-32b",
        api_key=groq_api_key,
        temperature=0.3
    )
    
    return llm


async def generate_answer_with_groq(
    query: str, 
    context: str, 
    history: Optional[List[Dict[str, str]]] = None
) -> str:
    """
    Generate an answer using Groq LLM with RAG context and conversation history.
    
    Args:
        query: The user's question
        context: The relevant context from vector search (formatted string)
        history: Optional conversation history as list of {role, content} dicts
        
    Returns:
        The AI-generated answer as a string
        
    Raises:
        Exception: If LLM generation fails
    """
    # Initialize LLM
    llm = get_llm()
    
    # Build system prompt
    system_prompt = """You are an expert AI assistant specializing in geotechnical engineering and soil mechanics.

Your task is to answer questions accurately using the provided context from technical documents.

Guidelines:
- Use the provided context to answer questions
- If the context contains relevant information, cite the sources
- If the context doesn't have enough information, say so and provide general knowledge if helpful
- Be concise but thorough
- Use technical terminology appropriately
- Format your response clearly"""
    
    # Format conversation history if provided
    history_text = ""
    if history and len(history) > 0:
        history_text = "\n\nCONVERSATION HISTORY:\n"
        for msg in history[-5:]:  # Last 5 messages for context
            role = msg.get('role', 'user').upper()
            content = msg.get('content', '')
            history_text += f"{role}: {content}\n"
    
    # Format context section
    context_section = ""
    if context and context.strip():
        context_section = f"\n\nRELEVANT CONTEXT FROM DOCUMENTS:\n{context}\n"
    else:
        context_section = "\n\n[No relevant documents found in the knowledge base]\n"
    
    # Build the complete prompt
    full_prompt = f"""{system_prompt}
{history_text}
{context_section}

USER QUESTION: {query}

Please provide a detailed answer:"""
    
    # Generate response
    try:
        response = await llm.acomplete(full_prompt)
        return response.text.strip()
    except Exception as e:
        raise Exception(f"Failed to generate answer with Groq: {str(e)}")

