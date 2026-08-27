"""
Configuration for the Zendesk MCP server.

Two authentication modes are supported and selected from the environment:

* OAuth (preferred) — set ``ZENDESK_CLIENT_ID``. Each operator authorizes with
  their own Zendesk login via ``zendesk-auth``, so API calls carry their
  identity and are subject to the same permission checks as the Zendesk UI.
* API token (deprecated) — set ``ZENDESK_EMAIL`` and ``ZENDESK_API_KEY``.
  Zendesk deactivates all API tokens on 2027-04-30.

Credentials are validated here, before any client is constructed, so a
misconfiguration produces a readable message instead of a library traceback.
"""
from __future__ import annotations

import logging
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Mapping

logger = logging.getLogger(__name__)

# Scope identifiers required by the tools this server exposes:
#   tickets:read              tickets, comments, tags, fields, audits, metrics
#   tickets:write             create/update tickets, add comments
#   ticket_attachments:read   fetch files attached to tickets
#   users:read                requester and assignee details
#   hc:read                   Help Center articles for the knowledge base
#
# Zendesk accepts unrecognized scope strings when issuing a token but then
# rejects every request made with it as 403, so keep these exact.
DEFAULT_OAUTH_SCOPES = (
    "tickets:read tickets:write ticket_attachments:read users:read hc:read"
)

# Must match a redirect URL registered on the OAuth client in Admin Center.
DEFAULT_REDIRECT_URI = "http://localhost:4567/callback"

API_TOKEN_DEPRECATION_MESSAGE = (
    "Zendesk API token authentication is deprecated. Zendesk deactivates unused "
    "API tokens from 2026-07-28, blocks creation of new ones from 2026-10-27, and "
    "stops accepting all API tokens on 2027-04-30. It also grants this server the "
    "full access of the token's user rather than the permissions of the operator "
    "using it. Migrate to OAuth by setting ZENDESK_CLIENT_ID and running "
    "zendesk-auth."
)

_MISSING_CREDENTIALS_MESSAGE = (
    "No Zendesk credentials configured. Set ZENDESK_SUBDOMAIN plus either:\n"
    "  - ZENDESK_CLIENT_ID for OAuth (recommended), then run zendesk-auth, or\n"
    "  - ZENDESK_EMAIL and ZENDESK_API_KEY for deprecated API token auth.\n"
    "See .env.example."
)


class ConfigurationError(RuntimeError):
    """Raised when the environment does not describe a usable configuration."""


@dataclass(frozen=True)
class OAuthSettings:
    """OAuth authorization-code-with-PKCE configuration."""

    subdomain: str
    client_id: str
    token_file: Path
    scopes: str = DEFAULT_OAUTH_SCOPES
    redirect_uri: str = DEFAULT_REDIRECT_URI

    @property
    def token_endpoint(self) -> str:
        return f"https://{self.subdomain}.zendesk.com/oauth/tokens"

    @property
    def authorize_endpoint(self) -> str:
        return f"https://{self.subdomain}.zendesk.com/oauth/authorizations/new"


@dataclass(frozen=True)
class ApiTokenSettings:
    """Deprecated email + API token configuration."""

    subdomain: str
    email: str
    # repr=False so the token cannot leak into logs or tracebacks.
    token: str = field(repr=False)


Settings = OAuthSettings | ApiTokenSettings


def default_token_file() -> Path:
    """
    Location of the OAuth token store.

    Deliberately outside the project directory so tokens are never picked up by
    version control or a Docker build context.
    """
    config_home = os.environ.get("XDG_CONFIG_HOME")
    base = Path(config_home) if config_home else Path.home() / ".config"
    return base / "zendesk-mcp" / "tokens.json"


def _clean(value: str | None) -> str | None:
    return value.strip() if value and value.strip() else None


def load_settings(env: Mapping[str, str] | None = None) -> Settings:
    """
    Build settings from the environment.

    OAuth wins when ``ZENDESK_CLIENT_ID`` is present, so an operator migrating
    from API tokens can leave the old variables in place.
    """
    env = os.environ if env is None else env

    subdomain = _clean(env.get("ZENDESK_SUBDOMAIN"))
    if not subdomain:
        raise ConfigurationError(
            "ZENDESK_SUBDOMAIN is not set. For https://acme.zendesk.com the "
            "subdomain is 'acme'."
        )

    client_id = _clean(env.get("ZENDESK_CLIENT_ID"))
    if client_id:
        token_file = _clean(env.get("ZENDESK_TOKEN_FILE"))
        settings = OAuthSettings(
            subdomain=subdomain,
            client_id=client_id,
            token_file=Path(token_file).expanduser() if token_file else default_token_file(),
            scopes=_clean(env.get("ZENDESK_OAUTH_SCOPES")) or DEFAULT_OAUTH_SCOPES,
            redirect_uri=_clean(env.get("ZENDESK_OAUTH_REDIRECT_URI")) or DEFAULT_REDIRECT_URI,
        )
        logger.info(
            "Using Zendesk OAuth authentication (client_id=%s, token file=%s).",
            settings.client_id,
            settings.token_file,
        )
        return settings

    email = _clean(env.get("ZENDESK_EMAIL"))
    token = _clean(env.get("ZENDESK_API_KEY"))
    if email and token:
        logger.warning(API_TOKEN_DEPRECATION_MESSAGE)
        return ApiTokenSettings(subdomain=subdomain, email=email, token=token)

    if email or token:
        missing = "ZENDESK_API_KEY" if email else "ZENDESK_EMAIL"
        raise ConfigurationError(
            f"Incomplete API token configuration: {missing} is not set. "
            "Set both, or switch to OAuth with ZENDESK_CLIENT_ID."
        )

    raise ConfigurationError(_MISSING_CREDENTIALS_MESSAGE)
