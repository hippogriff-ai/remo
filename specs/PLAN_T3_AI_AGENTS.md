# T3: AI Agents (Intake + Shopping) — Team Implementation Plan

> **Derived from**: `specs/PLAN_FINAL.md` v2.0
> **Date**: 2026-02-10
---

## 1. Big Picture

### What is Remo?

Remo is an AI-powered room redesign app: users photograph their room, describe their style, and receive photorealistic redesign options they can iteratively refine, culminating in a downloadable design image and a shoppable product list with real purchase links.

### Architecture

```
                    ┌─────────────────────────┐
                    │     iOS App (SwiftUI)    │
                    │  Polls Temporal state     │
                    │  Sends signals via API    │
                    └────────────┬─────────────┘
                                 │ HTTPS
                                 ▼
                    ┌─────────────────────────┐
                    │   FastAPI Gateway        │
                    │  (T0 owns)               │
                    └────────────┬─────────────┘
                                 │
                    ┌────────────▼─────────────┐
                    │   Temporal Server         │
                    │  DesignProjectWorkflow    │
                    └────────────┬─────────────┘
                                 │
                    ┌────────────▼─────────────┐
                    │   Temporal Worker         │
                    │                          │
                    │  Activities:              │
                    │   ├── run_intake_chat ◄──── T3 OWNS
                    │   ├── generate_designs    │
                    │   ├── edit_design (chat)  │
                    │   ├── generate_shopping ◄── T3 OWNS
                    │   └── purge_project       │
                    └──┬──────┬──────┬─────────┘
                       │      │      │
                Anthropic    Exa   Cloudflare R2
                (Claude)     API   (images)
```

### Where T3 Fits

T3 owns the **AI brain** of Remo:

1. **Intake conversation** — the Claude-powered chat that understands what the user wants, reasons like a professional designer, and produces a structured DesignBrief with prompt-ready specificity
2. **Shopping pipeline** — turns an approved design image into a purchasable product list with real buy links

**Critical framing**: The intake agent is NOT an information collector. It is a **design translator** — it hears "I want it cozy" and produces "warm palette (amber, terracotta), layered textiles (knit throws, velvet), low warm lighting 2200-2700K, intimate-scale furniture, refuge-dominant layout." That translation is what makes Gemini generate the right image. See `specs/DESIGN_INTELLIGENCE.md` for the full design reasoning framework.

Both use Claude via the **raw Anthropic Python SDK** with tool use. **NO agent harness, NO LangChain, NO framework.** This is just API calls with `client.messages.create()` and the `tools` parameter.

### 4-Team Structure

| Team | Responsibility |
|------|---------------|
| **T0: Platform** | Contracts, Temporal, FastAPI, DB, R2, CI/CD, integration lead |
| **T1: iOS** | All SwiftUI/UIKit screens and navigation |
| **T2: Image Gen** | Gemini-based generation, inpainting, regeneration |
| **T3: AI Agents** | Intake chat agent + shopping list pipeline (this plan) |

### Phase Overview

| Phase | Focus | T3 Role |
|-------|-------|---------|
| **P0: Foundation** | Contracts, scaffold, infra | Prompt engineering in notebook (no hard deliverables) |
| **P1: Independent Build** | All teams build in parallel | Quick Intake + full shopping pipeline |
| **P2: Integration** | Wire real activities | Full Intake mode |
| **P3: Stabilization** | Bugs, polish | Open Conversation mode |

---

## 2. Your Team

- **Worktree**: `/Hanalei/remo-ai`
- **Branch prefix**: `team/ai/*`
- **Setup command**:
  ```bash
  git worktree add /Hanalei/remo-ai team/ai/intake-agent
  ```

---

## 3. What You Own

| File | Purpose |
|------|---------|
| `backend/activities/intake.py` | `run_intake_chat` Temporal activity |
| `backend/activities/shopping.py` | `generate_shopping_list` Temporal activity |
| `backend/prompts/intake_system.txt` | Intake agent system prompt |
| `backend/prompts/item_extraction.txt` | Shopping list item extraction prompt |
| `backend/prompts/product_scoring.txt` | Shopping list rubric-based scoring prompt |

You **read but do not modify**:
- `specs/DESIGN_INTELLIGENCE.md` — Design reasoning framework (translation tables, diagnostic techniques, elevation rules). **Distill relevant sections into your system prompts.**

You do NOT own and must NOT modify:
- `backend/models/contracts.py` (T0 owns)
- `backend/workflows/design_project.py` (T0 owns)
- `backend/api/routes/*` (T0 owns)

---

## 4. Deliverables by Phase

### P0: Foundation (No Hard Deliverables)

T3 has no P0 exit-gate deliverables. Use P0 for exploration:
- Iterate on Claude system prompts in a notebook
- Test tool-use patterns with the Anthropic SDK
- Experiment with Exa search queries for furniture
- No dependencies — start immediately

### P1: Independent Build

