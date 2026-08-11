"""Credentials for the customer portal (agent/portal.py): password hashing,
the session-signing key, and founder-issued dashboard access links. Fully
separate from the founder's HTTP-Basic admin auth in app.py — no shared
credential path between the two.
"""

from typing import Optional

import bcrypt
from itsdangerous import BadSignature, SignatureExpired, URLSafeTimedSerializer


def hash_password(password: str) -> str:
    return bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")


def verify_password(password: str, password_hash: str) -> bool:
    return bcrypt.checkpw(password.encode("utf-8"), password_hash.encode("utf-8"))


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
