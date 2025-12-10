"""
Authentication middleware for FastAPI.

Handles Bearer token extraction and session validation for MCP requests.
"""

import logging
from contextvars import ContextVar
from functools import wraps
from typing import Callable, Optional

from fastapi import Request, HTTPException, status
from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware

from zendesk_mcp_server.oauth.storage import OAuthSession

logger = logging.getLogger(__name__)

# Context variable to store the current session
_current_session: ContextVar[Optional[OAuthSession]] = ContextVar(
    "current_session", default=None
)


def get_current_session() -> Optional[OAuthSession]:
    """Get the current OAuth session from context."""
    return _current_session.get()


def set_current_session(session: Optional[OAuthSession]) -> None:
    """Set the current OAuth session in context."""
    _current_session.set(session)


def require_auth(func: Callable) -> Callable:
    """
    Decorator that requires authentication for a route.

    Usage:
        @app.get("/protected")
        @require_auth
        async def protected_route():
            session = get_current_session()
            ...
    """
    @wraps(func)
    async def wrapper(*args, **kwargs):
        session = get_current_session()
        if session is None:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Authentication required",
                headers={
                    "WWW-Authenticate": 'Bearer realm="zendesk-mcp"',
                },
            )
        return await func(*args, **kwargs)
    return wrapper


class AuthMiddleware(BaseHTTPMiddleware):
    """
    Middleware that extracts Bearer tokens and validates sessions.

    Public paths (like OAuth endpoints and discovery) bypass authentication.
    MCP endpoints require valid Bearer tokens.
    """

    # Paths that don't require authentication
    PUBLIC_PATHS = {
        "/",
        "/health",
        "/docs",
        "/openapi.json",
        "/oauth/authorize",
        "/oauth/callback",
        "/oauth/token",
        "/oauth/revoke",
        "/.well-known/oauth-protected-resource",
        "/.well-known/oauth-authorization-server",
    }

    def __init__(self, app, oauth_provider=None):
        """
        Initialize the middleware.

        Args:
            app: The FastAPI application
            oauth_provider: The ZendeskOAuthProvider instance
        """
        super().__init__(app)
        self.oauth_provider = oauth_provider

    def _is_public_path(self, path: str) -> bool:
        """Check if path is public (no auth required)."""
        return path in self.PUBLIC_PATHS or path.startswith("/.well-known/")

    def _extract_bearer_token(self, request: Request) -> Optional[str]:
        """Extract Bearer token from Authorization header."""
        auth_header = request.headers.get("Authorization")
        if not auth_header:
            return None

        parts = auth_header.split()
        if len(parts) != 2 or parts[0].lower() != "bearer":
            return None

        return parts[1]

    async def dispatch(self, request: Request, call_next):
        """Process the request, extracting and validating auth tokens."""
        # Reset session context for this request
        set_current_session(None)

        # Allow public paths without authentication
        if self._is_public_path(request.url.path):
            return await call_next(request)

        # Extract Bearer token
        token = self._extract_bearer_token(request)
        if not token:
            logger.debug(f"No Bearer token for path: {request.url.path}")
            return self._unauthorized_response()

        # Validate session if OAuth provider is available
        if self.oauth_provider:
            session = await self.oauth_provider.get_valid_session(token)
            if session is None:
                logger.warning(f"Invalid or expired session token")
                return self._unauthorized_response("Invalid or expired token")

            # Store session in context for route handlers
            set_current_session(session)
            logger.debug(f"Authenticated request for subdomain: {session.zendesk_subdomain}")
        else:
            # No OAuth provider - running in local mode, allow through
            logger.debug("No OAuth provider configured, skipping auth")

        return await call_next(request)

    def _unauthorized_response(self, detail: str = "Authentication required") -> JSONResponse:
        """Create a 401 Unauthorized response with WWW-Authenticate header."""
        return JSONResponse(
            status_code=status.HTTP_401_UNAUTHORIZED,
            content={
                "error": "unauthorized",
                "error_description": detail,
            },
            headers={
                "WWW-Authenticate": 'Bearer realm="zendesk-mcp", '
                    'error="invalid_token", '
                    f'error_description="{detail}"',
            },
        )


class SessionDependency:
    """
    FastAPI dependency for getting the current session.

    Usage:
        @app.get("/api/tickets")
        async def get_tickets(session: OAuthSession = Depends(SessionDependency())):
            ...
    """

    def __init__(self, required: bool = True):
        """
        Initialize the dependency.

        Args:
            required: If True, raises 401 if no session. If False, returns None.
        """
        self.required = required

    async def __call__(self, request: Request) -> Optional[OAuthSession]:
        """Get the session from context."""
        session = get_current_session()
        if session is None and self.required:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Authentication required",
                headers={"WWW-Authenticate": 'Bearer realm="zendesk-mcp"'},
            )
        return session
