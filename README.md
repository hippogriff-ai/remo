# Remo

AI-powered room redesign iOS app. Photograph a room, describe your style via an AI chat, receive photorealistic redesign options, iteratively refine with annotation-based editing, and get a shoppable product list.

## Prerequisites

- **Docker & Docker Compose** (PostgreSQL + Temporal)
- **Python 3.12+**
- **Xcode 15+** / Swift 5.9+ (iOS development)

## Quick Start

### 1. Environment

```bash
cp .env.example .env
# Fill in API keys (at minimum ANTHROPIC_API_KEY for local dev with mocks)
```

Key variables:

| Variable | Purpose | Required |
|----------|---------|----------|
| `ANTHROPIC_API_KEY` | Claude (intake agent, photo validation) | Yes |
| `GOOGLE_AI_API_KEY` | Gemini (image generation) | For real image gen |
| `EXA_API_KEY` | Product search (shopping list) | For real shopping |
| `R2_ACCOUNT_ID` / `R2_ACCESS_KEY_ID` / `R2_SECRET_ACCESS_KEY` | Cloudflare R2 (image storage) | For real storage |
| `USE_TEMPORAL` | Enable Temporal workflows (`false` = mock mode) | No (default: `false`) |
| `USE_MOCK_ACTIVITIES` | Stub AI calls (`true` = fast dev) | No (default: `true`) |

### 2. Infrastructure

```bash
docker compose up -d    # PostgreSQL (5432) + Temporal (7233) + Temporal UI (8233)
```

### 3. Backend

```bash
cd backend
python -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"

# Run migrations
DATABASE_URL="postgresql+asyncpg://remo:remo_dev@localhost:5432/remo" \
    python -m alembic upgrade head

# Start API server (port 8000)
python -m uvicorn app.main:app --reload

# Start Temporal worker (separate terminal)
python -m app.worker
```

For real AI activities (not mocks):

```bash
USE_TEMPORAL=true USE_MOCK_ACTIVITIES=false python -m uvicorn app.main:app --reload
USE_MOCK_ACTIVITIES=false python -m app.worker
```

Verify: `curl http://localhost:8000/health`

### 4. iOS

```bash
# Generate Xcode project
cd ios && xcodegen generate
```

Create `ios/local.xcconfig` (gitignored):

```
DEVELOPMENT_TEAM = YOUR_TEAM_ID
BACKEND_URL = http:/$()/localhost:8001
```

For device testing, use your Mac's local IP instead of `localhost`:

```
BACKEND_URL = http:/$()/192.168.1.XXX:8001
```

Open `ios/Remo.xcodeproj` in Xcode, select your target device, and run.

## Running Tests

### Backend

```bash
cd backend

# All tests
.venv/bin/python -m pytest -x -q

# Specific file
.venv/bin/python -m pytest tests/test_workflow.py -x

# Single test, verbose
.venv/bin/python -m pytest -k "test_name" -xvs

# With coverage
.venv/bin/python -m pytest --cov=app --cov-report=term-missing
```

### Linting & Types

```bash
cd backend
.venv/bin/python -m ruff check .
.venv/bin/python -m ruff format --check .
.venv/bin/python -m mypy app/
```

### iOS (Swift Package Tests)

```bash
swift test --package-path ios/Packages/RemoModels        # 92 tests
swift test --package-path ios/Packages/RemoNetworking    # 47 tests
swift test --package-path ios/Packages/RemoAnnotation    # 10 tests
```

### Maestro UI Tests

Requires a running simulator with the app installed:

```bash
maestro test ios/.maestro/flows/happy-path.yaml          # Full happy path
maestro test ios/.maestro/flows/03-intake-chat.yaml      # Single subflow
```

## Architecture

```
iOS (SwiftUI) ──HTTPS──▶ FastAPI Gateway ──signals/queries──▶ Temporal Workflow
                              │                                      │
                              │                                 Activities
                              │                              (Claude, Gemini,
                              ▼                               Exa, R2)
                         PostgreSQL
```

- **FastAPI** is a thin proxy; never calls AI APIs directly (except sync photo validation)
- **Temporal** owns all workflow state; activities are stateless
- iOS polls `GET /projects/{id}` for state changes + SSE for streaming (intake chat, shopping list)

### iOS Packages

| Package | Purpose |
|---------|---------|
| RemoModels | Shared Pydantic-mirrored Swift models |
| RemoNetworking | API client, SSE parsers, polling |
| RemoPhotoUpload | Camera, photo grid, upload |
| RemoChatUI | Intake conversation UI |
| RemoAnnotation | PencilKit annotation editor |
| RemoDesignViews | Generation, selection, iteration, shopping screens |
| RemoShoppingList | Product cards, shopping list display |
| RemoLiDAR | RoomPlan scanning, ARKit integration |

## Useful URLs (Local)

| Service | URL |
|---------|-----|
| API | http://localhost:8000 |
| API docs | http://localhost:8000/docs |
| Health check | http://localhost:8000/health |
| Temporal UI | http://localhost:8233 |

## License

[MIT](LICENSE)
