"""
Authentication routes: signup, login, and the current-user probe.

GET /auth/me is protected by the get_current_user dependency (and reused by the
frontend to answer "am I logged in"). As of Phase 4 the chat/threads/files
routes are protected too and scope every query by the authenticated user's id;
the legacy USER_ID = "default-user" constant is no longer read by any route.
"""
from datetime import datetime

from bson import ObjectId
from bson.errors import InvalidId
from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from pydantic import BaseModel, EmailStr, field_validator
from pymongo.errors import DuplicateKeyError

from app.core import config
from app.core.config import (
    COOKIE_SECURE,
    JWT_EXPIRE_DAYS,
    RATE_LIMIT_FORGOT_PASSWORD,
    RATE_LIMIT_LOGIN,
    RATE_LIMIT_RESET_PASSWORD,
)
from app.core.database import users_collection
from app.core.rate_limit import limiter
from app.dependencies.auth import get_current_user
from app.services.auth_service import (
    create_access_token,
    effective_token_version,
    hash_password,
    verify_password,
)
from app.services.email_service import get_email_sender
from app.services.password_reset_service import (
    create_reset_token,
    email_throttle_allow,
    mark_all_user_tokens_used,
    verify_reset_token,
    consume_reset_token,
)
from models import User, UserCreate, UserPublic, validate_password_rules

router = APIRouter(prefix="/auth", tags=["auth"])


class LoginRequest(BaseModel):
    """Plain-JSON login body (not OAuth2PasswordRequestForm) so it is easy to
    test with curl and matches how the Next.js frontend will POST."""
    email: EmailStr
    password: str


class ForgotPasswordRequest(BaseModel):
    """Body for POST /auth/forgot-password."""
    email: EmailStr


class ResetPasswordRequest(BaseModel):
    """Body for POST /auth/reset-password.

    new_password delegates to validate_password_rules (models.py) -- the SAME
    shared rule set UserCreate.password uses for signup, so the two paths can
    never enforce different rules. Any future rule change happens in that one
    function only.
    """
    token: str
    new_password: str

    @field_validator("new_password")
    @classmethod
    def _password_rules(cls, value: str) -> str:
        return validate_password_rules(value)


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
        # Session-invalidation counter (see models.User.token_version). New
        # accounts start at 1 explicitly; only pre-backfill docs lack it.
        "token_version": 1,
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
@limiter.limit(RATE_LIMIT_LOGIN)
async def login(request: Request, payload: LoginRequest, response: Response):
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

    # The token carries the user's CURRENT token_version as its "tv" claim, so
    # a later version bump (password reset) invalidates this session. Missing
    # field on a pre-backfill user doc means version 1.
    token = create_access_token(
        str(user_doc["_id"]),
        effective_token_version(user_doc.get("token_version")),
    )

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


# ---------------------------------------------------------------------------
# Password reset (Phase 3). Self-gating via PASSWORD_RESET_ENABLED -> 404 when
# off (matching the KB router's flag pattern), so registering these routes is
# invisible on a disabled deployment.
# ---------------------------------------------------------------------------

# The one response forgot-password ever returns (flag-on). Identical for a
# real account, an unknown address, a throttled address, and a failed send, so
# the endpoint cannot be used to enumerate registered users.
_FORGOT_GENERIC_RESPONSE = {
    "message": "If an account exists for that address, a reset link has been sent."
}

# The one 400 reset-password ever returns for a bad token. Unknown, expired,
# and already-used tokens are deliberately indistinguishable.
_RESET_TOKEN_ERROR = "Invalid or expired reset token"


def _require_reset_enabled() -> None:
    """404 when the password-reset feature flag is off. Read at call time
    (config module attribute) so tests can toggle it without re-import."""
    if not config.PASSWORD_RESET_ENABLED:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Not found")


def _build_reset_email(reset_url: str) -> tuple:
    """Return (subject, html, text) for the reset email. The raw URL appears
    ONLY here and in the sender's own output -- never in server logs."""
    minutes = config.RESET_TOKEN_TTL_MINUTES
    subject = "Reset your password"
    text = (
        "A password reset was requested for your account.\n\n"
        "Open this link to choose a new password (valid for %d minutes):\n"
        "%s\n\n"
        "If you did not request this, you can ignore this email; your "
        "password is unchanged." % (minutes, reset_url)
    )
    html = (
        "<p>A password reset was requested for your account.</p>"
        '<p><a href="%s">Reset your password</a> (valid for %d minutes)</p>'
        "<p>If you did not request this, you can ignore this email; your "
        "password is unchanged.</p>" % (reset_url, minutes)
    )
    return subject, html, text


