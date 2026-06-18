"""
FastAPI Application Entry Point - Modular Structure
"""
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from slowapi.errors import RateLimitExceeded

from app.core.config import CORS_ORIGINS
from app.core.database import close_mongo_connection, ensure_indexes
from app.core.rate_limit import limiter, rate_limit_exceeded_handler
from app.routers import chat, threads, files, auth

# Initialize FastAPI
app = FastAPI(
    title="AI Geotechnical Chat API",
    description="RAG-powered Geotechnical AI Assistant using Groq, MongoDB Atlas, and Redis",
    version="2.0.0"
)

# Rate limiting (slowapi). Register the limiter on app.state and a clean 429
# handler so a tripped limit returns JSON, never a 500. Limits are applied
# per-route via @limiter.limit decorators (see auth/chat/files routers).
app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, rate_limit_exceeded_handler)

# Configure CORS. allow_credentials=True is REQUIRED so the browser will send
# and accept the httpOnly access_token cookie on cross-origin requests
# (http://localhost:3000 -> http://localhost:8000 in dev). The CORS spec forbids
# combining credentials with a "*" origin and browsers reject it, so
# allow_origins MUST be an explicit list of exact origins -- see CORS_ORIGINS in
# config (the "*" entry was removed for exactly this reason).
app.add_middleware(
    CORSMiddleware,
    allow_origins=CORS_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Include routers
app.include_router(chat.router)
app.include_router(threads.router)
app.include_router(files.router)
app.include_router(auth.router)


@app.get("/")
async def root():
    """Health check endpoint"""
    return {
        "status": "ok",
        "message": "AI Geotechnical Chat API is running - Modular Architecture",
        "version": "2.0.0",
        "architecture": "modular"
    }


@app.on_event("startup")
async def startup_event():
    """Ensure DB indexes (e.g. unique users.email) exist before serving."""
    await ensure_indexes()


@app.on_event("shutdown")
async def shutdown_event():
    """Clean up resources on shutdown"""
    await close_mongo_connection()


if __name__ == "__main__":
    import uvicorn
    
    # Run the server
    uvicorn.run(
        "app.main:app",  # Updated to use app.main since file is now in app/
        host="0.0.0.0",
        port=8000,
        reload=True,
        log_level="info"
    )
