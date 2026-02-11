# Continuity Ledger

## Goal
Create a refined, team-reviewed implementation plan for the Remo iOS app MVP. Output: `specs/PLAN_FINAL.md` + per-team sub-plans. DONE.

## Constraints/Assumptions
- iOS native app (SwiftUI-primary, iOS 17+)
- Hackathon MVP scope — ~12 calendar days with 4 teams (4-6 people)
- No auth (anonymous users)
- 4-team structure: Platform, iOS, Image Gen, AI Agents

## Key Decisions
- **4 teams** (consolidated from 6 in draft): T0 Platform, T1 iOS, T2 Image Gen, T3 AI Agents
- **Temporal Cloud** for workflow durability ($1K free credits, then $100/mo Essentials)
- **Gemini 3 Pro Image** for all image gen (with P0 quality spike; Gemini 2.5 Flash Image fallback)
- **Claude Opus 4.6** for intake agent and shopping scoring (raw Anthropic SDK, no agent harness)
- **Claude Haiku 4.5** for photo validation
- **Polling over SSE** for MVP (simpler, no Redis)
- **SPM local packages** for parallel iOS development
- **Contracts frozen at P0 exit gate** as hard deadline
- **Squash merge** to main; git worktrees per team
- **Annotation-based editing replaces lasso/mask inpainting** — users mark areas with numbered circles, Gemini edits targeted areas. Simpler iOS UX, eliminates mask generation pipeline. Google's intended interaction pattern (Gemini Markup tool).
- **Multi-turn Gemini chat for iteration** — generation is standalone (2 parallel calls), but all edits (annotation + text feedback) happen in a persistent Gemini chat session. Chat history (including thought signatures) serialized to R2 between Temporal activity calls.
- **Two T2 activities, not three** — `generate_designs` (standalone) + `edit_design` (multi-turn). Replaces generate_designs + generate_inpaint + generate_regen.
- **`gemini-3-pro-image-preview` likely required** — supports up to 14 input images (we need 5+ refs). Flash model limited to 3. P0 spike confirms.
- **Rubric-based scoring** for shopping list confidence
- **R2 lifecycle 120h** (not 72h) to prevent premature deletion
- **Photo validation synchronous** in API handler (not Temporal activity)
- **Railway PostgreSQL** (not Neon) — deploy on Railway
- **Phase-based timeline**: P0 Foundation → P1 Independent Build → P2 Integration → P3 Stabilization
- **Only 2 AI providers**: Anthropic + Google (eliminated OpenAI dependency)
- **Design intelligence framework for T3** — intake agent is a design translator (not information collector). Three-layer reasoning stack (Ching spatial → de Wolfe human-centered → Draper emotional), translation engine for vague→specific, DIAGNOSE pipeline for diagnostic probing. See `specs/DESIGN_INTELLIGENCE.md`.

## State
- Done: Product spec, all plans, T0 P0 #2-#8 + #10 (all P0 complete), P1 #11-#12, purge, worker, validation, migration, error handling hardened, validation.py silent failure fixes
- Done: **T0 code migration complete** — lasso/inpaint/regen → annotation-based edit system. All 13+ files updated (contracts, mock_stubs, worker, workflow, API routes, db, migration, 6 test files). PR self-review complete — added ValueError guards for unknown action types in workflow, mock API sets chat_history_key on revision. 320 tests pass, 0 warnings, ruff clean, format clean, mypy clean.
- Done: **T2 P0 #1 Gemini quality spike** — both models pass all 4 scenarios. Winner: `gemini-3-pro-image-preview` (14 input images, higher photorealism). Flash as fallback.
- Done: **T2 P0 #2 Model selection decision** — see `spike/results/MODEL_DECISION.md`.
- Done: **T2 P1 #3-#7 all complete** — annotation utility, chat manager, prompt templates, generate_designs activity, edit_design activity.
- Done: **T2 P2 #8 integration tests** — 6 real API tests pass (initial gen, detailed brief, quality check, annotation edit, clean output, chat round-trip).
- Done: **T2 code review fixes (2 rounds)** — 11 issues found and fixed: project_id extraction (was random UUID), user turn in chat continuation, deserialization validation, both annotations+feedback support, HTTP error handling (retryable vs non-retryable), content-type validation, shared serialization helper, R2 error handling, JSON corruption handling.
- Done: **T2 test coverage 100%** — 138 T2-specific tests (132 unit + 6 integration), 100% coverage across ALL T2 files. 460 total tests pass, ruff clean, format clean, mypy clean.
- Now: T2 complete, ready for PR.
- Next: T0 P2 #13 (wire real activities into workflow).

## Open Questions
- Gemini annotation targeting quality: **PASS with caveat** — both models leave annotation artifacts in output. Stronger prompting needed in edit.txt template.
- RoomPlan serialization format (P0 end)

## Working Set
- specs/PRODUCT_SPEC.md (input spec)
- specs/PLAN_FINAL.md (master plan v2.0)
- specs/PLAN_T0_PLATFORM.md (T0 sub-plan)
- specs/PLAN_T1_IOS.md (T1 sub-plan)
- specs/PLAN_T2_IMAGE_GEN.md (T2 sub-plan, rewritten for annotation-based editing)
- specs/PLAN_T3_AI_AGENTS.md (T3 sub-plan, updated with design intelligence framework)
- specs/DESIGN_INTELLIGENCE.md (design reasoning reference for T3 intake + shopping agents)
- specs/PROMPT_T1_IOS.md (T1 ralph loop prompt, gitignored)
- specs/PROMPT_T2_IMAGE_GEN.md (T2 ralph loop prompt, gitignored)
- specs/.planning/ (intermediate analysis files)
