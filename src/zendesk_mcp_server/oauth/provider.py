"""
Zendesk OAuth 2.0 Provider.

Implements the OAuth 2.0 Authorization Code flow with PKCE support
for Zendesk API authentication.
"""

import hashlib
import logging
import secrets
import base64
from dataclasses import dataclass
from datetime import datetime, timezone, timedelta
from typing import Optional
from urllib.parse import urlencode

import httpx

from zendesk_mcp_server.config import ZendeskOAuthConfig
from zendesk_mcp_server.oauth.storage import OAuthSession, TokenStorage

logger = logging.getLogger(__name__)


@dataclass
class PKCEChallenge:
    """PKCE code challenge and verifier."""
    code_verifier: str
    code_challenge: str
    code_challenge_method: str = "S256"

    @classmethod
    def generate(cls) -> "PKCEChallenge":
        """Generate a new PKCE challenge pair."""
        # Generate code verifier (43-128 characters, URL-safe)
        code_verifier = secrets.token_urlsafe(32)

        # Generate code challenge using S256 method
        digest = hashlib.sha256(code_verifier.encode()).digest()
        code_challenge = base64.urlsafe_b64encode(digest).decode().rstrip("=")

        return cls(
            code_verifier=code_verifier,
            code_challenge=code_challenge,
            code_challenge_method="S256",
        )


@dataclass
class AuthorizationState:
    """State for tracking authorization requests."""
    state: str
    pkce: PKCEChallenge
    subdomain: str
    redirect_uri: str
    created_at: datetime

    @classmethod
    def generate(cls, subdomain: str, redirect_uri: str) -> "AuthorizationState":
        """Generate new authorization state."""
        return cls(
            state=secrets.token_urlsafe(32),
            pkce=PKCEChallenge.generate(),
            subdomain=subdomain,
            redirect_uri=redirect_uri,
            created_at=datetime.now(timezone.utc),
        )

    def is_expired(self, max_age_seconds: int = 600) -> bool:
        """Check if state has expired (default 10 minutes)."""
        age = datetime.now(timezone.utc) - self.created_at
        return age.total_seconds() > max_age_seconds


