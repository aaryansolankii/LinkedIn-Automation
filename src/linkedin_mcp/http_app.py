"""Hosted mode: the MCP server over streamable HTTP, plus the OAuth callback and the scheduler hook.

Routes (everything else is a plain 404):
  /mcp/<MCP_PATH_SECRET>   MCP endpoint for the claude.ai custom connector
  /callback                LinkedIn's OAuth redirect (state is HMAC-signed, see auth.py)
  /internal/publish-due    POST with the X-Publish-Due-Secret header; publishes approved posts that are due

Secrets are only ever compared, never logged; run uvicorn with access_log=False so request
paths (which contain the MCP secret) don't reach the logs either.
"""

from __future__ import annotations

import hashlib
import hmac
import os
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from starlette.applications import Starlette
from starlette.concurrency import run_in_threadpool
from starlette.requests import Request
from starlette.responses import HTMLResponse, JSONResponse, PlainTextResponse, Response
from starlette.routing import Route
from starlette.types import Receive, Scope, Send

from . import auth
from .server import mcp, publish_due

MIN_SECRET_LENGTH = 32
PUBLISH_SECRET_HEADER = "X-Publish-Due-Secret"


def _matches(supplied: str, secret: bytes) -> bool:
    """Constant-time comparison that also copes with non-ASCII input."""
    return hmac.compare_digest(supplied.encode(), secret)


async def _not_found(request: Request, exc: Exception | None = None) -> Response:
    return PlainTextResponse("Not Found", status_code=404)


class _SecretPathMCP:
    """ASGI endpoint that hands the request to MCP only when the path token is the secret."""

    def __init__(self, secret: bytes) -> None:
        self._secret = secret

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if _matches(scope["path_params"].get("token", ""), self._secret):
            await mcp.session_manager.asgi_app(scope, receive, send)
        else:
            await PlainTextResponse("Not Found", status_code=404)(scope, receive, send)


def build_app(path_secret: str | None = None, publish_secret: str | None = None) -> Starlette:
    """Build the hosted ASGI app; secrets default to the MCP_PATH_SECRET / PUBLISH_DUE_SECRET env vars."""
    path_secret = path_secret if path_secret is not None else os.environ.get("MCP_PATH_SECRET", "")
    publish_secret = publish_secret if publish_secret is not None else os.environ.get("PUBLISH_DUE_SECRET", "")
    for name, value in (("MCP_PATH_SECRET", path_secret), ("PUBLISH_DUE_SECRET", publish_secret)):
        if len(value) < MIN_SECRET_LENGTH:
            raise RuntimeError(f"{name} must be a random value of at least {MIN_SECRET_LENGTH} characters.")
    if path_secret == publish_secret:
        raise RuntimeError("MCP_PATH_SECRET and PUBLISH_DUE_SECRET must be different.")

    auth.enable_hosted_login(hmac.new(path_secret.encode(), b"linkedin-oauth-state", hashlib.sha256).digest())
    # Stateless JSON responses suit Cloud Run: no session survives scale-to-zero anyway.
    mcp.streamable_http_app(json_response=True, stateless_http=True, host="0.0.0.0")

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
            Route("/mcp/{token}", endpoint=_SecretPathMCP(path_secret.encode())),
            Route("/callback", callback, methods=["GET"]),
            Route("/internal/publish-due", publish_due_hook, methods=["GET", "POST", "PUT", "PATCH", "DELETE"]),
        ],
        exception_handlers={404: _not_found, 405: _not_found},
        lifespan=lifespan,
    )
