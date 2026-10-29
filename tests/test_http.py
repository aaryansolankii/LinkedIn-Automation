"""Hosted mode: what is served, startup checks, and publish-due only posting approved posts."""

import pytest
from conftest import ISSUER, PUBLISH_SECRET, SIGNING_KEY

from linkedin_mcp import linkedin, oauth, store
from linkedin_mcp.http_app import PUBLISH_SECRET_HEADER, build_app


def test_only_known_routes_are_served(client):
    for path in ["/", "/mcp/anything", "/mcp/" + SIGNING_KEY, "/docs", "/oauth/google"]:
        assert client.get(path).status_code == 404, path
    assert client.get("/token").status_code == 404  # wrong method


def test_oauth_metadata_points_claude_at_this_service(client):
    for path in ["/.well-known/oauth-protected-resource", "/.well-known/oauth-protected-resource/mcp"]:
        metadata = client.get(path).json()
        assert metadata["resource"] == f"{ISSUER}/mcp"
        assert metadata["authorization_servers"] == [ISSUER]

    metadata = client.get("/.well-known/oauth-authorization-server").json()
    assert metadata["issuer"] == ISSUER
    for endpoint in ("authorization", "token", "registration", "revocation"):
        assert metadata[f"{endpoint}_endpoint"].startswith(ISSUER + "/")
    assert metadata["code_challenge_methods_supported"] == ["S256"]


def test_startup_refuses_weak_reused_or_missing_settings(provider, monkeypatch):
    with pytest.raises(RuntimeError):
        build_app("too-short", provider)
    with pytest.raises(RuntimeError):
        build_app(SIGNING_KEY, provider)  # same value as OAUTH_SIGNING_KEY

    for name in ("OAUTH_ISSUER_URL", "GOOGLE_OAUTH_CLIENT_ID", "GOOGLE_OAUTH_CLIENT_SECRET", "ALLOWED_EMAILS", "OAUTH_SIGNING_KEY"):
        monkeypatch.delenv(name, raising=False)
    with pytest.raises(RuntimeError, match="OAUTH_ISSUER_URL"):
        build_app(PUBLISH_SECRET)
    for bad in [("http://connector.example", "a@b.c", SIGNING_KEY), (ISSUER + "/mcp", "a@b.c", SIGNING_KEY),
                (ISSUER, " , ", SIGNING_KEY), (ISSUER, "a@b.c", "short")]:
        issuer, emails, key = bad
        with pytest.raises(RuntimeError):
            oauth.OAuthConfig.create(issuer, "id", "secret", emails, key)


def test_publish_due_requires_post_and_the_secret_header(client):
    assert client.post("/internal/publish-due").status_code == 404
    assert client.post("/internal/publish-due", headers={PUBLISH_SECRET_HEADER: "wrong"}).status_code == 404
    assert client.post("/internal/publish-due", headers={PUBLISH_SECRET_HEADER: SIGNING_KEY}).status_code == 404
    assert client.post("/internal/publish-due", headers={"Authorization": f"Bearer {PUBLISH_SECRET}"}).status_code == 404
    assert client.get("/internal/publish-due", headers={PUBLISH_SECRET_HEADER: PUBLISH_SECRET}).status_code == 404
    assert client.post("/internal/publish-due", headers={PUBLISH_SECRET_HEADER: PUBLISH_SECRET}).status_code == 200


def test_publish_due_publishes_only_approved_posts_that_are_due(client, monkeypatch):
    published = []

    def fake_publish(text, image_path=""):
        published.append(text)
        return linkedin.PublishedPost(urn="urn:li:share:1", url=linkedin.post_url("urn:li:share:1"))

    monkeypatch.setattr(linkedin, "publish", fake_publish)
    past, future = "2026-01-01 09:00", "2099-01-01 09:00"
    approved = store.add_post(title="approved", content="Approved text", status="scheduled", publish_at=past)
    draft = store.add_post(title="draft", content="Draft text", status="draft", publish_at=past)
    failed = store.add_post(title="failed", content="Failed text", status="failed", publish_at=past)
    idea = store.add_post(title="idea", status="idea", publish_at=past)
    later = store.add_post(title="later", content="Later text", status="scheduled", publish_at=future)

    response = client.post("/internal/publish-due", headers={PUBLISH_SECRET_HEADER: PUBLISH_SECRET})

    assert response.status_code == 200
    assert published == ["Approved text"]
    assert store.get_post(approved["id"])["status"] == "published"
    assert store.get_post(draft["id"])["status"] == "draft"
    assert store.get_post(failed["id"])["status"] == "failed"
    assert store.get_post(idea["id"])["status"] == "idea"
    assert store.get_post(later["id"])["status"] == "scheduled"


def test_linkedin_callback_rejects_unsigned_state(client, monkeypatch):
    exchanged = []
    monkeypatch.setattr("linkedin_mcp.auth.exchange_code", lambda *args: exchanged.append(args))
    for state in ["", "garbage", "9999999999.nonce.forged-signature"]:
        assert client.get("/callback", params={"state": state, "code": "x"}).status_code == 400
    assert exchanged == []
