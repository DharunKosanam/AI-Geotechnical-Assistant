"""
FastAPI Application Entry Point - Modular Structure
"""
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from slowapi.errors import RateLimitExceeded

from app.core.config import CORS_ORIGINS
from app.core.database import close_mongo_connection, ensure_indexes
from app.core.rate_limit import limiter, rate_limit_exceeded_handler
from app.routers import chat, threads, files, auth, health, inventory, kb, kb_web, highlights
from app.workspace.routes import router as workspace_router
from app.workspace import dataset_routes as workspace_dataset_routes

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
# Thread sharing (CHAT_SHARING_ENABLED): join/leave/member routes exist ONLY
# when the flag is on — flag-off the route table is byte-identical to today.
from app.core import config as _config  # noqa: E402

if _config.CHAT_SHARING_ENABLED:
    app.include_router(threads.sharing_router)
app.include_router(files.router)
app.include_router(auth.router)
app.include_router(health.router)
# Student KB upload (Phase 4). Self-gates via KB_UPLOAD_ENABLED -> 404 when off,
# so registering it is byte-identical to absence for the disabled deployment.
app.include_router(kb.router)
# Engineering Workspace (GeoPilot). Additive and self-gating via
# WORKSPACE_ENABLED -- including the router does not affect chat/RAG.
app.include_router(workspace_router)
# Message highlights + notes. Registered ONLY when HIGHLIGHTS_ENABLED is on --
# with the flag off the routes are absent (not present-and-404ing), so the
# route table is identical to before the feature.
highlights.register(app)
# GeoPilot instrument datasets. Same contract as highlights: the routes are
# included ONLY when INSTRUMENT_PARSERS_ENABLED is on; off = absent.
workspace_dataset_routes.register(app)
# KB web-page ingestion by pasted URL. Same contract: routes included ONLY
# when WEB_INGEST_ENABLED is on; off = absent, route table unchanged.
kb_web.register(app)
# Lab inventory CRUD. Same contract: routes included ONLY when
# INVENTORY_ENABLED is on; off = absent, route table unchanged.
inventory.register(app)


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
    # Inventory reminder digest ticker: started ONLY when both flags are on
    # (single uvicorn worker — see inventory_reminders.py). Flag-off: no task,
    # nothing scheduled, startup byte-identical to before.
    from app.core import config as _config

    if _config.INVENTORY_ENABLED and _config.INVENTORY_REMINDERS_ENABLED:
        import asyncio

        from app.services.inventory_reminders import reminders_loop

        asyncio.create_task(reminders_loop())


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
