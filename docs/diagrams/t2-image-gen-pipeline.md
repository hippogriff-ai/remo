# T2 Image Generation Pipeline — Architecture Diagram

## 1. System Overview

Shows how T2 components connect to the Temporal workflow, Gemini API, and R2 storage.

```mermaid
flowchart TD
    subgraph Temporal["Temporal Workflow"]
        WF["DesignProjectWorkflow"]
    end

    subgraph T2["T2 Image Gen Activities"]
        GEN["generate_designs"]
        EDIT["edit_design"]
    end

    subgraph Utils["T2 Utilities"]
        CHAT["gemini_chat.py"]
        ANNO["image.py"]
    end

    subgraph Prompts["Prompt Templates"]
        GEN_TXT["generation.txt"]
        EDIT_TXT["edit.txt"]
        PRES_TXT["room_preservation.txt"]
    end

    subgraph External["External Services"]
        GEMINI["Gemini 3 Pro Image"]
        R2["Cloudflare R2"]
    end

    WF -->|"GenerateDesignsInput"| GEN
    WF -->|"EditDesignInput"| EDIT
    GEN --> CHAT
    GEN --> GEN_TXT
    GEN --> PRES_TXT
    EDIT --> CHAT
    EDIT --> ANNO
    EDIT --> EDIT_TXT
    CHAT -->|"generate_content / chats.create"| GEMINI
    CHAT -->|"serialize / restore JSON"| R2
    GEN -->|"upload PNG"| R2
    EDIT -->|"upload PNG"| R2
    GEN -->|"GenerateDesignsOutput"| WF
    EDIT -->|"EditDesignOutput"| WF
```

## 2. generate_designs Flow

Shows the standalone generation path: room photos + brief produce 2 parallel design options.

```mermaid
flowchart TD
    START(["generate_designs called"])
    EXTRACT["Extract project_id from R2 URLs"]
    DL["Download room photos + inspiration images concurrently"]
    BRIEF["Build prompt from DesignBrief + templates"]
    PAR{{"Parallel Gemini calls"}}
    OPT0["_generate_single_option 0"]
    OPT1["_generate_single_option 1"]
    CHECK0{{"Image in response?"}}
    CHECK1{{"Image in response?"}}
    RETRY0["Retry: Please generate the room image now"]
    RETRY1["Retry: Please generate the room image now"]
    UP0["Upload option_0.png to R2"]
    UP1["Upload option_1.png to R2"]
    OUT(["Return GenerateDesignsOutput with 2 DesignOptions"])

    START --> EXTRACT --> DL --> BRIEF --> PAR
    PAR --> OPT0
    PAR --> OPT1
    OPT0 --> CHECK0
    OPT1 --> CHECK1
    CHECK0 -->|"Yes"| UP0
    CHECK0 -->|"No"| RETRY0 --> UP0
    CHECK1 -->|"Yes"| UP1
    CHECK1 -->|"No"| RETRY1 --> UP1
    UP0 --> OUT
    UP1 --> OUT

    subgraph ErrorHandling["Error Classification"]
        E429["429 / RESOURCE_EXHAUSTED -> retryable"]
        ESAFE["SAFETY / blocked -> non-retryable"]
        EOTHER["Other -> retryable"]
    end
```

## 3. edit_design Flow

Shows the branching logic: bootstrap new chat vs continue from R2 history.

