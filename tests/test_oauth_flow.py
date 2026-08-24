"""
Tests for the PKCE authorization code flow and the zendesk-auth bootstrap.
"""
import base64
import hashlib
import re
from urllib.parse import parse_qs, urlparse

import pytest
import responses

from zendesk_mcp_server import auth_cli
from zendesk_mcp_server.auth_cli import AuthorizationError, _validate_callback
from zendesk_mcp_server.config import OAuthSettings
from zendesk_mcp_server.oauth import (
    ACCESS_TOKEN_TTL_SECONDS,
    REFRESH_TOKEN_TTL_SECONDS,
    OAuthError,
    ReauthorizationRequired,
    build_authorization_url,
    exchange_authorization_code,
    generate_pkce_pair,
    generate_state,
)

SUBDOMAIN = "example"
CLIENT_ID = "zendesk-mcp-client"
TOKEN_URL = "https://example.zendesk.com/oauth/tokens"


@pytest.fixture
def settings(tmp_path):
    return OAuthSettings(
        subdomain=SUBDOMAIN,
        client_id=CLIENT_ID,
        token_file=tmp_path / "tokens.json",
        scopes="tickets:read tickets:write",
        redirect_uri="http://localhost:4567/callback",
    )


def test_pkce_challenge_is_the_s256_digest_of_the_verifier():
    pkce = generate_pkce_pair()

    expected = (
        base64.urlsafe_b64encode(hashlib.sha256(pkce.verifier.encode()).digest())
        .rstrip(b"=")
        .decode()
    )
    assert pkce.challenge == expected
    assert pkce.method == "S256"


def test_pkce_verifier_meets_rfc7636_character_rules():
    pkce = generate_pkce_pair()

    assert 43 <= len(pkce.verifier) <= 128
    assert re.fullmatch(r"[A-Za-z0-9\-._~]+", pkce.verifier)
    assert "=" not in pkce.challenge


def test_pkce_pairs_are_unique_per_invocation():
    assert generate_pkce_pair().verifier != generate_pkce_pair().verifier


def test_pkce_verifier_is_not_exposed_in_repr():
    pkce = generate_pkce_pair()

    assert pkce.verifier not in repr(pkce)


def test_authorization_url_carries_pkce_and_state(settings):
    pkce = generate_pkce_pair()
    state = generate_state()

    url = build_authorization_url(settings, state=state, pkce=pkce)

    parsed = urlparse(url)
    query = {key: values[0] for key, values in parse_qs(parsed.query).items()}
    assert parsed.netloc == "example.zendesk.com"
    assert parsed.path == "/oauth/authorizations/new"
    assert query["response_type"] == "code"
    assert query["client_id"] == CLIENT_ID
    assert query["redirect_uri"] == settings.redirect_uri
    assert query["scope"] == settings.scopes
    assert query["state"] == state
    assert query["code_challenge"] == pkce.challenge
    assert query["code_challenge_method"] == "S256"
    # The verifier must never travel in the authorization request.
    assert pkce.verifier not in url


@responses.activate
def test_code_exchange_sends_the_verifier_and_no_client_secret(settings):
    responses.add(
        responses.POST,
        TOKEN_URL,
        json={
            "access_token": "new-access",
            "refresh_token": "new-refresh",
            "token_type": "bearer",
            "scope": "tickets:read tickets:write",
            "expires_in": 1800,
            "refresh_token_expires_in": 7776000,
        },
        status=200,
    )
    pkce = generate_pkce_pair()

    tokens = exchange_authorization_code(settings, code="auth-code", pkce=pkce)

    sent = parse_qs(responses.calls[0].request.body)
    assert sent["grant_type"] == ["authorization_code"]
    assert sent["code"] == ["auth-code"]
    assert sent["client_id"] == [CLIENT_ID]
    assert sent["code_verifier"] == [pkce.verifier]
    assert sent["redirect_uri"] == [settings.redirect_uri]
    # A public client has no secret to send.
    assert "client_secret" not in sent
    # expires_in must be explicit or legacy clients issue no refresh token.
    assert sent["expires_in"] == [str(ACCESS_TOKEN_TTL_SECONDS)]
    assert sent["refresh_token_expires_in"] == [str(REFRESH_TOKEN_TTL_SECONDS)]

    assert tokens.access_token == "new-access"
    assert tokens.refresh_token == "new-refresh"
    assert tokens.client_id == CLIENT_ID
    assert tokens.subdomain == SUBDOMAIN


