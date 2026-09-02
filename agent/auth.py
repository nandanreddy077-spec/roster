"""Credentials for the customer portal (agent/portal.py): password hashing,
the session-signing key, and founder-issued dashboard access links. Fully
separate from the founder's HTTP-Basic admin auth in app.py — no shared
credential path between the two.
"""

from typing import Optional

import bcrypt
from itsdangerous import BadSignature, SignatureExpired, URLSafeTimedSerializer

# bcrypt refuses anything longer and RAISES rather than truncating (it changed
# to raise in 4.0). Both functions below took whatever the form posted straight
# to the C call, so a 73-byte password turned the login page into an unhandled
# 500 — for a wrong guess, a password-manager blob, or a pasted paragraph.
# Handled here, in the one module that owns credentials, rather than at each
# call site: a guard per caller leaves whichever caller is added next broken.
_BCRYPT_MAX_BYTES = 72


def hash_password(password: str) -> str:
    return bcrypt.hashpw(_bcrypt_bytes(password), bcrypt.gensalt()).decode("utf-8")


def verify_password(password: str, password_hash: str) -> bool:
    """False for a wrong password — and for anything malformed. Never raises:
    an attacker (or a password manager) must not be able to turn a login
    attempt into a 500."""
    try:
        return bcrypt.checkpw(_bcrypt_bytes(password), password_hash.encode("utf-8"))
    except (ValueError, TypeError):
        return False


def _bcrypt_bytes(password: str) -> bytes:
    """Truncate to bcrypt's byte limit.

    Truncation, not rejection: it is what bcrypt itself did for its whole
    history and what other implementations still do, so a hash made under the
    old behaviour keeps verifying. Rejecting long passwords outright would
    lock those accounts out.

    The cut is at a byte offset and may land mid-character in a multi-byte
    sequence. That is fine and deliberate — bcrypt hashes bytes, never text,
    so a split character is just bytes like any other, and it is what the
    original C implementation does. Re-encoding to a character boundary would
    produce a DIFFERENT byte string and break exactly the compatibility this
    function exists to keep."""
    raw = (password or "").encode("utf-8")
    if len(raw) <= _BCRYPT_MAX_BYTES:
        return raw
    return raw[:_BCRYPT_MAX_BYTES]


def resolve_session_secret(environ) -> str:
    """The key that signs the portal session cookie AND founder-issued access
    links. Fails closed in production rather than falling back to the shared
    dev secret: a deploy that forgot it would otherwise mint cookies and
    access links that anyone reading this source could forge."""
    secret = environ.get("SESSION_SECRET_KEY")
    if secret:
        return secret
    if environ.get("ROSTER_ENV") == "production":
        raise RuntimeError(
            "SESSION_SECRET_KEY must be set in production — refusing to start with the dev fallback."
        )
    return "dev-only-insecure-secret-change-in-production"


# A founder-issued dashboard link is a bearer credential sitting in a text
# message: whoever holds the URL is that business's owner until it expires.
# 14 days is long enough for an owner to open it days after the setup call,
# short enough that a forwarded or screenshotted link stops working. Roster
# provisions every customer by hand, so this is the ONLY way an owner who
# never chose a password reaches their own dashboard.
ACCESS_LINK_MAX_AGE_SECONDS = 14 * 24 * 60 * 60

# Salted so an access-link signature can never be replayed as a session cookie
# (or vice versa) even though both are signed with SESSION_SECRET_KEY.
_ACCESS_SALT = "roster-dashboard-access"


def _access_serializer(environ) -> URLSafeTimedSerializer:
    return URLSafeTimedSerializer(resolve_session_secret(environ), salt=_ACCESS_SALT)


def make_access_token(business_id: int, environ) -> str:
    return _access_serializer(environ).dumps(business_id)


def read_access_token(token: str, environ) -> Optional[int]:
    """The business this link logs in, or None if it's forged, tampered with,
    or expired. Returns None rather than raising: a stale link in a months-old
    text message is ordinary traffic, not an error condition."""
    try:
        return _access_serializer(environ).loads(token, max_age=ACCESS_LINK_MAX_AGE_SECONDS)
    except (BadSignature, SignatureExpired):
        return None
