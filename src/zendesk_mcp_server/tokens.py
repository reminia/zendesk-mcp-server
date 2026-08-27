"""
Local storage for OAuth tokens.

Zendesk rotates the refresh token on every refresh and invalidates the previous
one immediately, so a lost write costs the operator a full re-authorization.
Two safeguards follow from that:

* writes are atomic, so a crash mid-write cannot truncate the file, and
* reads and writes can be wrapped in a cross-process lock, so two MCP clients
  running at once cannot refresh concurrently and discard each other's token.

The file holds live credentials and is created ``0600`` inside a ``0700``
directory.
"""
from __future__ import annotations

import json
import logging
import os
import stat
import tempfile
from contextlib import contextmanager
from dataclasses import dataclass, field, replace
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterator, Mapping

from filelock import FileLock, Timeout

logger = logging.getLogger(__name__)

# Refresh slightly early so a request is never sent with a token that expires
# in flight, and to absorb modest clock drift against Zendesk.
DEFAULT_EXPIRY_SKEW = timedelta(seconds=60)

_LOCK_TIMEOUT_SECONDS = 30
_FILE_MODE = 0o600
_DIR_MODE = 0o700


class TokenStoreError(RuntimeError):
    """Raised when tokens cannot be read from or written to the store."""


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _to_iso(moment: datetime | None) -> str | None:
    return moment.astimezone(timezone.utc).isoformat() if moment else None


def _from_iso(value: Any) -> datetime | None:
    if value in (None, ""):
        return None
    try:
        parsed = datetime.fromisoformat(value)
    except (TypeError, ValueError) as exc:
        raise TokenStoreError(f"Invalid timestamp in token store: {value!r}") from exc
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


