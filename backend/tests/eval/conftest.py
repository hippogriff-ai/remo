"""Eval test configuration — loads .env for API keys."""

from pathlib import Path

from dotenv import load_dotenv

# Load project .env so eval tests can access GOOGLE_AI_API_KEY, ANTHROPIC_API_KEY, etc.
_env_path = Path(__file__).parent.parent.parent.parent / ".env"
if _env_path.exists():
    load_dotenv(_env_path)