| # | Deliverable | Success Metric |
|---|------------|----------------|
| 1 | Design intelligence integration into system prompt | Translation table, DIAGNOSE pipeline, room-specific guidance from `DESIGN_INTELLIGENCE.md` encoded in prompt; agent elevates "cozy" → professional design parameters |
| 2 | Claude system prompt for Quick Intake | ~3-turn adaptive flow with diagnostic probing produces valid, ELEVATED DesignBrief JSON 100% of the time |
| 3 | Structured output via tool use (DesignBrief) | Every response calls `update_design_brief` + `respond_to_user` tools |
| 4 | `run_intake_chat` activity (Quick mode) | Temporal activity completes in <60s per turn; output matches IntakeChatOutput contract |
| 5 | Intake eval harness (DesignBrief Quality Rubric) | Automated rubric scoring: ≥ 85/100 across 8 golden test conversations |
| 6 | Shopping list: design-aware item extraction | Extracts 6+ items using brief + iteration history + image; professional material vocabulary; correct source tagging ≥ 90% |
| 7 | Shopping list: Exa search integration | Parallelized search returns product pages for 80%+ of items |
| 8 | Shopping list: rubric-based scoring | Scores products on 5 criteria + design-principle alignment; produces calibrated 0-1 scores |
| 9 | Shopping pipeline eval suite | Automated eval for extraction, search, and scoring criteria |
| 10 | `generate_shopping_list` activity | Takes image + brief + iterations → returns ProductMatch list; 5+ items with working URLs |
| 11 | Golden test suite for intake | 8 scripted conversations; brief quality ≥ 70/100; adaptive behavior + brief elevation verified |

**Recommended P1 build order**:
1. Design intelligence integration (distill `DESIGN_INTELLIGENCE.md` into system prompt — the foundation everything else depends on)
2. Quick Intake prompt + activity (simplest — ~3 turns, but WITH elevation)
3. Intake eval harness (so you can iterate on prompt quality with automated rubric feedback)
4. Shopping pipeline (design-aware extraction → Exa → scoring → filtering)
5. Shopping eval suite + golden tests

### P2: Integration

| # | Deliverable | Success Metric |
|---|------------|----------------|
| 12 | Full Intake mode (~10 turns, adaptive) | Domain notepad tracking works; agent adapts question order based on responses; DIAGNOSE pipeline active; brief quality ≥ 80/100 |

### P3: Stabilization

| # | Deliverable | Success Metric |
|---|------------|----------------|
| 13 | Open Conversation mode | Free-form conversation with domain notepad; caps at ~15 turns; diagnostic probing; gracefully wraps up |

---

## 5. Dependencies

### What T3 Depends On

| Dependency | From | When |
|-----------|------|------|
| Pydantic contract models frozen | T0 | P0 exit gate |
| `IntakeChatInput`/`IntakeChatOutput` shapes | T0 | P0 exit gate |
| `GenerateShoppingListInput`/`GenerateShoppingListOutput` shapes | T0 | P0 exit gate |

### What T3 Does NOT Depend On

- T1 (iOS) — T3 works against contracts, not UI
- T2 (Image Gen) — T3 uses design image URLs but doesn't need T2's activities
- Temporal workflow wiring — T0 handles this in P2

### What Can Start Immediately (No Dependencies)

- Claude system prompt iteration in a notebook
- Exa search API experimentation
- Tool-use pattern prototyping with the Anthropic SDK

---

## 6. Technical Details

### Implementation Approach: Raw Anthropic SDK

**CRITICAL: No agent harness. No LangChain. No framework.**

Use the `anthropic` Python SDK directly:

```python
import anthropic

client = anthropic.Anthropic()

response = client.messages.create(
    model="claude-opus-4-6",
    max_tokens=4096,
    system=system_prompt,
    tools=tools,
    messages=conversation_history,
)
```

- The intake chat is just **multi-turn conversation with tool use**
- The shopping pipeline is just **sequential Claude calls + Exa API calls**
- All state is passed in via activity inputs (stateless activities)

---

### Intake Chat Agent

#### System Prompt Structure (6 Sections)

The system prompt (`backend/prompts/intake_system.txt`) has 6 sections. Sections 2–4 are distilled from `specs/DESIGN_INTELLIGENCE.md`.

1. **Identity & Design Philosophy**: "You are a professional interior design consultant — not an information collector. You reason like a designer: first analyzing the space (is it organized correctly?), then filtering through the human lens (is it right for these people?), then elevating with emotional impact (does it create joy?). You hear vague desires and translate them into specific, actionable design language."

2. **Translation Engine**: A lookup table mapping vague client language to specific design parameters. Include the full translation table from `DESIGN_INTELLIGENCE.md` Section 2 — at minimum the 10 core translations (cozy, modern, more space, calm, luxurious, bright & airy, rustic, minimalist, bohemian, scandinavian). Instruct the model: "When a user says a vague preference, internally map it to the corresponding design parameters. Fill the DesignBrief with the TRANSLATED parameters, not the user's raw words. In your summary, show the translation so the user can correct it."

