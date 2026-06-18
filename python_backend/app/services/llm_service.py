"""
LLM service for managing Groq AI interactions
"""
import os
import re
import sys
from typing import List, Dict, Optional
from llama_index.llms.groq import Groq
from dotenv import load_dotenv

from app.core.config import GROQ_MODEL

# Load environment variables
load_dotenv()


def safe_print(message: str) -> None:
    """print() that never crashes the request path on a non-UTF-8 console.

    On Windows the redirected stdout codec is often cp1252, which raises
    UnicodeEncodeError when a debug line interpolates a non-English query (e.g. a
    Chinese question) or a Greek symbol (γ, σ). We fall back to backslash escapes
    so logging degrades gracefully instead of taking down the chat request.
    """
    try:
        print(message)
    except UnicodeEncodeError:
        enc = sys.stdout.encoding or "ascii"
        print(message.encode(enc, errors="backslashreplace").decode(enc))


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


# Deterministic summary-request detection. The rewriter LLM is unreliable for
# these (with long histories it expands "summarize..." into a real topic query
# instead of returning NONE), so we catch them with cheap patterns first.
# Extend this list as we observe phrasings that slip through.
SUMMARY_TRIGGERS = [
    re.compile(pattern, re.IGNORECASE)
    for pattern in (
        r"^summari[sz]e",
        r"^sum up",
        r"^recap",
        r"^tl;?dr",
        r"what (have|did) we (discuss|talk about|cover)",
        r"what was (the|our) (discussion|conversation) about",
        r"give me a summary",
        r"can you summari[sz]e",
    )
]


# Unicode ranges for scripts that must never reach the English knowledge base.
# We delete these as a safety net (rules 2/4 of QUERY_REWRITE_PROMPT); Latin
# text, Greek technical symbols (γ, φ, σ, μ), the degree sign and math
# punctuation are all preserved.
_NON_LATIN_SCRIPTS = re.compile(
    "["
    "一-鿿"   # CJK unified ideographs (Chinese / Kanji)
    "぀-ヿ"   # Hiragana + Katakana
    "가-힯"   # Hangul syllables
    "Ѐ-ӿ"   # Cyrillic
    "؀-ۿ"   # Arabic
    "֐-׿"   # Hebrew
    "　-〿"   # CJK symbols & punctuation
    "＀-￯"   # Fullwidth / halfwidth forms
    "]+"
)


def _needs_translation(text: str) -> bool:
    """True when `text` contains characters from a non-Latin script (CJK,
    Cyrillic, Arabic, ...) and therefore needs translating to English. A plain
    Latin-script query (English, possibly with γ/σ/° symbols) returns False, so
    the first-turn rewrite LLM call can be skipped for it."""
    return _NON_LATIN_SCRIPTS.search(text) is not None


def _strip_non_latin(text: str) -> str:
    """Remove any leftover non-Latin-script characters the rewriter may have
    leaked, then collapse whitespace. Greek symbols and ASCII survive. Returns
    "" only if the whole string was non-Latin."""
    return re.sub(r"\s+", " ", _NON_LATIN_SCRIPTS.sub("", text)).strip()


# Merged rewrite prompt: history-aware follow-up resolution + summary detection
# (NONE) + language normalization (translate to English, Latin-script output).
# Used with .format(history_block=..., query=...).
QUERY_REWRITE_PROMPT = """You are a query rewriter for a geotechnical engineering RAG search system. Rewrite the user's LATEST MESSAGE into a single standalone English search query that retrieves well from an English-language knowledge base, using the CONVERSATION HISTORY for context.

STRICT RULES — follow these exactly:
1. Output ONLY the rewritten query. No explanations, no preamble, no quotes, no labels.
2. Output MUST be in English. Use Latin letters, digits, basic punctuation, and standard technical symbols only (e.g. °, μ, γ, φ, σ). Do NOT emit Chinese, Japanese, Korean, Arabic, Cyrillic, or any other non-Latin script.
3. If the LATEST MESSAGE is not in English, translate it to English first, then rewrite.
4. Do NOT keep the original-language text and do NOT add a parenthetical translation — English only.
5. Resolve context from the history: if the LATEST MESSAGE is vague or a follow-up ("how does it differ", "what about clay"), expand it into a self-contained query using the topic from the history.
6. If the LATEST MESSAGE is only an acknowledgement ("ok", "thanks", "continue", "go on"), output the previous USER question (translated to English) instead.
7. If the LATEST MESSAGE asks for a summary or recap of the conversation, output exactly: NONE
8. Keep technical terms (e.g. "Meyerhof", "Nγ", "OCR", "CPT") and numeric values with units (e.g. "35°", "10 kPa", "1.5 m") exactly as written.
9. If the LATEST MESSAGE is already a concise, clear, standalone English query, return it unchanged.
10. Maximum output length: 30 words.

Good rewrites:
- LATEST MESSAGE: "What is the Meyerhof bearing capacity factor Nγ for a friction angle of 35°, and how does it differ from Terzaghi's value?"
  Output: Meyerhof bearing capacity factor Nγ friction angle 35° comparison Terzaghi value
- LATEST MESSAGE: "为什么粘土斜坡分析中要考虑孔隙水压力？" (Chinese)
  Output: pore water pressure consideration in clay slope stability analysis
- HISTORY discusses Terzaghi bearing capacity; LATEST MESSAGE: "how does it differ from Meyerhof?"
  Output: difference between Terzaghi and Meyerhof bearing capacity factors
- LATEST MESSAGE: "summarize what we discussed"
  Output: NONE

Bad rewrites (NEVER do this):
- Mixing languages: "pore water pressure 孔隙水压力 analysis"
- Adding translations: "pore water pressure (孔隙水压力)"
- Adding commentary: "The user is asking about..."
- Wrapping in quotes: "Meyerhof Nγ 35°"

CONVERSATION HISTORY:
{history_block}

LATEST MESSAGE: {query}

Rewritten query:"""


