"""
Builds an authenticated ZendeskClient from configuration.

Kept separate from both config and client modules so the wiring between them is
in one obvious place.
"""
from __future__ import annotations

from zendesk_mcp_server.auth import ApiTokenAuthProvider, AuthProvider
from zendesk_mcp_server.config import (
    ApiTokenSettings,
    OAuthSettings,
    Settings,
    load_settings,
)
from zendesk_mcp_server.zendesk_client import ZendeskClient


def build_auth_provider(settings: Settings) -> AuthProvider:
    if isinstance(settings, ApiTokenSettings):
        return ApiTokenAuthProvider(email=settings.email, token=settings.token)
    if isinstance(settings, OAuthSettings):
        # Imported lazily to keep the API-token path free of OAuth machinery.
        from zendesk_mcp_server.oauth import OAuthAuthProvider

        return OAuthAuthProvider(settings)
    raise TypeError(f"Unsupported settings type: {type(settings).__name__}")


def build_client(settings: Settings | None = None) -> ZendeskClient:
    settings = load_settings() if settings is None else settings
    return ZendeskClient(
        subdomain=settings.subdomain,
        auth=build_auth_provider(settings),
    )