3. **Diagnostic Reasoning (DIAGNOSE Pipeline)**: Instruct the model to follow the 8-step reasoning process from `DESIGN_INTELLIGENCE.md` Section 3. Key behavioral instructions:
   - Detect contradictions ("I love minimalism" + can't throw things away → real need is organized storage that LOOKS minimal)
   - Use the "three-why" technique: probe beneath surface answers to find root causes
   - Reframe surface requests into underlying design problems ("I need new furniture" → "the living room doesn't support how the family uses it")
   - Check internal consistency before finalizing (does color palette work with lighting plan?)

4. **Room-Specific Guidance**: Load the relevant room section from `DESIGN_INTELLIGENCE.md` Section 8 based on `room_type` (detected from first user response or photo analysis). Example: if room_type=bedroom, include sleep hygiene rules (2200K only, blackout capability, bed facing door, cool temperature).

5. **Behavioral Rules**: Adaptive questioning, domain tracking, skip covered domains, follow-up on unexpected topics, quick-reply chip generation. Use diagnostic questions from `DESIGN_INTELLIGENCE.md` Section 4 instead of generic questions when probing deeper.

6. **Output Format**: Must call `update_design_brief` and `respond_to_user` tools on every turn. Brief fields must contain ELEVATED design language (see elevation rules in `DESIGN_INTELLIGENCE.md` Section 5). Lighting always specifies 3 layers + Kelvin. Colors always include 60/30/10 proportions. Textures use professional material descriptors.

#### Mode Differentiation

**Guiding principle**: The agent has a **notepad** of 10 design domains that keeps it on track and prevents sidetracking, but it uses its intelligence to react to user responses and decide what to ask next. This is NOT a fixed questionnaire — if a questionnaire solved the problem, we wouldn't need an agent.

| Mode | System Prompt Instruction | Target Turns |
|------|--------------------------|-------------|
| **Quick** | "You have a notepad of 10 design domains. Select the 3 most impactful for {room_type} (consult the room-specific guidance). Pre-plan 3 questions using diagnostic alternatives from the question bank. Adapt — if the user's answer covers multiple domains, skip ahead. Apply the translation engine to every response. Target ~3 turns, then synthesize an elevated brief." | ~3 |
| **Full** | "You have a notepad of 10 design domains. Pre-plan 10 questions in priority order using the diagnostic question bank. After each response, run the DIAGNOSE pipeline: detect contradictions, interpret vague terms via translation engine, analyze root causes with the three-why technique. Reorder remaining questions based on what you've learned. Skip domains already covered. The notepad keeps you on track; your design intelligence picks the best next question and probes deeper when answers are surface-level." | ~10 |
| **Open** | "Begin with an open prompt: 'Tell us about this room — what's on your mind, what you love, what you'd change, anything.' Follow the user's lead. Apply the DIAGNOSE pipeline continuously. Track domains on your notepad internally. When conversation energy slows, probe uncovered domains using diagnostic questions. Cap at ~15 turns — synthesize an elevated brief and summarize." | ~15 |

#### Turn Counter

Track **server-side** (not relying on model to count):
- Model reports domain coverage in `update_design_brief`
- Server increments turn counter
- Quick mode terminates around 3 turns, Full around 10, Open caps at ~15
- This prevents the model from miscounting or drifting

#### Tool Definitions

The model calls two tools on every turn:

```python
tools = [
    {
        "name": "update_design_brief",
        "description": "Update the design brief with information gathered from the conversation. Call this on EVERY turn to keep the brief up to date.",
        "input_schema": {
            "type": "object",
            "properties": {
                "room_type": {"type": "string"},
                "occupants": {"type": "string"},
                "pain_points": {"type": "array", "items": {"type": "string"}},
                "keep_items": {"type": "array", "items": {"type": "string"}},
                "style_profile": {
                    "type": "object",
                    "properties": {
                        "lighting": {"type": "string"},
                        "colors": {"type": "array", "items": {"type": "string"}},
                        "textures": {"type": "array", "items": {"type": "string"}},
                        "clutter_level": {"type": "string"},
                        "mood": {"type": "string"}
                    }
                },
                "constraints": {"type": "array", "items": {"type": "string"}},
                "inspiration_notes": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "photo_index": {"type": "integer"},
                            "note": {"type": "string"},
                            "agent_clarification": {"type": "string"}
                        }
                    }
                },
                "domains_covered": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "List of design domains covered so far in the conversation"
                }
            },
            "required": ["room_type"]
        }
    },
    {
        "name": "respond_to_user",
        "description": "Send a response to the user. Call this on EVERY turn after updating the design brief.",
        "input_schema": {
            "type": "object",
            "properties": {
                "message": {
                    "type": "string",
                    "description": "The message to show the user"
                },
                "options": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "number": {"type": "integer"},
                            "label": {"type": "string"},
                            "value": {"type": "string"}
                        },
                        "required": ["number", "label", "value"]
                    },
                    "description": "Quick-reply options for the user. Provide 2-4 options when appropriate."
                },
                "is_open_ended": {
                    "type": "boolean",
                    "description": "True if the question accepts free-text input (show text field)"
                },
                "is_summary": {
                    "type": "boolean",
                    "description": "True if this is the final summary message"
                }
            },
            "required": ["message"]
        }
    }
]
```

#### Intake Chat: Activity Implementation Pattern

```python
from temporalio import activity
import anthropic

@activity.defn
async def run_intake_chat(input: IntakeChatInput) -> IntakeChatOutput:
    """Stateless activity: receives full conversation history, returns next response."""
    client = anthropic.Anthropic()

    # Build system prompt based on mode
    system_prompt = load_system_prompt(input.mode, input.project_context)

    # Build messages from conversation history + new user message
    messages = build_messages(input.conversation_history, input.user_message)

    # Call Claude with tool use
    response = client.messages.create(
        model="claude-opus-4-6",
        max_tokens=4096,
        system=system_prompt,
        tools=tools,
        messages=messages,
    )

    # Parse tool use results from response
    brief_update = extract_tool_call(response, "update_design_brief")
    user_response = extract_tool_call(response, "respond_to_user")

    # Build output
    return IntakeChatOutput(
        agent_message=user_response["message"],
        options=[QuickReplyOption(**opt) for opt in user_response.get("options", [])],
        is_open_ended=user_response.get("is_open_ended", False),
        progress=f"Turn {len(input.conversation_history) // 2 + 1} — {domains_covered}/{total_domains} domains covered",
        is_summary=user_response.get("is_summary", False),
        partial_brief=DesignBrief(**brief_update) if brief_update else None,
    )
```

#### Intake Chat: Build Order

1. **Quick Intake first (P1)** — simplest, ~3 turns, most constrained scope
2. **Full Intake (P2)** — ~10 turns, domain notepad tracking, adaptive question reordering
3. **Open Conversation (P3)** — free-form, agent follows user's lead with domain notepad

#### Intake Eval: DesignBrief Quality Rubric (out of 100)

The intake agent shouldn't just capture what the user says — it should **elevate** user input into professional design language. "I want it cozy" should become specific guidance on color warmth, lighting layers, and textile choices. The Translation Engine from `DESIGN_INTELLIGENCE.md` drives this elevation.

Every prompt change must be evaluated against golden test conversations using this rubric. A second Claude call scores the output brief + conversation transcript.

| Criterion | Weight | Full marks | Zero |
|-----------|--------|-----------|------|
| **Style Coherence** | 10 | Named style with 2-3 defining characteristics. No contradictions. Brief reads as a unified design narrative, not a disconnected checklist. | Conflicting styles, or vague ("nice, modern") |
| **Color Strategy** | 15 | Named palette with 60/30/10 proportions and application context. e.g., "warm ivory walls (60%), natural oak + cream textiles (30%), navy accent pillows + art (10%)" — consistent undertones throughout | Just color names with no relationship or proportions |
| **Lighting Design** | 15 | All three layers specified (ambient, task, accent) with Kelvin temperatures and placement. e.g., "warm ambient 2700K, brass table lamps at reading positions (task), accent uplighting on textured wall" | Missing layers, or just "warm lighting" |
| **Material & Texture Specificity** | 15 | Precise professional descriptors: "weathered oak," "brushed brass," "boucle upholstery," "raw linen curtains." Minimum 3 distinct texture types. | Generic ("wood," "metal," "nice fabric") |
| **Design Intelligence** | 10 | Agent applied the three-layer stack: spatial awareness (furniture scale, circulation), human-centered filter (suitable for these people's lifestyle), emotional layer (mood-enhancing elements). Brief includes at least one biophilic element and addresses prospect-refuge for primary seating. | No evidence of design reasoning — just echoed user preferences |
| **Diagnostic Depth** | 5 | Agent probed beneath surface requests. Pain points reveal root causes (not just "I want new furniture" but "seating doesn't support how we use the room"). Agent detected and resolved at least one contradiction or vague answer. | Accepted all surface answers without follow-up |
| **Actionability** | 15 | Every field translates directly into Gemini generation prompt language. A prompt engineer needs zero guesswork. Textures, colors, and lighting are specific enough to render. | Abstract feelings with no visual anchor |
| **Completeness** | 10 | Covers: room purpose, style, colors (with proportions), lighting (with layers), textures (3+ types), key furniture, constraints, keep_items. Room-specific design rules applied. | Only 1-2 domains populated |
| **User Fidelity** | 5 | Every preference traces to a user statement. Agent-inferred preferences are clearly marked ("I interpreted your preference for natural light as..."). Translations shown in summary for user correction. | Hallucinated preferences user never expressed |

**Thresholds**: ≥85 `PASS:EXCELLENT` | 70-84 `PASS` | 50-69 `FAIL:WEAK` | <50 `FAIL:POOR`

**Automated eval loop**:
```python
for test_conversation in golden_test_suite:
    brief = run_intake_agent(test_conversation)
    score = evaluate_brief(brief, test_conversation, BRIEF_QUALITY_RUBRIC)
    # Returns: { total: 78, tag: "PASS", breakdown: { style: 13/15, color: 9/15, ... } }
    # Per-criterion breakdown tells you which prompt dimension to improve
```

---

### Shopping List Pipeline

**Three input sources** — avoids the "telephone game" (text → image → text) by anchoring search in what we already know:

| Source | What it gives us | Priority |
|--------|-----------------|----------|
| **DesignBrief** (from intake) | User's *intent* — style, colors, textures, specific requests, keep_items | Highest — user's own words |
| **Iteration history** (annotation/feedback) | Amendments — what changed from original vision | Overrides brief for changed items |
| **Final approved image** | Ground truth — what's actually rendered, including AI additions | Fills gaps for items not in brief/iterations |

```
DesignBrief + Iteration History + Approved Image + Original Room Photos
    ↓
[1] Anchored Item Extraction (Claude Opus 4.6, image input)
    → Receives ALL three sources + original room photos
    → For each item, classifies source:
      (a) BRIEF-ANCHORED — explicitly in DesignBrief → use user's language
      (b) ITERATION-ANCHORED — changed during annotation/feedback → use iteration instruction
      (c) IMAGE-ONLY — AI addition not in brief or iterations → vision-derived description
      (d) EXISTING — in original room photo AND keep_items → SKIP
    → 6-10 items with: category, style, material, color, proportions, source_tag
    ↓
[2] Exa Search (parallelized, queries differ by source)
    → BRIEF-ANCHORED: "{user's own words from brief} + {style_profile}"
    → ITERATION-ANCHORED: "{iteration instruction keywords}"
    → IMAGE-ONLY: "{AI-described category} {material} {color}"
    → 2-3 query variants per item; dimension-aware if LiDAR available
    ↓
[3] Rubric-Based Scoring (Claude Opus 4.6, parallelized)
    → Category match: +0.3
    → Material match: +0.2
    → Color match: +0.2
    → Style match: +0.2
    → Dimensions match (if LiDAR): +0.1
    → Sum scores for calibrated 0-1 confidence
    ↓
[4] Dimension Filtering (if LiDAR)
    → Cross-reference product dimensions against room geometry
    → Assign fit badge: "fits" / "tight" / filter out
    ↓
[5] Confidence Filtering
    → ≥0.8: show normally
    → 0.5-0.79: show with "Close match" label
    → <0.5: hide; show Google Shopping fallback link
```

#### Step 1: Anchored Item Extraction

Claude receives all three sources in a single call:

```python
response = client.messages.create(
    model="claude-opus-4-6",
    max_tokens=4096,
    messages=[{
        "role": "user",
        "content": [
            {"type": "image", "source": {"type": "url", "url": design_image_url}},
            *[{"type": "image", "source": {"type": "url", "url": url}}
              for url in original_room_photo_urls],
            {"type": "text", "text": load_prompt("item_extraction.txt").format(
                design_brief=design_brief.model_dump_json() if design_brief else "None",
                iteration_history=format_iterations(revision_history),
                keep_items=design_brief.keep_items if design_brief else [],
            )}
        ]
    }]
)
```

The prompt (`backend/prompts/item_extraction.txt`) instructs Claude to:
- Cross-reference the design image against the DesignBrief and iteration history
- Tag each item with its source: `BRIEF_ANCHORED`, `ITERATION_ANCHORED`, or `IMAGE_ONLY`
- Skip items that are in the original room photos AND in keep_items
- For brief-anchored items, preserve the user's original language (don't paraphrase)
- For iteration-anchored items, use the instruction text (e.g., "replace with marble coffee table")
- For image-only items, describe what's visible (category, style, material, color)
- **Use professional material vocabulary** from `DESIGN_INTELLIGENCE.md` Section 6: not "brown table" but "walnut dining table with turned legs"; not "gold lamp" but "brushed brass arc floor lamp with linen shade"
- **Follow the standard extraction taxonomy** (10 categories in priority order): primary seating, tables, storage, rugs, lighting fixtures, window treatments, soft furnishings, wall art, plants/planters, hardware/accents
- **Apply proportion constraints** if LiDAR is available (coffee table ≈ 2/3 sofa length; 14-18" clearance; rug extends 6"+ beyond sofa)

#### Step 2: Exa Search (Parallelized, Source-Aware)

Search queries differ based on item source — brief-anchored items get the most specific queries:

```python
async def search_item(item):
    if item.source_tag == "BRIEF_ANCHORED":
        # Use the user's own language — most specific
        queries = [
            f"{item.user_description}",  # preserved from brief
            f"{item.category} {item.style} {item.material}",
        ]
    elif item.source_tag == "ITERATION_ANCHORED":
        # Use iteration instruction keywords
        queries = [
            f"{item.iteration_instruction}",
            f"{item.category} {item.material} {item.color}",
        ]
    else:  # IMAGE_ONLY
        # Vision-derived, less specific
        queries = [
            f"{item.category} {item.material} {item.color}",
            f"{item.category} {item.style}",
        ]
    if item.dimensions:
        queries.append(f"{item.category} {item.dimensions}")

    tasks = [exa.search(q, num_results=3, type="neural") for q in queries]
    results = await asyncio.gather(*tasks)
    return deduplicate(flatten(results))

all_results = await asyncio.gather(*[search_item(item) for item in items])
```

#### Step 3: Rubric-Based Scoring (Parallelized)

Score each product candidate against the extracted item using Claude:

```python
# Prompt in backend/prompts/product_scoring.txt
# Claude scores each criterion independently:
#   Category match: +0.3
#   Material match: +0.2
#   Color match: +0.2
#   Style match: +0.2
#   Dimensions match (if LiDAR): +0.1

scoring_tasks = []
for item, products in zip(items, all_results):
    for product in products:
        scoring_tasks.append(score_product(client, item, product))

scores = await asyncio.gather(*scoring_tasks)
```

#### Step 4: Dimension Filtering (If LiDAR)

If LiDAR data is available:
- Cross-reference product dimensions against room geometry
- Assign fit badge: "fits" / "tight" / filter out items that clearly don't fit

#### Step 5: Confidence Filtering

Apply thresholds to determine display behavior:
- **>= 0.8**: Show normally
- **0.5 - 0.79**: Show with "Close match" label
- **< 0.5**: Hide; generate a Google Shopping fallback link for the `UnmatchedItem`

### Shopping Pipeline Eval Criteria

**Item Extraction Eval** (run against 5+ test cases with known briefs + iteration histories):

| Criterion | Metric | Pass | How to test |
|-----------|--------|------|-------------|
| Brief coverage | % of DesignBrief items found | ≥ 80% | Compare extracted items against brief fields |
| No hallucinations | % of extracted items visible in image | 100% | Second Claude call to verify |
| keep_items respected | None of keep_items in extraction | 100% | String match |
| Source tagging | Items correctly tagged brief/iteration/image-only | ≥ 90% | Compare against brief + iteration content |
| Structured output | Every item has all required fields | 100% | Pydantic validation |

**Search Query Eval** (run against 20+ extracted items):

| Criterion | Metric | Pass | How to test |
|-----------|--------|------|-------------|
| URL validity | product_urls returning HTTP 200 | ≥ 90% | Automated HEAD requests |
| Result relevance | Searches returning ≥1 product scoring ≥ 0.5 | ≥ 80% | Check rubric scores |
| Query specificity | Brief-anchored queries use user's terminology | 100% | Check query contains brief keywords |

**Scoring Eval** (run against 30+ scored products):

| Criterion | Metric | Pass | How to test |
|-----------|--------|------|-------------|
| Calibration | ≥0.8 products visually better than 0.5-0.7 | ≥ 85% pairwise | Second Claude call |
| Rubric compliance | Each sub-score independently correct | ≥ 90% per criterion | Second Claude call |
| Discrimination | Score spread uses full 0-1 range | Std dev > 0.15 | Statistical check |

---

## 7. Contracts You Implement

T3 **consumes** these contracts (T0 owns them). Do not modify — request changes via T0.

### Activity Input/Output Models

```python
# --- Intake Chat ---
class IntakeChatInput(BaseModel):
    mode: Literal["quick", "full", "open"]
    project_context: dict                # room photos, inspiration notes, scan data
    conversation_history: list[ChatMessage]
    user_message: str

class IntakeChatOutput(BaseModel):
    agent_message: str
    options: list[QuickReplyOption] | None = None
    is_open_ended: bool = False
    progress: str | None = None          # "Question 2 of 3"
    is_summary: bool = False
    partial_brief: DesignBrief | None = None

# --- Shopping List ---
class GenerateShoppingListInput(BaseModel):
    design_image_url: str
    original_room_photo_urls: list[str]      # to identify existing items (skip)
    design_brief: DesignBrief | None = None
    revision_history: list[RevisionRecord] = []  # iteration amendments
    room_dimensions: RoomDimensions | None = None

class GenerateShoppingListOutput(BaseModel):
    items: list[ProductMatch]
    unmatched: list[UnmatchedItem] = []
    total_estimated_cost_cents: int
```

### Shared Types

```python
class DesignBrief(BaseModel):
    room_type: str
    occupants: str | None = None
    pain_points: list[str] = []
    keep_items: list[str] = []
    style_profile: StyleProfile | None = None
    constraints: list[str] = []
    inspiration_notes: list[InspirationNote] = []

class StyleProfile(BaseModel):
    lighting: str | None = None       # ELEVATED: "warm ambient 2700K, task at reading chair, accent on gallery wall"
    colors: list[str] = []            # ELEVATED: ["warm ivory walls (60%)", "natural oak (30%)", "navy accents (10%)"]
    textures: list[str] = []          # ELEVATED: ["boucle upholstery", "weathered oak", "brushed brass", "raw linen"]
    clutter_level: str | None = None  # minimal / curated / layered
    mood: str | None = None           # ELEVATED: "intimate refuge — deep-seated furniture, layered warm textiles, soft pools of light"

class InspirationNote(BaseModel):
    photo_index: int
    note: str
    agent_clarification: str | None = None

class ChatMessage(BaseModel):
    role: str                            # "user" or "assistant"
    content: str

class QuickReplyOption(BaseModel):
    number: int
    label: str
    value: str

class ProductMatch(BaseModel):
    category_group: str
    product_name: str
    retailer: str
    price_cents: int
    product_url: str
    image_url: str | None = None
    confidence_score: float              # 0-1, rubric-based
    why_matched: str
    fit_status: str | None = None        # "fits" / "tight" / None
    fit_detail: str | None = None
    dimensions: str | None = None

class UnmatchedItem(BaseModel):
    category: str
    search_keywords: str
    google_shopping_url: str

class RoomDimensions(BaseModel):
    width_m: float
    length_m: float
    height_m: float
    walls: list[dict] = []
    openings: list[dict] = []
```

---

## 8. Cost Per Call

| Component | Cost |
|-----------|------|
| Intake chat (Claude Opus 4.6, full mode, ~8 turns) | $0.15 |
| Shopping list extraction (Claude Opus 4.6, image input) | $0.03 |
| Exa search (~8 queries) | $0.04 |
| Shopping list scoring (~8 Claude calls) | $0.10 |
| **T3 total per session (typical)** | **~$0.32** |

---

## 9. Git & Collaboration

### Worktree Setup

```bash
# From the main remo repo:
git worktree add /Hanalei/remo-ai team/ai/intake-agent
```

### Branch Naming

```
team/ai/intake-quick       # Quick Intake activity (P1)
team/ai/shopping-pipeline   # Shopping list pipeline (P1)
team/ai/intake-full         # Full Intake mode (P2)
team/ai/intake-open         # Open Conversation mode (P3)
```

### PR Merge Order

T3's PRs are in **group 6** — any order during P1:

```
Group 6 (P1, any order):
  - team/ai/intake-quick       → main
  - team/ai/shopping-pipeline  → main
```

### PR Review

- T0 reviews T3 PRs for **contract compliance**
- Ensure activity inputs/outputs match Pydantic models exactly
- T3 self-merges bug fixes if tests pass

---

## 10. Success Metrics

| Metric | Verification |
|--------|-------------|
| Quick Intake: valid brief | 100% valid DesignBrief JSON across 5 test conversations |
| Brief quality score | ≥ 70/100 on DesignBrief Quality Rubric across golden test suite |
| Brief elevates user input | Lighting design ≥ 12/15, material specificity ≥ 12/15, color strategy ≥ 12/15 on rubric |
| Design intelligence active | Design intelligence ≥ 7/10 on rubric (three-layer stack evidence, biophilic element, prospect-refuge) |
| Translation engine works | Vague input ("cozy", "modern") → elevated design parameters in brief (not echoed back raw) |
| Diagnostic probing works | Surface answers get follow-up; contradictions get resolved; diagnostic depth ≥ 3/5 on rubric |
| Adaptive skipping works | Multi-domain answer → agent correctly skips covered domains |
| Shopping: 5+ matched products | Test image + brief + iterations → 5+ items with confidence >= 0.5 |
| Brief-anchored items use user language | Search queries for brief-anchored items contain brief keywords |
| keep_items excluded | None of the keep_items appear in shopping extraction |
| Product URLs work | HTTP HEAD on product_url returns 200 for 90%+ |
| Rubric scoring calibrated | Category match contributes 0.3; material 0.2; etc. |
| Scoring discrimination | Score std dev > 0.15 across test set |
| End-to-end latency | Shopping list pipeline < 20s |

---

## 11. Code Quality

### Testing: Golden Test Suite

Build a suite of **8 scripted conversations** that verify:
- Quick mode produces a valid brief in ~3 turns (may be fewer if user covers multiple domains)
- Agent adapts: multi-domain answers cause it to skip or merge planned questions
- Unexpected topics get intelligent follow-up (agent doesn't ignore them to stick to a script)
- Summary message is generated when domain coverage is sufficient
- DesignBrief JSON validates against the Pydantic model
- **Translation engine fires**: vague inputs ("cozy", "modern", "calm") produce elevated parameters, not echoed words
- **Three-layer stack active**: brief includes biophilic element, lighting layers, and spatial awareness
- **Diagnostic probing works**: surface answers trigger intelligent follow-up

**Tests use real API calls** (not mocked Claude). This is necessary because the behavior being tested IS the Claude response quality.

```python
# Example golden test: translation engine must fire
@pytest.mark.integration
async def test_quick_intake_elevation():
    """Agent should translate 'cozy' into specific design parameters, not echo it back."""
    scripted_answers = [
        "It's a living room for our family. We have 2 dogs.",
        "I just want it to feel cozy",
        "Warm lighting please, nothing too bright"
    ]
    history = []
    for answer in scripted_answers:
        result = await run_intake_chat(IntakeChatInput(
            mode="quick",
            project_context={"room_photos": [TEST_PHOTO_URL]},
            conversation_history=history,
            user_message=answer,
        ))
        history.append(ChatMessage(role="user", content=answer))
        history.append(ChatMessage(role="assistant", content=result.agent_message))

    assert result.is_summary is True
    brief = result.partial_brief
    assert brief is not None
    assert brief.room_type != ""
    # Translation engine: "cozy" should become specific parameters
    sp = brief.style_profile
    assert sp is not None
    # Lighting should mention layers or Kelvin, not just "warm"
    assert any(kw in sp.lighting.lower() for kw in ["2700", "ambient", "task", "accent", "layer"])
    # Textures should be specific materials, not generic
    assert not any(t.lower() in ("soft", "nice", "good") for t in sp.textures)
    # Constraints should mention pets (detected from "2 dogs")
    assert any("dog" in c.lower() or "pet" in c.lower() for c in brief.constraints)

# Example golden test: diagnostic probing
@pytest.mark.integration
async def test_quick_intake_probes_vague_answer():
    """Agent should follow up on vague answers, not accept them at face value."""
    history = []
    # First turn: room type
    result = await run_intake_chat(IntakeChatInput(
        mode="quick",
        project_context={"room_photos": [TEST_PHOTO_URL]},
        conversation_history=history,
        user_message="It's a bedroom",
    ))
    history.append(ChatMessage(role="user", content="It's a bedroom"))
    history.append(ChatMessage(role="assistant", content=result.agent_message))

    # Second turn: deliberately vague
    result = await run_intake_chat(IntakeChatInput(
        mode="quick",
        project_context={"room_photos": [TEST_PHOTO_URL]},
        conversation_history=history,
        user_message="I just want it to look nice",
    ))
    # Agent should NOT accept "nice" — should probe with options or follow-up
    assert result.options is not None and len(result.options) >= 2, \
        "Agent should offer quick-reply options to refine 'nice'"
```

### Error Handling

Activities distinguish between retryable and non-retryable errors:

```python
from temporalio.exceptions import ApplicationError

# Retryable (Temporal retries automatically)
raise ApplicationError("Claude rate limited", non_retryable=False)

# Non-retryable (report to user)
raise ApplicationError("Content policy violation", non_retryable=True)
```

Retryable errors:
- Claude API rate limits (429)
- Exa API rate limits
- Transient network failures

Non-retryable errors:
- Content policy violations
- Invalid input data
- Malformed API responses after retries

### Statelessness

Activities are **stateless**: they receive all inputs, produce all outputs. No database reads/writes, no file system access, no shared state.

- Conversation history is passed in via `IntakeChatInput.conversation_history`
- Design image URL is passed in via `GenerateShoppingListInput.design_image_url`
- The activity does NOT maintain state between calls

---

## 12. Risks & Open Questions

### Risks

| Risk | Severity | Mitigation |
|------|----------|-----------|
| Exa returns irrelevant products | Medium | Multi-query strategy (2-3 variants per item); professional material vocabulary improves query specificity; Google Shopping fallback link for unmatched items |
| Claude Opus 4.6 intake costs higher than estimated ($0.15/session) | Low | Monitor per-session costs; translation table and DIAGNOSE pipeline add prompt length but improve output quality. Downgrade to Sonnet 4.5 if needed |
| Claude fails to call both tools consistently | Medium | Explicit instruction in system prompt; validate tool calls in activity; retry once if missing |
| Exa search quality varies by product category | Medium | Test 20+ furniture queries in P0/early P1; adjust query templates based on results |
| System prompt too long with design intelligence | Medium | The translation table + room guidance + DIAGNOSE pipeline add ~2K tokens. If latency is an issue, load room-specific guidance dynamically (only bedroom rules for bedrooms) instead of the full table. Monitor first-token latency. |
| Agent over-corrects user preferences (too opinionated) | Medium | User fidelity criterion (5/100) in rubric catches this. The agent translates but does NOT override — "I interpreted your preference as X, does that sound right?" Inferred preferences must be marked in the brief. |
| Brief elevation reduces user trust ("I said cozy, not all this") | Low | The summary step shows the translation explicitly and invites correction. Quick-reply chip: "1. That captures it / 2. I meant something different". Agent should frame translations as "Here's how I'd bring that to life..." not "This is what you really mean." |

### Open Questions

| # | Question | Decision Needed By |
|---|----------|-------------------|
| 1 | Exa search quality: test 20+ furniture queries for relevance | Mid-P1 |
| 2 | Claude Opus 4.6 intake cost in practice vs $0.15 estimate | Mid-P1 |
| 3 | Should intake track "domains_covered" as a list in the tool call, or infer from brief fields? | P1 start |

---

## Quick Reference

- **Master plan**: `specs/PLAN_FINAL.md`
- **Design intelligence reference**: `specs/DESIGN_INTELLIGENCE.md` — **READ THIS FIRST.** It contains the translation tables, diagnostic techniques, and elevation rules that define your agent's design IQ.
- **Your files**: `backend/activities/intake.py`, `backend/activities/shopping.py`, `backend/prompts/intake_system.txt`, `backend/prompts/item_extraction.txt`, `backend/prompts/product_scoring.txt`
- **Your worktree**: `/Hanalei/remo-ai`
- **Your branches**: `team/ai/*`
- **Model**: Claude Opus 4.6 (`claude-opus-4-6`) via raw `anthropic` Python SDK
- **No frameworks**: No LangChain, no agent harness, just `client.messages.create()` with `tools`
- **Key principle**: The intake agent is a design TRANSLATOR, not an information COLLECTOR. It hears vague desires and produces prompt-ready design parameters.

---

*End of T3 plan. See `specs/PLAN_FINAL.md` for full system context.*
