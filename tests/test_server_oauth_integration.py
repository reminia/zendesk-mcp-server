"""
End-to-end wiring: MCP tools running over OAuth against a mocked Zendesk.

Covers all four ways this server talks to Zendesk, since each authenticates
differently and could regress independently:

* zenpy reads     — GET through the injected session
* zenpy writes    — PUT/POST through the injected session
* direct session  — attachment download
* direct urllib   — get_tickets pagination
"""
import asyncio
import json
import urllib.request
from datetime import datetime, timedelta, timezone

import pytest
import responses

from zendesk_mcp_server.tokens import TokenSet, TokenStore

SUBDOMAIN = "example"
CLIENT_ID = "zendesk-mcp-client"
API = f"https://{SUBDOMAIN}.zendesk.com/api/v2"
ACCESS_TOKEN = "oauth-access-token"
BEARER = f"Bearer {ACCESS_TOKEN}"

PNG_MAGIC = b"\x89PNG\r\n\x1a\n"
ATTACHMENT_URL = f"https://{SUBDOMAIN}.zendesk.com/attachments/token/abc/?name=x.png"

TICKET_JSON = {
    "id": 42,
    "subject": "Printer on fire",
    "description": "It is smoking",
    "status": "open",
    "priority": "urgent",
    "created_at": "2026-08-01T10:00:00Z",
    "updated_at": "2026-08-02T11:00:00Z",
    "requester_id": 7,
    "assignee_id": 9,
    "organization_id": 3,
    "tags": ["hardware"],
    "type": "incident",
}


@pytest.fixture
def oauth_server(monkeypatch, tmp_path):
    """A freshly imported server module configured for OAuth."""
    token_file = tmp_path / "tokens.json"
    TokenStore(token_file).save(
        TokenSet(
            access_token=ACCESS_TOKEN,
            subdomain=SUBDOMAIN,
            client_id=CLIENT_ID,
            refresh_token="oauth-refresh-token",
            expires_at=datetime.now(timezone.utc) + timedelta(minutes=30),
            refresh_token_expires_at=datetime.now(timezone.utc) + timedelta(days=90),
            scope="tickets:read tickets:write ticket_attachments:read users:read hc:read",
        )
    )
    monkeypatch.setenv("ZENDESK_SUBDOMAIN", SUBDOMAIN)
    monkeypatch.setenv("ZENDESK_CLIENT_ID", CLIENT_ID)
    monkeypatch.setenv("ZENDESK_TOKEN_FILE", str(token_file))

    from zendesk_mcp_server import server as server_module

    # Reset the cached client so each test builds one from this environment.
    monkeypatch.setattr(server_module, "_zendesk_client", None)
    server_module.get_cached_kb.cache_clear()
    return server_module


def call_tool(server_module, name, arguments):
    return asyncio.run(server_module.handle_call_tool(name, arguments))


def payload_of(result):
    assert result, "tool returned no content"
    text = result[0].text
    assert not text.startswith("Error:"), text
    return json.loads(text)


def authorization_headers():
    return [
        call.request.headers.get("Authorization")
        for call in responses.calls
        if call.request.url.startswith(f"https://{SUBDOMAIN}.zendesk.com/api")
    ]


@responses.activate
def test_get_ticket_over_oauth(oauth_server):
    responses.add(responses.GET, f"{API}/tickets/42.json", json={"ticket": TICKET_JSON})

    ticket = payload_of(call_tool(oauth_server, "get_ticket", {"ticket_id": 42}))

    assert ticket["id"] == 42
    assert ticket["subject"] == "Printer on fire"
    assert authorization_headers() == [BEARER]


@responses.activate
def test_get_ticket_comments_over_oauth(oauth_server):
    responses.add(
        responses.GET,
        f"{API}/tickets/42/comments.json",
        json={
            "comments": [
                {
                    "id": 1,
                    "author_id": 7,
                    "body": "hello",
                    "html_body": "<p>hello</p>",
                    "public": True,
                    "created_at": "2026-08-01T10:05:00Z",
                    "attachments": [
                        {
                            "id": 5,
                            "file_name": "x.png",
                            "content_url": ATTACHMENT_URL,
                            "content_type": "image/png",
                            "size": 8,
                        }
                    ],
                }
            ]
        },
    )

    comments = payload_of(call_tool(oauth_server, "get_ticket_comments", {"ticket_id": 42}))

    assert comments[0]["body"] == "hello"
    assert comments[0]["attachments"][0]["content_url"] == ATTACHMENT_URL
    assert authorization_headers() == [BEARER]


@responses.activate
def test_create_ticket_comment_over_oauth(oauth_server):
    """A write path: zenpy reads the ticket, then PUTs the new comment."""
    responses.add(responses.GET, f"{API}/tickets/42.json", json={"ticket": TICKET_JSON})
    responses.add(
        responses.PUT,
        f"{API}/tickets/42.json",
        json={"ticket": TICKET_JSON, "audit": {"id": 1, "ticket_id": 42, "events": []}},
    )

    result = call_tool(
        oauth_server,
        "create_ticket_comment",
        {"ticket_id": 42, "comment": "**fixed**", "public": False},
    )

    assert "Comment created successfully" in result[0].text
    put = [c.request for c in responses.calls if c.request.method == "PUT"][0]
    assert put.headers["Authorization"] == BEARER
    # Markdown is rendered to html_body before being sent.
    assert "<strong>fixed</strong>" in json.loads(put.body)["ticket"]["comment"]["html_body"]
    assert set(authorization_headers()) == {BEARER}


