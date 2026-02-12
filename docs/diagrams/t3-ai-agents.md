# T3 AI Agents — Architecture Diagram

Two stateless Temporal activities that power Remo's AI brain: a design-consultant intake chat and a shopping list pipeline.

## System Overview

```mermaid
flowchart TD
    subgraph Client["iOS App"]
        A1[User sends message<br/>or approves design]
    end

    subgraph API["FastAPI Gateway"]
        A2["/projects/{id}/intake" or<br/>"/projects/{id}/shopping"]
    end

    subgraph TW["Temporal Workflow"]
        W1{Which activity?}
    end

    A1 --> A2 --> W1

    subgraph Intake["run_intake_chat Activity"]
        direction TB
        I1["Load system prompt<br/>(mode: quick|full|open)"]
        I2{"Turn 1?"}
        I3["Inject room + inspiration<br/>photos as image blocks"]
        I4["Inject previous brief<br/>(GATHERED SO FAR)"]
        I5["Build messages<br/>(conversation history + user msg)"]
        I6["Claude Opus 4.6<br/>tool_use: update_design_brief<br/>+ respond_to_user"]
        I7["Extract tool calls"]
        I8{"turn >= max_turns?"}
        I9["Force is_summary = true"]
        I10["Build IntakeChatOutput<br/>(agent_message, options,<br/>partial_brief, progress)"]

        I1 --> I2
        I2 -->|Yes| I3 --> I5
        I2 -->|"No (turn 2+)"| I4 --> I5
        I5 --> I6
        I6 --> I7 --> I8
        I8 -->|Yes| I9 --> I10
        I8 -->|No| I10
    end

    subgraph Shopping["generate_shopping_list Activity"]
        direction TB
        S1["Step 1: Extract Items<br/>Claude Opus 4.6 vision<br/>(design image + room photos)"]
        S2["Validate extracted items<br/>(drop malformed, normalize tags)"]
        S3["Step 2: Search Products<br/>Exa API (parallel per item)<br/>retry on 429/500, content retrieval"]
        S4["Step 3: Score Products<br/>claude-sonnet-4-5-20250929 rubric<br/>(parallel, semaphore=5,<br/>failure-tolerant)"]
        S5["Step 4: Dimension Filter<br/>(LiDAR — future)"]
        S6["Step 5: Confidence Filter<br/>+ cross-item URL dedup"]
        S7{"Score >= 0.8?"}
        S8["ProductMatch<br/>fit_status: fits"]
        S9{"Score >= 0.5?"}
        S10["ProductMatch<br/>fit_status: tight<br/>+ fit_detail"]
        S11["UnmatchedItem<br/>+ Google Shopping<br/>fallback URL"]
        S12["GenerateShoppingListOutput<br/>(items, unmatched,<br/>total_estimated_cost_cents)"]

        S1 --> S2 --> S3 --> S4 --> S5 --> S6
        S6 --> S7
        S7 -->|Yes| S8 --> S12
        S7 -->|No| S9
        S9 -->|Yes| S10 --> S12
        S9 -->|No| S11 --> S12
    end

    W1 -->|IntakeChatInput| Intake
    W1 -->|GenerateShoppingListInput| Shopping

    subgraph Ext["External APIs"]
        E1["Anthropic Claude<br/>(claude-opus-4-6: intake + extraction<br/>claude-sonnet-4-5-20250929: scoring)"]
        E2["Exa Search API<br/>(neural search + content)"]
    end

    I6 -.-> E1
    S1 -.-> E1
    S4 -.-> E1
    S3 -.-> E2

    subgraph Errors["Error Classification"]
        ER1["Retryable<br/>429 rate limit<br/>500+ server error"]
        ER2["Non-retryable<br/>400 content policy<br/>missing API keys"]
    end

    Intake -.->|ApplicationError| Errors
    Shopping -.->|ApplicationError| Errors
```

## Legend

| Shape | Meaning |
|-------|---------|
| Rectangles | Processing steps |
| Diamonds | Decision points |
| Dashed arrows | External API calls |
| Solid arrows | Internal data flow |
| Subgraphs | Logical groupings |

## Key Design Decisions

- **Stateless activities**: All state passed via `*Input` / `*Output` Pydantic models. No database access from activities.
- **Server-side turn enforcement**: The workflow counts turns, not the model. Prevents runaway conversations.
- **Parallel scoring with semaphore**: `asyncio.gather` with `MAX_CONCURRENT_SCORES=5` balances throughput vs rate limits.
- **Failure-tolerant scoring**: Individual score failures are caught and skipped — remaining scores preserved.
- **Cross-item URL dedup**: Same product URL can't match multiple items. Falls back to next-best product.
- **Design translation**: The intake agent is a design *translator* (cozy → warm palette, layered textiles, 2200-2700K) not an information collector.