@dataclass(frozen=True)
class TokenSet:
    """
    An access token and the refresh token that renews it.

    ``expires_at`` is None for tokens issued by OAuth clients created before
    2026-04-30 without an explicit ``expires_in``; those never expire and have
    no refresh token.
    """

    # repr=False keeps secrets out of logs, tracebacks and pytest output.
    access_token: str = field(repr=False)
    subdomain: str
    client_id: str
    refresh_token: str | None = field(default=None, repr=False)
    expires_at: datetime | None = None
    refresh_token_expires_at: datetime | None = None
    scope: str | None = None

    def access_token_expired(self, skew: timedelta = DEFAULT_EXPIRY_SKEW) -> bool:
        if self.expires_at is None:
            return False
        return _utcnow() + skew >= self.expires_at

    def refresh_token_expired(self, skew: timedelta = DEFAULT_EXPIRY_SKEW) -> bool:
        if self.refresh_token_expires_at is None:
            return False
        return _utcnow() + skew >= self.refresh_token_expires_at

    def can_refresh(self) -> bool:
        return bool(self.refresh_token) and not self.refresh_token_expired()

    @classmethod
    def from_token_response(
        cls,
        payload: Mapping[str, Any],
        *,
        subdomain: str,
        client_id: str,
        issued_at: datetime | None = None,
    ) -> "TokenSet":
        """Build a TokenSet from a Zendesk ``/oauth/tokens`` response body."""
        access_token = payload.get("access_token")
        if not access_token:
            raise TokenStoreError("Zendesk token response did not include an access_token.")

        issued_at = issued_at or _utcnow()
        expires_in = payload.get("expires_in")
        refresh_expires_in = payload.get("refresh_token_expires_in")

        return cls(
            access_token=access_token,
            subdomain=subdomain,
            client_id=client_id,
            refresh_token=payload.get("refresh_token"),
            expires_at=issued_at + timedelta(seconds=int(expires_in)) if expires_in else None,
            refresh_token_expires_at=(
                issued_at + timedelta(seconds=int(refresh_expires_in))
                if refresh_expires_in
                else None
            ),
            scope=payload.get("scope"),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "access_token": self.access_token,
            "refresh_token": self.refresh_token,
            "expires_at": _to_iso(self.expires_at),
            "refresh_token_expires_at": _to_iso(self.refresh_token_expires_at),
            "subdomain": self.subdomain,
            "client_id": self.client_id,
            "scope": self.scope,
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "TokenSet":
        try:
            return cls(
                access_token=data["access_token"],
                subdomain=data["subdomain"],
                client_id=data["client_id"],
                refresh_token=data.get("refresh_token"),
                expires_at=_from_iso(data.get("expires_at")),
                refresh_token_expires_at=_from_iso(data.get("refresh_token_expires_at")),
                scope=data.get("scope"),
            )
        except KeyError as exc:
            raise TokenStoreError(
                f"Token store is missing the {exc.args[0]!r} field. "
                "Re-run zendesk-auth to recreate it."
            ) from exc

    def replace(self, **changes: Any) -> "TokenSet":
        return replace(self, **changes)


class TokenStore:
    """Reads and writes a :class:`TokenSet` as JSON on the local filesystem."""

    def __init__(self, path: Path):
        self.path = Path(path).expanduser()
        self.lock_path = self.path.with_name(self.path.name + ".lock")

    def exists(self) -> bool:
        return self.path.is_file()

    @contextmanager
    def locked(self) -> Iterator[None]:
        """
        Hold an exclusive cross-process lock.

        Wrap the read-refresh-write cycle in this so a second MCP process cannot
        refresh at the same time and invalidate the token this one just stored.
        """
        self._ensure_directory()
        try:
            with FileLock(str(self.lock_path), timeout=_LOCK_TIMEOUT_SECONDS):
                yield
        except Timeout as exc:
            raise TokenStoreError(
                f"Timed out after {_LOCK_TIMEOUT_SECONDS}s waiting for the token store "
                f"lock at {self.lock_path}. Another process may be stuck; remove the "
                "lock file if no other Zendesk MCP server is running."
            ) from exc

    def load(self) -> TokenSet:
        try:
            raw = self.path.read_text(encoding="utf-8")
        except FileNotFoundError as exc:
            raise TokenStoreError(
                f"No Zendesk OAuth tokens found at {self.path}. Run zendesk-auth to "
                "authorize this machine."
            ) from exc
        except OSError as exc:
            raise TokenStoreError(f"Could not read {self.path}: {exc}") from exc

        try:
            data = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise TokenStoreError(
                f"Token store at {self.path} is not valid JSON. Re-run zendesk-auth "
                "to recreate it."
            ) from exc

        return TokenSet.from_dict(data)

    def save(self, tokens: TokenSet) -> None:
        """Write tokens atomically, replacing any existing file."""
        self._ensure_directory()
        payload = json.dumps(tokens.to_dict(), indent=2, sort_keys=True) + "\n"

        # mkstemp creates the file 0600 and in the destination directory, so the
        # replace below is atomic and the secret is never briefly world-readable.
        handle, temp_name = tempfile.mkstemp(
            dir=str(self.path.parent), prefix=self.path.name + ".", suffix=".tmp"
        )
        temp_path = Path(temp_name)
        try:
            with os.fdopen(handle, "w", encoding="utf-8") as stream:
                stream.write(payload)
                stream.flush()
                os.fsync(stream.fileno())
            os.chmod(temp_path, _FILE_MODE)
            os.replace(temp_path, self.path)
        except OSError as exc:
            temp_path.unlink(missing_ok=True)
            raise TokenStoreError(f"Could not write {self.path}: {exc}") from exc

        logger.debug("Stored Zendesk OAuth tokens at %s", self.path)

    def _ensure_directory(self) -> None:
        directory = self.path.parent
        try:
            directory.mkdir(parents=True, exist_ok=True)
            if stat.S_IMODE(directory.stat().st_mode) != _DIR_MODE:
                os.chmod(directory, _DIR_MODE)
        except OSError as exc:
            raise TokenStoreError(f"Could not prepare {directory}: {exc}") from exc
