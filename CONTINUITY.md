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
- **Lasso MVP (1 region)** first, multi-region in Phase 2
- **Rubric-based scoring** for shopping list confidence
- **R2 lifecycle 120h** (not 72h) to prevent premature deletion
- **Photo validation synchronous** in API handler (not Temporal activity)
- **Railway PostgreSQL** (not Neon) — deploy on Railway
- **Phase-based timeline**: P0 Foundation → P1 Independent Build → P2 Integration → P3 Stabilization
- **Only 2 AI providers**: Anthropic + Google (eliminated OpenAI dependency)

## State
- Done: Product spec, draft plan (v1.0), 5 specialist analyses, final plan (v2.0), all refinements, per-team sub-plans
- Now: Complete — all planning artifacts written
- Next: Implementation (when requested)

## Open Questions
- Gemini mask quality pass/fail? (P0 end)
- RoomPlan serialization format (P0 end)

## Working Set
- specs/PRODUCT_SPEC.md (input spec)
- specs/PLAN_0210.md (draft plan v1.0)
- specs/PLAN_FINAL.md (master plan v2.0)
- specs/PLAN_T0_PLATFORM.md (T0 sub-plan, 897 lines)
- specs/PLAN_T1_IOS.md (T1 sub-plan, 780 lines)
- specs/PLAN_T2_IMAGE_GEN.md (T2 sub-plan, 586 lines)
- specs/PLAN_T3_AI_AGENTS.md (T3 sub-plan, 747 lines)
- specs/.planning/ (intermediate analysis files)
