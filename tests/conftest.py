"""Point the app at a throwaway data dir (local SQLite mode) before anything imports it."""

import os
import tempfile

os.environ["LINKEDIN_DATA_DIR"] = tempfile.mkdtemp(prefix="linkedin-mcp-tests-")
for name in ("STORAGE", "IMAGES_BUCKET", "LINKEDIN_ACCESS_TOKEN", "LINKEDIN_AUTHOR_URN", "LINKEDIN_SCOPES", "PAGE_ONLY"):
    os.environ.pop(name, None)

import base64  # noqa: E402
import hashlib  # noqa: E402
import secrets  # noqa: E402
import time  # noqa: E402
from types import SimpleNamespace  # noqa: E402
from urllib.parse import parse_qs, urlparse  # noqa: E402

import httpx  # noqa: E402
import jwt  # noqa: E402
import pytest  # noqa: E402
from cryptography.hazmat.primitives.asymmetric import rsa  # noqa: E402
from starlette.testclient import TestClient  # noqa: E402

from linkedin_mcp import oauth  # noqa: E402
from linkedin_mcp.config import DB_FILE  # noqa: E402
from linkedin_mcp.http_app import build_app  # noqa: E402

ISSUER = "https://connector.example"
ALLOWED = "owner@example.com"
GOOGLE_CLIENT_ID = "google-client-id.apps.googleusercontent.com"
CLAUDE_CALLBACK = "https://claude.ai/api/mcp/auth_callback"
PUBLISH_SECRET = secrets.token_urlsafe(32)
SIGNING_KEY = secrets.token_urlsafe(32)


@pytest.fixture(autouse=True)
def empty_calendar():
    """Start every test with an empty calendar."""
    DB_FILE.unlink(missing_ok=True)
    yield
    DB_FILE.unlink(missing_ok=True)


class FakeGoogle:
    """Stands in for Google: signs ID tokens with a test key and answers the code exchange."""

    _key = rsa.generate_private_key(public_exponent=65537, key_size=2048)

    def __init__(self) -> None:
        self.claims: dict = {}
        self.signing_key = self._key
        self.exchanges: list[dict] = []

    def get_signing_key_from_jwt(self, token: str) -> SimpleNamespace:  # the JWKS client interface
        return SimpleNamespace(key=self._key.public_key())

    def answer(self, email: str, nonce: str, overrides: dict | None = None) -> None:
        """Make the next code exchange return an ID token for `email` (claims can be overridden)."""
        now = int(time.time())
        self.claims = {
            "iss": "https://accounts.google.com", "aud": GOOGLE_CLIENT_ID, "sub": "1234567890", "iat": now,
            "exp": now + 3600, "email": email, "email_verified": True, "nonce": nonce, **(overrides or {}),
        }

    def post(self, url: str, data: dict, timeout: float) -> httpx.Response:
        self.exchanges.append(data)
        id_token = jwt.encode(self.claims, self.signing_key, algorithm="RS256")
        return httpx.Response(200, json={"id_token": id_token, "access_token": "google-access-token"})


@pytest.fixture
def google(monkeypatch):
    fake = FakeGoogle()
    monkeypatch.setattr(oauth.httpx, "post", fake.post)
    return fake


@pytest.fixture
def provider(google):
    config = oauth.OAuthConfig.create(ISSUER, GOOGLE_CLIENT_ID, "google-client-secret", f"{ALLOWED}, Second@Example.com", SIGNING_KEY)
    sign_in = oauth.GoogleSignIn(GOOGLE_CLIENT_ID, "google-client-secret", config.google_redirect_uri, jwks=google)
    return oauth.AllowListProvider(config, oauth.MemoryStore(), sign_in)


@pytest.fixture
def client(provider):
    with TestClient(build_app(PUBLISH_SECRET, provider), base_url=ISSUER) as test_client:
        yield test_client


class Connector:
    """Drives the OAuth flow the way claude.ai does: register, authorize, Google, token."""

    def __init__(self, client: TestClient, google: FakeGoogle) -> None:
        self.client, self.google = client, google
        response = client.post("/register", json={
            "client_name": "claudeai", "redirect_uris": [CLAUDE_CALLBACK], "scope": "claudeai",
            "grant_types": ["authorization_code", "refresh_token"], "response_types": ["code"],
            "token_endpoint_auth_method": "client_secret_post",
        })
        assert response.status_code == 201, response.text
        self.client_id, self.client_secret = response.json()["client_id"], response.json()["client_secret"]
        self.verifier = secrets.token_urlsafe(48)

    def start(self, **params) -> dict:
        """GET /authorize; return the query of the Google URL it redirects to."""
        challenge = base64.urlsafe_b64encode(hashlib.sha256(self.verifier.encode()).digest()).decode().rstrip("=")
        response = self.client.get("/authorize", params={
            "response_type": "code", "client_id": self.client_id, "redirect_uri": CLAUDE_CALLBACK,
            "code_challenge": challenge, "code_challenge_method": "S256", "state": "claude-state",
            "scope": "claudeai", "resource": f"{ISSUER}/mcp", **params,
        }, follow_redirects=False)
        assert response.status_code == 302, response.text
        return {key: values[0] for key, values in parse_qs(urlparse(response.headers["location"]).query).items()}

    def google_returns(self, email: str, claims: dict | None = None) -> httpx.Response:
        """Sign in at Google as `email`; return the response of /oauth/google/callback."""
        google_query = self.start()
        self.google.answer(email, google_query["nonce"], claims)
        return self.client.get(
            "/oauth/google/callback", params={"state": google_query["state"], "code": "google-code"}, follow_redirects=False
        )

    def code(self, email: str = ALLOWED) -> str:
        response = self.google_returns(email)
        assert response.status_code == 302, response.text
        query = parse_qs(urlparse(response.headers["location"]).query)
        assert query["state"] == ["claude-state"]
        return query["code"][0]

    def token(self, **form) -> httpx.Response:
        return self.client.post("/token", data={"client_id": self.client_id, "client_secret": self.client_secret, **form})

    def exchange(self, code: str) -> httpx.Response:
        return self.token(grant_type="authorization_code", code=code, redirect_uri=CLAUDE_CALLBACK, code_verifier=self.verifier)

    def sign_in(self, email: str = ALLOWED) -> dict:
        response = self.exchange(self.code(email))
        assert response.status_code == 200, response.text
        return response.json()


@pytest.fixture
def connector(client, google):
    return Connector(client, google)
