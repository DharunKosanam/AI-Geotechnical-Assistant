"""Engineering Workspace (Phase 2).

Backend modules for the deterministic CPT lane and the AI Interpretation
feature. Everything here is gated behind the ``WORKSPACE_ENABLED`` feature flag
(see ``app.core.config``) and is completely independent of the live chatbot /
RAG stack -- importing this package must never touch chat, retrieval, or auth.
"""
