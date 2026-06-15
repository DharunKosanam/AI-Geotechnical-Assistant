"""
Authentication service.

Provides password hashing (passlib bcrypt), JWT access-token creation, and JWT
decode/verification (python-jose, HS256). The decode side intentionally does
NOT translate failures into HTTP responses -- the get_current_user dependency
(app/dependencies/auth.py) owns that mapping.

Uses passlib's CryptContext with bcrypt. bcrypt silently truncates input at
72 bytes; passlib handles that internally, so callers do not need to.
"""
from datetime import datetime, timedelta

from jose import jwt
from passlib.context import CryptContext

from app.core.config import JWT_ALGORITHM, JWT_EXPIRE_DAYS, JWT_SECRET_KEY

# A single shared context. deprecated="auto" lets us add/rotate schemes later
# without breaking hashes already stored under bcrypt.
_pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")


def hash_password(plain: str) -> str:
    """Return a bcrypt hash of a plain-text password (safe to store)."""
    return _pwd_context.hash(plain)


def verify_password(plain: str, hashed: str) -> bool:
    """Return True if the plain-text password matches the stored bcrypt hash."""
    return _pwd_context.verify(plain, hashed)


def create_access_token(user_id: str) -> str:
    """Create a signed JWT access token for the given user id.

    Encodes the standard JWT claims "sub" (subject = user id) and "exp"
    (expiry = now + JWT_EXPIRE_DAYS), signed with JWT_SECRET_KEY using
    JWT_ALGORITHM (HS256). python-jose converts the datetime "exp" to a Unix
    timestamp internally. See decode_access_token for the inverse.
    """
    expire = datetime.utcnow() + timedelta(days=JWT_EXPIRE_DAYS)
    payload = {"sub": user_id, "exp": expire}
    return jwt.encode(payload, JWT_SECRET_KEY, algorithm=JWT_ALGORITHM)


def decode_access_token(token: str) -> dict:
    """Decode and validate a JWT, returning its claims dict.

    The inverse of create_access_token: verifies the HS256 signature against
    JWT_SECRET_KEY and enforces the "exp" claim. Raises python-jose errors on
    failure -- ExpiredSignatureError for an expired token, JWTError (its parent
    class) for a bad signature or malformed token. We deliberately do NOT catch
    them here; the get_current_user dependency maps them to HTTP 401.
    """
    return jwt.decode(token, JWT_SECRET_KEY, algorithms=[JWT_ALGORITHM])
