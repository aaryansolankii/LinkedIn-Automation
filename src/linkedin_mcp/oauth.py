"""Sign-in for the hosted connector: an OAuth authorization server that only lets ALLOWED_EMAILS in.

claude.ai registers itself (Dynamic Client Registration) and sends the browser to /authorize. Instead
of showing a login form, /authorize redirects to Google. Google sends the browser back to
/oauth/google/callback, which verifies Google's ID token and issues an authorization code only when
the verified email is in ALLOWED_EMAILS. The MCP SDK's handlers serve /authorize, /token, /register
and /revoke on top of the provider below (see http_app.py).

Nothing usable is stored: codes and tokens are kept as SHA-256 hashes, client secrets are derived
from OAUTH_SIGNING_KEY instead of stored, and Google's tokens are dropped as soon as the email is known.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import logging
import os
import secrets
import threading
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any
from urllib.parse import urlencode, urlparse

import httpx
import jwt
from mcp.server.auth.provider import (
    AccessToken,
    AuthorizationCode,
    AuthorizationParams,
    AuthorizeError,
    OAuthAuthorizationServerProvider,
    RefreshToken,
    RegistrationError,
    TokenError,
    construct_redirect_uri,
)
from mcp.server.auth.settings import AuthSettings, ClientRegistrationOptions, RevocationOptions
from mcp.shared.auth import OAuthClientInformationFull, OAuthToken
from starlette.concurrency import run_in_threadpool
from starlette.responses import HTMLResponse, RedirectResponse, Response

from . import auth
from .config import STORAGE

logger = logging.getLogger(__name__)

MCP_PATH = "/mcp"
GOOGLE_CALLBACK_PATH = "/oauth/google/callback"
MIN_SECRET_LENGTH = 32

LOGIN_TTL = 10 * 60  # to finish signing in with Google
CODE_TTL = 5 * 60
ACCESS_TTL = 60 * 60
REFRESH_TTL = 60 * 24 * 60 * 60

GOOGLE_AUTHORIZE_URL = "https://accounts.google.com/o/oauth2/v2/auth"
GOOGLE_TOKEN_URL = "https://oauth2.googleapis.com/token"
GOOGLE_CERTS_URL = "https://www.googleapis.com/oauth2/v3/certs"
GOOGLE_ISSUERS = ("https://accounts.google.com", "accounts.google.com")

# Clients may only receive codes at Claude's own callback, or on the user's own machine (loopback),
# so a self-registered client can't send a sign-in link that delivers a code to someone else's server.
CLAUDE_CALLBACKS = ("https://claude.ai/api/mcp/auth_callback", "https://claude.com/api/mcp/auth_callback")
LOOPBACK_HOSTS = ("localhost", "127.0.0.1", "::1")


def _now() -> float:
    return time.time()


def _expiry(seconds: int) -> datetime:
    """A UTC timestamp `seconds` from now (stored as `expire_at`, which Firestore TTL can also use)."""
    return datetime.fromtimestamp(_now() + seconds, timezone.utc)


def _expired(record: dict[str, Any]) -> bool:
    return record["expire_at"].timestamp() <= _now()


def _hash(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


@dataclass(frozen=True)
class OAuthConfig:
    """Settings for the connector's sign-in."""

    issuer_url: str  # the service URL, e.g. https://linkedin-mcp-123456789.asia-south1.run.app
    google_client_id: str
    google_client_secret: str
    allowed_emails: frozenset[str]
    signing_key: str  # derives client secrets and signs LinkedIn login links

    @property
    def resource_url(self) -> str:
        return self.issuer_url + MCP_PATH

    @property
    def google_redirect_uri(self) -> str:
        return self.issuer_url + GOOGLE_CALLBACK_PATH

    @classmethod
    def from_env(cls) -> OAuthConfig:
        """Read the settings from the environment and refuse to start with any of them missing."""
        names = ("OAUTH_ISSUER_URL", "GOOGLE_OAUTH_CLIENT_ID", "GOOGLE_OAUTH_CLIENT_SECRET", "ALLOWED_EMAILS", "OAUTH_SIGNING_KEY")
        values = {name: os.environ.get(name, "").strip() for name in names}
        missing = [name for name, value in values.items() if not value]
        if missing:
            raise RuntimeError(f"Hosted mode needs these settings (see deploy.md): {', '.join(missing)}.")
        return cls.create(
            values["OAUTH_ISSUER_URL"],
            values["GOOGLE_OAUTH_CLIENT_ID"],
            values["GOOGLE_OAUTH_CLIENT_SECRET"],
            values["ALLOWED_EMAILS"],
            values["OAUTH_SIGNING_KEY"],
        )

    @classmethod
    def create(cls, issuer_url: str, client_id: str, client_secret: str, allowed_emails: str, signing_key: str) -> OAuthConfig:
        issuer_url = issuer_url.strip().rstrip("/")
        url = urlparse(issuer_url)
        if (url.scheme != "https" and url.hostname not in LOOPBACK_HOSTS) or url.path or url.query:
            raise RuntimeError(f"OAUTH_ISSUER_URL must be the service's https:// URL with no path, not {issuer_url!r}.")
        emails = frozenset(email.strip().lower() for email in allowed_emails.split(",") if email.strip())
        if not emails:
            raise RuntimeError("ALLOWED_EMAILS must list at least one email address.")
        if len(signing_key) < MIN_SECRET_LENGTH:
            raise RuntimeError(f"OAUTH_SIGNING_KEY must be a random value of at least {MIN_SECRET_LENGTH} characters.")
        return cls(issuer_url, client_id, client_secret, emails, signing_key)

    def auth_settings(self) -> AuthSettings:
        """The MCP SDK's settings for this authorization server and the /mcp resource."""
        return AuthSettings(
            issuer_url=self.issuer_url,
            resource_server_url=self.resource_url,
            client_registration_options=ClientRegistrationOptions(enabled=True),
            revocation_options=RevocationOptions(enabled=True),
            validate_token_resource=True,
        )


