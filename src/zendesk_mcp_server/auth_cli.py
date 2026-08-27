"""
``zendesk-auth`` — one-time OAuth authorization for this machine.

Run once per operator. It sends the operator to Zendesk in a browser, captures
the authorization code, exchanges it for tokens using PKCE, and stores the result
locally. From then on the MCP server renews the access token on its own.

Two ways to receive the code:

* loopback (default) — a local HTTP server catches Zendesk's redirect.
* ``--manual`` — the operator pastes the redirect URL. Needed when the OAuth
  client is registered with a redirect URL that this machine cannot serve, such
  as ``https://localhost``.
"""
from __future__ import annotations

import argparse
import logging
import sys
import threading
import webbrowser
from http.server import BaseHTTPRequestHandler, HTTPServer
from urllib.parse import parse_qs, urlparse

from dotenv import find_dotenv, load_dotenv

from zendesk_mcp_server.config import (
    ConfigurationError,
    OAuthSettings,
    load_settings,
)
from zendesk_mcp_server.oauth import (
    OAuthError,
    PkcePair,
    build_authorization_url,
    exchange_authorization_code,
    generate_pkce_pair,
    generate_state,
)
from zendesk_mcp_server.tokens import TokenSet, TokenStore

logger = logging.getLogger("zendesk-auth")

# Zendesk authorization codes expire 120 seconds after being issued, so there is
# no point waiting much longer than that for the redirect.
CALLBACK_TIMEOUT_SECONDS = 180

_SUCCESS_PAGE = b"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><title>Zendesk authorization complete</title></head>
<body><h1>Authorization complete</h1>
<p>You can close this tab and return to the terminal.</p></body></html>
"""

_FAILURE_PAGE = b"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><title>Zendesk authorization failed</title></head>
<body><h1>Authorization failed</h1>
<p>Return to the terminal for details.</p></body></html>
"""


class AuthorizationError(RuntimeError):
    """The operator did not complete authorization successfully."""


class _CallbackHandler(BaseHTTPRequestHandler):
    """Captures the single redirect Zendesk makes back to this machine."""

    # Set by the server instance.
    result: dict[str, str]

    def do_GET(self):  # noqa: N802 - name mandated by BaseHTTPRequestHandler
        query = parse_qs(urlparse(self.path).query)
        captured = {key: values[0] for key, values in query.items() if values}

        if "code" in captured or "error" in captured:
            self.server.result.update(captured)  # type: ignore[attr-defined]
            body = _SUCCESS_PAGE if "code" in captured else _FAILURE_PAGE
            status = 200 if "code" in captured else 400
        else:
            body, status = _FAILURE_PAGE, 404

        self.send_response(status)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args):
        """Silence the default stderr access log; it would echo the code."""


class _CallbackServer(HTTPServer):
    def __init__(self, address):
        super().__init__(address, _CallbackHandler)
        self.result: dict[str, str] = {}


def _receive_code_via_loopback(settings: OAuthSettings, authorization_url: str) -> dict[str, str]:
    redirect = urlparse(settings.redirect_uri)
    if redirect.scheme != "http" or redirect.hostname not in ("localhost", "127.0.0.1"):
        raise AuthorizationError(
            f"ZENDESK_OAUTH_REDIRECT_URI is {settings.redirect_uri!r}, which this "
            "machine cannot listen on. Register an http://localhost:PORT/... "
            "redirect URL on the OAuth client, or re-run with --manual."
        )

    port = redirect.port or 80
    try:
        server = _CallbackServer((redirect.hostname, port))
    except OSError as exc:
        raise AuthorizationError(
            f"Could not listen on {redirect.hostname}:{port} ({exc}). "
            "Free the port, point ZENDESK_OAUTH_REDIRECT_URI elsewhere, or use --manual."
        ) from exc

    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        print(f"Opening your browser to authorize with Zendesk:\n  {authorization_url}\n")
        if not webbrowser.open(authorization_url):
            print("Could not open a browser automatically. Open the URL above manually.\n")
        print(f"Waiting up to {CALLBACK_TIMEOUT_SECONDS}s for the redirect...")

        deadline = threading.Event()
        waited = 0.0
        while not server.result and waited < CALLBACK_TIMEOUT_SECONDS:
            deadline.wait(0.25)
            waited += 0.25
    finally:
        server.shutdown()
        server.server_close()

    if not server.result:
        raise AuthorizationError(
            f"No redirect received within {CALLBACK_TIMEOUT_SECONDS}s. "
            "Re-run, or use --manual if the browser cannot reach this machine."
        )
    return server.result


