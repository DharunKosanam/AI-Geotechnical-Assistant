"""
Pydantic models for request/response validation.
These mirror the TypeScript interfaces from the Next.js app.
"""

from pydantic import BaseModel, Field
from typing import Optional
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

