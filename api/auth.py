"""Google Sign-In (OAuth 2.0 / OIDC) for the MIOS dashboard.

Easy Skill Australia runs on Google Workspace, so staff sign in with the account
they already have and access is restricted to the company domain.

Flow (Authorization Code + PKCE, session held server-side):

    browser              FastAPI                    Google
      |  GET /auth/login   |                          |
      |------------------->| state+nonce+PKCE stored  |
      |                    |  in a temp cookie        |
      |<-- 302 to Google --|------------------------->|
      |                    |                          | user consents
      |  GET /auth/callback?code=...&state=...        |
      |------------------->| verify state             |
      |                    | exchange code -> tokens  |
      |                    | verify ID token via JWKS |
      |                    | check hd / email claims  |
      |<-- 302 to web app -| session cookie set       |

Design notes:

* Authlib does the OIDC discovery, PKCE, state/nonce and ID-token signature
  verification. Hand-rolling any of that is how auth bugs happen.
* The `hd` (hosted domain) claim is the *only* trustworthy domain signal, and
  only because the ID token's signature is verified first. The `hd` parameter we
  send on the authorization request is a UX hint that pre-fills the account
  chooser - a user can edit it out of the URL, so it is never a control.
* The session is a signed cookie (Starlette SessionMiddleware). No session table
  to maintain; the trade-off is that sign-out is client-side only, so sessions
  are kept short (SESSION_MAX_AGE, default 12h).
"""
from __future__ import annotations

import logging
import re
from typing import Any
from urllib.parse import urlencode, urlparse

from authlib.integrations.starlette_client import OAuth, OAuthError
from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.responses import RedirectResponse

from config.settings import settings

log = logging.getLogger(__name__)

router = APIRouter()

GOOGLE_DISCOVERY_URL = "https://accounts.google.com/.well-known/openid-configuration"
SESSION_USER_KEY = "user"
#: Session key holding the post-login destination path within the web app.
SESSION_NEXT_KEY = "next"

oauth = OAuth()
if settings.oauth_configured:
    oauth.register(
        name="google",
        client_id=settings.google_client_id,
        client_secret=settings.google_client_secret,
        server_metadata_url=GOOGLE_DISCOVERY_URL,
        client_kwargs={"scope": "openid email profile", "code_challenge_method": "S256"},
    )


class NotAuthorised(Exception):
    """Raised when a verified Google identity is not permitted to use MIOS."""


#: Google client IDs are `<project-number>-<random>.apps.googleusercontent.com`.
CLIENT_ID_RE = re.compile(r"^\d+-[a-z0-9]+\.apps\.googleusercontent\.com$")
CLIENT_ID_SUFFIX = ".apps.googleusercontent.com"


def check_google_client_id(client_id: str) -> list[str]:
    """Describe anything obviously wrong with a configured client ID.

    Google answers a malformed client ID with a bare "Error 401: invalid_client /
    The OAuth client was not found", which says nothing about *why*. These checks
    turn the common paste mistakes into a message that names the problem.
    """
    problems: list[str] = []
    if not client_id:
        return problems

    if client_id.count(CLIENT_ID_SUFFIX) > 1:
        problems.append(
            f"'{CLIENT_ID_SUFFIX}' appears {client_id.count(CLIENT_ID_SUFFIX)} times — "
            "the ID already ends with it, so it should not be appended again"
        )
    elif not client_id.endswith(CLIENT_ID_SUFFIX):
        problems.append(f"does not end with '{CLIENT_ID_SUFFIX}'")

    if client_id.strip("\"'") != client_id:
        problems.append("has surrounding quotes — .env values are used literally")

    if not problems and not CLIENT_ID_RE.fullmatch(client_id):
        problems.append(
            f"does not match <project-number>-<random>{CLIENT_ID_SUFFIX} "
            f"(got {len(client_id)} chars; these are normally ~72)"
        )
    return problems


# --------------------------------------------------------------------------
# Pure authorisation policy (unit-testable, no network / no request object)
# --------------------------------------------------------------------------


def authorize_claims(claims: dict[str, Any]) -> dict[str, Any]:
    """Turn verified ID-token claims into a MIOS user, or raise NotAuthorised.

    `claims` MUST already have had its signature verified — this function decides
    *who is allowed in*, it does not decide *whether the token is genuine*.

    Policy, in order:
      1. The email must be present and Google-verified.
      2. An explicit ALLOWED_EMAILS entry always passes (dev accounts).
      3. Otherwise, if ALLOWED_GOOGLE_DOMAIN is set, the `hd` claim must match.
      4. If neither is configured, any verified Google account is allowed — we
         log a warning because that is almost never what you want in production.
    """
    email = (claims.get("email") or "").strip().lower()
    if not email:
        raise NotAuthorised("Google did not return an email address")
    if not claims.get("email_verified"):
        raise NotAuthorised(f"{email} is not a verified Google account")

    allowed_emails = {e.lower() for e in settings.allowed_emails}
    domain = settings.allowed_google_domain.strip().lower()
    hd = (claims.get("hd") or "").strip().lower()

    if email in allowed_emails:
        pass  # explicit allowlist wins
    elif domain:
        if hd != domain:
            # Personal gmail accounts have no `hd` at all, hence the friendlier message.
            raise NotAuthorised(
                f"{email} is not a {domain} account. "
                "MIOS is restricted to Easy Skill Google Workspace accounts."
            )
    else:
        log.warning(
            "auth: neither ALLOWED_GOOGLE_DOMAIN nor ALLOWED_EMAILS is set — "
            "any Google account can sign in to MIOS"
        )

    return {
        "sub": claims.get("sub"),
        "email": email,
        "name": claims.get("name") or email.split("@")[0],
        "picture": claims.get("picture"),
        "domain": hd or None,
    }


