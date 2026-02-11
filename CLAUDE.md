# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project

Remo is an AI-powered room redesign iOS app. Users photograph a room, describe their style via an AI chat, receive photorealistic redesign options, iteratively refine them with lasso-based inpainting, and get a shoppable product list.

## Status

Pre-implementation. Only specs exist. See `CONTINUITY.md` for current state.

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
| CI | GitHub Actions → Railway auto-deploy |

## Planned Repository Structure

```
backend/
  app/
    models/contracts.py    # ALL Pydantic models (T0 owns exclusively)
    models/db.py           # SQLAlchemy models
    api/routes/            # FastAPI endpoints
    workflows/design_project.py  # Temporal workflow
    activities/            # One file per activity (generate, inpaint, regen, intake, shopping, validation, purge)
    utils/                 # R2 client, image processing
    prompts/               # Versioned prompt templates
  migrations/              # Alembic
  tests/
ios/
  Remo.xcodeproj
  Packages/                # Local SPM packages (RemoModels, RemoNetworking, RemoPhotoUpload, RemoChatUI, RemoLasso, RemoDesignViews, RemoShoppingList, RemoLiDAR)
```

## Team Structure & File Ownership

4 teams work in parallel via git worktrees. **File ownership is strict** — only the owning team modifies their files.

| Team | Worktree | Branch prefix | Owns |
|------|----------|---------------|------|
| T0: Platform | `/Hanalei/remo` (main) | `team/platform/*` | contracts, DB, API, workflow, CI, validation, purge, R2 |
| T1: iOS | `/Hanalei/remo-ios` | `team/ios/*` | All `ios/` and `Packages/` |
| T2: Image Gen | `/Hanalei/remo-gen` | `team/gen/*` | `activities/{generate,inpaint,regen}.py`, image utils, gen prompts |
| T3: AI Agents | `/Hanalei/remo-ai` | `team/ai/*` | `activities/{intake,shopping}.py`, AI prompts |

## Specs & Plans

| File | Purpose |
|------|---------|
| `specs/PRODUCT_SPEC.md` | Product requirements (source of truth for features) |
| `specs/PLAN_FINAL.md` | Master implementation plan (architecture, contracts, phases, all teams) |
| `specs/PLAN_T0_PLATFORM.md` | T0 sub-plan |
| `specs/PLAN_T1_IOS.md` | T1 sub-plan |
| `specs/PLAN_T2_IMAGE_GEN.md` | T2 sub-plan |
| `specs/PLAN_T3_AI_AGENTS.md` | T3 sub-plan |

## Key Contracts

All Pydantic models live in `backend/app/models/contracts.py` (T0 owns exclusively, frozen at P0 exit gate). Activity contracts follow the pattern `{Action}Input` / `{Action}Output`. The workflow exposes state via `WorkflowState` query. iOS Swift models mirror the Pydantic models exactly.

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