@responses.activate
def test_expired_code_asks_the_operator_to_reauthorize(settings):
    responses.add(
        responses.POST,
        TOKEN_URL,
        json={
            "error": "invalid_grant",
            "error_description": "The provided access grant is invalid, expired, or revoked.",
        },
        status=400,
    )

    with pytest.raises(ReauthorizationRequired, match="zendesk-auth"):
        exchange_authorization_code(settings, code="stale", pkce=generate_pkce_pair())


@responses.activate
def test_invalid_scope_explains_the_allowed_scopes_ceiling(settings):
    responses.add(
        responses.POST,
        TOKEN_URL,
        json={"error": "invalid_scope", "error_description": "Requested scope is invalid."},
        status=400,
    )

    with pytest.raises(OAuthError, match="allowed"):
        exchange_authorization_code(settings, code="c", pkce=generate_pkce_pair())


@responses.activate
def test_token_error_does_not_echo_the_code(settings):
    responses.add(responses.POST, TOKEN_URL, json={"error": "invalid_request"}, status=400)

    with pytest.raises(OAuthError) as excinfo:
        exchange_authorization_code(settings, code="super-secret-code", pkce=generate_pkce_pair())

    assert "super-secret-code" not in str(excinfo.value)


def test_callback_with_matching_state_returns_the_code():
    assert _validate_callback({"code": "abc", "state": "s"}, "s") == "abc"


def test_callback_with_mismatched_state_is_rejected():
    with pytest.raises(AuthorizationError, match="state value"):
        _validate_callback({"code": "abc", "state": "forged"}, "expected")


def test_callback_reporting_an_error_is_rejected():
    with pytest.raises(AuthorizationError, match="denied by the user"):
        _validate_callback(
            {"error": "access_denied", "error_description": "denied by the user"}, "s"
        )


def test_callback_without_a_code_is_rejected():
    with pytest.raises(AuthorizationError, match="did not include an authorization code"):
        _validate_callback({"state": "s"}, "s")


def test_bare_code_without_state_is_allowed_with_a_warning(caplog):
    with caplog.at_level("WARNING"):
        assert _validate_callback({"code": "abc"}, "s") == "abc"

    assert "could not be verified" in caplog.text


def test_cli_refuses_to_run_without_oauth_configuration(monkeypatch, capsys, tmp_path):
    # An empty working directory, so no real .env can be picked up.
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("ZENDESK_SUBDOMAIN", SUBDOMAIN)
    monkeypatch.setenv("ZENDESK_EMAIL", "agent@example.com")
    monkeypatch.setenv("ZENDESK_API_KEY", "token")

    exit_code = auth_cli.main([])

    assert exit_code == 2
    assert "ZENDESK_CLIENT_ID" in capsys.readouterr().err


def test_cli_reports_missing_configuration(monkeypatch, capsys, tmp_path):
    monkeypatch.chdir(tmp_path)

    exit_code = auth_cli.main([])

    assert exit_code == 2
    assert "ZENDESK_SUBDOMAIN" in capsys.readouterr().err


@responses.activate
def test_cli_manual_mode_stores_tokens(monkeypatch, capsys, tmp_path):
    """End-to-end bootstrap with the operator pasting the redirect URL."""
    token_file = tmp_path / "tokens.json"
    monkeypatch.setenv("ZENDESK_SUBDOMAIN", SUBDOMAIN)
    monkeypatch.setenv("ZENDESK_CLIENT_ID", CLIENT_ID)
    monkeypatch.setenv("ZENDESK_TOKEN_FILE", str(token_file))
    monkeypatch.setenv("ZENDESK_OAUTH_SCOPES", "tickets:read")
    responses.add(
        responses.POST,
        TOKEN_URL,
        json={
            "access_token": "cli-access",
            "refresh_token": "cli-refresh",
            "scope": "tickets:read",
            "expires_in": 1800,
            "refresh_token_expires_in": 7776000,
        },
        status=200,
    )

    captured_state = {}

    def fake_input(_prompt):
        # Echo back the state from the authorization URL the CLI just built.
        return f"http://localhost:4567/callback?code=pasted-code&state={captured_state['state']}"

    real_build = auth_cli.build_authorization_url

    def spy_build(settings, *, state, pkce):
        captured_state["state"] = state
        return real_build(settings, state=state, pkce=pkce)

    monkeypatch.setattr(auth_cli, "build_authorization_url", spy_build)
    monkeypatch.setattr("builtins.input", fake_input)

    exit_code = auth_cli.main(["--manual"])

    assert exit_code == 0
    from zendesk_mcp_server.tokens import TokenStore

    stored = TokenStore(token_file).load()
    assert stored.access_token == "cli-access"
    assert stored.refresh_token == "cli-refresh"

    output = capsys.readouterr().out
    assert "Authorization complete" in output
    # Secrets must never be printed.
    assert "cli-access" not in output
    assert "cli-refresh" not in output


