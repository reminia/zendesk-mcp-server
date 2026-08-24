"""
Tests for OAuthAuthProvider: proactive refresh, reactive retry, and the
re-authorization path.
"""
from datetime import datetime, timedelta, timezone

import pytest
import responses

from zendesk_mcp_server.config import OAuthSettings
from zendesk_mcp_server.oauth import OAuthAuthProvider, ReauthorizationRequired
from zendesk_mcp_server.tokens import TokenSet, TokenStore
from zendesk_mcp_server.zendesk_client import ZendeskClient

SUBDOMAIN = "example"
CLIENT_ID = "zendesk-mcp-client"
TOKEN_URL = "https://example.zendesk.com/oauth/tokens"
TICKETS_URL = "https://example.zendesk.com/api/v2/tickets.json"

INVALID_TOKEN_BODY = {
    "error": "invalid_token",
    "error_description": "The access token provided is expired, revoked, malformed or invalid.",
}


@pytest.fixture
def settings(tmp_path):
    return OAuthSettings(
        subdomain=SUBDOMAIN,
        client_id=CLIENT_ID,
        token_file=tmp_path / "tokens.json",
        scopes="tickets:read",
    )


@pytest.fixture
def store(settings):
    return TokenStore(settings.token_file)


def stored_tokens(store, *, expires_in=timedelta(minutes=30), refresh_token="refresh-1", **kw):
    tokens = TokenSet(
        access_token=kw.get("access_token", "access-1"),
        subdomain=kw.get("subdomain", SUBDOMAIN),
        client_id=kw.get("client_id", CLIENT_ID),
        refresh_token=refresh_token,
        expires_at=datetime.now(timezone.utc) + expires_in if expires_in else None,
        refresh_token_expires_at=kw.get(
            "refresh_token_expires_at", datetime.now(timezone.utc) + timedelta(days=90)
        ),
        scope="tickets:read",
    )
    store.save(tokens)
    return tokens


def add_refresh_response(access_token="access-2", refresh_token="refresh-2"):
    responses.add(
        responses.POST,
        TOKEN_URL,
        json={
            "access_token": access_token,
            "refresh_token": refresh_token,
            "scope": "tickets:read",
            "expires_in": 1800,
            "refresh_token_expires_in": 7776000,
        },
        status=200,
    )


def test_valid_token_is_used_as_a_bearer_header(settings, store):
    stored_tokens(store)
    provider = OAuthAuthProvider(settings, store=store)

    assert provider.auth_header() == "Bearer access-1"


def test_missing_token_file_tells_the_operator_to_bootstrap(settings, store):
    provider = OAuthAuthProvider(settings, store=store)

    with pytest.raises(Exception, match="Run zendesk-auth"):
        provider.auth_header()


@responses.activate
def test_expired_token_is_refreshed_proactively(settings, store):
    # Inside the skew window, so treated as expired before it actually is.
    stored_tokens(store, expires_in=timedelta(seconds=10))
    add_refresh_response()
    provider = OAuthAuthProvider(settings, store=store)

    assert provider.auth_header() == "Bearer access-2"

    body = responses.calls[0].request.body
    assert "grant_type=refresh_token" in body
    assert "refresh_token=refresh-1" in body
    # Public client: no secret is sent.
    assert "client_secret" not in body


@responses.activate
def test_rotated_refresh_token_is_persisted_immediately(settings, store):
    stored_tokens(store, expires_in=timedelta(seconds=10))
    add_refresh_response(access_token="access-2", refresh_token="refresh-2")
    provider = OAuthAuthProvider(settings, store=store)

    provider.auth_header()

    reloaded = store.load()
    assert reloaded.access_token == "access-2"
    assert reloaded.refresh_token == "refresh-2"


@responses.activate
def test_refresh_response_without_a_new_refresh_token_keeps_the_old_one(settings, store):
    stored_tokens(store, expires_in=timedelta(seconds=10))
    responses.add(
        responses.POST,
        TOKEN_URL,
        json={"access_token": "access-2", "expires_in": 1800},
        status=200,
    )
    provider = OAuthAuthProvider(settings, store=store)

    provider.auth_header()

    assert store.load().refresh_token == "refresh-1"


def test_expired_refresh_token_requires_reauthorization(settings, store):
    stored_tokens(
        store,
        expires_in=timedelta(seconds=10),
        refresh_token_expires_at=datetime.now(timezone.utc) - timedelta(days=1),
    )
    provider = OAuthAuthProvider(settings, store=store)

    with pytest.raises(ReauthorizationRequired, match="zendesk-auth"):
        provider.auth_header()


def test_absent_refresh_token_requires_reauthorization(settings, store):
    stored_tokens(store, expires_in=timedelta(seconds=10), refresh_token=None)
    provider = OAuthAuthProvider(settings, store=store)

    with pytest.raises(ReauthorizationRequired, match="zendesk-auth"):
        provider.auth_header()


@responses.activate
def test_failed_refresh_reports_reauthorization(settings, store):
    stored_tokens(store, expires_in=timedelta(seconds=10))
    responses.add(
        responses.POST,
        TOKEN_URL,
        json={"error": "invalid_grant", "error_description": "revoked"},
        status=400,
    )
    provider = OAuthAuthProvider(settings, store=store)

    with pytest.raises(ReauthorizationRequired, match="zendesk-auth"):
        provider.auth_header()


