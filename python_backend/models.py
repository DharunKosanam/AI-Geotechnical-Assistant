"""
Pydantic models for request/response validation.
These mirror the TypeScript interfaces from the Next.js app.
"""

from pydantic import BaseModel, Field, EmailStr
from typing import Optional, List, Any, Dict
from datetime import datetime


class ChatRequest(BaseModel):
    """Request model for sending a chat message"""
    content: str = Field(..., description="The message content from the user")
    threadId: str = Field(..., description="The conversation thread ID")
    assistantId: Optional[str] = Field(None, description="Optional assistant ID override")


class ChatResponse(BaseModel):
    """Response model for chat endpoint"""
    success: bool
    run_id: str
    thread_id: str
    message: str = "Message sent successfully"


class ConversationDocument(BaseModel):
    """MongoDB document structure for conversations collection"""
    userId: str = "default-user"
    threadId: str
    name: str
    isGroup: bool = False
    createdAt: datetime
    updatedAt: datetime


class ErrorResponse(BaseModel):
    """Standard error response"""
    error: str
    details: Optional[str] = None


class ThreadCreateResponse(BaseModel):
    """Response model for thread creation"""
    threadId: str


class ThreadHistoryResponse(BaseModel):
    """Response model for thread history list"""
    threads: List[Dict[str, Any]]


class UpdateThreadRequest(BaseModel):
    """Request model for updating thread metadata.

    expectedCurrentName makes the rename conditional: the update applies only
    while the thread still carries exactly that name. The first-action title
    (format click on a fresh thread) uses it so an auto-timestamp placeholder
    is replaced atomically -- a name the user typed, or any other title that
    landed first, is never overwritten (the mismatch returns 409)."""
    threadId: str
    newName: Optional[str] = None
    isGroup: Optional[bool] = None
    expectedCurrentName: Optional[str] = None


class DeleteThreadRequest(BaseModel):
    """Request model for deleting a thread"""
    threadId: str
    isGroup: Optional[bool] = None


class CreateThreadHistoryRequest(BaseModel):
    """Request model for creating a thread in history"""
    threadId: str
    name: str
    isGroup: bool = False


class TitleGenerationRequest(BaseModel):
    """Request model for generating thread title"""
    message: Optional[str] = None  # Frontend sends 'message'
    content: Optional[str] = None  # Fallback for backward compatibility
    
    @property
    def text(self) -> str:
        """Get the message text from either field"""
        return self.message or self.content or ""


class SubmitActionsRequest(BaseModel):
    """Request model for submitting tool actions"""
    toolCallOutputs: List[Dict[str, Any]]
    runId: str


# Phase 4: RAG Chat Models
class RAGChatRequest(BaseModel):
    """Request model for RAG chat endpoint"""
    query: str = Field(..., description="The user's question or query")
    history: Optional[List[Dict[str, str]]] = Field(
        default=None, 
        description="Optional conversation history (list of {role, content} dicts)"
    )
    threadId: Optional[str] = Field(
        None,
        description="Thread ID to save messages to for history"
    )
    thread_id: Optional[str] = Field(
        None,
        description="Alternative field name for thread ID"
    )


class RAGChatResponse(BaseModel):
    """Response model for RAG chat endpoint"""
    answer: str = Field(..., description="The AI-generated answer")
    sources: List[Any] = Field(
        ...,
        description="List of source objects ({title, url}) or legacy strings",
    )
    no_high_confidence_sources: bool = Field(
        default=False,
        description=(
            "True when every retrieved chunk scored below the reranker "
            "threshold: 'sources' is empty and the answer is built only from "
            "low-confidence context. Defaults False for cached/legacy responses."
        ),
    )


# ---------------------------------------------------------------------------
# Authentication models (JWT email/password auth)
# ---------------------------------------------------------------------------
# Three-way split keeps the password hash off the wire:
#   UserCreate  -- what the client sends to sign up (plain password in)
#   User        -- the internal/stored shape (carries hashed_password)
#   UserPublic  -- what the client gets back (NO password field, ever)
class UserCreate(BaseModel):
    """Signup input. The plain password is hashed before storage and never
    persisted or returned."""
    email: EmailStr
    password: str = Field(..., description="Plain-text password (hashed before storage)")
    full_name: Optional[str] = None


class User(BaseModel):
    """Internal user record as stored in the 'users' collection.

    `id` is the stringified Mongo _id (None until inserted). `hashed_password`
    is a bcrypt hash -- it must never be serialized to a client; use UserPublic
    for responses.
    """
    id: Optional[str] = Field(default=None, description="Stringified Mongo _id")
    email: EmailStr
    hashed_password: str
    full_name: Optional[str] = None
    created_at: datetime = Field(default_factory=datetime.now)
    role: str = "user"


class UserPublic(BaseModel):
    """Client-facing user shape. Deliberately omits hashed_password."""
    id: str
    email: EmailStr
    full_name: Optional[str] = None
    role: str = "user"