def _receive_code_manually(authorization_url: str) -> dict[str, str]:
    print("Open this URL in a browser and approve access:\n")
    print(f"  {authorization_url}\n")
    print(
        "Zendesk then redirects to your OAuth client's redirect URL. The page may "
        "fail to load; that is expected.\nCopy the full URL from the address bar "
        "and paste it here."
    )
    pasted = input("\nRedirect URL (or just the code): ").strip()
    if not pasted:
        raise AuthorizationError("Nothing was pasted, so authorization cannot continue.")

    parsed = urlparse(pasted)
    if parsed.query:
        return {key: values[0] for key, values in parse_qs(parsed.query).items() if values}
    # Bare code pasted: there is no state to compare against.
    return {"code": pasted}


def _validate_callback(captured: dict[str, str], expected_state: str) -> str:
    if "error" in captured:
        detail = captured.get("error_description") or captured["error"]
        raise AuthorizationError(f"Zendesk declined the authorization request: {detail}")

    code = captured.get("code")
    if not code:
        raise AuthorizationError("The redirect did not include an authorization code.")

    returned_state = captured.get("state")
    if returned_state is None:
        logger.warning(
            "No state value was returned, so the callback could not be verified. "
            "This is expected only when pasting a bare code."
        )
    elif returned_state != expected_state:
        raise AuthorizationError(
            "The state value returned by Zendesk does not match the one sent. "
            "Discarding this response and not exchanging the code."
        )
    return code


def _report(tokens: TokenSet, store: TokenStore, settings: OAuthSettings) -> None:
    print("\nAuthorization complete.")
    print(f"  Tokens stored at:   {store.path}")
    print(f"  Granted scope:      {tokens.scope or '(not reported by Zendesk)'}")
    print(f"  Access token until: {tokens.expires_at or 'no expiry'}")
    print(f"  Refresh until:      {tokens.refresh_token_expires_at or 'no expiry'}")

    granted = set((tokens.scope or "").split())
    requested = set(settings.scopes.split())
    if granted and granted != requested:
        print(
            f"\nNote: the granted scope differs from the requested "
            f"{sorted(requested)}. Zendesk accepts unknown scope names but then "
            "rejects requests with 403, so check for typos in ZENDESK_OAUTH_SCOPES."
        )
    if not tokens.refresh_token:
        print(
            "\nWarning: Zendesk issued no refresh token, so the server cannot renew "
            "access automatically and you will have to re-run this command. This "
            "happens with OAuth clients created before 2026-04-30 in some "
            "configurations."
        )


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    parser = argparse.ArgumentParser(
        prog="zendesk-auth",
        description="Authorize this machine to use the Zendesk API via OAuth.",
    )
    parser.add_argument(
        "--manual",
        action="store_true",
        help="Paste the redirect URL instead of running a local callback server.",
    )
    args = parser.parse_args(argv)

    # The server reads .env on import; this command has to do it for itself.
    # usecwd=True searches from the working directory rather than from this
    # file's location, so `uv run --directory X zendesk-auth` picks up X/.env.
    load_dotenv(find_dotenv(usecwd=True))

    try:
        settings = load_settings()
    except ConfigurationError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    if not isinstance(settings, OAuthSettings):
        print(
            "error: OAuth is not configured. Set ZENDESK_CLIENT_ID to the identifier "
            "of a public OAuth client from Admin Center (Apps and integrations > "
            "APIs > OAuth clients).",
            file=sys.stderr,
        )
        return 2

    pkce = generate_pkce_pair()
    state = generate_state()
    authorization_url = build_authorization_url(settings, state=state, pkce=pkce)

    store = TokenStore(settings.token_file)
    try:
        captured = (
            _receive_code_manually(authorization_url)
            if args.manual
            else _receive_code_via_loopback(settings, authorization_url)
        )
        code = _validate_callback(captured, state)
        tokens = exchange_authorization_code(settings, code=code, pkce=pkce)
        with store.locked():
            store.save(tokens)
    except (AuthorizationError, OAuthError) as exc:
        print(f"\nerror: {exc}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print("\nCancelled.", file=sys.stderr)
        return 130

    _report(tokens, store, settings)
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