def test_mismatched_subdomain_is_warned_about(settings, store, caplog):
    stored_tokens(store, subdomain="other-tenant")
    provider = OAuthAuthProvider(settings, store=store)

    with caplog.at_level("WARNING"):
        provider.auth_header()

    assert "other-tenant" in caplog.text
    assert "zendesk-auth" in caplog.text


@responses.activate
def test_non_expiring_legacy_token_is_never_refreshed(settings, store):
    stored_tokens(store, expires_in=None, refresh_token=None)
    provider = OAuthAuthProvider(settings, store=store)

    assert provider.auth_header() == "Bearer access-1"
    assert len(responses.calls) == 0


@responses.activate
def test_refresh_is_skipped_when_another_process_already_rotated(settings, store):
    """
    The refresh token can only be spent once, so if a concurrent MCP process has
    already rotated it, adopt that result instead of refreshing again.
    """
    stored_tokens(store)
    provider = OAuthAuthProvider(settings, store=store)
    assert provider.auth_header() == "Bearer access-1"

    stored_tokens(store, access_token="access-9", refresh_token="refresh-9")

    adopted = provider._renew(reason="test", rejected_token="access-1")

    assert adopted.access_token == "access-9"
    assert len(responses.calls) == 0


def test_get_tickets_sends_the_bearer_token(settings, store, monkeypatch):
    """The direct urllib path authenticates through the same provider."""
    import json
    import urllib.request

    stored_tokens(store)
    client = ZendeskClient(subdomain=SUBDOMAIN, auth=OAuthAuthProvider(settings, store=store))
    captured = {}

    class FakeResponse:
        def read(self):
            return json.dumps({"tickets": [], "next_page": None}).encode()

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

    def fake_urlopen(request, *args, **kwargs):
        captured["authorization"] = request.get_header("Authorization")
        return FakeResponse()

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)

    client.get_tickets()

    assert captured["authorization"] == "Bearer access-1"


@responses.activate
def test_get_tickets_refreshes_an_expired_token_before_calling(settings, store, monkeypatch):
    """Proactive refresh covers the urllib path, which has no response hook."""
    import json
    import urllib.request

    stored_tokens(store, expires_in=timedelta(seconds=10))
    add_refresh_response()
    client = ZendeskClient(subdomain=SUBDOMAIN, auth=OAuthAuthProvider(settings, store=store))
    captured = {}

    class FakeResponse:
        def read(self):
            return json.dumps({"tickets": [], "next_page": None}).encode()

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

    def fake_urlopen(request, *args, **kwargs):
        captured["authorization"] = request.get_header("Authorization")
        return FakeResponse()

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)

    client.get_tickets()

    assert captured["authorization"] == "Bearer access-2"


class TestReactiveRetry:
    """The 401 response hook, exercised through a real client session."""

    @responses.activate
    def test_invalid_token_401_is_retried_once_with_a_fresh_token(self, settings, store):
        stored_tokens(store)
        provider = OAuthAuthProvider(settings, store=store)
        client = ZendeskClient(subdomain=SUBDOMAIN, auth=provider)

        responses.add(responses.GET, TICKETS_URL, json=INVALID_TOKEN_BODY, status=401)
        add_refresh_response()
        responses.add(responses.GET, TICKETS_URL, json={"tickets": []}, status=200)

        response = client.session.get(TICKETS_URL)

        assert response.status_code == 200
        attempts = [call.request for call in responses.calls if call.request.url.startswith(TICKETS_URL)]
        assert len(attempts) == 2
        assert attempts[0].headers["Authorization"] == "Bearer access-1"
        assert attempts[1].headers["Authorization"] == "Bearer access-2"

    @responses.activate
    def test_scope_401_is_not_retried(self, settings, store):
        """A permissions problem must surface, not be masked by a refresh."""
        stored_tokens(store)
        provider = OAuthAuthProvider(settings, store=store)
        client = ZendeskClient(subdomain=SUBDOMAIN, auth=provider)
        responses.add(
            responses.GET,
            TICKETS_URL,
            json={"error": "Couldn't authenticate you"},
            status=401,
        )

        response = client.session.get(TICKETS_URL)

        assert response.status_code == 401
        assert len(responses.calls) == 1

    @responses.activate
    def test_403_is_not_retried(self, settings, store):
        stored_tokens(store)
        provider = OAuthAuthProvider(settings, store=store)
        client = ZendeskClient(subdomain=SUBDOMAIN, auth=provider)
        responses.add(responses.GET, TICKETS_URL, json={"error": "Forbidden"}, status=403)

        response = client.session.get(TICKETS_URL)

        assert response.status_code == 403
        assert len(responses.calls) == 1

    @responses.activate
    def test_retry_happens_at_most_once(self, settings, store):
        """A token that is still rejected after refreshing must not loop."""
        stored_tokens(store)
        provider = OAuthAuthProvider(settings, store=store)
        client = ZendeskClient(subdomain=SUBDOMAIN, auth=provider)

        responses.add(responses.GET, TICKETS_URL, json=INVALID_TOKEN_BODY, status=401)
        add_refresh_response()
        responses.add(responses.GET, TICKETS_URL, json=INVALID_TOKEN_BODY, status=401)

        response = client.session.get(TICKETS_URL)

        assert response.status_code == 401
        attempts = [c for c in responses.calls if c.request.url.startswith(TICKETS_URL)]
        assert len(attempts) == 2

    @responses.activate
    def test_api_token_provider_has_no_retry_hook(self):
        client = ZendeskClient(
            subdomain=SUBDOMAIN, email="agent@example.com", token="api-token"
        )

        assert client.session.hooks["response"] == []