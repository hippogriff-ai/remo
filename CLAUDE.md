# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project

Remo is an AI-powered room redesign iOS app. Users photograph a room, describe their style via an AI chat, receive photorealistic redesign options, iteratively refine them with annotation-based editing, and get a shoppable product list.

## Status

P0–P2 complete. Full pipeline verified end-to-end with real AI (Claude Opus, Gemini 3 Pro, Exa). Golden path test: 216s. LLM response caching for dev/test.

## Development Commands

```bash
cd backend

# Install (editable + dev deps)
pip install -e ".[dev]"

# Run tests
.venv/bin/python -m pytest -x -q                    # all tests
.venv/bin/python -m pytest tests/test_workflow.py -x  # workflow only
.venv/bin/python -m pytest -k "test_name" -xvs        # single test, verbose
.venv/bin/python -m pytest --cov=app --cov-report=term-missing  # with coverage

# Lint + type check + format
.venv/bin/python -m ruff check .
.venv/bin/python -m ruff format --check .             # format check (CI enforces)
.venv/bin/python -m mypy app/

# Run API server (local)
docker compose up -d  # PostgreSQL + Temporal
.venv/bin/python -m uvicorn app.main:app --reload

# Run Temporal worker (local)
.venv/bin/python -m app.worker

# Database migrations
.venv/bin/python -m alembic upgrade head
```

### iOS

```bash
# Swift unit tests (run from repo root)
swift test --package-path ios/Packages/RemoModels        # 54 tests
swift test --package-path ios/Packages/RemoNetworking    # 35 tests
swift test --package-path ios/Packages/RemoAnnotation    # 10 tests

# Generate Xcode project (after changing project.yml)
cd ios && xcodegen generate

# Build for simulator
xcodebuild -project ios/Remo.xcodeproj -scheme Remo -configuration Debug \
  -destination 'platform=iOS Simulator,name=iPhone 16 Pro'

# Maestro UI tests (requires simulator running with app installed)
maestro test ios/.maestro/flows/happy-path.yaml          # full happy path (8 subflows)
maestro test ios/.maestro/flows/03-intake-chat.yaml      # single subflow
```

After editing iOS files, run Swift unit tests to verify. Run Maestro happy path after UI changes to catch regressions.

## Architecture

- **iOS app** (SwiftUI, iOS 17+) communicates via HTTPS to a **FastAPI gateway**
- FastAPI is a thin proxy to **Temporal** (Python SDK `temporalio`) which owns all workflow state
- **Temporal Worker** (separate Railway service) runs activities that call external AI APIs
- iOS polls `GET /projects/{id}` for state changes (no SSE/WebSocket for MVP)

Key rule: API layer never calls AI APIs (except sync photo validation). Workflow layer never does I/O. Activities are stateless.

## Tech Stack

| Layer | Tech |
|-------|------|
| iOS | SwiftUI (iOS 17+), local SPM packages, `@Observable` |
| Backend | Python 3.12+, FastAPI, Temporal (`temporalio`) |
| Image gen | Gemini 3 Pro Image (`gemini-3-pro-image-preview`, configurable via `GEMINI_MODEL`) |
| AI chat/scoring | Claude Opus 4.6 via raw `anthropic` SDK with tool use (no framework) |
| Photo validation | Claude Haiku 4.5 (sync in API handler) |
| Product search | Exa API |
| Storage: PostgreSQL | Railway PostgreSQL — projects, briefs, products (9 tables, every artifact persisted) |
| Storage: R2 | Cloudflare R2 — photos, designs, scans (S3-compatible, presigned URLs) |
| Hosting | Railway (2 services: API + Worker), Temporal Cloud |
| CI | GitHub Actions (2-job: lint→test, pip cache, coverage) → Railway auto-deploy |

## Repository Structure