class ZendeskOAuthProvider:
    """
    Zendesk OAuth 2.0 Provider.

    Implements the authorization code flow with PKCE as required by
    Zendesk (effective February 2025).
    """

    # Zendesk OAuth endpoints
    AUTHORIZE_PATH = "/oauth/authorizations/new"
    TOKEN_PATH = "/oauth/tokens"

    def __init__(
        self,
        config: ZendeskOAuthConfig,
        token_storage: TokenStorage,
    ):
        """
        Initialize the OAuth provider.

        Args:
            config: Zendesk OAuth configuration
            token_storage: Backend for storing tokens
        """
        self.config = config
        self.token_storage = token_storage
        self._pending_states: dict[str, AuthorizationState] = {}
        self._http_client: Optional[httpx.AsyncClient] = None

    async def _get_http_client(self) -> httpx.AsyncClient:
        """Get or create HTTP client."""
        if self._http_client is None or self._http_client.is_closed:
            self._http_client = httpx.AsyncClient(timeout=30.0)
        return self._http_client

    async def close(self) -> None:
        """Close HTTP client."""
        if self._http_client and not self._http_client.is_closed:
            await self._http_client.aclose()

    def _get_zendesk_url(self, subdomain: str, path: str) -> str:
        """Build Zendesk URL for a given subdomain."""
        return f"https://{subdomain}.zendesk.com{path}"

    def generate_authorization_url(self, subdomain: str) -> tuple[str, AuthorizationState]:
        """
        Generate the Zendesk OAuth authorization URL.

        Args:
            subdomain: The Zendesk subdomain to authenticate against

        Returns:
            Tuple of (authorization_url, state_object)
        """
        auth_state = AuthorizationState.generate(
            subdomain=subdomain,
            redirect_uri=self.config.redirect_uri,
        )

        # Store state for validation during callback
        self._pending_states[auth_state.state] = auth_state

        # Build authorization URL with PKCE
        params = {
            "response_type": "code",
            "client_id": self.config.client_id,
            "redirect_uri": self.config.redirect_uri,
            "scope": " ".join(self.config.scopes),
            "state": auth_state.state,
            "code_challenge": auth_state.pkce.code_challenge,
            "code_challenge_method": auth_state.pkce.code_challenge_method,
        }

        base_url = self._get_zendesk_url(subdomain, self.AUTHORIZE_PATH)
        authorization_url = f"{base_url}?{urlencode(params)}"

        logger.info(f"Generated authorization URL for subdomain: {subdomain}")
        return authorization_url, auth_state

    def validate_state(self, state: str) -> Optional[AuthorizationState]:
        """
        Validate and retrieve authorization state.

        Args:
            state: The state parameter from callback

        Returns:
            AuthorizationState if valid, None otherwise
        """
        auth_state = self._pending_states.get(state)
        if auth_state is None:
            logger.warning(f"Unknown state parameter: {state[:20]}...")
            return None

        if auth_state.is_expired():
            logger.warning(f"Expired state parameter for subdomain: {auth_state.subdomain}")
            del self._pending_states[state]
            return None

        return auth_state

    def consume_state(self, state: str) -> Optional[AuthorizationState]:
        """
        Validate and remove authorization state (one-time use).

        Args:
            state: The state parameter from callback

        Returns:
            AuthorizationState if valid, None otherwise
        """
        auth_state = self.validate_state(state)
        if auth_state:
            del self._pending_states[state]
        return auth_state

    async def exchange_code_for_tokens(
        self,
        code: str,
        auth_state: AuthorizationState,
    ) -> OAuthSession:
        """
        Exchange authorization code for access tokens.

        Args:
            code: The authorization code from callback
            auth_state: The authorization state object

        Returns:
            OAuthSession with tokens

        Raises:
            ValueError: If token exchange fails
        """
        client = await self._get_http_client()
        token_url = self._get_zendesk_url(auth_state.subdomain, self.TOKEN_PATH)

        # Prepare token request with PKCE verifier
        data = {
            "grant_type": "authorization_code",
            "code": code,
            "client_id": self.config.client_id,
            "client_secret": self.config.client_secret,
            "redirect_uri": auth_state.redirect_uri,
            "code_verifier": auth_state.pkce.code_verifier,
            "scope": " ".join(self.config.scopes),
        }

        try:
            response = await client.post(
                token_url,
                data=data,
                headers={"Content-Type": "application/x-www-form-urlencoded"},
            )
            response.raise_for_status()
            token_data = response.json()
        except httpx.HTTPStatusError as e:
            logger.error(f"Token exchange failed: {e.response.status_code} - {e.response.text}")
            raise ValueError(f"Token exchange failed: {e.response.text}")
        except Exception as e:
            logger.error(f"Token exchange error: {e}")
            raise ValueError(f"Token exchange error: {str(e)}")

        # Calculate expiration time
        expires_at = None
        if "expires_in" in token_data:
            expires_at = datetime.now(timezone.utc) + timedelta(seconds=token_data["expires_in"])

        # Create session
        now = datetime.now(timezone.utc)
        session = OAuthSession(
            session_id=TokenStorage.generate_session_id(),
            zendesk_subdomain=auth_state.subdomain,
            access_token=token_data["access_token"],
            refresh_token=token_data.get("refresh_token"),
            token_type=token_data.get("token_type", "Bearer"),
            expires_at=expires_at,
            scopes=self.config.scopes,
            created_at=now,
            updated_at=now,
        )

        # Store session
        await self.token_storage.store_session(session)
        logger.info(f"Created OAuth session for subdomain: {auth_state.subdomain}")

        return session

    async def refresh_access_token(self, session: OAuthSession) -> OAuthSession:
        """
        Refresh an expired access token.

        Args:
            session: The session with expired token

        Returns:
            Updated session with new tokens

        Raises:
            ValueError: If refresh fails or no refresh token available
        """
        if not session.refresh_token:
            raise ValueError("No refresh token available")

        client = await self._get_http_client()
        token_url = self._get_zendesk_url(session.zendesk_subdomain, self.TOKEN_PATH)

        data = {
            "grant_type": "refresh_token",
            "refresh_token": session.refresh_token,
            "client_id": self.config.client_id,
            "client_secret": self.config.client_secret,
            "scope": " ".join(session.scopes),
        }

        try:
            response = await client.post(
                token_url,
                data=data,
                headers={"Content-Type": "application/x-www-form-urlencoded"},
            )
            response.raise_for_status()
            token_data = response.json()
        except httpx.HTTPStatusError as e:
            logger.error(f"Token refresh failed: {e.response.status_code}")
            raise ValueError(f"Token refresh failed: {e.response.text}")

        # Update session with new tokens
        session.access_token = token_data["access_token"]
        if "refresh_token" in token_data:
            session.refresh_token = token_data["refresh_token"]

        if "expires_in" in token_data:
            session.expires_at = datetime.now(timezone.utc) + timedelta(seconds=token_data["expires_in"])

        session.updated_at = datetime.now(timezone.utc)

        # Update stored session
        await self.token_storage.update_session(session)
        logger.info(f"Refreshed access token for subdomain: {session.zendesk_subdomain}")

        return session

    async def get_valid_session(self, session_id: str) -> Optional[OAuthSession]:
        """
        Get a valid (non-expired) session, refreshing if necessary.

        Args:
            session_id: The session ID

        Returns:
            Valid session or None if not found/refresh failed
        """
        session = await self.token_storage.get_session(session_id)
        if session is None:
            return None

        # Check if token needs refresh
        if session.is_expired():
            if session.refresh_token:
                try:
                    session = await self.refresh_access_token(session)
                except ValueError as e:
                    logger.warning(f"Failed to refresh token: {e}")
                    # Delete invalid session
                    await self.token_storage.delete_session(session_id)
                    return None
            else:
                logger.warning(f"Token expired and no refresh token available")
                await self.token_storage.delete_session(session_id)
                return None

        return session

    async def revoke_session(self, session_id: str) -> bool:
        """
        Revoke and delete a session.

        Args:
            session_id: The session to revoke

        Returns:
            True if session was deleted, False if not found
        """
        session = await self.token_storage.get_session(session_id)
        if session is None:
            return False

        # Optionally revoke token with Zendesk (if they support it)
        # For now, just delete from storage
        await self.token_storage.delete_session(session_id)
        logger.info(f"Revoked session: {session_id}")
        return True

    def cleanup_expired_states(self) -> int:
        """Remove expired pending authorization states."""
        expired = [
            state for state, auth_state in self._pending_states.items()
            if auth_state.is_expired()
        ]
        for state in expired:
            del self._pending_states[state]
        return len(expired)
