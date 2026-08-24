"""
Pluggable authentication for the Zendesk API.

Zendesk is retiring API tokens as an authentication method: unused tokens start
being deactivated on 2026-07-28, no new tokens can be created after 2026-10-27,
and all API tokens stop working on 2027-04-30. OAuth is the replacement.

Both mechanisms are expressed here as ``AuthProvider`` implementations so the
rest of the code never has to care which one is in use. A provider is a
``requests`` auth callable, which is what makes rotating credentials work: the
callable runs on every request, so an expiring OAuth token can be refreshed
transparently rather than being baked into a header once at construction time.
"""
from __future__ import annotations

import base64
from typing import Protocol, runtime_checkable

from requests.auth import AuthBase


@runtime_checkable
class AuthProvider(Protocol):
    """
    Supplies an ``Authorization`` header value for Zendesk API requests.

    Implementations are also ``requests`` auth callables so they can be attached
    to a session and applied per request.
    """

    def auth_header(self) -> str:
        """
        Return the current ``Authorization`` header value.

        Implementations that hold expiring credentials refresh them here, so
        callers can rely on the returned value being usable right now.
        """
        ...

    def __call__(self, request):  # pragma: no cover - protocol definition
        ...


class _HeaderAuthProvider(AuthBase):
    """Applies whatever ``auth_header()`` returns to an outgoing request."""

    def auth_header(self) -> str:
        raise NotImplementedError

    def __call__(self, request):
        request.headers["Authorization"] = self.auth_header()
        return request


class ApiTokenAuthProvider(_HeaderAuthProvider):
    """
    Basic authentication with an email address and a Zendesk API token.

    Deprecated: Zendesk deactivates all API tokens on 2027-04-30. Use
    ``OAuthAuthProvider`` instead.
    """

    def __init__(self, email: str, token: str):
        if not email or not token:
            raise ValueError(
                "API token authentication requires both an email address and an API token."
            )
        self._header = "Basic " + base64.b64encode(
            f"{email}/token:{token}".encode()
        ).decode("ascii")

    def auth_header(self) -> str:
        return self._header
