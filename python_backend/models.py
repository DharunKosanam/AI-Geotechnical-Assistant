"""
Pydantic models for request/response validation.
These mirror the TypeScript interfaces from the Next.js app.
"""

from pydantic import BaseModel, Field
from typing import Optional, List, Any, Dict
from datetime import datetime


class ChatRequest(BaseModel):
    """Request model for sending a chat message"""
    content: str = Field(..., description="The message content from the user")
    threadId: str = Field(..., description="The OpenAI thread ID")
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
    """Request model for updating thread metadata"""
    threadId: str
    newName: Optional[str] = None
    isGroup: Optional[bool] = None


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


class RAGChatResponse(BaseModel):
    """Response model for RAG chat endpoint"""
    answer: str = Field(..., description="The AI-generated answer")
    sources: List[str] = Field(..., description="List of source filenames used")