```mermaid
flowchart TD
    START(["edit_design called"])
    VALIDATE{"annotations or feedback?"}
    DL_BASE["Download base_image from URL"]
    BRANCH{"chat_history_key is None?"}

    subgraph Bootstrap["Bootstrap Path"]
        DL_REF["Download room + inspiration images"]
        CREATE["create_chat via gemini_chat"]
        CTX["Send context: ref images + design + prompt"]
        DRAW_B{"Has annotations?"}
        ANNO_B["draw_annotations on base_image"]
        LOAD_B["Load edit.txt template"]
        FB_B{"Has feedback?"}
        SEND_B["chat.send_message with edit parts"]
        CHECK_B{"Image in response?"}
        RETRY_B["Retry: generate edited image"]
        SER_B["serialize_to_r2 chat history"]
    end

    subgraph Continue["Continue Path"]
        RESTORE["restore_from_r2 chat history JSON"]
        DRAW_C{"Has annotations?"}
        ANNO_C["draw_annotations on base_image"]
        FB_C{"Has feedback?"}
        CONT_C["continue_chat with history + message"]
        CHECK_C{"Image in response?"}
        RETRY_C["Retry: generate edited image"]
        SER_C["serialize_contents_to_r2"]
    end

    UPLOAD["Upload revised image to R2"]
    OUT(["Return EditDesignOutput"])

    START --> VALIDATE
    VALIDATE -->|"Neither"| ERR1(["ApplicationError: non-retryable"])
    VALIDATE -->|"Yes"| DL_BASE --> BRANCH
    BRANCH -->|"Yes: first call"| DL_REF --> CREATE --> CTX
    CTX --> DRAW_B
    DRAW_B -->|"Yes"| ANNO_B --> LOAD_B
    DRAW_B -->|"No"| FB_B
    LOAD_B --> FB_B
    FB_B -->|"Yes"| SEND_B
    FB_B -->|"No annotations either"| SEND_B
    SEND_B --> CHECK_B
    CHECK_B -->|"Yes"| SER_B
    CHECK_B -->|"No"| RETRY_B --> SER_B
    SER_B --> UPLOAD

    BRANCH -->|"No: has history"| RESTORE
    RESTORE --> DRAW_C
    DRAW_C -->|"Yes"| ANNO_C --> FB_C
    DRAW_C -->|"No"| FB_C
    FB_C --> CONT_C --> CHECK_C
    CHECK_C -->|"Yes"| SER_C
    CHECK_C -->|"No"| RETRY_C --> SER_C
    SER_C --> UPLOAD

    UPLOAD --> OUT
```

## 4. Chat History Serialization Round-Trip

Shows how multi-turn Gemini chat state survives between stateless Temporal activity calls.

```mermaid
sequenceDiagram
    participant WF as Temporal Workflow
    participant EA as edit_design Activity
    participant GM as gemini_chat.py
    participant GEM as Gemini API
    participant R2 as Cloudflare R2

    Note over WF,R2: First edit call - Bootstrap

    WF->>EA: EditDesignInput (chat_history_key=None)
    EA->>GM: create_chat(client)
    GM->>GEM: chats.create(model, config)
    GEM-->>GM: Chat object
    EA->>GM: chat.send_message(context + images)
    GM->>GEM: context turn
    GEM-->>GM: acknowledgment
    EA->>GM: chat.send_message(annotated image + edit prompt)
    GM->>GEM: edit turn
    GEM-->>GM: response with edited image
    EA->>GM: serialize_to_r2(chat, project_id)
    GM->>GM: serialize_history: text + base64 images + thought_signatures
    GM->>R2: upload JSON to projects/id/gemini_chat_history.json
    R2-->>GM: OK
    EA-->>WF: EditDesignOutput (chat_history_key, revised_image_url)

    Note over WF,R2: Second edit call - Continue

    WF->>EA: EditDesignInput (chat_history_key set)
    EA->>GM: restore_from_r2(project_id)
    GM->>R2: GET gemini_chat_history.json
    R2-->>GM: JSON bytes
    GM->>GM: deserialize_to_contents: reconstruct Content + Parts + thought_signatures
    EA->>GM: continue_chat(history, new_message, client)
    GM->>GEM: generate_content(contents=history + new_turn)
    GEM-->>GM: response with edited image
    EA->>GM: serialize_contents_to_r2(updated_history)
    GM->>R2: upload updated JSON
    EA-->>WF: EditDesignOutput (updated chat_history_key)
```

## Legend

| Component | File | Purpose |
|-----------|------|---------|
| generate_designs | `activities/generate.py` | 2 parallel standalone Gemini calls, no chat |
| edit_design | `activities/edit.py` | Bootstrap or continue multi-turn chat |
| gemini_chat.py | `utils/gemini_chat.py` | Chat create, serialize, restore, continue |
| image.py | `utils/image.py` | Draw numbered circle annotations on images |
| generation.txt | `prompts/generation.txt` | Initial generation prompt template |
| edit.txt | `prompts/edit.txt` | Annotation edit prompt template |
| room_preservation.txt | `prompts/room_preservation.txt` | Shared preservation clause |