@responses.activate
def test_update_ticket_over_oauth(oauth_server):
    responses.add(responses.GET, f"{API}/tickets/42.json", json={"ticket": TICKET_JSON})
    responses.add(
        responses.PUT,
        f"{API}/tickets/42.json",
        json={"ticket": TICKET_JSON, "audit": {"id": 1, "ticket_id": 42, "events": []}},
    )

    body = payload_of(
        call_tool(oauth_server, "update_ticket", {"ticket_id": 42, "status": "solved"})
    )

    assert body["ticket"]["id"] == 42
    assert set(authorization_headers()) == {BEARER}


@responses.activate
def test_create_ticket_over_oauth(oauth_server):
    responses.add(
        responses.POST,
        f"{API}/tickets.json",
        json={"ticket": TICKET_JSON, "audit": {"id": 1, "ticket_id": 42, "events": []}},
        status=201,
    )
    responses.add(responses.GET, f"{API}/tickets/42.json", json={"ticket": TICKET_JSON})

    body = payload_of(
        call_tool(
            oauth_server,
            "create_ticket",
            {"subject": "Printer on fire", "description": "It is smoking"},
        )
    )

    assert body["ticket"]["subject"] == "Printer on fire"
    post = [c.request for c in responses.calls if c.request.method == "POST"][0]
    assert post.headers["Authorization"] == BEARER


@responses.activate
def test_get_ticket_attachment_over_oauth(oauth_server):
    responses.add(
        responses.GET,
        ATTACHMENT_URL,
        body=PNG_MAGIC + b"payload",
        content_type="image/png",
    )

    result = call_tool(oauth_server, "get_ticket_attachment", {"content_url": ATTACHMENT_URL})

    assert result[0].type == "image"
    assert result[0].mimeType == "image/png"
    assert responses.calls[0].request.headers["Authorization"] == BEARER


@responses.activate
def test_knowledge_base_resource_over_oauth(oauth_server):
    responses.add(
        responses.GET,
        "https://example.zendesk.com/api/v2/help_center/sections.json",
        json={"sections": [{"id": 1, "name": "FAQ", "description": "Common questions"}]},
    )
    responses.add(
        responses.GET,
        "https://example.zendesk.com/api/v2/help_center/sections/1/articles.json",
        json={
            "articles": [
                {
                    "id": 11,
                    "title": "How to reset",
                    "body": "<p>Steps</p>",
                    "updated_at": "2026-08-01T10:00:00Z",
                    "html_url": "https://example.zendesk.com/hc/en-us/articles/11",
                }
            ]
        },
    )
    from pydantic import AnyUrl

    raw = asyncio.run(oauth_server.handle_read_resource(AnyUrl("zendesk://knowledge-base")))

    body = json.loads(raw)
    assert body["metadata"]["sections"] == 1
    assert body["knowledge_base"]["FAQ"]["articles"][0]["title"] == "How to reset"
    assert set(authorization_headers()) == {BEARER}


def test_get_tickets_over_oauth(oauth_server, monkeypatch):
    """get_tickets uses urllib directly, so it is checked separately."""
    captured = {}

    class FakeResponse:
        def read(self):
            return json.dumps({"tickets": [TICKET_JSON], "next_page": None}).encode()

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

    def fake_urlopen(request, *args, **kwargs):
        captured["authorization"] = request.get_header("Authorization")
        return FakeResponse()

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)

    body = payload_of(call_tool(oauth_server, "get_tickets", {"per_page": 5}))

    assert body["count"] == 1
    assert body["tickets"][0]["id"] == 42
    assert captured["authorization"] == BEARER


@responses.activate
def test_permission_error_from_zendesk_is_surfaced_not_masked(oauth_server):
    """
    A 403 from Zendesk means the operator lacks permission. It must reach the
    caller intact, since that is the whole point of per-operator OAuth.
    """
    responses.add(
        responses.GET,
        f"{API}/tickets/42.json",
        json={"error": "Forbidden", "description": "You do not have access to this ticket"},
        status=403,
    )

    result = call_tool(oauth_server, "get_ticket", {"ticket_id": 42})

    assert result[0].text.startswith("Error:")
    assert len(responses.calls) == 1, "a permissions failure must not trigger a token refresh"


def test_missing_tokens_tell_the_operator_to_bootstrap(oauth_server, tmp_path, monkeypatch):
    monkeypatch.setenv("ZENDESK_TOKEN_FILE", str(tmp_path / "absent.json"))
    monkeypatch.setattr(oauth_server, "_zendesk_client", None)

    result = call_tool(oauth_server, "get_ticket", {"ticket_id": 42})

    assert "zendesk-auth" in result[0].text
