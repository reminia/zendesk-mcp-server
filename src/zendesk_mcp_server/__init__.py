"""
Zendesk MCP Server.

A Model Context Protocol server for Zendesk Support integration.

Provides two modes of operation:
- Local mode (stdio): Uses API key authentication, run with `zendesk` command
- Remote mode (HTTP): Uses OAuth 2.0 authentication, run with `zendesk-remote` command
"""

import asyncio

from . import server


def main():
    """
    Entry point for local stdio-based MCP server.

    Uses API key authentication. Configure with:
    - ZENDESK_SUBDOMAIN
    - ZENDESK_EMAIL
    - ZENDESK_API_KEY
    """
    asyncio.run(server.main())


def main_remote():
    """
    Entry point for remote HTTP-based MCP server with OAuth.

    Uses OAuth 2.0 authentication. Configure with:
    - ZENDESK_OAUTH_CLIENT_ID
    - ZENDESK_OAUTH_CLIENT_SECRET
    - ZENDESK_OAUTH_REDIRECT_URI
    - MCP_SERVER_HOST (default: 0.0.0.0)
    - MCP_SERVER_PORT (default: 8080)
    - MCP_SERVER_BASE_URL
    """
    from .server_http import run_server
    run_server()


__all__ = ["main", "main_remote", "server"]
