"""
Rate limiting (slowapi), backed by the existing Redis.

Counters are stored in Redis via storage_uri = config.REDIS_URL -- the SAME
server the app already uses (the URI is assembled from the existing REDIS_*
settings). That means limits survive restarts and are shared across workers.
slowapi's `limits` library manages this storage connection internally; we do not
create a second Redis config.

This module is additive: it does not touch auth, the cookie mechanism, or
per-user data scoping.
"""
from fastapi import Depends, Request
from fastapi.responses import JSONResponse
from slowapi import Limiter
from slowapi.errors import RateLimitExceeded
from slowapi.util import get_remote_address

from app.core.config import REDIS_URL
from app.dependencies.auth import get_current_user
from models import User

# Default key = client IP. /auth/login (no authenticated user yet) uses this.
limiter = Limiter(key_func=get_remote_address, storage_uri=REDIS_URL)


def user_id_key(request: Request) -> str:
    """Per-user key for /chat and /upload.

    The authenticated user's id is stashed on request.state by
    `rate_limit_identify` during dependency resolution, which runs BEFORE the
    limit check. Falls back to client IP if somehow absent (e.g. misconfig), so
    the limiter never crashes for lack of a key.
    """
    uid = getattr(request.state, "rate_limit_user_id", None)
    return uid or get_remote_address(request)


async def rate_limit_identify(
    request: Request,
    current_user: User = Depends(get_current_user),
) -> User:
    """Wrapper dependency: resolves the user via the UNCHANGED get_current_user,
    stashes the id on request.state for user_id_key, and returns the same user.
    Endpoints use this in place of Depends(get_current_user) so the per-user key
    is available without duplicating any auth logic.
    """
    request.state.rate_limit_user_id = str(current_user.id)
    return current_user


def rate_limit_exceeded_handler(
    request: Request, exc: RateLimitExceeded
) -> JSONResponse:
    """Clean 429 JSON with a Retry-After header. No stack trace, no 500."""
    try:
        retry_after = int(exc.limit.limit.get_expiry())
    except Exception:
        retry_after = 60
    response = JSONResponse(
        status_code=429,
        content={"detail": "Too many requests, please wait a moment."},
    )
    response.headers["Retry-After"] = str(retry_after)
    return response
