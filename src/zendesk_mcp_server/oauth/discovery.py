"""
OAuth 2.0 Discovery Endpoints for MCP Server.

Implements the well-known endpoints required by the MCP specification:
- /.well-known/oauth-protected-resource (RFC 9728)
- /.well-known/oauth-authorization-server (RFC 8414)
"""

from typing import Optional
from dataclasses import dataclass, field, asdict


@dataclass
class ProtectedResourceMetadata:
    """
    OAuth 2.0 Protected Resource Metadata (RFC 9728).

    This metadata tells MCP clients how to authenticate with this server.
    """
    # The resource identifier (usually the server's base URL)
    resource: str

    # Authorization server(s) that can issue tokens for this resource
    authorization_servers: list[str]

    # Scopes supported by this resource
    scopes_supported: list[str] = field(default_factory=lambda: ["read", "write"])

    # Token endpoint auth methods the resource expects
    bearer_methods_supported: list[str] = field(
        default_factory=lambda: ["header"]
    )

    # Resource documentation URL
    resource_documentation: Optional[str] = None

    def to_dict(self) -> dict:
        """Convert to dictionary for JSON response."""
        data = asdict(self)
        # Remove None values
        return {k: v for k, v in data.items() if v is not None}


@dataclass
class AuthorizationServerMetadata:
    """
    OAuth 2.0 Authorization Server Metadata (RFC 8414).

    This metadata describes the OAuth endpoints and capabilities.
    """
    # Authorization server identifier
    issuer: str

    # OAuth endpoints
    authorization_endpoint: str
    token_endpoint: str
    registration_endpoint: Optional[str] = None
    revocation_endpoint: Optional[str] = None

    # Supported features
    response_types_supported: list[str] = field(
        default_factory=lambda: ["code"]
    )
    grant_types_supported: list[str] = field(
        default_factory=lambda: ["authorization_code", "refresh_token"]
    )
    code_challenge_methods_supported: list[str] = field(
        default_factory=lambda: ["S256"]
    )
    token_endpoint_auth_methods_supported: list[str] = field(
        default_factory=lambda: ["client_secret_post"]
    )

    # Scopes
    scopes_supported: list[str] = field(
        default_factory=lambda: ["read", "write"]
    )

    # Additional metadata
    service_documentation: Optional[str] = None

    def to_dict(self) -> dict:
        """Convert to dictionary for JSON response."""
        data = asdict(self)
        return {k: v for k, v in data.items() if v is not None}


def create_protected_resource_metadata(
    base_url: str,
    scopes: list[str],
) -> ProtectedResourceMetadata:
    """
    Create Protected Resource Metadata for this MCP server.

    Args:
        base_url: The server's base URL (e.g., https://mcp.example.com)
        scopes: Supported OAuth scopes

    Returns:
        ProtectedResourceMetadata instance
    """
    return ProtectedResourceMetadata(
        resource=base_url,
        authorization_servers=[f"{base_url}"],
        scopes_supported=scopes,
        bearer_methods_supported=["header"],
        resource_documentation=f"{base_url}/docs",
    )


def create_authorization_server_metadata(
    base_url: str,
    scopes: list[str],
) -> AuthorizationServerMetadata:
    """
    Create Authorization Server Metadata for this MCP server.

    Note: This MCP server acts as an OAuth authorization server that
    proxies authentication to Zendesk's OAuth server.

    Args:
        base_url: The server's base URL
        scopes: Supported OAuth scopes

    Returns:
        AuthorizationServerMetadata instance
    """
    return AuthorizationServerMetadata(
        issuer=base_url,
        authorization_endpoint=f"{base_url}/oauth/authorize",
        token_endpoint=f"{base_url}/oauth/token",
        registration_endpoint=None,  # Dynamic registration not supported
        revocation_endpoint=f"{base_url}/oauth/revoke",
        response_types_supported=["code"],
        grant_types_supported=["authorization_code", "refresh_token"],
        code_challenge_methods_supported=["S256"],  # PKCE required
        token_endpoint_auth_methods_supported=["client_secret_post"],
        scopes_supported=scopes,
        service_documentation=f"{base_url}/docs",
    )
