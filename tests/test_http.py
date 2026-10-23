"""Hosted mode: only the secret MCP path is served, and publish-due only posts approved posts."""

import logging
import secrets

import pytest
from starlette.testclient import TestClient

from linkedin_mcp import linkedin, store
from linkedin_mcp.http_app import PUBLISH_SECRET_HEADER, build_app

PATH_SECRET = secrets.token_urlsafe(32)
PUBLISH_SECRET = secrets.token_urlsafe(32)
MCP_HEADERS = {"Accept": "application/json, text/event-stream", "Content-Type": "application/json"}
INITIALIZE = {
    "jsonrpc": "2.0",
    "id": 1,
    "method": "initialize",
    "params": {"protocolVersion": "2025-06-18", "capabilities": {}, "clientInfo": {"name": "test", "version": "1"}},
}


@pytest.fixture
def client():
    with TestClient(build_app(PATH_SECRET, PUBLISH_SECRET)) as test_client:
        yield test_client


def test_mcp_is_served_only_on_the_secret_path(client, caplog):
    caplog.set_level(logging.DEBUG)
    response = client.post(f"/mcp/{PATH_SECRET}", json=INITIALIZE, headers=MCP_HEADERS)
    assert response.status_code == 200
    assert response.json()["result"]["serverInfo"]["name"] == "linkedin"

    for path in ["/", "/mcp", "/mcp/", "/mcp/wrong", f"/mcp/{PATH_SECRET[:-1]}", f"/mcp/{PATH_SECRET}x", "/docs"]:
        assert client.post(path, json=INITIALIZE, headers=MCP_HEADERS).status_code == 404, path
        assert client.get(path).status_code == 404, path

    # The test's own HTTP client (httpx) logs the URLs it requests; the server must never log them.
    server_logs = [record.getMessage() for record in caplog.records if not record.name.startswith("httpx")]
    assert server_logs and not any(PATH_SECRET in message for message in server_logs)


def test_weak_or_reused_secrets_are_refused():
    with pytest.raises(RuntimeError):
        build_app("too-short", PUBLISH_SECRET)
    with pytest.raises(RuntimeError):
        build_app(PATH_SECRET, PATH_SECRET)


def test_publish_due_requires_post_and_the_secret_header(client):
    assert client.post("/internal/publish-due").status_code == 404
    assert client.post("/internal/publish-due", headers={PUBLISH_SECRET_HEADER: "wrong"}).status_code == 404
    assert client.post("/internal/publish-due", headers={PUBLISH_SECRET_HEADER: PATH_SECRET}).status_code == 404
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


def test_callback_rejects_unsigned_state(client, monkeypatch):
    exchanged = []
    monkeypatch.setattr("linkedin_mcp.auth.exchange_code", lambda *args: exchanged.append(args))
    for state in ["", "garbage", "9999999999.nonce.forged-signature"]:
        assert client.get("/callback", params={"state": state, "code": "x"}).status_code == 400
    assert exchanged == []
