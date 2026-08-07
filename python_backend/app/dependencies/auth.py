"""
Authentication dependency: get_current_user.

Resolves the request's JWT -- taken from the "access_token" httpOnly cookie if
present, otherwise the "Authorization: Bearer <token>" header -- to the User it
represents. Every failure mode (no token, garbage/expired token, missing "sub",
or a user that no longer exists) collapses to the SAME opaque 401 so a caller
cannot tell why it failed.
"""
from typing import Optional

from bson import ObjectId
from bson.errors import InvalidId
from fastapi import Cookie, Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from jose import JWTError

from app.core.database import users_collection
from app.services.auth_service import decode_access_token, effective_token_version
from models import User, UserPublic

# HTTPBearer reads "Authorization: Bearer <token>" directly. We use it (not
# OAuth2PasswordBearer) because login is plain JSON, not an OAuth2 form.
# auto_error=False means a missing/malformed header yields credentials=None
# instead of HTTPBearer's built-in 403 -- we normalize EVERYTHING to 401 below
# so an absent header and a bad token look identical to the client.
_bearer_scheme = HTTPBearer(auto_error=False)

# One shared 401. WWW-Authenticate: Bearer is the spec-correct challenge header.
_credentials_exception = HTTPException(
    status_code=status.HTTP_401_UNAUTHORIZED,
    detail="Could not validate credentials",
    headers={"WWW-Authenticate": "Bearer"},
)


async def get_current_user(
    access_token: Optional[str] = Cookie(default=None),
    credentials: Optional[HTTPAuthorizationCredentials] = Depends(_bearer_scheme),
) -> User:
    """Resolve the request's token to the User it identifies, or raise 401.

    Token source precedence:
      1. the "access_token" httpOnly cookie (browser clients), else
      2. the Authorization: Bearer header (existing header-based clients/tests).
    After a token is found the steps are unchanged: decode+verify the JWT ->
    read "sub" (the user id) -> load that user from the DB. A deleted user or a
    stale token for a since-removed account fails at the final lookup.
    """
    # Cookie takes precedence over the Authorization header.
    token = access_token
    if not token and credentials is not None:
        token = credentials.credentials
    if not token:
        # Neither source supplied a token.
        raise _credentials_exception

    try:
        payload = decode_access_token(token)
    except JWTError:
        # Covers bad signature, malformed token, AND expiry
        # (ExpiredSignatureError is a JWTError subclass).
        raise _credentials_exception

    user_id = payload.get("sub")
    if not user_id:
        raise _credentials_exception

    try:
        object_id = ObjectId(user_id)
    except (InvalidId, TypeError):
        # "sub" is not a valid Mongo id -> it cannot map to a real user.
        raise _credentials_exception

    user_doc = await users_collection.find_one({"_id": object_id})
    if user_doc is None:
        # User was deleted after the token was issued.
        raise _credentials_exception

    # Session invalidation: the token's "tv" claim must match the user's
    # stored token_version. Both sides default to 1 when absent (a JWT minted
    # before the "tv" claim existed, or a user doc from before the backfill),
    # so pre-existing sessions keep working until a DELIBERATE version bump
    # (password reset) revokes them. Same opaque 401 as every other failure.
    try:
        token_tv = effective_token_version(payload.get("tv"))
        stored_tv = effective_token_version(user_doc.get("token_version"))
    except (TypeError, ValueError):
        # A non-numeric value on either side can never match anything.
        raise _credentials_exception
    if token_tv != stored_tv:
        raise _credentials_exception

    return User(
        id=str(user_doc["_id"]),
        email=user_doc["email"],
        hashed_password=user_doc["hashed_password"],
        full_name=user_doc.get("full_name"),
        created_at=user_doc["created_at"],
        role=user_doc.get("role", "user"),
        token_version=stored_tv,
    )


async def get_current_user_public(
    current_user: User = Depends(get_current_user),
) -> UserPublic:
    """Same identity check as get_current_user, but returns the client-safe
    UserPublic (no hashed_password). Convenience for response-facing routes."""
    return UserPublic(
        id=current_user.id,
        email=current_user.email,
        full_name=current_user.full_name,
        role=current_user.role,
    )