```
backend/
  app/
    models/contracts.py        # ALL Pydantic models (T0 owns exclusively, frozen)
    models/db.py               # SQLAlchemy models (9 tables)
    api/routes/projects.py     # FastAPI endpoints (mock state store, multi-step intake conversation, real intake agent wiring)
    api/routes/health.py       # Health check endpoint (version, environment, service status)
    workflows/design_project.py # Temporal workflow (17 signals, 1 query)
    activities/mock_stubs.py   # Mock activity stubs (dev/test fallback)
    activities/validation.py   # Photo validation (Pillow + Claude Haiku 4.5)
    activities/purge.py        # R2 cleanup activity
    utils/r2.py                # Cloudflare R2 client
    utils/lidar.py             # RoomPlan JSON → RoomDimensions parser
    utils/image_eval.py        # Fast eval layer (CLIP/SSIM/artifacts, $0)
    utils/score_tracking.py    # Eval score JSONL history + regression detection
    utils/prompt_versioning.py # Versioned prompt loading for A/B testing
    activities/design_eval.py  # Deep eval layer (Claude Vision judge, ~$0.02)
    logging.py                 # Shared structlog config
    config.py                  # pydantic-settings env vars (incl. EVAL_MODE)
    main.py                    # FastAPI app (request ID middleware, error handlers)
    worker.py                  # Temporal worker entrypoint
  migrations/versions/         # Alembic (001_initial_schema.py)
  tests/                       # pytest suite (module-scoped Temporal fixture)
ios/
  Remo/                        # App target (HomeScreen, ProjectFlow, Router)
  Packages/
    RemoModels/                # Models, ProjectState, ProjectStep, contracts
    RemoNetworking/            # RealWorkflowClient, MockWorkflowClient, SSE parsers
    RemoPhotoUpload/           # PhotoUploadScreen, CameraView
    RemoChatUI/                # IntakeChatScreen (SSE streaming)
    RemoDesignViews/           # Selection, Generating, Analyzing, Approval, Output
    RemoAnnotation/            # IterationScreen, AnnotationCanvas
    RemoShoppingList/          # ShoppingListScreen, ProductCard
    RemoLiDAR/                 # LiDARScanScreen (RoomPlan)
```

## Code Organization

All code lives in a single repo. Backend owns contracts, API, workflow, infra. iOS owns all `ios/` and `Packages/`. Activities are organized by domain: `generate.py`, `edit.py` (Gemini), `intake.py`, `shopping.py` (Claude + Exa), `analyze_room.py` (Claude), `validation.py` (Haiku), `purge.py` (R2).

## Specs & Docs

Reference docs in `specs/` (tracked). Agent prompts in `specs/PROMPT_*.md` (gitignored).

| File | Purpose |
|------|---------|
| `specs/PRODUCT_SPEC.md` | Product requirements (source of truth for features) |
| `specs/ARCHITECTURE.md` | System architecture diagrams (Mermaid): workflow state machine, API map, iOS navigation, data flows |
| `specs/ARCHITECTURE_AGENT_WORKFLOW.md` | Agent pipeline architecture: eager analysis, intake agent, shopping pipeline, room intelligence |
| `specs/DESIGN_INTELLIGENCE.md` | Design reasoning reference for intake + shopping agents |
| `specs/RESEARCH_GEMINI_PROMPTING.md` | Gemini 3 Pro Image prompt engineering research (quality eval, optimization) |
| `docs/EVAL_PIPELINE.md` | Eval pipeline guide (setup, usage, metrics, rubrics) |

## Key Contracts

All Pydantic models live in `backend/app/models/contracts.py` (T0 owns exclusively, frozen at P0 exit gate). Activity contracts follow the pattern `{Action}Input` / `{Action}Output`. The workflow exposes state via `WorkflowState` query. iOS Swift models mirror the Pydantic models exactly.

## Error Handling Convention

All errors return `ErrorResponse` JSON (`{"error": str, "message": str, "retryable": bool}`). This includes 404 (not found), 409 (wrong step), 413 (file too large), 422 (validation + invalid scan/selection), and 500 (unhandled). Custom exception handlers normalize Pydantic validation errors and unhandled exceptions to the same shape. Every response includes an `X-Request-ID` header for log correlation.

## Git Conventions

- Squash merge to `main`
- `main` requires 1 approval + passing CI
- Contract changes go through T0 exclusively
- PR size: 200-400 lines, single-purpose
