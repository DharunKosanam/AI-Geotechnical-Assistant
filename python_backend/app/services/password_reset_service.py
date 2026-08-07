"""
Password-reset token storage (Phase 1).

Single-use, time-limited reset tokens backed by a password_reset_tokens
collection. Only sha256(token) is ever stored -- the raw token exists solely
in the reset email -- so a database leak (or a log of this collection) cannot
be replayed into a password change. sha256 (not bcrypt) is deliberate: the
input is a 256-bit random string, not a low-entropy password, so brute force
is already infeasible and a fast hash keeps verification cheap.

Expiry is enforced twice, on purpose:
  * verify/consume compare expires_at themselves -- this is the CORRECT gate.
  * a TTL index reaps expired docs -- garbage collection only. Mongo's TTL
    monitor runs about once a minute, so an expired doc can linger briefly;
    nothing may rely on the reaper for security.

Consumption is a single atomic update_one filtered on used_at == None and an
unexpired expires_at, so two concurrent consume calls for the same token
cannot both succeed.

Collections follow the codebase pattern (module-level Motor collection off
the shared db handle in app/core/database.py); the collection is declared
HERE rather than in database.py so Phase 1 adds files without touching
existing ones. Nothing imports this module yet.
"""
import hashlib
import secrets
from datetime import datetime, timedelta
from typing import Optional

from app.core import config
from app.core.database import db
from app.services.cache_service import get_redis_client

password_reset_tokens_collection = db["password_reset_tokens"]


def _hash_token(raw_token: str) -> str:
    return hashlib.sha256(raw_token.encode("utf-8")).hexdigest()


async def ensure_password_reset_indexes() -> None:
    """Create the collection's indexes. Idempotent -- safe to call anytime.

    expireAfterSeconds=0 makes the TTL index reap a doc as soon as its
    expires_at value passes (rather than N seconds after it). The unique
    token_hash index is defense-in-depth (a 256-bit token cannot realistically
    collide) and doubles as the fast lookup path for every verify/consume.

    Called on application startup from database.ensure_indexes() (Phase 3),
    so token operations never pay an index-creation round trip.
    """
    await password_reset_tokens_collection.create_index(
        "expires_at", expireAfterSeconds=0, name="ttl_expires_at"
    )
    await password_reset_tokens_collection.create_index(
        "token_hash", unique=True, name="uniq_token_hash"
    )


async def create_reset_token(user_id: str) -> str:
    """Issue a reset token for user_id and return the RAW token string.

    The raw token is returned exactly once, for the caller to place in the
    reset email; only its sha256 is stored. Lifetime comes from
    config.RESET_TOKEN_TTL_MINUTES at call time, frozen into an absolute
    expires_at on the doc. Indexes are guaranteed by startup (see
    ensure_password_reset_indexes).
    """
    raw_token = secrets.token_urlsafe(32)
    now = datetime.utcnow()
    await password_reset_tokens_collection.insert_one(
        {
            "token_hash": _hash_token(raw_token),
            "user_id": user_id,
            "created_at": now,
            "expires_at": now + timedelta(minutes=config.RESET_TOKEN_TTL_MINUTES),
            "used_at": None,
        }
    )
    return raw_token


async def verify_reset_token(raw_token: str) -> Optional[str]:
    """Return the token's user_id if it is valid, else None.

    Rejects (returns None, never raises) when the token is unknown, already
    used, or expired. Read-only: verification does NOT consume the token, so
    Phase 2 can validate the token on the reset-form GET without burning it.
    """
    doc = await password_reset_tokens_collection.find_one(
        {"token_hash": _hash_token(raw_token)}
    )
    if doc is None:
        return None
    if doc.get("used_at") is not None:
        return None
    if doc["expires_at"] <= datetime.utcnow():
        return None
    return doc["user_id"]


async def consume_reset_token(raw_token: str) -> bool:
    """Mark the token used. True the first time, False ever after.

    The filter re-checks used_at and expires_at inside the single atomic
    update, so a token cannot be consumed twice even by concurrent requests,
    and an expired token cannot be consumed at all.
    """
    now = datetime.utcnow()
    result = await password_reset_tokens_collection.update_one(
        {
            "token_hash": _hash_token(raw_token),
            "used_at": None,
            "expires_at": {"$gt": now},
        },
        {"$set": {"used_at": now}},
    )
    return result.modified_count == 1


async def mark_all_user_tokens_used(user_id: str) -> int:
    """Mark every outstanding reset token for user_id as used. Returns count.

    Called after a successful password reset so any OTHER token the user
    requested (multiple forgot-password submissions) cannot perform a second
    reset. Idempotent: already-used tokens are excluded by the filter.
    """
    result = await password_reset_tokens_collection.update_many(
        {"user_id": user_id, "used_at": None},
        {"$set": {"used_at": datetime.utcnow()}},
    )
    return result.modified_count


async def email_throttle_allow(email: str) -> bool:
    """Per-EMAIL reset-request throttle: True = allowed to send, False = over.

    Counts EVERY forgot-password request for the address in Redis (INCR with a
    1-hour expiry on first touch), whether or not an account exists -- counting
    only real accounts would make the throttle itself an enumeration oracle.
    Complements the per-IP slowapi limit: a rotating-IP caller still cannot
    mail-bomb one address.

    Uses the existing cache_service Redis client (same server, same
    degrade-gracefully philosophy): if Redis is down the throttle FAILS OPEN
    so password reset keeps working; the per-IP slowapi limit (which has its
    own storage handling) still applies. The key holds a hash of the address,
    not the address itself, so the mailbox being throttled never sits in
    Redis in plain text.
    """
    redis_client = get_redis_client()
    if not redis_client.is_connected:
        return True
    key = "pwreset_email:" + hashlib.sha256(email.encode("utf-8")).hexdigest()[:32]
    try:
        count = await redis_client.client.incr(key)
        if count == 1:
            await redis_client.client.expire(key, 3600)
        return count <= config.PASSWORD_RESET_EMAIL_MAX_PER_HOUR
    except Exception as exc:
        print("[password-reset] WARNING: email throttle check failed (%s); allowing"
              % type(exc).__name__)
        return True
