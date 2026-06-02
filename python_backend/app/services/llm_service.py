"""
LLM service for managing Groq AI interactions
"""
import os
import re
from typing import List, Dict, Optional
from llama_index.llms.groq import Groq
from dotenv import load_dotenv

from app.core.config import GROQ_MODEL

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
        model=GROQ_MODEL,
        api_key=groq_api_key,
        temperature=0.3,
        max_tokens=4096,
        request_timeout=60.0,
    )

    return llm


def _model_emits_thinking_tags(model_name: str) -> bool:
    """Only qwen3 models wrap their chain-of-thought in <think>...</think>."""
    return "qwen" in (model_name or "").lower()


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
    system_prompt = """You are an expert AI research assistant specializing in geotechnical engineering and soil mechanics.

Your task is to answer questions accurately using the provided context from technical documents.

SCOPE RULES:
- If the user's question is NOT related to geotechnical engineering AND there is no prior conversation context that establishes a geotechnical topic, politely decline. Say: "I'm here to help with questions related to geotechnical engineering and soil mechanics. If you have a specific question about topics like soil properties, erosion mechanisms, or other geotechnical concepts, feel free to ask."
- However, if the conversation history shows the user is in the middle of discussing a geotechnical topic, treat follow-up questions, clarifications, short responses ('ok', 'go on', 'more detail'), and summarization requests as on-topic — they inherit the topic of the conversation.
- If the user uploaded a document and asks about it, answer based on that document even if it is not geotechnical.

Guidelines:
- Use the provided context to answer questions
- When citing sources inline, use the academic reference titles provided in [Source: ...] tags (e.g. "Bolton (1986)"). NEVER use raw .pdf filenames in your answer.
- If the context doesn't have enough information, say so and provide general knowledge if helpful
- Be concise but thorough
- Use technical terminology appropriately
- Format your response with clear markdown: use ### for section headings, numbered lists, and bullet points
- Do NOT add a "Sources" or "References" section at the end of your response. The application automatically appends a formatted, clickable Google Scholar Sources list below your answer.

CRITICAL: Do NOT use <think> tags or any XML tags in your response. Provide direct, clear answers only."""
    
    # Format conversation history if provided. The caller (chat.py) already
    # caps this list (last 20 turns, 6000-token budget), so include all of it
    # rather than re-truncating to the last few messages.
    history_text = ""
    if history and len(history) > 0:
        history_text = "\n\nCONVERSATION HISTORY:\n"
        for msg in history:
            role = msg.get('role', 'user').upper()
            content = msg.get('content', '')
            history_text += f"{role}: {content}\n"
        # Debug: confirm prior turns actually reach the prompt (wire check).
        print(f"[PROMPT] Including {len(history)} prior turns:{history_text}")
    else:
        print("[PROMPT] No CONVERSATION HISTORY in prompt (history empty)")
    
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
        
        # Capture raw response
        raw_answer = response.text
        
        print(f"   [TEXT] Raw answer length: {len(raw_answer)} chars")
        if len(raw_answer) > 0:
            print(f"   [TEXT] First 300 chars: {raw_answer[:300]}")
        
        # Strip <think>...</think> only for models that actually emit them
        # (qwen3). Llama 4 Scout and other non-thinking models never produce
        # these tags, so the scrub is skipped to avoid eating legitimate <>
        # content.
        if _model_emits_thinking_tags(GROQ_MODEL):
            cleaned_answer = re.sub(r'<think>.*?</think>', '', raw_answer, flags=re.DOTALL | re.IGNORECASE)
            cleaned_answer = re.sub(r'<[^>]+>', '', cleaned_answer)
        else:
            cleaned_answer = raw_answer
        
        # Preserve markdown structure: strip each line individually,
        # then collapse runs of 3+ blank lines down to one blank line.
        lines = cleaned_answer.split('\n')
        final_answer = '\n'.join(line.strip() for line in lines)
        final_answer = re.sub(r'\n{3,}', '\n\n', final_answer).strip()
        
        print(f"   [CLEAN] Cleaned answer length: {len(final_answer)} chars")
        
        # If cleaning removed everything, try to extract content after </think>
        if not final_answer and raw_answer:
            print("   [WARNING] Cleaning removed all content, trying to extract after </think>")
            # Try to find content after the closing think tag
            match = re.search(r'</think>\s*(.*)', raw_answer, re.DOTALL | re.IGNORECASE)
            if match:
                final_answer = match.group(1).strip()
                print(f"   [EXTRACTED] Found content after </think>: {len(final_answer)} chars")
            else:
                # Last resort: return raw answer
                final_answer = raw_answer.strip()
                print("   [FALLBACK] Using raw answer")
        
        print(f"   [OK] Final answer length: {len(final_answer)} chars")
        
        return final_answer
    except Exception as e:
        raise Exception(f"Failed to generate answer with Groq: {str(e)}")

