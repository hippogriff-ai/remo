"""Eval test configuration.

Provides a fixture to load .env for integration tests that need API keys.
NOT loaded at module level to prevent LANGSMITH_API_KEY and other .env
values from leaking into the unit-test session (which causes wrap_anthropic
and trace_thread to interfere with mock clients).
"""

from pathlib import Path

import pytest


@pytest.fixture(scope="module")
def load_env():
    """Load project .env into os.environ for integration tests.

    Usage: add ``load_env`` to the test function signature (or use
    ``@pytest.mark.usefixtures("load_env")``).  Not autouse — only
    tests that explicitly request it get .env values.
    """
    from dotenv import load_dotenv

    env_path = Path(__file__).parent.parent.parent.parent / ".env"
    if env_path.exists():
        load_dotenv(env_path)