class MemoryStore:
    """In-process OAuth records, for tests and for running serve-http without Firestore (lost on restart).

    Has the same functions as the oauth_* ones in firestore_store.
    """

    def __init__(self) -> None:
        self._records: dict[str, dict[str, dict[str, Any]]] = {}
        self._lock = threading.Lock()

    def oauth_put(self, kind: str, key: str, data: dict[str, Any]) -> None:
        with self._lock:
            self._records.setdefault(kind, {})[key] = dict(data)

    def oauth_get(self, kind: str, key: str) -> dict[str, Any] | None:
        with self._lock:
            record = self._records.get(kind, {}).get(key)
            return dict(record) if record is not None else None

    def oauth_take(self, kind: str, key: str) -> dict[str, Any] | None:
        with self._lock:
            return self._records.get(kind, {}).pop(key, None)

    def oauth_delete_where(self, kind: str, field: str, value: str) -> None:
        with self._lock:
            records = self._records.get(kind, {})
            for key in [key for key, record in records.items() if record.get(field) == value]:
                del records[key]


def default_store() -> Any:
    """Firestore in hosted mode; otherwise process memory."""
    if STORAGE == "firestore":
        from . import firestore_store

        return firestore_store
    logger.warning("STORAGE is not firestore: connector sign-ins are kept in memory and lost on restart.")
    return MemoryStore()


class SignInError(Exception):
    """Google's answer could not be trusted or used; the message is safe to show in the browser."""


