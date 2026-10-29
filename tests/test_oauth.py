"""Connector sign-in: only ALLOWED_EMAILS get codes, and codes and tokens can't be forged, replayed or outlive their time."""

import dataclasses
import logging
import time

import pytest
from conftest import ALLOWED, CLAUDE_CALLBACK, GOOGLE_CLIENT_ID, ISSUER, FakeGoogle

from linkedin_mcp import oauth

INITIALIZE = {
    "jsonrpc": "2.0",
    "id": 1,
    "method": "initialize",
    "params": {"protocolVersion": "2025-06-18", "capabilities": {}, "clientInfo": {"name": "test", "version": "1"}},
}


def call_mcp(client, access_token=None):
    headers = {"Accept": "application/json, text/event-stream", "Content-Type": "application/json"}
    if access_token is not None:
        headers["Authorization"] = f"Bearer {access_token}"
    return client.post("/mcp", json=INITIALIZE, headers=headers)


@pytest.fixture
def clock(monkeypatch):
    """Move the provider's clock forward: clock(seconds)."""
    def advance(seconds):
        monkeypatch.setattr(oauth, "_now", lambda: time.time() + seconds)
    return advance


def test_mcp_needs_a_token(client):
    response = call_mcp(client)
    assert response.status_code == 401
    assert f'resource_metadata="{ISSUER}/.well-known/oauth-protected-resource/mcp"' in response.headers["www-authenticate"]
    assert call_mcp(client, "forged-token").status_code == 401


def test_allowed_email_can_use_the_connector(client, connector, google, provider, caplog):
    caplog.set_level(logging.DEBUG)
    code = connector.code()
    tokens = connector.exchange(code).json()

    assert tokens["token_type"] == "Bearer" and tokens["expires_in"] == 3600 and tokens["refresh_token"]
    response = call_mcp(client, tokens["access_token"])
    assert response.status_code == 200
    assert response.json()["result"]["serverInfo"]["name"] == "linkedin"

    # Google was asked with PKCE, and the ID token's email is the only thing kept from it.
    assert google.exchanges[0]["code_verifier"] and google.exchanges[0]["client_id"] == GOOGLE_CLIENT_ID
    # Codes, tokens and secrets are neither stored in the clear nor logged.
    stored = repr(provider.store._records)
    secrets_seen = [code, tokens["access_token"], tokens["refresh_token"], connector.client_secret, "google-access-token"]
    assert not any(secret in stored for secret in secrets_seen)
    # The test's own HTTP client (httpx) logs the URLs it requests; the server must never log these.
    logs = " ".join(record.getMessage() for record in caplog.records if not record.name.startswith("httpx"))
    assert logs and not any(secret in logs for secret in secrets_seen)


def test_case_of_the_email_does_not_matter(connector):
    assert connector.sign_in("SECOND@example.com")["access_token"]


def test_other_email_is_denied_and_gets_no_code(connector, provider):
    response = connector.google_returns("someone.else@example.com")
    assert response.status_code == 403
    assert "Access denied" in response.text
    assert "location" not in response.headers
    assert "code=" not in response.text
    assert not provider.store._records.get("codes")


@pytest.mark.parametrize(
    "claims",
    [
        {"iss": "https://evil.example"},
        {"aud": "another-client.apps.googleusercontent.com"},
        {"exp": int(time.time()) - 600},
        {"email_verified": False},
        {"nonce": "from-another-sign-in"},
        {"email": ""},
    ],
    ids=["issuer", "audience", "expired", "unverified-email", "nonce", "no-email"],
)
def test_untrustworthy_id_tokens_are_refused(connector, provider, claims):
    response = connector.google_returns(ALLOWED, claims)
    assert response.status_code == 400
    assert "Sign-in failed" in response.text
    assert not provider.store._records.get("codes")


def test_id_token_signed_by_someone_else_is_refused(connector, google, provider):
    from cryptography.hazmat.primitives.asymmetric import rsa

    google.signing_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    response = connector.google_returns(ALLOWED)
    assert response.status_code == 400
    assert not provider.store._records.get("codes")


def test_forged_expired_and_reused_sign_in_links_are_refused(client, connector, google, clock):
    for state in ["", "forged"]:
        response = client.get("/oauth/google/callback", params={"state": state, "code": "google-code"}, follow_redirects=False)
        assert response.status_code == 400

    query = connector.start()
    google.answer(ALLOWED, query["nonce"])
    callback = {"state": query["state"], "code": "google-code"}
    assert client.get("/oauth/google/callback", params=callback, follow_redirects=False).status_code == 302
    assert client.get("/oauth/google/callback", params=callback, follow_redirects=False).status_code == 400

    query = connector.start()
    google.answer(ALLOWED, query["nonce"])
    clock(11 * 60)
    response = client.get("/oauth/google/callback", params={"state": query["state"], "code": "c"}, follow_redirects=False)
    assert response.status_code == 400


def test_codes_work_once_and_expire(connector, clock):
    code = connector.code()
    assert connector.exchange(code).status_code == 200
    assert connector.exchange(code).json()["error"] == "invalid_grant"
    assert connector.exchange("forged-code").json()["error"] == "invalid_grant"

    code = connector.code()
    clock(5 * 60 + 1)
    assert connector.exchange(code).json()["error"] == "invalid_grant"


