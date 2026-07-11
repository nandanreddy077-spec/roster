"""Google OAuth wiring for the self-serve customer portal (agent/portal.py).

Keeps all Authlib/OAuth setup out of portal.py. Google is *optional* — with
GOOGLE_CLIENT_ID / GOOGLE_CLIENT_SECRET unset, `google_enabled()` is False, the
"Continue with Google" button is hidden, and the /auth/google/* routes redirect
back to /login rather than erroring. That way the portal runs identically
whether or not credentials are configured (e.g. local dev before the Google
Cloud project exists).

The redirect URI must match one registered in the Google Cloud Console OAuth
client. In production Railway sits behind a proxy, so the request scheme can
read as http even though the public URL is https; set OAUTH_REDIRECT_BASE_URL
(e.g. https://rosterhires.com) to pin it and avoid a redirect_uri_mismatch.
"""
import os
from typing import Optional

from authlib.integrations.starlette_client import OAuth

GOOGLE_DISCOVERY_URL = "https://accounts.google.com/.well-known/openid-configuration"

_oauth: Optional[OAuth] = None


def google_enabled() -> bool:
    return bool(os.environ.get("GOOGLE_CLIENT_ID") and os.environ.get("GOOGLE_CLIENT_SECRET"))


def get_oauth() -> OAuth:
    """Lazily build and cache the OAuth registry. Reads env at first use, not at
    import, so tests and dev can set/unset credentials freely."""
    global _oauth
    if _oauth is None:
        oauth = OAuth()
        oauth.register(
            name="google",
            client_id=os.environ.get("GOOGLE_CLIENT_ID"),
            client_secret=os.environ.get("GOOGLE_CLIENT_SECRET"),
            server_metadata_url=GOOGLE_DISCOVERY_URL,
            client_kwargs={"scope": "openid email profile"},
        )
        _oauth = oauth
    return _oauth


def callback_url(request) -> str:
    """Absolute URL Google redirects back to. Prefer the pinned base URL when
    set (proxy-safe); otherwise derive it from the request."""
    base = os.environ.get("OAUTH_REDIRECT_BASE_URL")
    if base:
        return base.rstrip("/") + "/auth/google/callback"
    return str(request.url_for("google_callback"))
