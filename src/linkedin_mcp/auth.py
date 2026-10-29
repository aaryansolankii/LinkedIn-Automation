"""LinkedIn OAuth login that saves the token and author URN (DATA_DIR locally, Firestore when hosted).

Local mode runs a short-lived callback server on the redirect URI and opens the browser.
Hosted mode (serve-http) receives LinkedIn's redirect on the service's own /callback route and
uses an HMAC-signed `state`, so no login state has to survive between requests or instances.
"""

from __future__ import annotations

import hashlib
import hmac
import html
import logging
import secrets
import threading
import time
import webbrowser
from datetime import datetime, timedelta
from http.server import BaseHTTPRequestHandler, HTTPServer
from urllib.parse import parse_qs, urlencode, urlparse

from .config import ENV_FILE, Credentials, Settings, get_settings, save_credentials
from .errors import LinkedInError
from .linkedin import get_profile, request

logger = logging.getLogger(__name__)

AUTHORIZE_URL = "https://www.linkedin.com/oauth/v2/authorization"
TOKEN_URL = "https://www.linkedin.com/oauth/v2/accessToken"
LOGIN_TIMEOUT_SECONDS = 600

SETUP_HELP = """Logging in needs a LinkedIn developer app (one-time setup):
1. Create an app at https://www.linkedin.com/developers/apps
2. Products tab: add "Sign In with LinkedIn using OpenID Connect" and "Share on LinkedIn".
3. Auth tab: add the authorized redirect URL {redirect_uri}
4. Provide the app's credentials {where}:
   LINKEDIN_CLIENT_ID=...
   LINKEDIN_CLIENT_SECRET=...
Then run linkedin_login again."""

_PAGE = """<!doctype html><html><head><meta charset="utf-8"><title>LinkedIn for Claude</title></head>
<body style="font-family: system-ui, sans-serif; max-width: 520px; margin: 80px auto; line-height: 1.5">
<h2>{heading}</h2><p>{message}</p></body></html>"""

# Set by enable_hosted_login() when running as the hosted HTTP service.
_signing_key: bytes | None = None


def page(heading: str, message: str) -> str:
    """Render the small HTML page shown in the browser after a login."""
    return _PAGE.format(heading=html.escape(heading), message=html.escape(message))


def _require_app(settings: Settings) -> None:
    """Explain the one-time developer-app setup when its credentials are missing, and refuse member
    posting rights in page-only mode."""
    if not (settings.client_id and settings.client_secret):
        where = "as Cloud Run env var / secret (see deploy.md)" if is_hosted() else f"in {ENV_FILE}"
        raise LinkedInError(SETUP_HELP.format(redirect_uri=settings.redirect_uri, where=where))
    if settings.page_only and "w_member_social" in settings.scopes.split():
        raise LinkedInError(
            "PAGE_ONLY is on, so linkedin_login won't ask for w_member_social (permission to post as yourself). "
            "Remove it from LINKEDIN_SCOPES, e.g. LINKEDIN_SCOPES=w_organization_social r_organization_social."
        )


def authorize_url(settings: Settings, state: str) -> str:
    """Return LinkedIn's consent page URL."""
    query = {
        "response_type": "code",
        "client_id": settings.client_id,
        "redirect_uri": settings.redirect_uri,
        "state": state,
        "scope": settings.scopes,
    }
    return f"{AUTHORIZE_URL}?{urlencode(query)}"


def exchange_code(settings: Settings, code: str) -> Credentials:
    """Trade an authorization code for a token, look up the member, and save both."""
    token = request(
        "POST",
        TOKEN_URL,
        "token exchange",
        data={
            "grant_type": "authorization_code",
            "code": code,
            "client_id": settings.client_id,
            "client_secret": settings.client_secret,
            "redirect_uri": settings.redirect_uri,
        },
    ).json()
    access_token = token["access_token"]
    expires_in = int(token.get("expires_in") or 0)
    expires_at = (datetime.now() + timedelta(seconds=expires_in)).isoformat(timespec="minutes") if expires_in else ""

    author_urn, name = settings.author_urn, ""
    if "openid" in settings.scopes.split():
        profile = get_profile(access_token)
        author_urn, name = f"urn:li:person:{profile['sub']}", profile.get("name", "")

    credentials = Credentials(access_token=access_token, author_urn=author_urn, name=name, expires_at=expires_at)
    save_credentials(credentials)
    logger.info("Saved LinkedIn credentials for %s (expires %s)", name or author_urn, expires_at or "unknown")
    return credentials


def _connected_message(credentials: Credentials) -> str:
    return f"Claude can now post to LinkedIn as {credentials.name or credentials.author_urn}. You can close this tab."


# --- Local mode -------------------------------------------------------------------------------