async def _apply_password_reset(user_doc: dict, new_password: str) -> bool:
    """Set the new password hash and bump token_version in ONE atomic $set.

    The bump is read-modify-write ON PURPOSE, never $inc: $inc on a doc
    MISSING the field creates it at 1, which EQUALS the effective default
    get_current_user assumes for pre-backfill docs -- old sessions would
    silently keep working. Reading effective_token_version() (missing/None ->
    1) and writing that + 1 guarantees the stored version moves PAST every
    outstanding token's "tv" claim. Returns False if the user vanished.
    """
    new_version = effective_token_version(user_doc.get("token_version")) + 1
    result = await users_collection.update_one(
        {"_id": user_doc["_id"]},
        {
            "$set": {
                "hashed_password": hash_password(new_password),
                "token_version": new_version,
            }
        },
    )
    return result.matched_count == 1


@router.post("/forgot-password")
@limiter.limit(RATE_LIMIT_FORGOT_PASSWORD)
async def forgot_password(request: Request, payload: ForgotPasswordRequest):
    """Request a password-reset email.

    Anti-enumeration contract: every outcome (account exists, does not exist,
    per-email throttle tripped, email send failed) returns the SAME 200 body,
    and the user lookup runs on every path so the timing profile does not
    short-circuit on unknown addresses. Per-IP limit via slowapi (this
    decorator); per-email limit via Redis (email_throttle_allow), which also
    counts unknown addresses so the throttle is not an oracle either.

    The raw token / reset URL are handed ONLY to the email sender; they are
    never written to server logs (the console sender's own stdout output is
    that sender's deliberate delivery mechanism).
    """
    _require_reset_enabled()
    email = _normalize_email(payload.email)

    # Both of these run regardless of whether the account exists.
    allowed = await email_throttle_allow(email)
    user_doc = await users_collection.find_one({"email": email})

    if not allowed:
        print("[password-reset] per-email throttle tripped; not sending")
        return _FORGOT_GENERIC_RESPONSE
    if user_doc is None:
        return _FORGOT_GENERIC_RESPONSE

    raw_token = await create_reset_token(str(user_doc["_id"]))
    reset_url = (
        config.APP_BASE_URL.rstrip("/") + "/reset-password?token=" + raw_token
    )
    subject, html, text = _build_reset_email(reset_url)
    try:
        sent = get_email_sender().send_email(user_doc["email"], subject, html, text)
    except Exception as exc:
        # Type name only: an exception message could quote request payloads,
        # and nothing containing the token/URL may reach the logs.
        print("[password-reset] email send raised %s" % type(exc).__name__)
        sent = False
    if not sent:
        print("[password-reset] email send FAILED for a reset request")
    return _FORGOT_GENERIC_RESPONSE


@router.post("/reset-password")
@limiter.limit(RATE_LIMIT_RESET_PASSWORD)
async def reset_password(
    request: Request, payload: ResetPasswordRequest, response: Response
):
    """Complete a password reset with a token from the reset email.

    Step order is deliberate -- the token is consumed only AFTER the password
    write succeeds:
      1. verify_reset_token (read-only -- a failure here consumes nothing)
      2. load the user (failure -> generic 400, token still unconsumed)
      3. write new hash + bumped token_version in one atomic update; if THIS
         fails the token remains valid and the user can simply retry, so the
         token is never left consumed with the password unchanged
      4. consume the presented token, then mark every other outstanding token
         for the user as used; a failure past step 3 leaves at most a live
         token for an account whose password was just changed by the person
         holding that mailbox -- reusing it repeats a reset, it cannot undo one
      5. clear the auth cookie (the token_version bump already invalidated
         every outstanding session, this just drops the stale cookie too)
    """
    _require_reset_enabled()

    user_id = await verify_reset_token(payload.token)
    if user_id is None:
        raise HTTPException(status_code=400, detail=_RESET_TOKEN_ERROR)

    try:
        object_id = ObjectId(user_id)
    except (InvalidId, TypeError):
        raise HTTPException(status_code=400, detail=_RESET_TOKEN_ERROR)
    user_doc = await users_collection.find_one({"_id": object_id})
    if user_doc is None:
        raise HTTPException(status_code=400, detail=_RESET_TOKEN_ERROR)

    if not await _apply_password_reset(user_doc, payload.new_password):
        raise HTTPException(status_code=400, detail=_RESET_TOKEN_ERROR)

    # Past this point the password IS changed; token cleanup must not undo
    # that with an error response. consume_reset_token returning False here
    # means a concurrent request with the same token won the race -- same
    # outcome for the account owner either way.
    await consume_reset_token(payload.token)
    await mark_all_user_tokens_used(user_id)

    response.delete_cookie(
        key="access_token",
        path="/",
        samesite="lax",
        secure=COOKIE_SECURE,
    )
    return {"ok": True}