class GoogleSignIn:
    """Google's side of the sign-in: the consent URL, the code exchange and the ID-token checks."""

    def __init__(self, client_id: str, client_secret: str, redirect_uri: str, jwks: Any = None) -> None:
        self.client_id = client_id
        self._client_secret = client_secret
        self.redirect_uri = redirect_uri
        self._jwks = jwks or jwt.PyJWKClient(GOOGLE_CERTS_URL, timeout=15)

    def authorization_url(self, state: str, nonce: str, code_verifier: str) -> str:
        challenge = base64.urlsafe_b64encode(hashlib.sha256(code_verifier.encode()).digest()).decode().rstrip("=")
        query = {
            "client_id": self.client_id,
            "redirect_uri": self.redirect_uri,
            "response_type": "code",
            "scope": "openid email",
            "state": state,
            "nonce": nonce,
            "code_challenge": challenge,
            "code_challenge_method": "S256",
            "prompt": "select_account",
        }
        return f"{GOOGLE_AUTHORIZE_URL}?{urlencode(query)}"

    def verified_email(self, code: str, code_verifier: str, nonce: str) -> str:
        """Trade Google's code for an ID token and return its verified email. Google's tokens are not kept."""
        if not code:
            raise SignInError("Google did not send an authorization code.")
        try:
            response = httpx.post(
                GOOGLE_TOKEN_URL,
                data={
                    "grant_type": "authorization_code",
                    "code": code,
                    "code_verifier": code_verifier,
                    "client_id": self.client_id,
                    "client_secret": self._client_secret,
                    "redirect_uri": self.redirect_uri,
                },
                timeout=15,
            )
        except httpx.TransportError as exc:
            raise SignInError(f"Could not reach Google: {exc}") from exc
        if not response.is_success:
            raise SignInError(f"Google refused the sign-in code ({response.status_code}).")
        return self.check_id_token(response.json().get("id_token", ""), nonce)

    def check_id_token(self, id_token: str, nonce: str) -> str:
        """Verify the ID token's signature, issuer, audience, expiry, nonce and email_verified."""
        try:
            key = self._jwks.get_signing_key_from_jwt(id_token).key
            claims = jwt.decode(
                id_token,
                key,
                algorithms=["RS256"],
                audience=self.client_id,
                issuer=GOOGLE_ISSUERS,
                leeway=30,
                options={"require": ["iss", "aud", "exp", "iat", "sub"]},
            )
        except jwt.PyJWTError as exc:
            raise SignInError(f"Google's ID token is not valid ({exc}).") from exc
        if not hmac.compare_digest(str(claims.get("nonce", "")).encode(), nonce.encode()):
            raise SignInError("Google's ID token is not valid (it belongs to another sign-in).")
        email = str(claims.get("email", "")).strip().lower()
        if not email or claims.get("email_verified") not in (True, "true"):
            raise SignInError("Google did not confirm this account's email address.")
        return email


class StoredAccessToken(AccessToken):
    grant_id: str  # every token issued from one sign-in, so they can be revoked together


class StoredRefreshToken(RefreshToken):
    grant_id: str


