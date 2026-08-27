"""
OAuth 2.0 authorization code flow with PKCE against Zendesk.

The MCP server runs on each operator's own machine, so it is registered as a
*public* client: there is no client secret to distribute, and PKCE is what proves
the party redeeming the authorization code is the one that requested it.

Every operator authorizes with their own Zendesk login, so the resulting token
carries their identity and Zendesk applies the same role, group and ticket
permissions it applies in the UI.
"""
from __future__ import annotations

import base64
import hashlib
import logging
import secrets
import threading
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Mapping
from urllib.parse import urlencode

import requests

from zendesk_mcp_server.auth import _HeaderAuthProvider
from zendesk_mcp_server.config import OAuthSettings
from zendesk_mcp_server.tokens import TokenSet, TokenStore

logger = logging.getLogger(__name__)

# Zendesk allows 5 minutes to 48 hours for access tokens and 7 to 90 days for
# refresh tokens. Ask for the 90 day maximum on the refresh token: it is the
# credential that has to survive periods where nobody uses the MCP server.
#
# expires_in is always sent explicitly because OAuth clients created before
# 2026-04-30 only issue a refresh token when it is present; omitting it yields a
# non-expiring access token and no way to renew.
ACCESS_TOKEN_TTL_SECONDS = 1800
REFRESH_TOKEN_TTL_SECONDS = 7776000

_TOKEN_REQUEST_TIMEOUT = 30


class OAuthError(RuntimeError):
    """A Zendesk OAuth request failed."""


class ReauthorizationRequired(OAuthError):
    """
    The refresh token can no longer be used.

    Recovering needs a human at a browser, so this is raised separately from
    transient failures.
    """


@dataclass(frozen=True)
class PkcePair:
    """A PKCE ``code_verifier`` and the ``code_challenge`` derived from it."""

    verifier: str = field(repr=False)
    challenge: str
    method: str = "S256"


def generate_pkce_pair() -> PkcePair:
    """
    Create a PKCE verifier/challenge pair.

    32 random bytes base64url-encoded gives a 43 character verifier, the minimum
    RFC 7636 permits and what Zendesk's own examples use.
    """
    verifier = base64.urlsafe_b64encode(secrets.token_bytes(32)).rstrip(b"=").decode("ascii")
    challenge = (
        base64.urlsafe_b64encode(hashlib.sha256(verifier.encode("ascii")).digest())
        .rstrip(b"=")
        .decode("ascii")
    )
    return PkcePair(verifier=verifier, challenge=challenge)


def generate_state() -> str:
    """Opaque value echoed back by Zendesk, used to detect a forged callback."""
    return secrets.token_urlsafe(32)


def build_authorization_url(settings: OAuthSettings, *, state: str, pkce: PkcePair) -> str:
    query = urlencode(
        {
            "response_type": "code",
            "client_id": settings.client_id,
            "redirect_uri": settings.redirect_uri,
            "scope": settings.scopes,
            "state": state,
            "code_challenge": pkce.challenge,
            "code_challenge_method": pkce.method,
        }
    )
    return f"{settings.authorize_endpoint}?{query}"


def _post_token_request(
    settings: OAuthSettings,
    payload: Mapping[str, Any],
    *,
    session: requests.Session | None = None,
) -> dict[str, Any]:
    """
    Call the token endpoint.

    Deliberately not the client's authenticated session: token requests carry no
    Authorization header, and a public client sends no client_secret.
    """
    http = session or requests
    try:
        response = http.post(
            settings.token_endpoint, data=dict(payload), timeout=_TOKEN_REQUEST_TIMEOUT
        )
    except requests.RequestException as exc:
        raise OAuthError(f"Could not reach {settings.token_endpoint}: {exc}") from exc

    if response.status_code >= 400:
        raise _token_error(response)

    try:
        return response.json()
    except ValueError as exc:
        raise OAuthError(
            f"{settings.token_endpoint} returned a non-JSON response "
            f"(HTTP {response.status_code})."
        ) from exc


