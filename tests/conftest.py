"""
Shared test setup.

Both entry points read a ``.env`` file, which can pull a developer's real
credentials and subdomain into a test run. Every test is isolated from that two
ways: Zendesk environment variables are cleared, and the working directory is
moved somewhere without a ``.env``.
"""
import os

import pytest

ZENDESK_ENV_PREFIX = "ZENDESK_"


@pytest.fixture(autouse=True)
def isolated_zendesk_env(monkeypatch, tmp_path_factory):
    for name in [k for k in os.environ if k.startswith(ZENDESK_ENV_PREFIX)]:
        monkeypatch.delenv(name, raising=False)
    monkeypatch.chdir(tmp_path_factory.mktemp("cwd"))
