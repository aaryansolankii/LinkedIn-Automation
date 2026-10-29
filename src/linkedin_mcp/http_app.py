"""Hosted mode: the MCP server over streamable HTTP behind OAuth sign-in, plus LinkedIn's callback and the scheduler hook.

Routes (everything else is a plain 404):
  /mcp                                     MCP endpoint for the claude.ai connector; needs a bearer token
  /.well-known/oauth-protected-resource    where to sign in for /mcp (also served at .../mcp)
  /.well-known/oauth-authorization-server  OAuth metadata for claude.ai
  /register, /authorize, /token, /revoke   OAuth endpoints (MCP SDK handlers, oauth.AllowListProvider)
  /oauth/google/callback                   Google's redirect; only ALLOWED_EMAILS get an authorization code
  /callback                                LinkedIn's OAuth redirect (state is HMAC-signed, see auth.py)
  /internal/publish-due                    POST with the X-Publish-Due-Secret header; publishes approved posts that are due

Secrets, codes and tokens are only ever compared or hashed, never logged; run uvicorn with
access_log=False so request URLs (which carry one-time codes) don't reach the logs either.
"""

from __future__ import annotations

import hashlib
import hmac
import os
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from mcp.server.auth.middleware.auth_context import AuthContextMiddleware
from mcp.server.auth.middleware.bearer_auth import BearerAuthBackend, RequireAuthMiddleware
from mcp.server.auth.provider import ProviderTokenVerifier
from mcp.server.auth.routes import build_resource_metadata_url, create_auth_routes, create_protected_resource_routes
from mcp.server.streamable_http_manager import StreamableHTTPASGIApp
from starlette.applications import Starlette
from starlette.concurrency import run_in_threadpool
from starlette.middleware.authentication import AuthenticationMiddleware
from starlette.requests import Request
from starlette.responses import HTMLResponse, JSONResponse, PlainTextResponse, Response
from starlette.routing import Route

from . import auth, oauth
from .server import mcp, publish_due

PUBLISH_SECRET_HEADER = "X-Publish-Due-Secret"


def _matches(supplied: str, secret: bytes) -> bool:
    """Constant-time comparison that also copes with non-ASCII input."""
    return hmac.compare_digest(supplied.encode(), secret)


async def _not_found(request: Request, exc: Exception | None = None) -> Response:
    return PlainTextResponse("Not Found", status_code=404)


def build_app(publish_secret: str | None = None, provider: oauth.AllowListProvider | None = None) -> Starlette:
    """Build the hosted ASGI app; settings default to the environment (PUBLISH_DUE_SECRET, OAUTH_*, GOOGLE_OAUTH_*, ALLOWED_EMAILS)."""
    publish_secret = publish_secret if publish_secret is not None else os.environ.get("PUBLISH_DUE_SECRET", "")
    if len(publish_secret) < oauth.MIN_SECRET_LENGTH:
        raise RuntimeError(f"PUBLISH_DUE_SECRET must be a random value of at least {oauth.MIN_SECRET_LENGTH} characters.")
    provider = provider or oauth.AllowListProvider(oauth.OAuthConfig.from_env(), oauth.default_store())
    config = provider.config
    if _matches(config.signing_key, publish_secret.encode()):
        raise RuntimeError("OAUTH_SIGNING_KEY and PUBLISH_DUE_SECRET must be different.")

    auth.enable_hosted_login(hmac.new(config.signing_key.encode(), b"linkedin-oauth-state", hashlib.sha256).digest())
    # Stateless JSON responses suit Cloud Run: no session survives scale-to-zero anyway.
    mcp.streamable_http_app(json_response=True, stateless_http=True, host="0.0.0.0")

    # The same pieces MCPServer.streamable_http_app wires up when given `auth=` and `auth_server_provider=`.
    settings = config.auth_settings()
    assert settings.resource_server_url is not None
    resource_metadata_url = build_resource_metadata_url(settings.resource_server_url)
    mcp_endpoint = AuthenticationMiddleware(
        AuthContextMiddleware(
            RequireAuthMiddleware(StreamableHTTPASGIApp(mcp.session_manager), settings.required_scopes or [], resource_metadata_url)
        ),
        backend=BearerAuthBackend(ProviderTokenVerifier(provider), resource_server_url=settings.resource_server_url),
    )
    auth_routes = create_auth_routes(
        provider,
        settings.issuer_url,
        client_registration_options=settings.client_registration_options,
        revocation_options=settings.revocation_options,
    )
    resource_routes = create_protected_resource_routes(settings.resource_server_url, [settings.issuer_url])
    # Clients that don't follow the WWW-Authenticate hint look for the metadata at the root path too.
    resource_routes.append(
        Route("/.well-known/oauth-protected-resource", endpoint=resource_routes[0].endpoint, methods=["GET", "OPTIONS"])
    )

    async def google_callback(request: Request) -> Response:
        return await run_in_threadpool(provider.complete_google_login, dict(request.query_params))

    async def callback(request: Request) -> Response:
        status, heading, message = await run_in_threadpool(auth.complete_hosted_login, dict(request.query_params))
        return HTMLResponse(auth.page(heading, message), status_code=status)

    async def publish_due_hook(request: Request) -> Response:
        supplied = request.headers.get(PUBLISH_SECRET_HEADER, "")
        if request.method != "POST" or not _matches(supplied, publish_secret.encode()):
            return await _not_found(request)
        # publish_due only takes posts approved with schedule_post (status 'scheduled') whose time has come.
        return JSONResponse(await run_in_threadpool(publish_due))

    @asynccontextmanager
    async def lifespan(app: Starlette) -> AsyncIterator[None]:
        async with mcp.session_manager.run():
            yield

    return Starlette(
        routes=[
            Route(oauth.MCP_PATH, endpoint=mcp_endpoint),
            *auth_routes,
            *resource_routes,
            Route(oauth.GOOGLE_CALLBACK_PATH, google_callback, methods=["GET"]),
            Route("/callback", callback, methods=["GET"]),
            Route("/internal/publish-due", publish_due_hook, methods=["GET", "POST", "PUT", "PATCH", "DELETE"]),
        ],
        exception_handlers={404: _not_found, 405: _not_found},
        lifespan=lifespan,
    )
