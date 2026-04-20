import os

# Force test-safe defaults regardless of .env — must precede all app imports.
# Without this, .env values (USE_TEMPORAL=true, USE_MOCK_ACTIVITIES=false)
# cause tests to fail by attempting real Temporal connections and loading
# real activity modules that need API keys.
os.environ["USE_TEMPORAL"] = "false"
os.environ["USE_MOCK_ACTIVITIES"] = "true"

import pytest
from httpx import ASGITransport, AsyncClient

from app.main import app


@pytest.fixture
async def client():
    """Async test client for FastAPI integration tests."""
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        yield ac
