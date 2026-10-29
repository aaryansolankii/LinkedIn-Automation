"""linkedin_status without openid, and the PAGE_ONLY guard on publishing and login."""

import pytest

from linkedin_mcp import auth, linkedin, server
from linkedin_mcp.config import CREDENTIALS_FILE, Credentials, save_credentials
from linkedin_mcp.errors import LinkedInError

PAGE = "urn:li:organization:123456"


@pytest.fixture
def saved_login():
    def save(author_urn):
        save_credentials(Credentials(access_token="token", author_urn=author_urn, expires_at="2099-01-01T10:00"))
    yield save
    CREDENTIALS_FILE.unlink(missing_ok=True)


@pytest.fixture
def no_network(monkeypatch):
    def refuse(*args, **kwargs):
        raise AssertionError("LinkedIn must not be called")
    monkeypatch.setattr(linkedin, "request", refuse)


def test_status_without_openid_uses_the_saved_login(monkeypatch, saved_login, no_network):
    monkeypatch.setenv("LINKEDIN_SCOPES", "w_organization_social r_organization_social")
    saved_login(PAGE)
    account = server.linkedin_status()["account"]
    assert account["connected"] is True
    assert account["author_urn"] == PAGE
    assert account["token_expires_at"] == "2099-01-01T10:00"
    assert account["token_days_left"] > 0


def test_status_with_openid_still_checks_the_profile(monkeypatch, saved_login):
    monkeypatch.setattr(linkedin, "get_profile", lambda token: {"name": "Aaryan"})
    saved_login("urn:li:person:abc")
    account = server.linkedin_status()["account"]
    assert account["connected"] is True and account["name"] == "Aaryan"


def test_page_only_refuses_to_post_as_a_person(monkeypatch, saved_login, no_network):
    monkeypatch.setenv("PAGE_ONLY", "true")
    saved_login("urn:li:person:abc")
    with pytest.raises(LinkedInError, match="PAGE_ONLY"):
        linkedin.publish("Hello")
    monkeypatch.setenv("LINKEDIN_AUTHOR_URN", "urn:li:member:abc")
    with pytest.raises(LinkedInError, match="PAGE_ONLY"):
        linkedin.publish("Hello")


def test_page_only_allows_the_company_page(monkeypatch, saved_login):
    calls = []

    class Response:
        headers = {"x-restli-id": "urn:li:share:1"}

    def fake_request(method, url, action, **kwargs):
        calls.append(kwargs["json"]["author"])
        return Response()

    monkeypatch.setattr(linkedin, "request", fake_request)
    monkeypatch.setenv("PAGE_ONLY", "true")
    saved_login(PAGE)
    assert linkedin.publish("Hello").urn == "urn:li:share:1"
    assert calls == [PAGE]


def test_page_only_login_refuses_member_scopes(monkeypatch):
    monkeypatch.setenv("PAGE_ONLY", "true")
    monkeypatch.setenv("LINKEDIN_CLIENT_ID", "id")
    monkeypatch.setenv("LINKEDIN_CLIENT_SECRET", "secret")
    monkeypatch.setenv("LINKEDIN_SCOPES", "w_member_social w_organization_social")
    with pytest.raises(LinkedInError, match="w_member_social"):
        server.linkedin_login()
    with pytest.raises(LinkedInError, match="w_member_social"):
        auth._hosted_login_url()
