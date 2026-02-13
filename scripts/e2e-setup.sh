#!/usr/bin/env bash
# E2E infrastructure setup — starts Docker services and runs DB migration.
#
# Usage:
#   ./scripts/e2e-setup.sh          # mock activities (default)
#   ./scripts/e2e-setup.sh --real   # real AI activities (needs API keys in .env)
#
# Prerequisites:
#   - Docker and docker compose installed
#   - Python venv at backend/.venv with app installed
#   - For --real mode: .env file with API keys at repo root

set -euo pipefail
cd "$(dirname "$0")/.."

MODE="${1:-mock}"

echo "==> Starting Docker services..."
docker compose up -d

echo "==> Waiting for PostgreSQL..."
until docker compose exec -T postgres pg_isready -U remo -q 2>/dev/null; do
    sleep 1
done
echo "    PostgreSQL ready."

echo "==> Waiting for Temporal..."
until docker compose exec -T temporal temporal operator cluster health 2>/dev/null | grep -q SERVING; do
    sleep 2
done
echo "    Temporal ready."

echo "==> Running Alembic migration..."
cd backend
DATABASE_URL="postgresql+asyncpg://remo:remo_dev@localhost:5432/remo" \
    .venv/bin/python -m alembic upgrade head
cd ..

echo "==> Infrastructure ready."
echo ""
echo "Services:"
echo "  PostgreSQL:   localhost:5432  (remo/remo_dev)"
echo "  Temporal:     localhost:7233"
echo "  Temporal UI:  http://localhost:8233"
echo ""

if [ "$MODE" = "--real" ]; then
    echo "Mode: REAL activities (USE_MOCK_ACTIVITIES=false)"
    echo "  Ensure API keys are set in .env"
    echo ""
    echo "Run API server:"
    echo "  cd backend && USE_TEMPORAL=true USE_MOCK_ACTIVITIES=false .venv/bin/python -m uvicorn app.main:app --reload"
    echo ""
    echo "Run Worker:"
    echo "  cd backend && USE_MOCK_ACTIVITIES=false .venv/bin/python -m app.worker"
else
    echo "Mode: MOCK activities (USE_MOCK_ACTIVITIES=true)"
    echo ""
    echo "Run API server:"
    echo "  cd backend && USE_TEMPORAL=true .venv/bin/python -m uvicorn app.main:app --reload"
    echo ""
    echo "Run Worker:"
    echo "  cd backend && .venv/bin/python -m app.worker"
fi
echo ""
echo "Run E2E tests:"
echo "  cd backend && .venv/bin/python -m pytest tests/test_e2e.py -x -v"
