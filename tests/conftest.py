import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))

# Set DSNs before importing any application module. The production dotenv
# loader uses setdefault semantics, so this prevents tests from touching the
# developer's live databases.
os.environ.setdefault("DATABASE_URL_VIZ", "postgresql:///ghpulse_test")
os.environ.setdefault("DATABASE_URL_AUTH", "postgresql:///auth_test")
os.environ.setdefault("ADMIN_TOKEN", "test-admin")
os.environ.setdefault("COOKIE_SECURE", "0")

from backend import cache  # noqa: E402


@pytest.fixture(autouse=True)
def _reset_response_cache():
    # response_cache is a process-global. Two tests with different fixtures
    # but identical query params would otherwise read each other's payloads.
    cache.response_cache.clear()
    yield
    cache.response_cache.clear()