def test_cli_reads_configuration_from_a_dotenv_file(monkeypatch, tmp_path, capsys):
    """
    The README tells operators to put configuration in .env and then run
    zendesk-auth, so the command has to load it itself.
    """
    (tmp_path / ".env").write_text(
        f"ZENDESK_SUBDOMAIN={SUBDOMAIN}\nZENDESK_CLIENT_ID={CLIENT_ID}\n", encoding="utf-8"
    )
    monkeypatch.chdir(tmp_path)
    # Fail before the browser step, but only after configuration has been read.
    monkeypatch.setattr(
        auth_cli,
        "_receive_code_via_loopback",
        lambda settings, url: (_ for _ in ()).throw(
            AuthorizationError(f"stopped after loading config for {settings.client_id}")
        ),
    )

    exit_code = auth_cli.main([])

    assert exit_code == 1, "should reach the browser step, not fail on configuration"
    assert f"stopped after loading config for {CLIENT_ID}" in capsys.readouterr().err


def test_loopback_rejects_a_redirect_uri_it_cannot_serve(settings):
    https_settings = OAuthSettings(
        subdomain=SUBDOMAIN,
        client_id=CLIENT_ID,
        token_file=settings.token_file,
        redirect_uri="https://localhost",
    )

    with pytest.raises(AuthorizationError, match="--manual"):
        auth_cli._receive_code_via_loopback(https_settings, "https://example.zendesk.com/auth")


def _free_port() -> int:
    import socket

    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return probe.getsockname()[1]


def test_loopback_listener_captures_the_redirect(monkeypatch, tmp_path, capsys):
    """
    The default path: Zendesk redirects the browser to a local port and the CLI
    reads the code out of that request.
    """
    import threading
    import urllib.request

    port = _free_port()
    settings = OAuthSettings(
        subdomain=SUBDOMAIN,
        client_id=CLIENT_ID,
        token_file=tmp_path / "tokens.json",
        redirect_uri=f"http://localhost:{port}/callback",
    )
    fetched = {}

    def fake_browser_open(url):
        """Stands in for the operator's browser following Zendesk's redirect."""

        def visit():
            redirect = f"http://localhost:{port}/callback?code=loopback-code&state=xyz"
            with urllib.request.urlopen(redirect, timeout=5) as response:
                fetched["status"] = response.status
                fetched["body"] = response.read()

        threading.Timer(0.05, visit).start()
        return True

    monkeypatch.setattr(auth_cli.webbrowser, "open", fake_browser_open)

    captured = auth_cli._receive_code_via_loopback(settings, "https://example.zendesk.com/auth")

    assert captured == {"code": "loopback-code", "state": "xyz"}
    assert fetched["status"] == 200
    assert b"Authorization complete" in fetched["body"]
    # The code must not be echoed to the terminal.
    assert "loopback-code" not in capsys.readouterr().out


def test_loopback_listener_captures_a_denial(monkeypatch, tmp_path):
    import threading
    import urllib.error
    import urllib.request

    port = _free_port()
    settings = OAuthSettings(
        subdomain=SUBDOMAIN,
        client_id=CLIENT_ID,
        token_file=tmp_path / "tokens.json",
        redirect_uri=f"http://127.0.0.1:{port}/callback",
    )

    def fake_browser_open(url):
        def visit():
            try:
                urllib.request.urlopen(
                    f"http://127.0.0.1:{port}/callback?error=access_denied", timeout=5
                )
            except urllib.error.HTTPError:
                pass  # The handler answers 400 for a denial.

        threading.Timer(0.05, visit).start()
        return True

    monkeypatch.setattr(auth_cli.webbrowser, "open", fake_browser_open)

    captured = auth_cli._receive_code_via_loopback(settings, "https://example.zendesk.com/auth")

    assert captured["error"] == "access_denied"
    with pytest.raises(AuthorizationError, match="declined"):
        _validate_callback(captured, "xyz")


def test_loopback_times_out_without_a_redirect(monkeypatch, tmp_path):
    port = _free_port()
    settings = OAuthSettings(
        subdomain=SUBDOMAIN,
        client_id=CLIENT_ID,
        token_file=tmp_path / "tokens.json",
        redirect_uri=f"http://localhost:{port}/callback",
    )
    monkeypatch.setattr(auth_cli.webbrowser, "open", lambda url: True)
    monkeypatch.setattr(auth_cli, "CALLBACK_TIMEOUT_SECONDS", 0.5)

    with pytest.raises(AuthorizationError, match="No redirect received"):
        auth_cli._receive_code_via_loopback(settings, "https://example.zendesk.com/auth")
