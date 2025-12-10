"""
Token storage backends for OAuth sessions.

Provides encrypted storage for Zendesk OAuth tokens.
"""

import json
import logging
import secrets
from abc import ABC, abstractmethod
from dataclasses import dataclass, asdict
from datetime import datetime, timezone
from typing import Optional

import aiosqlite
from cryptography.fernet import Fernet

logger = logging.getLogger(__name__)


@dataclass
class OAuthSession:
    """Represents an OAuth session with tokens."""
    session_id: str
    zendesk_subdomain: str
    access_token: str
    refresh_token: Optional[str]
    token_type: str
    expires_at: Optional[datetime]
    scopes: list[str]
    created_at: datetime
    updated_at: datetime

    def is_expired(self) -> bool:
        """Check if the access token has expired."""
        if self.expires_at is None:
            return False
        return datetime.now(timezone.utc) >= self.expires_at

    def to_dict(self) -> dict:
        """Convert to dictionary for JSON serialization."""
        data = asdict(self)
        data["expires_at"] = self.expires_at.isoformat() if self.expires_at else None
        data["created_at"] = self.created_at.isoformat()
        data["updated_at"] = self.updated_at.isoformat()
        return data

    @classmethod
    def from_dict(cls, data: dict) -> "OAuthSession":
        """Create from dictionary."""
        if data.get("expires_at"):
            data["expires_at"] = datetime.fromisoformat(data["expires_at"])
        data["created_at"] = datetime.fromisoformat(data["created_at"])
        data["updated_at"] = datetime.fromisoformat(data["updated_at"])
        return cls(**data)


class TokenEncryption:
    """Handles encryption/decryption of OAuth tokens."""

    def __init__(self, key: Optional[str] = None):
        """
        Initialize encryption handler.

        Args:
            key: Base64-encoded Fernet key. If None, generates a new key.
        """
        if key:
            self._fernet = Fernet(key.encode())
        else:
            self._key = Fernet.generate_key()
            self._fernet = Fernet(self._key)
            logger.warning(
                "No encryption key provided. Generated temporary key. "
                "Set TOKEN_ENCRYPTION_KEY in production!"
            )

    def encrypt(self, plaintext: str) -> str:
        """Encrypt a string."""
        return self._fernet.encrypt(plaintext.encode()).decode()

    def decrypt(self, ciphertext: str) -> str:
        """Decrypt a string."""
        return self._fernet.decrypt(ciphertext.encode()).decode()

    @staticmethod
    def generate_key() -> str:
        """Generate a new encryption key."""
        return Fernet.generate_key().decode()


class TokenStorage(ABC):
    """Abstract base class for token storage backends."""

    @abstractmethod
    async def initialize(self) -> None:
        """Initialize the storage backend (create tables, etc.)."""
        pass

    @abstractmethod
    async def store_session(self, session: OAuthSession) -> None:
        """Store an OAuth session."""
        pass

    @abstractmethod
    async def get_session(self, session_id: str) -> Optional[OAuthSession]:
        """Retrieve an OAuth session by ID."""
        pass

    @abstractmethod
    async def update_session(self, session: OAuthSession) -> None:
        """Update an existing OAuth session."""
        pass

    @abstractmethod
    async def delete_session(self, session_id: str) -> None:
        """Delete an OAuth session."""
        pass

    @abstractmethod
    async def cleanup_expired(self) -> int:
        """Remove expired sessions. Returns count of deleted sessions."""
        pass

    @staticmethod
    def generate_session_id() -> str:
        """Generate a secure session ID."""
        return secrets.token_urlsafe(32)


