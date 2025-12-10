"""
Authentication module for Zendesk MCP Server.
"""

from zendesk_mcp_server.auth.middleware import (
    AuthMiddleware,
    get_current_session,
    require_auth,
)

__all__ = [
    "AuthMiddleware",
    "get_current_session",
    "require_auth",
]