async def rewrite_query_with_history(
    query: str,
    history: Optional[List[Dict[str, str]]] = None,
) -> str:
    """
    Rewrite the user's latest message into a single standalone *English* search
    query for the vector store. Two merged jobs:

      1. Language normalization — translate non-English queries to English and
         emit Latin-script output, so they retrieve against the English KB.
      2. History-aware resolution — expand vague follow-ups ("ok", "how does it
         differ") into self-contained queries using the conversation topic.

    Returns one of:
        - a short standalone English query (str) to embed + search, or
        - "NONE" when the user asks for a summary of the conversation — the
          caller should then skip retrieval and answer from history alone.

    Cost-aware: a plain-English first message (no history) is returned unchanged
    with no LLM call. Falls back to the raw query when there is no API key or the
    LLM call fails, so retrieval still happens.
    """
    # Deterministic summary detection — only meaningful mid-conversation. Skip
    # retrieval (and the rewriter LLM call) when the user asks to summarize.
    normalized = query.strip().lower()
    if history and any(pat.search(normalized) for pat in SUMMARY_TRIGGERS):
        safe_print(f'[QUERY_REWRITE] Original: "{query}" -> SKIPPED (summary detected, no LLM call)')
        return "NONE"

    # Skip the rewrite for a plain-English first turn: nothing to resolve and no
    # translation needed, so the raw query is already optimal (and free).
    if not history and not _needs_translation(query):
        return query

    groq_api_key = os.getenv("GROQ_API_KEY")
    if not groq_api_key:
        # No key — don't block retrieval, just use the raw query.
        return query

    # Dedicated low-temperature, short-output instance for deterministic,
    # cheap rewrites (separate from the answer LLM which runs at temp 0.3).
    rewriter = Groq(
        model=GROQ_MODEL,
        api_key=groq_api_key,
        temperature=0,
        max_tokens=120,
        request_timeout=30.0,
    )

    # Last 4 turns are enough context to resolve a follow-up. On a first-turn
    # (translation-only) call there is no history, so say so explicitly.
    recent = (history or [])[-4:]
    history_block = (
        "\n".join(
            f"{m.get('role', 'user').upper()}: {m.get('content', '')}" for m in recent
        )
        if recent
        else "(no prior conversation)"
    )

    rewrite_prompt = QUERY_REWRITE_PROMPT.format(history_block=history_block, query=query)

    try:
        response = await rewriter.acomplete(rewrite_prompt)
        rewritten = (response.text or "").strip()

        # Defensive cleanup: drop surrounding quotes/backticks and keep only the
        # first line in case the model adds commentary.
        if rewritten:
            rewritten = rewritten.splitlines()[0].strip().strip('"').strip("'").strip("`").strip()

        if not rewritten:
            return query
        if rewritten.upper() == "NONE":
            return "NONE"

        # Enforce the English contract: strip any leaked non-Latin characters
        # (rules 2/4 of the prompt). If that empties the result (translation
        # failed outright), fall back to the raw query so retrieval still runs.
        rewritten = _strip_non_latin(rewritten) or query

        # Keep it short (~30 words) — guard against the model over-expanding.
        words = rewritten.split()
        if len(words) > 30:
            rewritten = " ".join(words[:30])
        return rewritten
    except Exception as e:
        print(f"[QUERY_REWRITE] Rewrite failed ({e}); using raw query")
        return query