def test_code_needs_the_pkce_verifier_and_the_client_secret(connector):
    code = connector.code()
    wrong_verifier = connector.token(grant_type="authorization_code", code=code, redirect_uri=CLAUDE_CALLBACK, code_verifier="x" * 50)
    assert wrong_verifier.json()["error"] == "invalid_grant"
    connector.client_secret = "forged-secret"
    assert connector.exchange(code).status_code == 401


def test_access_tokens_expire(client, connector, clock):
    access_token = connector.sign_in()["access_token"]
    clock(60 * 60 + 1)
    assert call_mcp(client, access_token).status_code == 401


def test_refresh_rotates_both_tokens(client, connector):
    first = connector.sign_in()
    response = connector.token(grant_type="refresh_token", refresh_token=first["refresh_token"])
    assert response.status_code == 200
    second = response.json()
    assert second["refresh_token"] != first["refresh_token"]

    assert call_mcp(client, second["access_token"]).status_code == 200
    assert call_mcp(client, first["access_token"]).status_code == 401
    assert connector.token(grant_type="refresh_token", refresh_token="forged").json()["error"] == "invalid_grant"


def test_replaying_a_used_refresh_token_ends_the_sign_in(client, connector):
    # Someone copies the refresh token and refreshes first (twice); the real client's next refresh exposes it.
    stolen = connector.sign_in()
    attacker = connector.token(grant_type="refresh_token", refresh_token=stolen["refresh_token"]).json()
    attacker = connector.token(grant_type="refresh_token", refresh_token=attacker["refresh_token"]).json()
    assert call_mcp(client, attacker["access_token"]).status_code == 200

    replay = connector.token(grant_type="refresh_token", refresh_token=stolen["refresh_token"])
    assert replay.json()["error"] == "invalid_grant"
    assert call_mcp(client, attacker["access_token"]).status_code == 401
    assert connector.token(grant_type="refresh_token", refresh_token=attacker["refresh_token"]).json()["error"] == "invalid_grant"
    assert connector.sign_in()["access_token"]  # signing in again works


def test_refresh_tokens_expire_after_60_days(connector, clock):
    refresh_token = connector.sign_in()["refresh_token"]
    clock(60 * 24 * 60 * 60 + 1)
    assert connector.token(grant_type="refresh_token", refresh_token=refresh_token).json()["error"] == "invalid_grant"


def test_revoking_a_token_ends_the_sign_in(client, connector):
    tokens = connector.sign_in()
    response = client.post("/revoke", data={
        "token": tokens["access_token"], "client_id": connector.client_id, "client_secret": connector.client_secret,
    })
    assert response.status_code == 200
    assert call_mcp(client, tokens["access_token"]).status_code == 401
    assert connector.token(grant_type="refresh_token", refresh_token=tokens["refresh_token"]).json()["error"] == "invalid_grant"


def test_removing_an_email_from_the_list_cuts_access(client, connector, provider):
    tokens = connector.sign_in("second@example.com")
    provider.config = dataclasses.replace(provider.config, allowed_emails=frozenset({ALLOWED}))
    assert call_mcp(client, tokens["access_token"]).status_code == 401
    assert connector.token(grant_type="refresh_token", refresh_token=tokens["refresh_token"]).json()["error"] == "invalid_grant"


def test_clients_may_only_redirect_to_claude_or_loopback(client):
    def register(uri):
        return client.post("/register", json={"redirect_uris": [uri], "token_endpoint_auth_method": "client_secret_post"})

    allowed = [CLAUDE_CALLBACK, "https://claude.com/api/mcp/auth_callback", "http://localhost:33418/callback",
               "http://127.0.0.1:5000/cb", "http://[::1]:8080/cb"]
    refused = ["https://evil.example/callback", "https://claude.ai.evil.example/api/mcp/auth_callback",
               "http://claude.ai/api/mcp/auth_callback", "https://sub.claude.ai/api/mcp/auth_callback",
               "https://claude.ai/somewhere/else", "https://claude.ai:8443/api/mcp/auth_callback",
               "https://claude.ai/api/mcp/auth_callback?next=x", "myapp://localhost/cb", "ftp://127.0.0.1/x"]
    for uri in allowed:
        assert register(uri).status_code == 201, uri
    for uri in refused:
        response = register(uri)
        assert response.status_code == 400, uri
        assert response.json()["error"] == "invalid_redirect_uri"


def test_tokens_are_only_issued_for_this_connector(connector):
    response = connector.client.get("/authorize", params={
        "response_type": "code", "client_id": connector.client_id, "redirect_uri": CLAUDE_CALLBACK,
        "code_challenge": "x" * 43, "state": "s", "resource": "https://other.example/mcp",
    }, follow_redirects=False)
    assert response.status_code == 302
    assert "error=invalid_target" in response.headers["location"]


def test_fake_google_matches_the_real_jwks_interface():
    # GoogleSignIn only calls get_signing_key_from_jwt(token).key on the JWKS client.
    assert hasattr(oauth.jwt.PyJWKClient, "get_signing_key_from_jwt")
    assert FakeGoogle().get_signing_key_from_jwt("token").key is not None
