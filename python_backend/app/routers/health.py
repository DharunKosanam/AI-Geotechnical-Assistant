"""
Health check endpoints. PUBLIC by design -- no auth dependency, no rate-limit
decorator -- so an uptime monitor and manual debugging work even when the rest
of the app (or its DB) is unhealthy.

  GET /health        Shallow liveness. No deps touched. Just "is the web
                     process answering". This is what the uptime monitor pings.
  GET /health/ready  Deep readiness. Pings Mongo + Redis (reusing the existing
                     connections). 200 when all up, 503 if any down. Reports
                     only up/down booleans -- no connection strings, creds, or
                     hostnames.
"""
from fastapi import APIRouter
from fastapi.responses import JSONResponse

from app.core.database import mongo_client
from app.services.cache_service import get_redis_client

router = APIRouter(tags=["health"])


@router.get("/health")
async def health():
    """Liveness: fast, no MongoDB/Redis calls, nothing sensitive. A DB blip must
    NOT flip this to down (that is what /health/ready is for)."""
    return {"status": "ok"}


async def _check_mongo() -> bool:
    """Lightweight admin 'ping' on the existing Motor client. Never raises."""
    try:
        await mongo_client.admin.command("ping")
        return True
    except Exception:
        return False


async def _check_redis() -> bool:
    """PING the existing Redis client. Never raises."""
    try:
        client = get_redis_client().client
        if client is None:
            return False
        return bool(await client.ping())
    except Exception:
        return False


@router.get("/health/ready")
async def health_ready():
    """Readiness: report each dependency up/down + overall status. 200 when both
    are up, 503 otherwise (so a monitor can tell 'app up but DB down' apart from
    'app down'). Connection errors are caught and reported as false -- never a
    500."""
    mongo_ok = await _check_mongo()
    redis_ok = await _check_redis()
    all_ok = mongo_ok and redis_ok

    body = {
        "status": "ready" if all_ok else "degraded",
        "mongo": mongo_ok,
        "redis": redis_ok,
    }
    if all_ok:
        return body
    return JSONResponse(status_code=503, content=body)
