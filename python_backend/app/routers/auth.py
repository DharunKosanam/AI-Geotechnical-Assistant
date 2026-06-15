"""
Authentication routes: signup, login, and the current-user probe.

GET /auth/me is protected by the get_current_user dependency (and reused by the
frontend to answer "am I logged in"). As of Phase 4 the chat/threads/files
routes are protected too and scope every query by the authenticated user's id;
the legacy USER_ID = "default-user" constant is no longer read by any route.
"""
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Response, status
from pydantic import BaseModel, EmailStr
from pymongo.errors import DuplicateKeyError

from app.core.config import COOKIE_SECURE, JWT_EXPIRE_DAYS
from app.core.database import users_collection
from app.dependencies.auth import get_current_user
from app.services.auth_service import (
    create_access_token,
    hash_password,
    verify_password,
)
from models import User, UserCreate, UserPublic

router = APIRouter(prefix="/auth", tags=["auth"])


class LoginRequest(BaseModel):
    """Plain-JSON login body (not OAuth2PasswordRequestForm) so it is easy to
    test with curl and matches how the Next.js frontend will POST."""
    email: EmailStr
    password: str


def _normalize_email(email: str) -> str:
    """Lowercase + strip so 'A@B.com ' and 'a@b.com' are the same account."""
    return email.strip().lower()


def _to_public(user_doc: dict) -> UserPublic:
    """Map a stored Mongo user document to the client-safe UserPublic shape."""
    return UserPublic(
        id=str(user_doc["_id"]),
        email=user_doc["email"],
        full_name=user_doc.get("full_name"),
        role=user_doc.get("role", "user"),
    )


@router.post(
    "/signup",
    response_model=UserPublic,
    status_code=status.HTTP_201_CREATED,
)
async def signup(payload: UserCreate):
    """Create a new account.

    Does NOT auto-login -- it only creates the account and returns the public
    user. The bcrypt hash is never returned (UserPublic has no such field).
    """
    email = _normalize_email(payload.email)

    if await users_collection.find_one({"email": email}) is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="An account with this email already exists",
        )

    user_doc = {
        "email": email,
        "hashed_password": hash_password(payload.password),
        "full_name": payload.full_name,
        "created_at": datetime.utcnow(),
        "role": "user",
    }

    try:
        result = await users_collection.insert_one(user_doc)
    except DuplicateKeyError:
        # Race: a concurrent signup inserted the same email between our check
        # and this insert. The unique index on users.email is the backstop.
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="An account with this email already exists",
        )

    user_doc["_id"] = result.inserted_id
    return _to_public(user_doc)


@router.post("/login")
async def login(payload: LoginRequest, response: Response):
    """Verify credentials, set the JWT as an httpOnly cookie, and ALSO return it
    in the JSON body (back-compat with header-based clients/tests).

    Returns the SAME generic 401 whether the email is unknown or the password
    is wrong, so a caller cannot probe which emails are registered.
    """
    email = _normalize_email(payload.email)
    user_doc = await users_collection.find_one({"email": email})

    hashed = user_doc.get("hashed_password") if user_doc else None
    if not hashed or not verify_password(payload.password, hashed):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Incorrect email or password",
        )

    token = create_access_token(str(user_doc["_id"]))

    # Set the JWT as an httpOnly cookie so the browser authenticates without JS
    # ever touching the token (httpOnly keeps it out of document.cookie, which
    # blocks XSS token theft). samesite="lax" lets top-level navigations carry
    # the cookie while still blocking most CSRF. `secure` is config-driven:
    #   * Production (HTTPS): COOKIE_SECURE=True -> cookie sent ONLY over TLS.
    #   * Local dev (http://localhost): COOKIE_SECURE=False, because a Secure
    #     cookie is never sent over http and the browser would silently drop it.
    # The JSON body below is intentionally unchanged.
    response.set_cookie(
        key="access_token",
        value=token,
        httponly=True,
        samesite="lax",
        secure=COOKIE_SECURE,
        max_age=JWT_EXPIRE_DAYS * 24 * 60 * 60,
        path="/",
    )

    return {
        "access_token": token,
        "token_type": "bearer",
        "user": _to_public(user_doc),
    }


@router.post("/logout")
async def logout(response: Response):
    """Clear the access_token cookie. No auth required -- clearing your own
    cookie is harmless, and logout should still succeed even if the token has
    already expired. delete_cookie must match the key/path (and attributes) used
    when the cookie was set, or the browser will not remove it.
    """
    response.delete_cookie(
        key="access_token",
        path="/",
        samesite="lax",
        secure=COOKIE_SECURE,
    )
    return {"ok": True}


@router.get("/me", response_model=UserPublic)
async def me(current_user: User = Depends(get_current_user)):
    """Return the authenticated user. Proves the get_current_user dependency:
    a valid token resolves to the right UserPublic (200); a missing, garbage,
    or expired token -- or a deleted user -- is rejected with 401 inside the
    dependency before this body runs.
    """
    return UserPublic(
        id=current_user.id,
        email=current_user.email,
        full_name=current_user.full_name,
        role=current_user.role,
    )
