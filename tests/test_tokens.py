"""
Tests for the OAuth token store.
"""
import json
import os
import stat
import subprocess
import sys
from datetime import datetime, timedelta, timezone

import pytest

from zendesk_mcp_server.tokens import (
    DEFAULT_EXPIRY_SKEW,
    TokenSet,
    TokenStore,
    TokenStoreError,
)

SUBDOMAIN = "example"
CLIENT_ID = "zendesk-mcp-client"
ACCESS_TOKEN = "access-token-value"
REFRESH_TOKEN = "refresh-token-value"


def make_tokens(**overrides) -> TokenSet:
    defaults = dict(
        access_token=ACCESS_TOKEN,
        subdomain=SUBDOMAIN,
        client_id=CLIENT_ID,
        refresh_token=REFRESH_TOKEN,
        expires_at=datetime.now(timezone.utc) + timedelta(minutes=30),
        refresh_token_expires_at=datetime.now(timezone.utc) + timedelta(days=90),
        scope="tickets:read",
    )
    defaults.update(overrides)
    return TokenSet(**defaults)


@pytest.fixture
def store(tmp_path):
    return TokenStore(tmp_path / "config" / "zendesk-mcp" / "tokens.json")


def test_round_trip_preserves_every_field(store):
    tokens = make_tokens()

    store.save(tokens)
    loaded = store.load()

    assert loaded.access_token == tokens.access_token
    assert loaded.refresh_token == tokens.refresh_token
    assert loaded.subdomain == SUBDOMAIN
    assert loaded.client_id == CLIENT_ID
    assert loaded.scope == "tickets:read"
    assert loaded.expires_at == tokens.expires_at
    assert loaded.refresh_token_expires_at == tokens.refresh_token_expires_at


def test_secrets_are_not_exposed_in_repr():
    rendered = repr(make_tokens())

    assert ACCESS_TOKEN not in rendered
    assert REFRESH_TOKEN not in rendered


def test_file_and_directory_permissions_are_restrictive(store):
    store.save(make_tokens())

    assert stat.S_IMODE(store.path.stat().st_mode) == 0o600
    assert stat.S_IMODE(store.path.parent.stat().st_mode) == 0o700


def test_save_replaces_previous_contents_without_leaving_temp_files(store):
    store.save(make_tokens())
    store.save(make_tokens(access_token="second-token"))

    assert store.load().access_token == "second-token"
    siblings = [p.name for p in store.path.parent.iterdir()]
    assert siblings == ["tokens.json"]


def test_load_without_a_file_tells_the_operator_what_to_run(store):
    with pytest.raises(TokenStoreError, match="Run zendesk-auth"):
        store.load()


def test_corrupt_json_is_reported_clearly(store):
    store.save(make_tokens())
    store.path.write_text("{not json", encoding="utf-8")

    with pytest.raises(TokenStoreError, match="not valid JSON"):
        store.load()


def test_missing_field_is_reported_clearly(store):
    store.save(make_tokens())
    data = json.loads(store.path.read_text(encoding="utf-8"))
    del data["client_id"]
    store.path.write_text(json.dumps(data), encoding="utf-8")

    with pytest.raises(TokenStoreError, match="client_id"):
        store.load()


def test_expiry_uses_a_skew_margin():
    inside_skew = DEFAULT_EXPIRY_SKEW - timedelta(seconds=5)
    nearly_expired = make_tokens(expires_at=datetime.now(timezone.utc) + inside_skew)
    fresh = make_tokens(
        expires_at=datetime.now(timezone.utc) + DEFAULT_EXPIRY_SKEW + timedelta(minutes=5)
    )

    assert nearly_expired.access_token_expired() is True
    assert fresh.access_token_expired() is False


def test_token_without_expiry_never_expires():
    """Legacy OAuth clients issue non-expiring access tokens and no refresh token."""
    legacy = make_tokens(expires_at=None, refresh_token=None, refresh_token_expires_at=None)

    assert legacy.access_token_expired() is False
    assert legacy.can_refresh() is False


def test_can_refresh_requires_a_live_refresh_token():
    usable = make_tokens()
    expired_refresh = make_tokens(
        refresh_token_expires_at=datetime.now(timezone.utc) - timedelta(days=1)
    )
    no_refresh = make_tokens(refresh_token=None)

    assert usable.can_refresh() is True
    assert expired_refresh.can_refresh() is False
    assert no_refresh.can_refresh() is False


def test_from_token_response_maps_lifetimes():
    issued_at = datetime(2026, 8, 21, 12, 0, tzinfo=timezone.utc)

    tokens = TokenSet.from_token_response(
        {
            "access_token": ACCESS_TOKEN,
            "refresh_token": REFRESH_TOKEN,
            "token_type": "bearer",
            "scope": "tickets:read",
            "expires_in": 1800,
            "refresh_token_expires_in": 7776000,
        },
        subdomain=SUBDOMAIN,
        client_id=CLIENT_ID,
        issued_at=issued_at,
    )

    assert tokens.expires_at == issued_at + timedelta(seconds=1800)
    assert tokens.refresh_token_expires_at == issued_at + timedelta(days=90)
    assert tokens.scope == "tickets:read"


def test_from_token_response_rejects_a_body_without_an_access_token():
    with pytest.raises(TokenStoreError, match="access_token"):
        TokenSet.from_token_response({}, subdomain=SUBDOMAIN, client_id=CLIENT_ID)


def test_tokens_survive_a_separate_process(store):
    store.save(make_tokens(access_token="written-here"))

    result = subprocess.run(
        [
            sys.executable,
            "-c",
            "import sys;"
            "from zendesk_mcp_server.tokens import TokenStore;"
            "print(TokenStore(sys.argv[1]).load().access_token)",
            str(store.path),
        ],
        capture_output=True,
        text=True,
        check=True,
    )

    assert result.stdout.strip() == "written-here"


def test_lock_is_held_against_another_process(store):
    """A second process must wait rather than write concurrently."""
    store.save(make_tokens())
    probe = (
        "import sys;"
        "from filelock import FileLock, Timeout;"
        "lock = FileLock(sys.argv[1], timeout=0.2);"
        "\ntry:\n"
        "    lock.acquire()\n"
        "    print('acquired')\n"
        "except Timeout:\n"
        "    print('blocked')\n"
    )

    with store.locked():
        result = subprocess.run(
            [sys.executable, "-c", probe, str(store.lock_path)],
            capture_output=True,
            text=True,
            check=True,
        )

    assert result.stdout.strip() == "blocked"


def test_lock_is_released_after_the_block(store):
    with store.locked():
        pass

    probe = (
        "import sys;"
        "from filelock import FileLock, Timeout;"
        "lock = FileLock(sys.argv[1], timeout=0.2);"
        "\ntry:\n"
        "    lock.acquire()\n"
        "    print('acquired')\n"
        "except Timeout:\n"
        "    print('blocked')\n"
    )
    result = subprocess.run(
        [sys.executable, "-c", probe, str(store.lock_path)],
        capture_output=True,
        text=True,
        check=True,
    )

    assert result.stdout.strip() == "acquired"


def test_partial_write_cannot_be_observed(store, monkeypatch):
    """A failure mid-write must leave the previous file intact."""
    store.save(make_tokens(access_token="original"))

    def explode(*args, **kwargs):
        raise OSError("disk full")

    monkeypatch.setattr(os, "replace", explode)

    with pytest.raises(TokenStoreError, match="disk full"):
        store.save(make_tokens(access_token="never-lands"))

    assert store.load().access_token == "original"
    assert [p.name for p in store.path.parent.iterdir()] == ["tokens.json"]