class LoginFlow:
    """One OAuth authorization-code flow served by a short-lived local callback server."""

    def __init__(self, settings: Settings) -> None:
        _require_app(settings)
        self.settings = settings
        self._state = secrets.token_urlsafe(24)
        self.url = authorize_url(settings, self._state)
        redirect = urlparse(settings.redirect_uri)
        self._callback_path = redirect.path or "/"
        self._deadline = time.monotonic() + LOGIN_TIMEOUT_SECONDS
        self._done = threading.Event()
        self._credentials: Credentials | None = None
        self._error = ""
        try:
            self._server = HTTPServer(("127.0.0.1", redirect.port or 80), self._handler_class())
        except OSError as exc:
            raise LinkedInError(f"Cannot listen on {settings.redirect_uri} for the login callback: {exc}") from exc
        self._server.timeout = 1

    @property
    def active(self) -> bool:
        """Return True while the flow is still waiting for the browser."""
        return not self._done.is_set()

    def start(self) -> None:
        """Serve the callback in the background and open the consent page."""
        threading.Thread(target=self._serve, name="linkedin-login", daemon=True).start()
        webbrowser.open(self.url)

    def wait(self) -> Credentials:
        """Block until the login finishes and return the saved credentials."""
        self._done.wait()
        if self._credentials is None:
            raise LinkedInError(self._error)
        return self._credentials

    def _serve(self) -> None:
        """Handle callback requests until the flow completes or times out."""
        try:
            while self.active:
                if time.monotonic() > self._deadline:
                    self._error = "Login timed out after 10 minutes. Run linkedin_login again."
                    self._done.set()
                    break
                self._server.handle_request()
        finally:
            self._server.server_close()

    def _handle_callback(self, query: dict[str, str]) -> tuple[str, str]:
        """Finish the flow from LinkedIn's redirect; return a heading and message for the browser."""
        if query.get("state") != self._state:
            return "Link expired", "This login link is from an older attempt. Start the login again from Claude."
        try:
            if "error" in query:
                raise LinkedInError(f"LinkedIn declined the login: {query.get('error_description') or query['error']}")
            self._credentials = exchange_code(self.settings, query.get("code", ""))
        except Exception as exc:
            logger.exception("LinkedIn login failed")
            self._error = str(exc) if isinstance(exc, LinkedInError) else f"Unexpected login error: {exc!r}"
            return "Login failed", self._error
        finally:
            self._done.set()
        return "Connected", _connected_message(self._credentials)

    def _handler_class(self) -> type[BaseHTTPRequestHandler]:
        """Build the request handler bound to this flow."""
        flow = self

        class CallbackHandler(BaseHTTPRequestHandler):
            def do_GET(self) -> None:
                url = urlparse(self.path)
                if url.path != flow._callback_path:
                    self.send_error(404)
                    return
                query = {key: values[0] for key, values in parse_qs(url.query).items()}
                body = page(*flow._handle_callback(query)).encode()
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, format: str, *args: object) -> None:
                logger.debug("login callback: " + format, *args)

        return CallbackHandler


_current: LoginFlow | None = None
_lock = threading.Lock()


def start_local_login() -> LoginFlow:
    """Start a local login in the background, or return the one still waiting for the user."""
    global _current
    with _lock:
        if _current is None or not _current.active:
            _current = LoginFlow(get_settings())
            _current.start()
        return _current


# --- Hosted mode ------------------------------------------------------------------------------


def enable_hosted_login(signing_key: bytes) -> None:
    """Switch linkedin_login to the hosted flow, signing `state` with this key."""
    global _signing_key
    _signing_key = signing_key


def is_hosted() -> bool:
    """Return True when running as the hosted HTTP service."""
    return _signing_key is not None


def _sign(payload: str) -> str:
    assert _signing_key is not None
    return hmac.new(_signing_key, payload.encode(), hashlib.sha256).hexdigest()


def _hosted_login_url() -> str:
    """Return a consent URL whose state expires and can't be forged without the signing key."""
    settings = get_settings()
    _require_app(settings)
    payload = f"{int(time.time()) + LOGIN_TIMEOUT_SECONDS}.{secrets.token_urlsafe(12)}"
    return authorize_url(settings, f"{payload}.{_sign(payload)}")


def complete_hosted_login(query: dict[str, str]) -> tuple[int, str, str]:
    """Handle LinkedIn's redirect to /callback; return an HTTP status, heading and message."""
    expires, _, rest = query.get("state", "").partition(".")
    nonce, _, signature = rest.partition(".")
    payload = f"{expires}.{nonce}"
    signed = bool(nonce) and expires.isdigit() and hmac.compare_digest(signature.encode(), _sign(payload).encode())
    if not signed or int(expires) < time.time():
        return 400, "Link expired", "This login link is invalid or expired. Ask Claude to run linkedin_login again."
    if "error" in query:
        return 400, "Login failed", f"LinkedIn declined the login: {query.get('error_description') or query['error']}"
    try:
        credentials = exchange_code(get_settings(), query.get("code", ""))
    except Exception as exc:
        logger.exception("LinkedIn login failed")
        message = str(exc) if isinstance(exc, LinkedInError) else "Unexpected error; check the service logs."
        return 502, "Login failed", message
    return 200, "Connected", _connected_message(credentials)


def login_url() -> str:
    """Start a login and return the URL the user has to open."""
    return _hosted_login_url() if is_hosted() else start_local_login().url