def safe_next_path(raw: str | None) -> str:
    """Sanitise the post-login redirect target.

    Only same-app absolute paths are allowed. Anything with a scheme or host is
    dropped, otherwise `?next=https://evil.example` turns our sign-in into an
    open redirect that looks like it came from us.
    """
    if not raw or not raw.startswith("/") or raw.startswith("//"):
        return "/"
    parsed = urlparse(raw)
    if parsed.scheme or parsed.netloc:
        return "/"
    return raw


# --------------------------------------------------------------------------
# Dependencies
# --------------------------------------------------------------------------


def current_user(request: Request) -> dict[str, Any] | None:
    """The signed-in user, or None. Never raises — use for optional auth."""
    if settings.auth_disabled:
        return {
            "sub": "dev",
            "email": "dev@localhost",
            "name": "Dev (auth disabled)",
            "picture": None,
            "domain": None,
            "authDisabled": True,
        }
    user = request.session.get(SESSION_USER_KEY)
    return user if isinstance(user, dict) else None


def require_user(request: Request) -> dict[str, Any]:
    """FastAPI dependency: 401 unless a valid session is present."""
    user = current_user(request)
    if user is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Sign in with your Easy Skill Google account to access MIOS.",
            headers={"WWW-Authenticate": "Bearer"},
        )
    return user


# --------------------------------------------------------------------------
# Routes
# --------------------------------------------------------------------------


def _web_url(path: str, **params: str) -> str:
    url = f"{settings.web_app_url}{path}"
    return f"{url}?{urlencode(params)}" if params else url


@router.get("/auth/login", include_in_schema=False)
async def login(request: Request, next: str | None = None):
    """Kick off the Google flow. `next` is where to land after a successful login."""
    if settings.auth_disabled:
        return RedirectResponse(_web_url(safe_next_path(next)))
    if not settings.oauth_configured:
        log.error("auth: /auth/login hit but GOOGLE_CLIENT_ID/SECRET are not configured")
        return RedirectResponse(_web_url("/signin", error="not_configured"))

    request.session[SESSION_NEXT_KEY] = safe_next_path(next)

    kwargs: dict[str, Any] = {}
    if settings.allowed_google_domain and not settings.allowed_emails:
        # UX hint only: filters Google's account chooser to the Workspace domain.
        # The real check is the verified `hd` claim in authorize_claims().
        #
        # Deliberately suppressed when ALLOWED_EMAILS is set: those accounts are
        # admitted precisely *because* they're outside the domain, so filtering
        # the chooser would hide the very accounts we mean to let in.
        kwargs["hd"] = settings.allowed_google_domain
    return await oauth.google.authorize_redirect(
        request, settings.oauth_redirect_uri, **kwargs
    )


@router.get("/auth/callback", include_in_schema=False)
async def callback(request: Request):
    """Google redirects here. Verify everything, then hand back to the web app."""
    if not settings.oauth_configured:
        return RedirectResponse(_web_url("/signin", error="not_configured"))

    try:
        # Verifies the state, exchanges the code, and validates the ID token
        # signature + nonce against Google's JWKS.
        token = await oauth.google.authorize_access_token(request)
    except OAuthError as exc:
        log.warning("auth: token exchange failed: %s", exc)
        return RedirectResponse(_web_url("/signin", error="oauth_failed"))

    claims = token.get("userinfo") or {}
    try:
        user = authorize_claims(claims)
    except NotAuthorised as exc:
        log.warning("auth: rejected sign-in: %s", exc)
        return RedirectResponse(_web_url("/signin", error="not_authorised", detail=str(exc)))

    next_path = safe_next_path(request.session.pop(SESSION_NEXT_KEY, None))
    # Drop Authlib's leftover state/nonce entries so the cookie stays small.
    request.session.clear()
    request.session[SESSION_USER_KEY] = user
    log.info("auth: signed in %s (%s)", user["email"], user.get("domain") or "no domain")
    return RedirectResponse(_web_url(next_path))


@router.post("/auth/logout")
async def logout(request: Request) -> dict[str, bool]:
    request.session.clear()
    return {"ok": True}


@router.get("/api/me")
async def me(request: Request) -> dict[str, Any]:
    """Who am I? Always 200 so the dashboard can decide what to render.

    Also reports whether the server can actually perform a Google sign-in, so the
    sign-in screen can explain a missing configuration instead of dead-ending on
    a button that goes nowhere.
    """
    user = current_user(request)
    return {
        "authenticated": user is not None,
        "user": user,
        "authDisabled": settings.auth_disabled,
        "oauthConfigured": settings.oauth_configured,
        "domain": settings.allowed_google_domain or None,
        # Whether *some* accounts outside the domain are permitted — not which
        # ones. Lets the sign-in screen avoid claiming "domain accounts only"
        # when that isn't true.
        "hasAllowlist": bool(settings.allowed_emails),
        "loginUrl": f"{_api_base(request)}/auth/login",
    }


def _api_base(request: Request) -> str:
    """Absolute base URL of this API, for building the login link."""
    configured = settings.oauth_redirect_uri.removesuffix("/auth/callback")
    return configured or str(request.base_url).rstrip("/")
