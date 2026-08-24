"""
Characterization tests for the authentication behaviour that shipped before
OAuth support was added.

These lock in the exact wire format of API-token (basic) authentication and the
attachment-fetch safety checks, so the refactor to pluggable auth providers can
be shown not to change observable behaviour.
"""
import base64
import json
import urllib.request

import pytest
import responses

from zendesk_mcp_server.zendesk_client import ZendeskClient

SUBDOMAIN = "example"
EMAIL = "agent@example.com"
API_TOKEN = "test-api-token"

PNG_MAGIC = b"\x89PNG\r\n\x1a\n"
ATTACHMENT_URL = "https://example.zendesk.com/attachments/token/abc/?name=x.png"


@pytest.fixture
def client():
    return ZendeskClient(subdomain=SUBDOMAIN, email=EMAIL, token=API_TOKEN)


class FakeUrlopenResponse:
    """Minimal stand-in for the object urllib.request.urlopen returns."""

    def __init__(self, payload: dict):
        self._payload = json.dumps(payload).encode()

    def read(self):
        return self._payload

    def __enter__(self):
        return self

    def __exit__(self, *exc_info):
        return False


def test_api_token_auth_header_is_basic_email_slash_token(client):
    expected_credentials = f"{EMAIL}/token:{API_TOKEN}"
    expected = "Basic " + base64.b64encode(expected_credentials.encode()).decode("ascii")

    assert client.auth_header == expected


def test_get_tickets_sends_the_auth_header(client, monkeypatch):
    captured = {}

    def fake_urlopen(request, *args, **kwargs):
        captured["url"] = request.full_url
        # urllib normalizes header names to title case.
        captured["authorization"] = request.get_header("Authorization")
        return FakeUrlopenResponse({"tickets": [], "next_page": None})

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)

    client.get_tickets(page=2, per_page=10)

    assert captured["authorization"] == client.auth_header
    assert captured["url"].startswith(
        f"https://{SUBDOMAIN}.zendesk.com/api/v2/tickets.json?"
    )


@responses.activate
def test_get_ticket_attachment_returns_base64_for_allowed_image(client):
    body = PNG_MAGIC + b"payload"
    responses.add(
        responses.GET,
        ATTACHMENT_URL,
        body=body,
        content_type="image/png",
        status=200,
    )

    result = client.get_ticket_attachment(ATTACHMENT_URL)

    assert result["content_type"] == "image/png"
    assert base64.b64decode(result["data"]) == body
    assert responses.calls[0].request.headers["Authorization"] == client.auth_header


@responses.activate
def test_get_ticket_attachment_rejects_disallowed_content_type(client):
    responses.add(
        responses.GET,
        ATTACHMENT_URL,
        body=b"<svg/>",
        content_type="image/svg+xml",
        status=200,
    )

    with pytest.raises(ValueError, match="is not allowed"):
        client.get_ticket_attachment(ATTACHMENT_URL)


@responses.activate
def test_get_ticket_attachment_rejects_spoofed_magic_bytes(client):
    responses.add(
        responses.GET,
        ATTACHMENT_URL,
        body=b"definitely-not-a-png",
        content_type="image/png",
        status=200,
    )

    with pytest.raises(ValueError, match="does not match declared content type"):
        client.get_ticket_attachment(ATTACHMENT_URL)