class AllowListProvider(OAuthAuthorizationServerProvider[AuthorizationCode, StoredRefreshToken, StoredAccessToken]):
    """OAuth authorization server for the connector that hands out codes only to ALLOWED_EMAILS."""

    def __init__(self, config: OAuthConfig, store: Any, google: GoogleSignIn | None = None) -> None:
        self.config = config
        self.store = store
        self.google = google or GoogleSignIn(config.google_client_id, config.google_client_secret, config.google_redirect_uri)

    # --- Clients (Dynamic Client Registration) ---

    def _client_secret(self, client_id: str) -> str:
        key = self.config.signing_key.encode()
        return hmac.new(key, f"oauth-client-secret:{client_id}".encode(), hashlib.sha256).hexdigest()

    async def register_client(self, client_info: OAuthClientInformationFull) -> None:
        for uri in client_info.redirect_uris or []:
            loopback = uri.scheme == "http" and (uri.host or "").strip("[]").lower() in LOOPBACK_HOSTS
            if not (str(uri) in CLAUDE_CALLBACKS or loopback):
                logger.warning("Refused to register an OAuth client with redirect URI %s", uri)
                raise RegistrationError("invalid_redirect_uri", f"Redirect URI {uri} is not allowed for this connector.")
        # The SDK returns this same object to the client. Swap its random secret for one derived from
        # OAUTH_SIGNING_KEY, so client secrets never have to be stored.
        if client_info.client_secret is not None:
            client_info.client_secret = self._client_secret(client_info.client_id)
        record = json.dumps(client_info.model_dump(mode="json", exclude={"client_secret"}))
        await run_in_threadpool(self.store.oauth_put, "clients", _hash(client_info.client_id), {"client": record})

    async def get_client(self, client_id: str) -> OAuthClientInformationFull | None:
        record = await run_in_threadpool(self.store.oauth_get, "clients", _hash(client_id))
        if record is None:
            return None
        client = OAuthClientInformationFull.model_validate_json(record["client"])
        if client.token_endpoint_auth_method != "none":
            client.client_secret = self._client_secret(client.client_id)
        return client

    # --- Authorization: hand the browser to Google, then come back with a code ---

    async def authorize(self, client: OAuthClientInformationFull, params: AuthorizationParams) -> str:
        if params.resource and params.resource.rstrip("/") != self.config.resource_url:
            raise AuthorizeError("invalid_target", f"This server only issues tokens for {self.config.resource_url}.")
        state, nonce, verifier = secrets.token_urlsafe(32), secrets.token_urlsafe(16), secrets.token_urlsafe(48)
        login = {
            "client_id": client.client_id,
            "redirect_uri": str(params.redirect_uri),
            "redirect_uri_provided_explicitly": params.redirect_uri_provided_explicitly,
            "code_challenge": params.code_challenge,
            "state": params.state,
            "scopes": params.scopes or [],
            "nonce": nonce,
            "code_verifier": verifier,
            "expire_at": _expiry(LOGIN_TTL),
        }
        await run_in_threadpool(self.store.oauth_put, "logins", _hash(state), login)
        return self.google.authorization_url(state, nonce, verifier)

    def complete_google_login(self, query: dict[str, str]) -> Response:
        """Handle Google's redirect to /oauth/google/callback (blocking: run it in a thread)."""
        state = query.get("state", "")
        login = self.store.oauth_take("logins", _hash(state)) if state else None
        if login is None or _expired(login):
            return _page(400, "Link expired", "This sign-in link is invalid or has expired. Connect again from Claude.")
        if "error" in query:
            return _page(400, "Sign-in cancelled", f"Google returned: {query.get('error_description') or query['error']}")
        try:
            email = self.google.verified_email(query.get("code", ""), login["code_verifier"], login["nonce"])
        except SignInError as exc:
            logger.warning("Google sign-in failed: %s", exc)
            return _page(400, "Sign-in failed", str(exc))
        except Exception:
            logger.exception("Google sign-in failed")
            return _page(502, "Sign-in failed", "Unexpected error; check the service logs.")
        if email not in self.config.allowed_emails:
            logger.warning("Denied connector sign-in for %s (not in ALLOWED_EMAILS)", email)
            return _page(403, "Access denied", f"{email} is not allowed to use this connector.")

        code = secrets.token_urlsafe(32)
        self.store.oauth_put("codes", _hash(code), {
            "client_id": login["client_id"],
            "redirect_uri": login["redirect_uri"],
            "redirect_uri_provided_explicitly": login["redirect_uri_provided_explicitly"],
            "code_challenge": login["code_challenge"],
            "scopes": login["scopes"],
            "subject": email,
            "expire_at": _expiry(CODE_TTL),
        })
        logger.info("Connector sign-in for %s", email)
        redirect = construct_redirect_uri(login["redirect_uri"], code=code, state=login["state"])
        return RedirectResponse(redirect, status_code=302, headers={"Cache-Control": "no-store"})

    # --- Tokens ---

    async def load_authorization_code(self, client: OAuthClientInformationFull, authorization_code: str) -> AuthorizationCode | None:
        record = await run_in_threadpool(self.store.oauth_get, "codes", _hash(authorization_code))
        if record is None or record["client_id"] != client.client_id or _expired(record):
            return None
        return AuthorizationCode(
            code=authorization_code,
            scopes=record["scopes"],
            expires_at=record["expire_at"].timestamp(),
            client_id=record["client_id"],
            code_challenge=record["code_challenge"],
            redirect_uri=record["redirect_uri"],
            redirect_uri_provided_explicitly=record["redirect_uri_provided_explicitly"],
            resource=self.config.resource_url,
            subject=record["subject"],
        )

    async def exchange_authorization_code(self, client: OAuthClientInformationFull, authorization_code: AuthorizationCode) -> OAuthToken:
        # Taking the record deletes it, so a code can only ever be exchanged once.
        if await run_in_threadpool(self.store.oauth_take, "codes", _hash(authorization_code.code)) is None:
            raise TokenError("invalid_grant", "authorization code was already used")
        return await self._issue(client.client_id, authorization_code.scopes, authorization_code.subject or "", secrets.token_hex(16))

    async def load_refresh_token(self, client: OAuthClientInformationFull, refresh_token: str) -> StoredRefreshToken | None:
        used = await run_in_threadpool(self.store.oauth_get, "tokens", _hash(refresh_token))
        if used is not None and used["kind"] == "used-refresh":
            # A refresh token was replayed, so it leaked: end that sign-in for everyone holding its tokens.
            logger.warning("A used refresh token was presented again; signing out that connector sign-in")
            await run_in_threadpool(self.store.oauth_delete_where, "tokens", "grant_id", used["revokes"])
            return None
        record = await self._token(refresh_token, "refresh")
        if record is None or record["client_id"] != client.client_id:
            return None
        return StoredRefreshToken(
            token=refresh_token,
            client_id=record["client_id"],
            scopes=record["scopes"],
            expires_at=int(record["expire_at"].timestamp()),
            resource=self.config.resource_url,
            subject=record["subject"],
            grant_id=record["grant_id"],
        )

    async def exchange_refresh_token(
        self, client: OAuthClientInformationFull, refresh_token: StoredRefreshToken, scopes: list[str]
    ) -> OAuthToken:
        # Rotation: the old refresh token works once, and the sign-in's old access token stops working.
        # The old one is kept as a "used" marker until it would have expired, so a replay is noticed. The
        # marker names its sign-in under "revokes", not "grant_id", so later rotations don't delete it.
        old = await run_in_threadpool(self.store.oauth_take, "tokens", _hash(refresh_token.token))
        if old is None:
            raise TokenError("invalid_grant", "refresh token was already used")
        await run_in_threadpool(self.store.oauth_delete_where, "tokens", "grant_id", refresh_token.grant_id)
        marker = {"kind": "used-refresh", "revokes": refresh_token.grant_id, "expire_at": old["expire_at"]}
        await run_in_threadpool(self.store.oauth_put, "tokens", _hash(refresh_token.token), marker)
        if refresh_token.subject not in self.config.allowed_emails:
            raise TokenError("invalid_grant", "this account is no longer allowed to use the connector")
        return await self._issue(client.client_id, scopes, refresh_token.subject or "", refresh_token.grant_id)

    async def load_access_token(self, token: str) -> StoredAccessToken | None:
        record = await self._token(token, "access")
        if record is None:
            return None
        return StoredAccessToken(
            token=token,
            client_id=record["client_id"],
            scopes=record["scopes"],
            expires_at=int(record["expire_at"].timestamp()),
            resource=self.config.resource_url,
            subject=record["subject"],
            grant_id=record["grant_id"],
        )

    async def revoke_token(self, token: StoredAccessToken | StoredRefreshToken) -> None:
        await run_in_threadpool(self.store.oauth_delete_where, "tokens", "grant_id", token.grant_id)

    async def _token(self, token: str, kind: str) -> dict[str, Any] | None:
        """A live token record of this kind whose account is still allowed, or None."""
        record = await run_in_threadpool(self.store.oauth_get, "tokens", _hash(token))
        if record is None or record["kind"] != kind or _expired(record) or record["subject"] not in self.config.allowed_emails:
            return None
        return record

    async def _issue(self, client_id: str, scopes: list[str], subject: str, grant_id: str) -> OAuthToken:
        access, refresh = secrets.token_urlsafe(32), secrets.token_urlsafe(48)
        shared = {"client_id": client_id, "scopes": scopes, "subject": subject, "grant_id": grant_id}
        for token, kind, ttl in ((access, "access", ACCESS_TTL), (refresh, "refresh", REFRESH_TTL)):
            record = {**shared, "kind": kind, "expire_at": _expiry(ttl)}
            await run_in_threadpool(self.store.oauth_put, "tokens", _hash(token), record)
        return OAuthToken(access_token=access, expires_in=ACCESS_TTL, refresh_token=refresh, scope=" ".join(scopes) or None)


def _page(status: int, heading: str, message: str) -> Response:
    return HTMLResponse(auth.page(heading, message), status_code=status, headers={"Cache-Control": "no-store"})
