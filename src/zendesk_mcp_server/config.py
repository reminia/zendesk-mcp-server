"""
Configuration management for Zendesk MCP Server.

Supports both local (API key) and remote (OAuth) authentication modes.
"""

import os
import secrets
from dataclasses import dataclass, field
from enum import Enum
from typing import Optional

from dotenv import load_dotenv


class AuthMode(Enum):
    """Authentication mode for the server."""
    LOCAL = "local"      # API key authentication (original mode)
    OAUTH = "oauth"      # OAuth 2.0 authentication (remote mode)


@dataclass
class ZendeskOAuthConfig:
    """Zendesk OAuth 2.0 configuration."""
    client_id: str
    client_secret: str
    redirect_uri: str
    # Zendesk OAuth scopes - read and write access to tickets, users, etc.
    scopes: list[str] = field(default_factory=lambda: ["read", "write"])

    @classmethod
    def from_env(cls) -> Optional["ZendeskOAuthConfig"]:
        """Load OAuth config from environment variables."""
        client_id = os.getenv("ZENDESK_OAUTH_CLIENT_ID")
        client_secret = os.getenv("ZENDESK_OAUTH_CLIENT_SECRET")
        redirect_uri = os.getenv("ZENDESK_OAUTH_REDIRECT_URI")

        if not all([client_id, client_secret, redirect_uri]):
            return None

        scopes_str = os.getenv("ZENDESK_OAUTH_SCOPES", "read,write")
        scopes = [s.strip() for s in scopes_str.split(",")]

        return cls(
            client_id=client_id,
            client_secret=client_secret,
            redirect_uri=redirect_uri,
            scopes=scopes,
        )


@dataclass
class ZendeskAPIKeyConfig:
    """Zendesk API key configuration (legacy/local mode)."""
    subdomain: str
    email: str
    api_key: str

    @classmethod
    def from_env(cls) -> Optional["ZendeskAPIKeyConfig"]:
        """Load API key config from environment variables."""
        subdomain = os.getenv("ZENDESK_SUBDOMAIN")
        email = os.getenv("ZENDESK_EMAIL")
        api_key = os.getenv("ZENDESK_API_KEY")

        if not all([subdomain, email, api_key]):
            return None

        return cls(
            subdomain=subdomain,
            email=email,
            api_key=api_key,
        )


@dataclass
class ServerConfig:
    """HTTP server configuration for remote mode."""
    host: str = "0.0.0.0"
    port: int = 8080
    base_url: str = "http://localhost:8080"
    # Session and token settings
    session_secret: str = field(default_factory=lambda: secrets.token_urlsafe(32))
    token_encryption_key: Optional[str] = None
    # Database URL for token storage (SQLite by default)
    database_url: str = "sqlite+aiosqlite:///zendesk_mcp_tokens.db"
    # CORS settings
    cors_origins: list[str] = field(default_factory=lambda: ["*"])

    @classmethod
    def from_env(cls) -> "ServerConfig":
        """Load server config from environment variables."""
        cors_origins_str = os.getenv("MCP_CORS_ORIGINS", "*")
        cors_origins = [o.strip() for o in cors_origins_str.split(",")]

        return cls(
            host=os.getenv("MCP_SERVER_HOST", "0.0.0.0"),
            port=int(os.getenv("MCP_SERVER_PORT", "8080")),
            base_url=os.getenv("MCP_SERVER_BASE_URL", "http://localhost:8080"),
            session_secret=os.getenv("MCP_SESSION_SECRET", secrets.token_urlsafe(32)),
            token_encryption_key=os.getenv("TOKEN_ENCRYPTION_KEY"),
            database_url=os.getenv("DATABASE_URL", "sqlite+aiosqlite:///zendesk_mcp_tokens.db"),
            cors_origins=cors_origins,
        )


@dataclass
class Config:
    """Main configuration container."""
    auth_mode: AuthMode
    api_key_config: Optional[ZendeskAPIKeyConfig] = None
    oauth_config: Optional[ZendeskOAuthConfig] = None
    server_config: Optional[ServerConfig] = None
    timeout: int = 30

    @classmethod
    def load(cls, env_file: Optional[str] = None) -> "Config":
        """
        Load configuration from environment.

        Automatically detects auth mode based on available environment variables:
        - If ZENDESK_OAUTH_CLIENT_ID is set, uses OAuth mode
        - Otherwise, falls back to API key mode
        """
        if env_file:
            load_dotenv(env_file)
        else:
            load_dotenv()

        timeout = int(os.getenv("ZENDESK_TIMEOUT", "30"))

        # Check for OAuth configuration first
        oauth_config = ZendeskOAuthConfig.from_env()
        if oauth_config:
            server_config = ServerConfig.from_env()
            return cls(
                auth_mode=AuthMode.OAUTH,
                oauth_config=oauth_config,
                server_config=server_config,
                timeout=timeout,
            )

        # Fall back to API key configuration
        api_key_config = ZendeskAPIKeyConfig.from_env()
        if api_key_config:
            return cls(
                auth_mode=AuthMode.LOCAL,
                api_key_config=api_key_config,
                timeout=timeout,
            )

        # No valid configuration found
        raise ValueError(
            "No valid Zendesk configuration found. "
            "Set either ZENDESK_OAUTH_CLIENT_ID (OAuth mode) or "
            "ZENDESK_SUBDOMAIN/ZENDESK_EMAIL/ZENDESK_API_KEY (API key mode)."
        )

    @property
    def is_oauth_mode(self) -> bool:
        """Check if running in OAuth mode."""
        return self.auth_mode == AuthMode.OAUTH

    @property
    def is_local_mode(self) -> bool:
        """Check if running in local/API key mode."""
        return self.auth_mode == AuthMode.LOCAL


# Global config instance (lazy loaded)
_config: Optional[Config] = None


def get_config() -> Config:
    """Get the global configuration instance."""
    global _config
    if _config is None:
        _config = Config.load()
    return _config


def reset_config() -> None:
    """Reset the global configuration (useful for testing)."""
    global _config
    _config = None