def _token_error(response: requests.Response) -> OAuthError:
    """Translate an error body into an exception, without echoing credentials."""
    error = description = None
    try:
        body = response.json()
        if isinstance(body, dict):
            error = body.get("error")
            description = body.get("error_description")
    except ValueError:
        pass

    detail = f"{error}: {description}" if description else (error or "no error detail")
    message = f"Zendesk rejected the token request (HTTP {response.status_code}). {detail}"

    if error == "invalid_grant":
        return ReauthorizationRequired(
            f"{message}\nThe authorization code or refresh token is expired, revoked "
            "or already used. Run zendesk-auth to authorize again."
        )
    if error == "invalid_scope":
        return OAuthError(
            f"{message}\nThe requested scopes exceed the OAuth client's allowed "
            "scopes. Widen them in Admin Center or narrow ZENDESK_OAUTH_SCOPES."
        )
    return OAuthError(message)


def exchange_authorization_code(
    settings: OAuthSettings,
    *,
    code: str,
    pkce: PkcePair,
    session: requests.Session | None = None,
) -> TokenSet:
    """Redeem an authorization code for an access and refresh token."""
    issued_at = datetime.now(timezone.utc)
    payload = _post_token_request(
        settings,
        {
            "grant_type": "authorization_code",
            "code": code,
            "client_id": settings.client_id,
            "code_verifier": pkce.verifier,
            "redirect_uri": settings.redirect_uri,
            "scope": settings.scopes,
            "expires_in": ACCESS_TOKEN_TTL_SECONDS,
            "refresh_token_expires_in": REFRESH_TOKEN_TTL_SECONDS,
        },
        session=session,
    )
    return TokenSet.from_token_response(
        payload,
        subdomain=settings.subdomain,
        client_id=settings.client_id,
        issued_at=issued_at,
    )


def refresh_access_token(
    settings: OAuthSettings,
    tokens: TokenSet,
    *,
    session: requests.Session | None = None,
) -> TokenSet:
    """
    Exchange a refresh token for a new token pair.

    Zendesk rotates both tokens and invalidates the old pair immediately, so the
    result must be persisted before any further request is made.
    """
    if not tokens.refresh_token:
        raise ReauthorizationRequired(
            "No refresh token is stored, so the access token cannot be renewed. "
            "Run zendesk-auth to authorize again."
        )

    issued_at = datetime.now(timezone.utc)
    payload = _post_token_request(
        settings,
        {
            "grant_type": "refresh_token",
            "refresh_token": tokens.refresh_token,
            "client_id": settings.client_id,
            "expires_in": ACCESS_TOKEN_TTL_SECONDS,
            "refresh_token_expires_in": REFRESH_TOKEN_TTL_SECONDS,
        },
        session=session,
    )

    refreshed = TokenSet.from_token_response(
        payload,
        subdomain=settings.subdomain,
        client_id=settings.client_id,
        issued_at=issued_at,
    )
    # A response without a new refresh token means the old one stays valid.
    if not refreshed.refresh_token:
        refreshed = refreshed.replace(
            refresh_token=tokens.refresh_token,
            refresh_token_expires_at=tokens.refresh_token_expires_at,
        )
    logger.info("Renewed the Zendesk OAuth access token.")
    return refreshed


