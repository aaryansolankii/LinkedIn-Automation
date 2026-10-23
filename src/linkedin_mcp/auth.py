"""Browser-based LinkedIn OAuth login that saves the token and author URN to DATA_DIR."""

from __future__ import annotations

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
4. Put the app's credentials in {env_file}:
   LINKEDIN_CLIENT_ID=...
   LINKEDIN_CLIENT_SECRET=...
Then run linkedin_login again."""

_PAGE = """<!doctype html><html><head><meta charset="utf-8"><title>LinkedIn for Claude</title></head>
<body style="font-family: system-ui, sans-serif; max-width: 520px; margin: 80px auto; line-height: 1.5">
<h2>{heading}</h2><p>{message}</p></body></html>"""


class LoginFlow:
    """One OAuth authorization-code flow served by a short-lived local callback server."""

    def __init__(self, settings: Settings) -> None:
        if not (settings.client_id and settings.client_secret):
            raise LinkedInError(SETUP_HELP.format(redirect_uri=settings.redirect_uri, env_file=ENV_FILE))
        self.settings = settings
        self._state = secrets.token_urlsafe(24)
        self.url = f"{AUTHORIZE_URL}?" + urlencode(
            {
                "response_type": "code",
                "client_id": settings.client_id,
                "redirect_uri": settings.redirect_uri,
                "state": self._state,
                "scope": settings.scopes,
            }
        )
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
            self._credentials = self._exchange(query.get("code", ""))
        except Exception as exc:
            logger.exception("LinkedIn login failed")
            self._error = str(exc) if isinstance(exc, LinkedInError) else f"Unexpected login error: {exc!r}"
            return "Login failed", self._error
        finally:
            self._done.set()
        who = self._credentials.name or self._credentials.author_urn
        return "Connected", f"Claude can now post to LinkedIn as {who}. You can close this tab."

    def _exchange(self, code: str) -> Credentials:
        """Trade the authorization code for a token, look up the member, and save both."""
        token = request(
            "POST",
            TOKEN_URL,
            "token exchange",
            data={
                "grant_type": "authorization_code",
                "code": code,
                "client_id": self.settings.client_id,
                "client_secret": self.settings.client_secret,
                "redirect_uri": self.settings.redirect_uri,
            },
        ).json()
        access_token = token["access_token"]
        expires_in = int(token.get("expires_in") or 0)
        expires_at = (datetime.now() + timedelta(seconds=expires_in)).isoformat(timespec="minutes") if expires_in else ""

        author_urn, name = self.settings.author_urn, ""
        if "openid" in self.settings.scopes.split():
            profile = get_profile(access_token)
            author_urn, name = f"urn:li:person:{profile['sub']}", profile.get("name", "")

        credentials = Credentials(access_token=access_token, author_urn=author_urn, name=name, expires_at=expires_at)
        save_credentials(credentials)
        logger.info("Saved LinkedIn credentials for %s (expires %s)", name or author_urn, expires_at or "unknown")
        return credentials

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
                heading, message = flow._handle_callback(query)
                body = _PAGE.format(heading=html.escape(heading), message=html.escape(message)).encode()
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


def start_login() -> LoginFlow:
    """Start a login in the background, or return the one still waiting for the user."""
    global _current
    with _lock:
        if _current is None or not _current.active:
            _current = LoginFlow(get_settings())
            _current.start()
        return _current