class SQLiteTokenStorage(TokenStorage):
    """SQLite-based token storage with encryption."""

    def __init__(self, db_path: str = "zendesk_mcp_tokens.db", encryption_key: Optional[str] = None):
        """
        Initialize SQLite storage.

        Args:
            db_path: Path to SQLite database file
            encryption_key: Fernet encryption key for token encryption
        """
        # Handle SQLAlchemy-style URLs
        if db_path.startswith("sqlite"):
            # Extract path from sqlite:///path or sqlite+aiosqlite:///path
            db_path = db_path.split("///")[-1]

        self.db_path = db_path
        self.encryption = TokenEncryption(encryption_key)
        self._initialized = False

    async def initialize(self) -> None:
        """Create the sessions table if it doesn't exist."""
        if self._initialized:
            return

        async with aiosqlite.connect(self.db_path) as db:
            await db.execute("""
                CREATE TABLE IF NOT EXISTS oauth_sessions (
                    session_id TEXT PRIMARY KEY,
                    zendesk_subdomain TEXT NOT NULL,
                    access_token_encrypted TEXT NOT NULL,
                    refresh_token_encrypted TEXT,
                    token_type TEXT NOT NULL DEFAULT 'Bearer',
                    expires_at TEXT,
                    scopes TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                )
            """)
            await db.execute("""
                CREATE INDEX IF NOT EXISTS idx_sessions_subdomain
                ON oauth_sessions(zendesk_subdomain)
            """)
            await db.commit()

        self._initialized = True
        logger.info(f"SQLite token storage initialized at {self.db_path}")

    async def store_session(self, session: OAuthSession) -> None:
        """Store an OAuth session with encrypted tokens."""
        await self.initialize()

        encrypted_access = self.encryption.encrypt(session.access_token)
        encrypted_refresh = None
        if session.refresh_token:
            encrypted_refresh = self.encryption.encrypt(session.refresh_token)

        async with aiosqlite.connect(self.db_path) as db:
            await db.execute("""
                INSERT OR REPLACE INTO oauth_sessions
                (session_id, zendesk_subdomain, access_token_encrypted,
                 refresh_token_encrypted, token_type, expires_at, scopes,
                 created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                session.session_id,
                session.zendesk_subdomain,
                encrypted_access,
                encrypted_refresh,
                session.token_type,
                session.expires_at.isoformat() if session.expires_at else None,
                json.dumps(session.scopes),
                session.created_at.isoformat(),
                session.updated_at.isoformat(),
            ))
            await db.commit()

        logger.info(f"Stored OAuth session for subdomain: {session.zendesk_subdomain}")

    async def get_session(self, session_id: str) -> Optional[OAuthSession]:
        """Retrieve and decrypt an OAuth session."""
        await self.initialize()

        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row
            cursor = await db.execute(
                "SELECT * FROM oauth_sessions WHERE session_id = ?",
                (session_id,)
            )
            row = await cursor.fetchone()

            if not row:
                return None

            # Decrypt tokens
            access_token = self.encryption.decrypt(row["access_token_encrypted"])
            refresh_token = None
            if row["refresh_token_encrypted"]:
                refresh_token = self.encryption.decrypt(row["refresh_token_encrypted"])

            return OAuthSession(
                session_id=row["session_id"],
                zendesk_subdomain=row["zendesk_subdomain"],
                access_token=access_token,
                refresh_token=refresh_token,
                token_type=row["token_type"],
                expires_at=datetime.fromisoformat(row["expires_at"]) if row["expires_at"] else None,
                scopes=json.loads(row["scopes"]),
                created_at=datetime.fromisoformat(row["created_at"]),
                updated_at=datetime.fromisoformat(row["updated_at"]),
            )

    async def update_session(self, session: OAuthSession) -> None:
        """Update an existing session (e.g., after token refresh)."""
        session.updated_at = datetime.now(timezone.utc)
        await self.store_session(session)

    async def delete_session(self, session_id: str) -> None:
        """Delete a session."""
        await self.initialize()

        async with aiosqlite.connect(self.db_path) as db:
            await db.execute(
                "DELETE FROM oauth_sessions WHERE session_id = ?",
                (session_id,)
            )
            await db.commit()

        logger.info(f"Deleted OAuth session: {session_id}")

    async def cleanup_expired(self) -> int:
        """Remove expired sessions."""
        await self.initialize()

        now = datetime.now(timezone.utc).isoformat()

        async with aiosqlite.connect(self.db_path) as db:
            cursor = await db.execute(
                "DELETE FROM oauth_sessions WHERE expires_at IS NOT NULL AND expires_at < ?",
                (now,)
            )
            await db.commit()
            deleted = cursor.rowcount

        if deleted > 0:
            logger.info(f"Cleaned up {deleted} expired OAuth sessions")
        return deleted
