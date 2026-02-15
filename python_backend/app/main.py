"""
FastAPI Application Entry Point - Modular Structure
"""
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.core.config import CORS_ORIGINS
from app.core.database import close_mongo_connection
from app.routers import chat, threads, files

# Initialize FastAPI
app = FastAPI(
    title="AI Geotechnical Chat API",
    description="RAG-powered Geotechnical AI Assistant using Groq, MongoDB Atlas, and Redis",
    version="2.0.0"
)

# Configure CORS
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


@app.get("/")
async def root():
    """Health check endpoint"""
    return {
        "status": "ok",
        "message": "AI Geotechnical Chat API is running - Modular Architecture",
        "version": "2.0.0",
        "architecture": "modular"
    }


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
