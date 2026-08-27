"""Small, dependency-free helpers for cleaning raw LLM output before parsing.

Lives in ``app.core`` (next to ``config``) so it is importable from both the
chat services and the workspace package without either importing the other
(``services.intent_router`` deliberately avoids importing ``workspace`` at
import time) and without pulling in ``llm_service``'s heavy imports.

Used by the ``think=False`` JSON classifiers (intent router, doc-scope router,
workspace calculator router, inventory feasibility extractor). Those calls run
with a tiny ``num_predict`` on every turn, so they cannot afford ``think=True``
(reasoning tokens exhaust the budget and content comes back empty) -- instead
they strip whatever reasoning qwen3 leaks and then pull the JSON object out
deterministically.
"""
from __future__ import annotations

from typing import Optional

_CLOSE_TAG = "</think>"


def strip_unpaired_think(text: str) -> str:
    """qwen3's chat template prefills the opening <think>, so with think=False
    the model emits only the CLOSING tag. Everything up to and including it is
    reasoning."""
    idx = text.rfind(_CLOSE_TAG)
    return text[idx + len(_CLOSE_TAG):].lstrip() if idx != -1 else text


def extract_last_json_object(text: str) -> Optional[str]:
    """Return the LAST balanced ``{...}`` block in ``text``, or None.

    Belt and braces for the JSON classifiers: even if reasoning prose (which
    may itself contain braces) survives ahead of the answer, the model's JSON
    object is the last thing it writes. Brace-balanced scan that ignores braces
    inside JSON string literals; a trailing unbalanced ``{`` yields None.
    """
    if not text:
        return None
    end = text.rfind("}")
    while end != -1:
        depth = 0
        in_str = False
        i = end
        # Walk backwards to find the matching opening brace for text[end].
        while i >= 0:
            ch = text[i]
            if in_str:
                # Backward scan: a quote closes the literal unless it is escaped.
                if ch == '"' and not (i > 0 and text[i - 1] == "\\"):
                    in_str = False
            elif ch == '"':
                in_str = True
            elif ch == "}":
                depth += 1
            elif ch == "{":
                depth -= 1
                if depth == 0:
                    return text[i : end + 1]
            i -= 1
        # No opening brace matched this closing one; try the previous "}".
        end = text.rfind("}", 0, end)
    return None
