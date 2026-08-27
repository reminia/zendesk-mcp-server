"""
Tests for the pluggable auth provider layer.
"""
import base64

import pytest
import responses

from zendesk_mcp_server.auth import ApiTokenAuthProvider
from zendesk_mcp_server.zendesk_client import ZendeskClient

SUBDOMAIN = "example"
EMAIL = "agent@example.com"
API_TOKEN = "test-api-token"

PNG_MAGIC = b"\x89PNG\r\n\x1a\n"
ATTACHMENT_URL = "https://example.zendesk.com/attachments/token/abc/?name=x.png"
CDN_URL = "https://example.zdusercontent.com/attachment/abc/x.png"


class RotatingAuthProvider(ApiTokenAuthProvider):
    """Stands in for an OAuth provider whose token changes between requests."""

    def __init__(self):
        self.calls = 0

    def auth_header(self) -> str:
        self.calls += 1
        return f"Bearer token-{self.calls}"


def test_api_token_provider_builds_basic_header():
    provider = ApiTokenAuthProvider(email=EMAIL, token=API_TOKEN)
    expected = "Basic " + base64.b64encode(
        f"{EMAIL}/token:{API_TOKEN}".encode()
    ).decode("ascii")

    assert provider.auth_header() == expected


@pytest.mark.parametrize(
    "email,token",
    [(None, API_TOKEN), (EMAIL, None), ("", ""), (None, None)],
)
def test_api_token_provider_rejects_incomplete_credentials(email, token):
    with pytest.raises(ValueError, match="requires both an email address"):
        ApiTokenAuthProvider(email=email, token=token)


def test_client_hands_zenpy_a_session_it_will_not_reauthenticate():
    client = ZendeskClient(subdomain=SUBDOMAIN, email=EMAIL, token=API_TOKEN)

    assert client.session.authorized is True
    assert client.session.auth is client.auth
    # zenpy stores the session it was given on each sub-API's config.
    assert client.client.tickets.session is client.session


def test_auth_header_is_read_through_the_provider_each_time():
    provider = RotatingAuthProvider()
    client = ZendeskClient(subdomain=SUBDOMAIN, auth=provider)

    assert client.auth_header == "Bearer token-1"
    assert client.auth_header == "Bearer token-2"


@responses.activate
def test_rotating_credentials_are_applied_per_request():
    provider = RotatingAuthProvider()
    client = ZendeskClient(subdomain=SUBDOMAIN, auth=provider)
    for _ in range(2):
        responses.add(
            responses.GET,
            ATTACHMENT_URL,
            body=PNG_MAGIC,
            content_type="image/png",
            status=200,
        )

    client.get_ticket_attachment(ATTACHMENT_URL)
    client.get_ticket_attachment(ATTACHMENT_URL)

    sent = [call.request.headers["Authorization"] for call in responses.calls]
    assert sent == ["Bearer token-1", "Bearer token-2"]


@responses.activate
def test_attachment_redirect_to_cdn_drops_the_authorization_header():
    """
    Zendesk's CDN returns 403 when it receives an Authorization header, so the
    header must not survive the cross-origin redirect.
    """
    client = ZendeskClient(subdomain=SUBDOMAIN, email=EMAIL, token=API_TOKEN)
    responses.add(
        responses.GET,
        ATTACHMENT_URL,
        status=302,
        headers={"Location": CDN_URL},
    )
    responses.add(
        responses.GET,
        CDN_URL,
        body=PNG_MAGIC + b"payload",
        content_type="image/png",
        status=200,
    )

    result = client.get_ticket_attachment(ATTACHMENT_URL)

    assert result["content_type"] == "image/png"
    first, second = responses.calls
    assert first.request.headers["Authorization"] == client.auth_header
    assert "Authorization" not in second.request.headers
