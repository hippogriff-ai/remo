# Future Technical Enhancements

Tracked improvements that are architecturally sound but not needed for current single-instance deployment.

---

## 1. Move Intake Session State into Temporal Workflow

**Priority**: Required before horizontal API scaling or if Railway rolling restarts cause issues
**Effort**: Medium (touches API routes, workflow, and contracts)
**Source**: PR #7 review

### Problem

The intake conversation state (`mode`, `history`, `last_partial_brief`) lives in `_intake_sessions`, a process-local dict in `projects.py`. When `USE_TEMPORAL=true`:

- API restart mid-conversation loses all turns (user gets 409 "Call start_intake first")
- Multiple API replicas can't share sessions (load balancer may route to wrong instance)
- The Temporal workflow only knows `step=intake` but has no visibility into conversation progress

The workflow receives the final `DesignBrief` via `complete_intake` signal, but the multi-turn conversation itself (history accumulation, partial briefs, mode tracking) happens entirely outside Temporal.

### Current Flow

```
iOS → POST /intake/start     → API stores _IntakeSession in memory
iOS → POST /intake/message    → API calls _run_intake_core() directly (not via Temporal)
                              → history accumulates in _IntakeSession
                              → on summary: API signals workflow.complete_intake(brief)
```

### Why It Works Today

- Railway runs a single API instance (no replica routing issues)
- Intake conversations are short (3-15 turns, ~2 minutes)
- API restarts are infrequent in practice

### Proposed Fix: Option A — Temporal Activity per Turn

Route each intake turn through the workflow as a Temporal activity:

```
iOS → POST /intake/start     → API signals workflow.start_intake(mode)
iOS → POST /intake/message    → API signals workflow.intake_message(text)
                              → Workflow runs intake_chat activity
                              → Workflow stores history + partial brief in its state
                              → iOS polls GET /projects/{id} for response
```

**Pros**: Full durability, workflow owns all state, survives restarts
**Cons**: Adds ~200ms latency per turn (Temporal round-trip), polling delay for response, requires WorkflowState schema changes

### Proposed Fix: Option B — Database-backed Sessions

Persist `_IntakeSession` to PostgreSQL instead of in-memory:

```
iOS → POST /intake/start     → API writes session to DB
iOS → POST /intake/message    → API reads session from DB, calls agent, writes back
                              → on summary: API signals workflow.complete_intake(brief)
```

**Pros**: Survives restarts, works with replicas, minimal architecture change
**Cons**: Adds DB round-trip per turn, session cleanup needed, still bypasses Temporal for conversation

### Recommendation

Option B for P3 (simpler, lower risk). Option A for P4+ if we want full workflow observability over intake conversations.

---

## 2. Error Injection via Temporal Signal (Replace /tmp Sentinel)

**Priority**: Low (only affects E2E tests, not production)
**Effort**: Small
**Source**: PR #7 review

### Problem

The `POST /debug/force-failure` endpoint arms failure by touching `/tmp/remo-force-failure`. This only works when API and worker share a filesystem (colocated processes). In containerized deployments with separate API/worker services, the signal never reaches the worker.

### Current Scope

This is gated behind `use_mock_activities=True` and `environment=development`, so it only affects E2E tests run locally. Not a production concern.

### Proposed Fix

Replace the file sentinel with a Temporal signal:
- API sends `force_failure` signal to the workflow
- Workflow sets a flag, passes it to the next activity call
- Activity checks the flag and raises `ApplicationError`

This would work across process boundaries but adds complexity to the workflow for a test-only feature.

---

## 3. Intake Lifestyle Field Population

**Priority**: Low (cosmetic — data flows correctly via occupants merge)
**Effort**: Small (T3-owned)
**Source**: Code audit

### Problem

`DesignBrief` has a dedicated `lifestyle` field (added in Phase 1a), but `intake.py:build_brief()` still merges lifestyle into `occupants` for downstream compatibility (`generate.py` reads `brief.occupants`). The `lifestyle` field on the returned `DesignBrief` is always `None`.

### Proposed Fix

Set both fields in `build_brief()`:
```python
return DesignBrief(
    ...
    occupants=merged_occupants,  # keep for backward compat
    lifestyle=lifestyle,          # also populate dedicated field
)
```

Then update `generate.py` to read `brief.lifestyle` when available.

---

## 4. Budget-Aware Shopping List

**Priority**: Medium (meaningful product feature, but non-trivial)
**Effort**: Large
**Source**: Device testing feedback

### Problem

Users may have a total budget for their room redesign (e.g., "$2,000"). Currently the intake agent doesn't ask about budget, and the shopping phase returns products without price constraints. Adding budget-awareness requires solving a hard allocation problem.

### Why It's Not a Quick Win

1. **Budget allocation**: A $2,000 total budget must be split across 8-15 items. A sofa gets more than a lamp, but the right ratios depend on room type, style, and what the user is keeping vs. replacing.
2. **No price filtering in search**: Exa (product search API) doesn't support price range queries. You'd need to search, check prices, and filter post-hoc.
3. **Iterative rebalancing**: If the sofa costs $800 instead of the allocated $600, every other allocation shifts. This requires multiple search rounds.
4. **Price availability**: Many product listings don't expose prices in metadata. You'd need to scrape or use a commerce-specific API.

### Proposed Approach (When Prioritized)

1. Add `budget` field to `DesignBrief` (optional, user-provided total)
2. In intake: ask about budget as domain #10 (restore `budget` to constraints)
3. Pre-shopping: allocate budget per product category using a heuristic table (e.g., seating 30-40%, lighting 10-15%, decor 10-15%, textiles 10-15%)
4. Shopping activity: pass per-item budget hint to Exa queries as keywords (e.g., "modern sofa under $600")
5. Post-shopping: sum prices, flag if over budget, suggest alternatives for most expensive items
6. UI: show total cost vs. budget in shopping list screen

### Current State

Budget removed from intake domains (not asked). Constraints field still collects pets, kids, mobility, rental, timeline — these affect design choices and are used in generation/edit prompts.
