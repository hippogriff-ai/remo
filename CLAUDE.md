# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project

Remo is an AI-powered room redesign iOS app. Users photograph a room, describe their style via an AI chat, receive photorealistic redesign options, iteratively refine them with annotation-based editing, and get a shoppable product list.

## Status

T0 (Platform) P0+P1 complete. Backend fully scaffolded with 301 passing tests, 0 warnings, ruff clean, ruff format clean, mypy clean. P2 (integration) blocked on T2/T3 activity implementations. See `CONTINUITY.md` for current state.

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
| Image gen | Gemini 3 Pro Image or Gemini 2.5 Flash Image (P0 spike picks winner) |
| AI chat/scoring | Claude Opus 4.6 via raw `anthropic` SDK with tool use (no framework) |
| Photo validation | Claude Haiku 4.5 (sync in API handler) |
| Product search | Exa API |
| Storage | Cloudflare R2 (images), Railway PostgreSQL (metadata) |
| Hosting | Railway (2 services: API + Worker), Temporal Cloud |
| CI | GitHub Actions (2-job: lint→test, pip cache, coverage) → Railway auto-deploy |

## Repository Structure

```
backend/
  app/
    models/contracts.py        # ALL Pydantic models (T0 owns exclusively, frozen)
    models/db.py               # SQLAlchemy models (9 tables)
    api/routes/projects.py     # FastAPI endpoints (17 endpoints, mock state store, multi-step intake conversation)
    api/routes/health.py       # Health check endpoint (version, environment, service status)
    workflows/design_project.py # Temporal workflow (12 signals, 1 query)
    activities/mock_stubs.py   # Mock activities (T0-owned, swapped in P2)
    activities/validation.py   # Photo validation (Pillow + Claude Haiku 4.5)
    activities/purge.py        # R2 cleanup activity
    utils/r2.py                # Cloudflare R2 client
    utils/lidar.py             # RoomPlan JSON → RoomDimensions parser
    logging.py                 # Shared structlog config
    config.py                  # pydantic-settings env vars
    main.py                    # FastAPI app (request ID middleware, error handlers)
    worker.py                  # Temporal worker entrypoint
  migrations/versions/         # Alembic (001_initial_schema.py)
  tests/                       # 301 tests across 11 test files (module-scoped Temporal fixture)
ios/                           # (T1-owned, not yet scaffolded in this worktree)
```

## Team Structure & File Ownership

4 teams work in parallel via git worktrees. **File ownership is strict** — only the owning team modifies their files.

| Team | Worktree | Branch prefix | Owns |
|------|----------|---------------|------|
| T0: Platform | `/Hanalei/remo` (main) | `team/platform/*` | contracts, DB, API, workflow, CI, validation, purge, R2 |
| T1: iOS | `/Hanalei/remo-ios` | `team/ios/*` | All `ios/` and `Packages/` |
| T2: Image Gen | `/Hanalei/remo-gen` | `team/gen/*` | `activities/{generate,edit}.py`, image utils, gemini chat, gen prompts |
| T3: AI Agents | `/Hanalei/remo-ai` | `team/ai/*` | `activities/{intake,shopping}.py`, AI prompts |

## Specs & Plans

Plans in `specs/` (tracked). Agent prompts in `specs/PROMPT_*.md` (gitignored — not open-sourced).

| File | Purpose |
|------|---------|
| `specs/PRODUCT_SPEC.md` | Product requirements (source of truth for features) |
| `specs/PLAN_FINAL.md` | Master implementation plan (architecture, contracts, phases, all teams) |
| `specs/PLAN_T0_PLATFORM.md` | T0 sub-plan |
| `specs/PLAN_T1_IOS.md` | T1 sub-plan |
| `specs/PLAN_T2_IMAGE_GEN.md` | T2 sub-plan |
| `specs/PLAN_T3_AI_AGENTS.md` | T3 sub-plan |
| `specs/DESIGN_INTELLIGENCE.md` | Design reasoning reference for T3 intake + shopping agents |
| `specs/PROMPT_T1_IOS.md` | T1 ralph loop prompt (gitignored) |
| `specs/PROMPT_T2_IMAGE_GEN.md` | T2 ralph loop prompt (gitignored) |

## Key Contracts

All Pydantic models live in `backend/app/models/contracts.py` (T0 owns exclusively, frozen at P0 exit gate). Activity contracts follow the pattern `{Action}Input` / `{Action}Output`. The workflow exposes state via `WorkflowState` query. iOS Swift models mirror the Pydantic models exactly.

## Mock API Behavior

The mock API (pre-P2) uses in-memory state stores. Key behaviors:
- **Intake conversation**: 3-step flow tracking user messages per project. Step 1: room type → style options. Step 2: style → open-ended preferences. Step 3+: summary with partial `DesignBrief`. Conversation state resets on `start_over` and `delete`.
- **Photo upload**: Runs real `validate_photo` (Pillow checks) synchronously. Auto-transitions to `scan` step after 2+ valid photos.
- **Iteration**: `_apply_revision` caps at 5 rounds then forces `approval` step.

## Error Handling Convention

All errors return `ErrorResponse` JSON (`{"error": str, "message": str, "retryable": bool}`). This includes 404 (not found), 409 (wrong step), 413 (file too large), 422 (validation + invalid scan/selection), and 500 (unhandled). Custom exception handlers normalize Pydantic validation errors and unhandled exceptions to the same shape. Every response includes an `X-Request-ID` header for log correlation.

## Build Phases

- **P0 (Foundation)**: Contracts, scaffold, infra, Gemini quality spike. Gate: contracts frozen + mock API works.
- **P1 (Independent Build)**: All teams build in parallel against contracts/mocks. No cross-team deps.
- **P2 (Integration)**: Wire real activities incrementally. T0 leads.
- **P3 (Stabilization)**: Bug fixes, resume testing, demo prep.

## Git Conventions

- Squash merge to `main`
- `main` requires 1 approval + passing CI
- Contract changes go through T0 exclusively
- PR size: 200-400 lines, single-purpose