class OAuthAuthProvider(_HeaderAuthProvider):
    """
    Authenticates requests with a stored OAuth access token.

    Renewal happens two ways:

    * proactively, whenever the stored token is inside its expiry skew window, and
    * reactively, when Zendesk answers ``401`` with ``{"error": "invalid_token"}``,
      in which case the request is retried once with a fresh token.

    Only ``invalid_token`` triggers a retry. A ``401`` or ``403`` caused by
    insufficient scope or by the operator's Zendesk permissions is passed through
    untouched, because refreshing would not fix it and hiding it would make the
    permission model impossible to debug.
    """

    def __init__(
        self,
        settings: OAuthSettings,
        store: TokenStore | None = None,
        session: requests.Session | None = None,
    ):
        self._settings = settings
        self._store = store if store is not None else TokenStore(settings.token_file)
        # Separate from the client's session: token requests must not carry an
        # Authorization header.
        self._token_session = session
        self._tokens: TokenSet | None = None
        # Reentrant because _access_token holds it while calling _renew.
        self._lock = threading.RLock()

    def auth_header(self) -> str:
        return f"Bearer {self._access_token()}"

    def _access_token(self) -> str:
        with self._lock:
            tokens = self._tokens
            if tokens is None:
                tokens = self._read_store()
            if tokens.access_token_expired():
                tokens = self._renew(reason="the stored access token has expired")
            return tokens.access_token

    def _read_store(self) -> TokenSet:
        tokens = self._store.load()
        self._warn_on_mismatch(tokens)
        self._tokens = tokens
        return tokens

    def _warn_on_mismatch(self, tokens: TokenSet) -> None:
        if tokens.subdomain != self._settings.subdomain:
            logger.warning(
                "Stored tokens belong to subdomain %r but ZENDESK_SUBDOMAIN is %r. "
                "Re-run zendesk-auth if requests fail.",
                tokens.subdomain,
                self._settings.subdomain,
            )
        if tokens.client_id != self._settings.client_id:
            logger.warning(
                "Stored tokens were issued to a different OAuth client than "
                "ZENDESK_CLIENT_ID. Re-run zendesk-auth if requests fail."
            )

    def _renew(self, *, reason: str, rejected_token: str | None = None) -> TokenSet:
        """
        Refresh the access token, persisting the rotated pair before returning.

        The store lock is held across read-refresh-write so a second MCP process
        cannot refresh at the same time; whichever process gets the lock second
        finds the token already renewed and reuses it instead of spending the
        refresh token twice.

        ``rejected_token`` is the access token Zendesk just refused. It is what
        distinguishes "somebody else already rotated this" from "this really does
        need refreshing", since a rejected token can still look unexpired.
        """
        with self._lock, self._store.locked():
            tokens = self._store.load()

            if self._already_renewed(tokens, rejected_token):
                logger.debug("Another process already renewed the access token.")
                self._tokens = tokens
                return tokens

            if not tokens.can_refresh():
                raise ReauthorizationRequired(
                    "The Zendesk refresh token is missing or expired, so access "
                    f"cannot be renewed ({reason}). Run zendesk-auth to authorize "
                    "this machine again."
                )

            logger.info("Renewing the Zendesk access token because %s.", reason)
            refreshed = refresh_access_token(
                self._settings, tokens, session=self._token_session
            )
            # Persist before use: Zendesk invalidates the old refresh token the
            # moment this call succeeds, so losing this write costs a re-auth.
            self._store.save(refreshed)
            self._tokens = refreshed
            return refreshed

    @staticmethod
    def _already_renewed(tokens: TokenSet, rejected_token: str | None) -> bool:
        if rejected_token is not None:
            # Only another process storing a different token counts as renewed.
            return tokens.access_token != rejected_token
        return not tokens.access_token_expired()

    def response_hook(self, response: requests.Response, **kwargs):
        """
        requests response hook that retries once after an expired-token 401.

        Follows the same resend pattern as requests' own auth handlers.
        """
        if response.status_code != 401 or not _is_invalid_token(response):
            return response

        request = response.request
        if getattr(request, "_zendesk_oauth_retried", False):
            logger.warning(
                "Zendesk still reports an invalid token after refreshing; not retrying again."
            )
            return response

        # Drain and release the connection before reusing it.
        response.content
        response.close()

        refreshed_request = request.copy()
        refreshed_request._zendesk_oauth_retried = True
        self._renew(
            reason="Zendesk reported the access token as invalid",
            rejected_token=_bearer_token(request.headers.get("Authorization")),
        )
        refreshed_request.headers["Authorization"] = self.auth_header()

        retried = response.connection.send(refreshed_request, **kwargs)
        retried.history.append(response)
        retried.request = refreshed_request
        return retried


def _bearer_token(header: str | None) -> str | None:
    """Extract the token from an ``Authorization: Bearer <token>`` header."""
    if not header:
        return None
    scheme, _, value = header.partition(" ")
    if scheme.lower() != "bearer":
        return None
    return value or None


def _is_invalid_token(response: requests.Response) -> bool:
    """
    True only when Zendesk says the token itself is bad.

    Zendesk documents the body as
    ``{"error": "invalid_token", "error_description": "..."}``.
    """
    try:
        body = response.json()
    except ValueError:
        return False
    if not isinstance(body, dict):
        return False
    error = body.get("error")
    if isinstance(error, str):
        return error.lower() == "invalid_token"
    return False
