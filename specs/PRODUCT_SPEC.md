# Remo — Product Specification

> **Version**: 1.0 — Hackathon MVP
> **Last updated**: 2026-02-10
> **Product**: Remo (short for Remodeling)
> **Platform**: iOS native (iPhone Pro / iPad Pro for full experience; non-Pro supported with degraded features)

---

## 1. Product Overview

Remo is an AI-powered interior design assistant for homeowners and renters who want to restyle their living spaces — not renovate structurally, but redecorate: furniture, finishes, lighting, textiles, and layout. Users photograph their room, describe what they want, and Remo generates photorealistic redesign options they can iteratively refine using visual annotations and natural-language feedback, culminating in a downloadable design image and a shoppable specification list.

### Scope
- Each design project covers **one room only**. The user cannot work on multiple rooms within a single project.
### Out of Scope (MVP)
- **User authentication**: No sign-in for MVP. Users are anonymous. Sign in with Apple will be added when long-term history and cross-device access are needed.
- **Multi-room projects**: Each project covers one room. Multi-room projects with cohesive styling across rooms are a future enhancement.
- **User memory / preference learning**: The app does not remember user preferences, style choices, dislikes, or space usage patterns across projects. Each design project starts fresh with no carry-over from previous projects.
- **Long-term project history**: Approved projects are purged after 24 hours. Persistent history requires auth and is a future enhancement.
- **Switching design options after selection**: Once the user selects a design option, the unselected option is no longer accessible. Allowing users to switch back is a future enhancement.

### Success Criteria
- A user can go from "here's my room" to "here's my approved design + a shopping list of real products I can buy right now" in a single session.
- The shopping list contains real products with prices and purchase links — not generic keywords.
- If the user completed a LiDAR scan, recommended products are filtered to fit their actual room dimensions.
- Every behavior described below has corresponding test cases.

---

## 2. User Persona

**Primary user**: A homeowner or renter who wants to redecorate a room. They are not a professional designer. They have taste preferences but lack vocabulary to articulate them precisely. They may be time-constrained and prefer voice over typing on mobile.

**Key assumptions**:
- Users own an iPhone (Pro model for full experience with dimension-accurate shopping; non-Pro for style-matched shopping without dimensions).
- Users have at least one room they want to restyle.
- Users can take photos and optionally have inspiration images saved to their camera roll.
- Users want actionable output — not just inspiration, but real products they can buy.

---

## 3. Core User Flow (Happy Path)

```
Open App (no sign-in)
    ↓
New Design Project (or resume pending)
    ↓
Upload Current Room Photos (2 required, different angles)
    ↓
Upload Inspiration Photos (optional, up to 3, with notes)
    ↓
Photo Validation (all uploads checked)
    ↓
Room Scan via LiDAR (optional — user notified of trade-offs if skipped)
    ↓
Intake Chat Agent (optional but strongly recommended; short or extensive form)
    ↓
AI Generates 2 Design Options
    ↓
User Picks One Option
    ↓
Iteration Loop (up to 5 rounds):
    → Lasso Annotation (mark specific regions + structured feedback), OR
    → Full Regenerate (overall text feedback)
    ↓
User Approves Final Design
    ↓
Output: Downloadable Design Image + Shopping List (real products, prices, buy links)
    → With LiDAR: products are dimension-verified to fit your room
    → Without LiDAR: products matched by style (no size verification)
```

---

## 4. Feature Specifications

---

### 4.1 Home Screen

**Behavior**: No sign-in required. The app launches directly into the home screen. Users are anonymous — identified only by a device-local session token.

**Rules**:
- On launch, the user sees the home screen immediately (no auth wall).
- The home screen shows:
  - A prominent "New Design" button.
  - A list of **active (pending) projects** from the current device. Each shows: latest image thumbnail, room label, and which step the user left off at.
- Tapping a pending project resumes the workflow at the exact step where the user left off.
- **Completed projects are not shown** — once approved and downloaded, project data is purged (see 4.10 Data & Privacy).
- If there are no pending projects, show an empty state: "Start your first design" with the "New Design" button.

**Test Cases**:
| # | Scenario | Expected Result |
|---|----------|-----------------|
| HOME-1 | First-time user opens the app | Home screen shown immediately (no sign-in); "New Design" button visible; empty state message shown |
| HOME-2 | User taps "New Design" | App navigates to the photo upload step |
| HOME-3 | User has 1 pending project (left at intake step) | Pending project shown with thumbnail, room label, and "Resume" badge; tapping it resumes at intake chat with prior answers preserved |
| HOME-4 | User has 1 pending project (left mid-iteration, round 2 of 5) | Tapping it resumes at iteration view with latest image and all prior history intact |
| HOME-5 | User approved a project yesterday and opens app today | That project is no longer listed (data purged after grace period) |

---

### 4.3 Photo Upload & Validation

**Behavior**: The user uploads photos of their current room and optionally uploads inspiration photos. All photos are validated before proceeding.

#### 4.3.1 Current Room Photos

**Rules**:
- **Always 2 photos required**, taken from different angles.
- **User-facing instruction** (shown on the upload screen): "Take 2 photos from opposite corners of the room so we can see the full space." Accompanied by a simple top-down diagram showing a room with two camera icons in opposite corners, each with a field-of-view cone.
- **Internal goal**: The two photos should provide roughly 170°+ combined visual coverage of the room. This is not communicated to the user — the "opposite corners" instruction achieves it naturally.
- Photos are sourced from the device camera or camera roll.
- Each photo undergoes validation (see 4.3.3).

#### 4.3.2 Inspiration Photos

**Rules**:
- Optional. User can upload 0 to 3 inspiration photos from their camera roll.
- For each inspiration photo, the user can add a **short text note** (optional, max 200 characters) describing what they like about it. Example: "Love the warm lighting and the layered textiles."
- These notes are passed to the intake agent and the generation prompt to guide the design.

#### 4.3.3 Photo Validation

Every uploaded photo (current room or inspiration) is validated. A photo **fails** validation if any of the following are true:

| Failure Condition | User-Facing Message |
|-------------------|---------------------|
| Image is blurry (below sharpness threshold) | "This photo looks blurry. Please retake with a steady hand." |
| Image resolution is below 1024px on the shortest side | "This photo is too low resolution. Please use a higher quality image." |
| Image contains too many people (more than incidental presence) | "This looks like a photo of people, not a room. Please upload a photo of your space." |
| Image is not a room / interior space | "We couldn't identify a room in this photo. Please upload a photo of an interior space." |

**Rules**:
- Validation happens immediately after each photo is selected (not in batch after all photos are uploaded).
- If a photo fails, the user sees the specific error message and can retry with a different photo.
- The user cannot proceed to the next step until all required photos pass validation.
- Inspiration photos are validated for blur, resolution, and content. They are not required to be rooms (they could be detail shots of textures, furniture, etc.), but they **must not contain people or animals**. If they do, show: "Inspiration photos should show spaces, furniture, or design details — not people or animals. Please choose a different image."

**Test Cases**:
| # | Scenario | Expected Result |
|---|----------|-----------------|
| PHOTO-1 | User uploads 2 sharp, high-res room photos | Both pass validation; "Next" button becomes active |
| PHOTO-2 | User uploads 1 blurry photo | Validation fails with blur message; user prompted to retake |
| PHOTO-3 | User uploads a photo of a person (not a room) | Validation fails with "not a room" message |
| PHOTO-4 | User uploads a 480px wide image | Validation fails with resolution message |
| PHOTO-5 | User uploads only 1 valid room photo and taps "Next" | "Next" button disabled; message: "Please upload a second photo from a different angle" |
| PHOTO-7 | User uploads 3 inspiration photos, each with a text note | All 3 accepted; notes are saved and visible in a summary before proceeding |
| PHOTO-8 | User uploads 0 inspiration photos and taps "Skip" | App proceeds without inspiration; intake agent and generation work from room photos only |
| PHOTO-9 | User uploads an inspiration photo that is blurry | Validation fails with blur message for that specific photo |
| PHOTO-11 | User uploads an inspiration photo containing a person | Validation fails: "Inspiration photos should show spaces, furniture, or design details — not people or animals." |
| PHOTO-12 | User uploads an inspiration photo of a pet | Validation fails with same people/animals message |
| PHOTO-10 | User uploads 4 inspiration photos | App prevents the 4th upload with message: "Maximum 3 inspiration photos" |

---

### 4.4 Room Scan (LiDAR)

**Behavior**: The user can optionally scan their room using LiDAR to capture spatial dimensions. This enables dimension-aware design generation and size-verified product recommendations in the shopping list.

**Rules**:
- This step appears after photo upload.
- The app checks whether the device supports LiDAR.
  - **If LiDAR is available**: Show "Scan Your Room" with a brief explanation: "Scanning lets Remo design to your exact dimensions and give you precise shopping measurements."
  - **If LiDAR is not available**: Show a message: "Room scanning requires an iPhone Pro or iPad Pro. You can skip this step — your design will still look great, and you'll still get a shopping list, but we won't be able to verify that products fit your exact dimensions." Provide a "Continue without scan" button.
- **If the user skips** (by choice or device limitation): Display a clear, one-time notification: "Without a room scan, you'll still get a beautiful design and shopping list — but we can't verify that products fit your exact space. You can always measure manually." The user confirms and proceeds.
- **If the user scans**: The app launches the LiDAR scanning experience. The scan produces room geometry data (walls, floor, ceiling, openings). On completion, the app confirms: "Scan complete! We've captured your room's dimensions."
- The scan data is associated with the project and used in generation and shoppable specs.

**Test Cases**:
| # | Scenario | Expected Result |
|---|----------|-----------------|
| SCAN-1 | User on iPhone Pro taps "Scan Your Room" and completes scan | Scan data saved; confirmation shown; user proceeds to intake |
| SCAN-2 | User on iPhone Pro taps "Skip Scan" | Notification about trade-offs shown; user confirms; proceeds without scan data |
| SCAN-3 | User on non-Pro iPhone reaches scan step | Message about device limitation shown; "Continue without scan" button displayed; no scan option available |
| SCAN-4 | User starts scan but cancels mid-scan | No partial data saved; user can retry or skip |
| SCAN-5 | User completes scan and proceeds | 2 room photos are still required (scan does not reduce photo requirement) |

---

### 4.5 Intake Chat Agent

**Behavior**: An AI chat agent interviews the user to understand their needs, preferences, and constraints. The conversation produces a structured Design Brief that drives the generation step.

**Rules**:

#### Entry & Form Selection
- This step appears after room scan (or skip).
- The user sees two options:
  - **Quick Intake** — "~3 questions, ~2 minutes"
  - **Full Intake** — "~10 questions, ~8 minutes"
  - **Open Conversation** — "Tell us everything, take your time". An open-ended conversational mode (caps at ~15 turns).
  - **Skip** — "Jump straight to design (not recommended)". **Only available if the user uploaded at least 1 inspiration photo.** If the user skipped inspiration photos, the Skip option is hidden and intake is mandatory (Quick, Full, or Open Conversation).
- If the user chooses Skip (when available), show a soft warning: "The intake helps Remo understand your style and needs. Designs are significantly better with it. Skip anyway?" with "Yes, skip" and "Start intake" buttons.
- For all modes, show domain-based progress: "3 of 10 domains covered". This reflects actual coverage rather than a rigid question count, since the agent may cover multiple domains in one turn.

#### Voice Input
- Every text input field in the intake chat supports iOS native dictation (the microphone button on the standard iOS keyboard).
- No custom voice UI is needed — the system keyboard's built-in dictation is sufficient.

#### Question Planning & Adaptive Behavior

The intake agent operates from a **pre-planned question list** (the domains below) that acts as a guiding checklist. However, the agent is **adaptive** — if the user volunteers information that answers an upcoming question, the agent checks it off and moves on. If the user raises something unexpected and important, the agent pursues it before returning to the plan.

**Planning rules:**
- Before the conversation begins, the agent assembles an internal question checklist from the domains below, ordered by priority for the selected mode.
- As the user responds, the agent tracks which domains are covered (explicitly answered or inferred from the user's words).
- If a user's answer naturally covers a future question, the agent skips it and acknowledges: "You already mentioned you have two dogs — I'll factor that in."
- If a user's answer reveals something important not on the list (e.g., "we're selling in 6 months"), the agent asks a brief follow-up before returning to the plan.
- The agent never re-asks a domain that's already been adequately covered.

#### Question Domains

| Domain | What to Ask | Example Question |
|--------|-------------|-----------------|
| Room usage & occupants | Who uses this room, how often, for what activities | "Who spends time in this room and what do they typically do here?" |
| Current pain points | What's not working, what frustrates them | "What bothers you most about this room right now?" |
| Items to keep vs. replace | What existing furniture/items must stay | "Is there anything in the room you definitely want to keep?" |
| Lighting feel | Warm, cool, bright natural, dim ambient | "How do you want the room to feel lighting-wise — warm and cozy, or bright and airy?" |
| Color direction | Earth tones, neutrals, bold, monochrome | "What colors are you drawn to — warm earth tones, cool neutrals, something bold?" |
| Texture & materials | Soft & plush, clean & smooth, raw & natural | "Do you prefer soft fabrics like velvet and wool, or cleaner materials like leather and linen?" |
| Clutter level | Minimal, curated, layered & full | "Do you lean toward minimal with few objects, or do you like a room that feels layered and full?" |
| Overall mood | Calm retreat, energizing, cozy cocoon, polished | "In one phrase, how should this room make you feel?" |
| Practical constraints | Pets, kids, allergies, mobility needs, durability | "Any practical needs we should design around — pets, kids, allergies?" |
| Inspiration photo references | What specifically they like from uploaded inspo | "You noted 'warm lighting' on your first inspiration photo — should we match that exact warmth, or just lean warmer than what you have now?" |

**Mode behavior:**
- **Quick Intake**: Agent selects the 3 most impactful domains based on the room type and any inspiration photo notes. Adaptive — if the user covers extra domains in their answers, the agent captures it without adding questions.
- **Full Intake**: Agent covers all 10 domains. Adaptive — skips domains already covered by earlier answers.
- **Open Conversation**: Agent starts with a single open prompt: "Tell us about this room — what's on your mind, what you love, what you'd change, anything." The agent listens, follows up on what the user shares, and uses the domain notepad internally to identify gaps. When the user's natural conversation slows, the agent asks about any uncovered domains. The agent ends the conversation when all key domains are sufficiently covered, or when the user signals they're done. Caps at ~15 turns to prevent endless conversations.

#### Response Format: Numbered Quick-Reply

Every intake question is presented with **numbered options** the user can tap, plus a free-text option as the last choice. This minimizes typing on mobile.

**Example:**
```
How do you want the room to feel lighting-wise?

1. Warm & cozy
2. Cool & calm
3. Bright & airy
4. Something else (type your answer)
```

**Interaction rules:**
- Each question shows 3–4 predefined options as tappable buttons, rendered as a vertical list of numbered chips.
- The last option is always a free-text escape hatch: "Something else (type your answer)."
- Tapping a numbered chip immediately submits that answer — no extra confirmation tap needed.
- Tapping "Something else" opens the text input field (with iOS native dictation available).
- The user can also type the number in the text field (e.g., "1") or type a number plus elaboration (e.g., "1 but not too dim") — both are valid.
- If the user types free text without a number, the agent interprets it as a custom answer.

**Questions that are inherently open-ended** (e.g., "What bothers you most about this room?", "Who uses this room?") do not use numbered options — they show only a text input field since predefined choices wouldn't make sense.

#### Agent Behavior
- The agent asks one question at a time and waits for the user's response.
- The agent acknowledges the user's answer briefly before moving to the next question (e.g., "Got it — layered and warm." then the next question).
- The agent does not ask about budget.
- If the user provides a vague free-text answer like "I want it to look nice," the agent follows up with a numbered quick-reply to make it actionable: "1. Calm spa retreat / 2. Warm inviting living room / 3. Polished & sophisticated / 4. Something else"
- When all questions are complete, the agent summarizes the brief: "Here's what I heard: [summary]. Does this sound right?" The user can confirm or correct. The summary confirmation also uses numbered reply: "1. Looks good / 2. I want to change something."

#### Output: Structured Design Brief
The intake produces a structured Design Brief object containing:
- `room_type`: e.g., "living room", "bedroom"
- `occupants`: who and how they use it
- `pain_points`: list of current issues
- `keep_items`: list of items to preserve
- `style_profile`:
  - `lighting`: warm / cool / bright natural
  - `colors`: list of color directions
  - `textures`: list of preferred materials/textures
  - `clutter_level`: minimal / curated / layered
  - `mood`: one-phrase descriptor
- `constraints`: list of practical constraints (pets, kids, etc.)
- `inspiration_notes`: list of { photo_index, note, agent_clarification }

This Design Brief is displayed to the user as a summary card before generation begins.

**Test Cases**:
| # | Scenario | Expected Result |
|---|----------|-----------------|
| INTAKE-1 | User selects "Quick Intake" | Agent asks ~3 adaptive questions covering the most impactful domains; progress shows domains covered |
| INTAKE-2 | User selects "Full Intake" | Agent asks ~10 adaptive questions covering all domains; reorders/merges based on user responses; progress shows domains covered |
| INTAKE-3 | User taps "Skip" (has inspiration photos) | Warning message shown; if confirmed, user proceeds directly to generation with no brief |
| INTAKE-3a | User skipped inspiration photos and reaches intake step | Only "Quick Intake", "Full Intake", and "Open Conversation" options shown; "Skip" is hidden; intake is mandatory |
| INTAKE-4 | User gives vague free-text answer ("make it look good") | Agent follows up with numbered quick-reply options to clarify |
| INTAKE-5 | Agent finishes all questions | Summary card displayed: "Here's what I heard..." with "1. Looks good / 2. I want to change something" |
| INTAKE-6 | User taps "2" on the summary (wants to change) | Agent asks what to change; updates the brief and re-displays the corrected summary |
| INTAKE-7 | User taps "1" on the summary (looks good) | Design Brief is locked; user proceeds to generation |
| INTAKE-8 | User uses voice dictation for a response | Response is transcribed and submitted as normal text input |
| INTAKE-9 | User uploaded inspiration photos with notes | Agent references those notes in at least one question |
| INTAKE-10 | User skipped intake and proceeds to generation | Generation uses only photo data and inspiration notes (if any); no style profile is applied |
| INTAKE-11 | Agent asks a style question with numbered options | 3–4 tappable numbered chips shown + "Something else" as last option |
| INTAKE-12 | User taps numbered chip "2" | Answer is immediately submitted; agent acknowledges and moves to next question |
| INTAKE-13 | User types "1 but not too dim" in text field | Agent interprets as option 1 with elaboration; records both the selection and the note |
| INTAKE-14 | User taps "Something else" | Text input field opens with keyboard; user types or dictates custom answer |
| INTAKE-15 | Agent asks an open-ended question ("What bothers you about this room?") | No numbered options shown; only text input field displayed |
| INTAKE-16 | User selects "Open Conversation" | Agent opens with: "Tell us about this room..." prompt; no question count shown; conversation flows freely |
| INTAKE-17 | In Quick Intake, user's first answer covers 2 domains ("We have 2 dogs and I hate how dark it is") | Agent acknowledges both (constraints: pets + pain point: lighting); skips those domains; only asks 1 remaining question instead of 2 more |
| INTAKE-18 | In Full Intake, user volunteers info about an unplanned topic ("we're selling in 6 months") | Agent asks a brief follow-up ("Should the design appeal to potential buyers or just you?") then returns to the planned checklist |
| INTAKE-19 | In Open Conversation, user talks freely for several exchanges then pauses | Agent identifies uncovered domains and asks about them: "You haven't mentioned colors — any direction you're leaning?" |
| INTAKE-20 | In Open Conversation, user says "I think that's everything" | Agent checks the domain checklist; if key domains are covered, proceeds to summary; if gaps remain, asks: "One more thing — [uncovered domain question]" |

---

### 4.6 Design Generation

**Behavior**: The AI generates 2 photorealistic design options based on the room photos, inspiration photos, room scan data (if available), and the Design Brief (if completed).

**Rules**:
- Generation begins automatically after the user confirms the Design Brief (or after skipping intake).
- A loading state is shown during generation with a message: "Designing your space..." and an estimated wait indicator (e.g., a progress animation — not a specific time estimate).
- The app presents **2 design options** in one of two view modes:
  - **Side by side** — Both options visible for direct comparison.
  - **Swipeable** — One option at a time, swipe horizontally to switch.
- **Default behavior**: The view mode adapts to screen size (side-by-side on tablets/landscape, swipeable on phones/portrait).
- **User control**: A toggle icon allows users to override the default and switch between view modes at any time.
- Each option is a photorealistic image showing the redesigned room from the same angle as the primary room photo.
- Below each option, a brief text caption describes the key design choices (e.g., "Warm minimalist — linen sofa, walnut coffee table, warm pendant lighting").
- The user taps one option to select it. The selected option is highlighted.
- A "Choose this design" button confirms the selection and advances to the iteration phase.
- The unselected option is **not accessible** after selection.
- If the user doesn't like either option, they can tap "Start Over" which returns them to the intake step to adjust their brief. This does not count toward the 5-iteration limit.

**Generation inputs** (assembled automatically, not visible to user):
- Current room photos
- Inspiration photos + user notes
- Room geometry (if LiDAR scan completed)
- Structured Design Brief (if intake completed)

**Test Cases**:
| # | Scenario | Expected Result |
|---|----------|-----------------|
| GEN-1 | User confirms Design Brief | Loading state appears; 2 design options are generated and displayed |
| GEN-2 | User skips intake and proceeds to generation | 2 options generated using only photo data and inspiration notes |
| GEN-3 | User taps option A then taps "Choose this design" | Option A is selected; iteration phase begins with option A as the base image |
| GEN-4 | User taps "Start Over" | User returns to intake; no iteration count consumed |
| GEN-5 | Generation fails (model error, timeout) | Error message: "Something went wrong generating your design. Tap to retry." with a retry button |
| GEN-6 | User completed LiDAR scan | Generated designs respect room dimensions (furniture fits the space proportionally) |
| GEN-7 | User did not complete LiDAR scan | Generated designs are based on visual estimation from photos only |
| GEN-8 | User uploaded inspiration photos with notes | Generated designs visibly incorporate referenced elements from inspiration (e.g., warm lighting if noted) |
| GEN-9 | User views options on tablet (landscape) | Side-by-side view mode is shown by default |
| GEN-10 | User views options on phone (portrait) | Swipeable view mode is shown by default |
| GEN-11 | User taps view toggle icon | View mode switches between side-by-side and swipeable; preference persists for current session |

---

### 4.7 Iteration: Lasso Annotation

**Behavior**: The user marks specific regions on the design image using a freehand lasso tool, attaches structured feedback to each region, and generates a revised image. This is the primary iteration method.

#### 4.7.1 Entering Lasso Mode

- After selecting a design option (or viewing a revision), the user sees the design image full-screen with an "Annotate" button.
- Tapping "Annotate" enters Lasso Mode. The UI shows:
  - The design image (zoomable, pannable).
  - A lasso drawing tool active by default (finger draws on the image).
  - A side panel (on iPad) or bottom sheet (on iPhone) showing the **Edit List** (initially empty).
  - A "Generate Revision" button (disabled until at least 1 valid region exists).
  - An iteration counter: "Revision 1 of 5" (or current count).

#### 4.7.2 Drawing a Region

**Rules**:
- The user draws a freehand closed loop by dragging their finger on the image.
- The loop **auto-closes**: when the user lifts their finger, the app draws a straight line from the endpoint back to the start point to close the shape.
- A region is **valid** only if:
  - It is a closed shape (guaranteed by auto-close).
  - It exceeds a minimum area threshold (to avoid accidental tiny regions — threshold: at least 2% of the image area).
  - It does not self-intersect. If the drawn path crosses itself, the app shows: "Your selection crossed over itself. Please try again." and discards the attempt.
- **No overlapping regions allowed.** If a new region overlaps any existing region, the app shows: "Regions can't overlap. Please draw around a different area, or delete an existing region first." and discards the attempt.
- **Maximum 3 regions per revision.** If the user tries to draw a 4th region, the app shows: "For best results, limit to 3 edits per revision. Tap 'Generate Revision' and make additional changes in the next round."

#### 4.7.3 Region Rendering

- Each valid region is rendered as:
  - A **thin high-contrast outline** (e.g., white with dark shadow or vice versa, adapting to image brightness) following the drawn path.
  - A **number chip** (circle with the region number: "1", "2", "3") placed **outside** the region boundary, near the top-right of the region's bounding box, with a small offset.
- **Chip placement rules**:
  - Default: top-right of the region bounding box, offset outward by 12pt.
  - If the default position would go off-canvas, place at the nearest visible edge.
  - Chips must not overlap each other. If they would, shift subsequent chips along the region boundary.

#### 4.7.4 Region Editor

Immediately after a valid region is created, the **Region Editor** opens (as a sheet/modal). The editor contains:

| Field | Required? | Type | Options/Constraints |
|-------|-----------|------|---------------------|
| Action | Required | Single select | Replace · Remove · Change finish/color/material · Resize (bigger/smaller) · Reposition (move slightly) |
| Instruction | Required | Text input (1 sentence) | Min 10 characters. Example: "Replace rug with solid neutral wool rug, no pattern" |
| Avoid | Optional | Text tokens | Comma-separated short phrases. Example: "no brass, no patterns, no glossy finish" |
| Style nudges | Optional | Toggle chips | cheaper · premium · more minimal · more cozy · more modern · pet-friendly · kid-friendly · low maintenance |

- The user fills out the editor and taps "Save." The region appears in the Edit List.
- If the user taps "Cancel" in the editor, the region is discarded (outline removed).

#### 4.7.5 Edit List (Side Panel / Bottom Sheet)

- Shows all regions as an **ordered numbered list**: 1, 2, 3.
- Each list item shows: number, action, first ~40 characters of instruction.
- **Selecting**: Tapping a list item or tapping the region outline/chip on the image selects that region (highlighted outline, editor accessible).
- **Editing**: Tapping a selected list item opens the Region Editor with existing values pre-filled.
- **Deleting**: Swipe-to-delete on a list item, or a delete button in the selected state. Removes the region and renumbers remaining regions.
- **Reordering**: Long-press and drag list items to change priority order. When reordered, all region numbers update (chips on image and list entries) to maintain a 1..N mapping.
- Numbers represent **application priority** — region 1 is applied first, region 2 second, etc.

#### 4.7.6 Generate Revision

- The "Generate Revision" button is enabled when at least 1 region with a completed editor exists.
- Tapping it produces the following output artifacts (assembled by the system, not shown to the user in this form):

**Output artifacts**:
1. **Base image**: The current design image being edited.
2. **Overlay image**: The base image with region outlines and number chips rendered on top (visual reference for the model).
3. **Edit instruction payload** (structured):
   ```
   {
     "regions": [
       {
         "region_id": 1,
         "action": "Replace",
         "instruction": "Replace rug with solid neutral wool rug, no pattern",
         "avoid": ["brass", "patterns", "glossy finish"],
         "constraints": ["more minimal", "pet-friendly"]
       },
       ...
     ],
     "global_locks": "Do not change anything outside the numbered regions.",
     "preserve": "Preserve camera angle, room architecture, lighting direction, and all unchanged items.",
     "global_constraints": ["No text in final image", "Photorealistic"],
     "edit_order": "Apply edits in numbered order (1, 2, 3)."
   }
   ```
4. **Prompt** (generated verbatim at runtime):
   ```
   You are editing Image A. Image B shows numbered regions.
   1) Region #1 ([Action]): [Instruction]. Avoid: [avoid tokens]. Constraints: [constraint tokens].
   2) Region #2 ([Action]): [Instruction]. ...
   Do not change anything outside the numbered regions.
   Preserve camera angle, room architecture, lighting direction, and all unchanged items.
   No text in the final image.
   Output must be photorealistic.
   ```

- After generation completes, the new image replaces the current view. The iteration counter increments.
- The user can annotate the new image for another round, or approve.

#### 4.7.7 Revision History

Each "Generate Revision" appends a revision record:
- Timestamp
- Base image reference
- Overlay image reference
- Ordered edits payload
- Resulting image reference

The user can swipe back through previous revisions to compare. This is view-only — they cannot branch from an older revision (future enhancement).

**Test Cases**:
| # | Scenario | Expected Result |
|---|----------|-----------------|
| LASSO-1 | User draws a clean closed loop exceeding minimum area | Region created with outline + number chip; Region Editor opens |
| LASSO-2 | User draws a tiny region (< 2% of image area) | Region discarded with message: "Selection too small. Please draw a larger area." |
| LASSO-3 | User draws a self-intersecting path | Region discarded with message: "Your selection crossed over itself. Please try again." |
| LASSO-4 | User draws a region overlapping an existing region | Region discarded with overlap warning message |
| LASSO-5 | User draws a 4th region | Blocked with message about limiting to 3 per revision |
| LASSO-6 | User completes Region Editor with action + instruction (12 chars) and saves | Region appears in Edit List with number, action, and instruction preview |
| LASSO-7 | User enters instruction < 10 characters | Save button disabled; hint shown: "Please describe the change in at least 10 characters" |
| LASSO-8 | User cancels the Region Editor | Region outline is removed; no entry in Edit List |
| LASSO-9 | User taps a region chip on the image | Corresponding region is selected (highlighted); list scrolls to that entry |
| LASSO-10 | User taps a list item | Corresponding region is highlighted on image; editor accessible |
| LASSO-11 | User deletes region #2 out of 3 | Region #2 removed; former region #3 becomes #2; chips and list renumber |
| LASSO-12 | User reorders: drags region #3 to position #1 | All regions renumber: former #3 → #1, former #1 → #2, former #2 → #3; chips update on image |
| LASSO-13 | Number chip would render off-canvas (region at top-right of image) | Chip placed at nearest visible edge |
| LASSO-14 | User taps "Generate Revision" with 2 completed regions | Loading state; new image generated; iteration counter increments; new image replaces current view |
| LASSO-15 | User taps "Generate Revision" with 0 regions | Button is disabled; cannot tap |
| LASSO-16 | Generation fails | Error message with retry button; iteration count not incremented |
| LASSO-17 | User is on revision 5 of 5 and tries to annotate again | Message: "You've used all 5 revision rounds. Please approve your design or start a new project." Annotate button disabled |
| LASSO-18 | User swipes back to view revision 1 | Previous revision image shown in read-only mode |

---

### 4.8 Iteration: Full Regenerate

**Behavior**: Instead of annotating specific regions, the user provides overall text feedback to regenerate the entire design from scratch. This shares the same 5-iteration pool as lasso annotations.

**Rules**:
- On the iteration screen, alongside the "Annotate" button, there is also a "Regenerate" button.
- Tapping "Regenerate" opens a text input sheet with:
  - Prompt: "What would you like to change overall?"
  - A text field (supports iOS native dictation).
  - A "Generate" button (disabled until input is at least 10 characters).
- Submitting triggers a full regeneration using the original inputs (photos, scan, brief) plus the user's feedback as an additional directive.
- The new image replaces the current design. The iteration counter increments.
- Full regeneration and lasso annotation share the same iteration pool (max 5 total).

**Test Cases**:
| # | Scenario | Expected Result |
|---|----------|-----------------|
| REGEN-1 | User taps "Regenerate" and enters "Make the whole room feel warmer and more inviting with earth tones" | Input accepted (>10 chars); "Generate" button enabled |
| REGEN-2 | User enters "darker" (7 chars) | "Generate" button disabled; hint: "Please provide more detail (at least 10 characters)" |
| REGEN-3 | User submits valid feedback | Loading state; new full design generated; iteration counter increments |
| REGEN-4 | User has used 3 lasso iterations, then uses "Regenerate" | Iteration counter shows "4 of 5"; regeneration proceeds |
| REGEN-5 | User has used 5 iterations (any mix of lasso and regenerate) | Both "Annotate" and "Regenerate" buttons are disabled; only "Approve" is available; message shown about revision limit |
| REGEN-6 | User uses voice dictation for feedback | Transcribed text appears in the field; character count applies to transcribed text |

---

### 4.9 Approval & Output

**Behavior**: The user approves the current design, which becomes the final output. They receive a downloadable design image and an actionable shopping list with real products they can buy.

**Rules**:
- An "Approve Design" button is visible at every iteration step (after initial generation).
- Tapping "Approve Design" shows a confirmation: "Happy with this design? Once approved, it's final." with "Approve" and "Keep editing" buttons.
- On approval:
  - The design image is marked as final.
  - The project status changes to "Approved."
  - The system generates the shopping list (see 4.9.2).
  - The user sees the **Output Screen**.

#### 4.9.1 Output Screen

**Design Image**:
- Displayed full-screen.
- "Save to Photos" button: saves the image to the device camera roll.
- Standard iOS share sheet available for sharing via Messages, AirDrop, etc.

**Shopping List** (always shown — the core deliverable):
- Displayed below the design image as a scrollable product list.
- The system analyzes the approved design image + Design Brief to identify every distinct furnishing, fixture, and decor element, then matches each to a real purchasable product.

#### 4.9.2 Shopping List Generation

**How product matching works**:
1. The AI analyzes the final design image and identifies each distinct item (e.g., sofa, rug, coffee table, pendant light, throw pillow, wall art).
2. For each item, the AI extracts: category, style attributes, material, color, and approximate proportions.
3. If LiDAR scan was completed: the system cross-references room dimensions to determine real-world size requirements for each item (e.g., "rug must be ~6×4 feet to fit this floor area").
4. The system searches the product catalog for the closest match by style + material + color + dimensions (if available).
5. Each product is scored by match confidence. Only products above a confidence threshold are shown.

**Product search approach (MVP)**:
- Product matching is powered by **Exa search API** — no pre-curated catalog needed.
- For each item identified in the design, the system constructs a search query from the item's attributes (category, style, material, color, dimensions if LiDAR available) and sends it to Exa.
- Exa returns real product pages from major retailers (Amazon, Wayfair, West Elm, Overstock, CB2, IKEA, etc.) with product names, prices, descriptions, and direct purchase URLs.
- The system extracts structured product data from Exa results (product name, price, retailer, URL, dimensions if listed) using an LLM pass over the returned content.
- **Fallback**: If Exa returns no usable results for an item, show a Google Shopping search link with pre-filled keywords.
- Post-MVP: add affiliate link wrapping for revenue, retailer preference settings, price comparison across results.

#### 4.9.3 Shopping List Display

Each product card shows:
| Field | Example | Source |
|-------|---------|--------|
| **Product image** | (thumbnail from retailer) | Product catalog |
| **Product name** | "Harmony Linen Sofa — Oat" | Product catalog |
| **Retailer** | "West Elm" | Product catalog |
| **Price** | "$1,299" | Product catalog |
| **Dimensions** | "84″W × 36″D × 33″H" | Product catalog |
| **Why this match** | "Matches the linen sofa in your design — similar color, material, and scale" | AI-generated |
| **Buy** button | Opens product URL in Safari | Product catalog |
| **Fit badge** (LiDAR only) | "✓ Fits your space" or "⚠ May be tight" | Computed from scan dimensions vs. product dimensions |

**Rules**:
- Products are grouped by area of the room (e.g., "Seating", "Lighting", "Rugs & Flooring", "Decor & Accessories").
- Each group is collapsible.
- A **total estimated cost** is shown at the top of the shopping list (sum of all product prices).
- A "Share Shopping List" button generates a formatted text list (product name, price, link per item) and opens the iOS share sheet.
- A "Copy All" button copies the full list as text to clipboard.
- Each individual product card has a "Copy Link" button.

**LiDAR vs. non-LiDAR experience**:
| Feature | With LiDAR | Without LiDAR |
|---------|-----------|---------------|
| Product style matching | Yes | Yes |
| Product dimension filtering | Yes — only shows products that physically fit | No — shows best style match regardless of size |
| Fit badge per product | Yes — "Fits your space" / "May be tight" | Not shown |
| Dimension callout | "Your wall is 8ft — this bookshelf is 6ft wide ✓" | Not shown |
| Banner message | None | "Tip: We matched products by style. For size-verified recommendations, use Room Scan on an iPhone Pro next time." |

#### 4.9.4 Product Match Confidence

- Each matched product has an internal confidence score (not shown to user).
- **High confidence (≥0.8)**: Product shown normally.
- **Medium confidence (0.5–0.79)**: Product shown with label "Close match" — the AI couldn't find an exact match.
- **Low confidence (<0.5)**: Product is **not shown**. Instead, the item slot shows: "We couldn't find an exact match for [item]. Try searching: [search keywords]" with a button that opens a Google Shopping search with those keywords.
- This ensures the shopping list never feels padded with irrelevant products.

**Test Cases**:
| # | Scenario | Expected Result |
|---|----------|-----------------|
| APPROVE-1 | User taps "Approve Design" and confirms | Project marked as approved; shopping list generates; Output Screen shown |
| APPROVE-2 | User taps "Approve Design" then "Keep editing" | Returns to iteration view; nothing changes |
| APPROVE-3 | User taps "Save to Photos" | Image saved to camera roll; success confirmation shown |
| APPROVE-4 | User taps share button on design image | iOS share sheet opens with the design image |
| APPROVE-5 | Approved project with LiDAR scan | Shopping list shows products with fit badges and dimension callouts |
| APPROVE-6 | Approved project without LiDAR scan | Shopping list shows style-matched products; no fit badges; tip banner about Room Scan shown |
| APPROVE-7 | User taps "Buy" on a product card | Safari opens the product URL |
| APPROVE-8 | User taps "Share Shopping List" | iOS share sheet opens with formatted text list of all products (name, price, link) |
| APPROVE-9 | User taps "Copy All" | Full product list copied to clipboard; toast: "Shopping list copied!" |
| APPROVE-10 | User taps "Copy Link" on a single product | Product URL copied to clipboard |
| APPROVE-11 | User returns to home screen after approval | Project no longer appears in pending list (data will be purged after grace period) |
| APPROVE-12 | AI finds high-confidence match for a sofa | Product card shown normally with name, price, image, buy link |
| APPROVE-13 | AI finds medium-confidence match for a lamp | Product card shown with "Close match" label |
| APPROVE-14 | AI cannot find a good match for a specific art piece | Slot shows "We couldn't find an exact match" + search keywords + Google Shopping button |
| APPROVE-15 | Design has 8 identifiable items | Shopping list shows up to 8 product cards grouped by room area |
| APPROVE-16 | Total estimated cost is displayed | Sum of all matched product prices shown at top of shopping list |

---

### 4.10 Data & Privacy

**Behavior**: Remo stores project data only for the duration of an active design session. No user accounts, no long-term storage. All data is automatically purged after the project lifecycle ends.

**Rules**:

#### Data Lifecycle

| Project State | Data Behavior |
|---------------|---------------|
| **Active (in-progress)** | All project data (photos, scan, brief, generated images, annotations) is persisted server-side to support reliability and resume. |
| **Interrupted (app crash, user leaves)** | Data remains persisted. User can reopen the app and resume at the exact step they left off. |
| **Approved (user downloads final output)** | A **24-hour grace period** begins. During this window the user can still re-open and re-download. After 24 hours, all project data is permanently deleted from the server. |
| **Abandoned (user never returns)** | If no activity for **48 hours**, all project data is permanently deleted from the server. |

#### Storage model

"Ephemeral" means **no permanent retention**, not "no storage at all." During an active project lifecycle, data is persisted to enable reliability and resume.

| Location | What is stored | Purpose |
|----------|---------------|---------|
| **Server-side** | All project data (photos, scan, brief, generated images, annotations, revision history) | Workflow reliability — enables crash recovery and resume |
| **Device-side** | Lightweight project ID(s) only | Reconnects the app to the correct server-side workflow on reopen |

**Resume flow**:
1. User starts a project → server creates a workflow and returns a project ID → app saves that ID locally on device.
2. User leaves (or app crashes) → workflow pauses server-side; project ID persists on device.
3. User reopens app → app reads local project IDs → asks server if each workflow is still alive → if yes, shows it as a resumable pending project.
4. If the app is deleted or device is wiped → local project IDs are lost → server-side data auto-purges at the 48-hour abandonment window.

#### What is stored server-side during an active session
- Uploaded photos (current room + inspiration)
- Inspiration photo notes
- LiDAR scan data (if captured)
- Design Brief (intake output)
- All generated images (options + revisions)
- Annotation data (lasso regions, edit payloads)
- Revision history

#### What is never stored
- User identity (no accounts, no Apple ID, no email)
- Cross-project data (no preference learning, no history beyond active sessions)
- Data beyond the purge window

#### User communication
- On first use, a brief onboarding tooltip: "Your design data is temporary — save your final image to Photos when you're done. We automatically delete all project data within 48 hours."
- On approval screen, a reminder: "Make sure to save your design image and copy your specs. Project data will be deleted after 24 hours."

**Test Cases**:
| # | Scenario | Expected Result |
|---|----------|-----------------|
| DATA-1 | App crashes mid-generation | User reopens app; pending project appears on home screen; resumes at the correct step |
| DATA-2 | User force-quits app during intake chat | User reopens app; pending project shown; intake resumes with prior answers preserved |
| DATA-3 | User approves design and reopens app within 24 hours | Project data still accessible for re-download |
| DATA-4 | User approves design and reopens app after 24 hours | Project data is gone; home screen shows empty state or other active projects only |
| DATA-5 | User starts a project, never returns for 48 hours | Project data is automatically purged; no trace remains |
| DATA-6 | User sees onboarding tooltip on first launch | Tooltip explains temporary data policy; dismissible |
| DATA-7 | User is on approval screen | Reminder about saving before purge is visible |

---

## 5. Priority Matrix (Hackathon MVP)

| Priority | Feature | Status | Notes |
|----------|---------|--------|-------|
| **Must Ship** | A — Photo Upload & Validation | Core | |
| **Must Ship** | B — LiDAR Room Scan | Core | Enables dimension-accurate shopping |
| **Must Ship** | C — Intake Chat Agent | Core | |
| **Must Ship** | D — Design Generation (2 options) | Core | |
| **Must Ship** | E — Lasso Annotation Iteration | Core | |
| **Must Ship** | F — Full Regenerate Iteration | Core | |
| **Must Ship** | G — Shoppable Shopping List | Core | The payoff — real products, prices, buy links. Pre-curated catalog for MVP. |
| **Must Ship** | H — Session Reliability (resume interrupted workflows) | Core | |

**Note on auth**: No user authentication for MVP. Users are anonymous. Auth (Sign in with Apple) is a future enhancement, needed when long-term project history and cross-device access are added.

---

## 6. Global Constraints & Edge Cases

### Iteration Limits
- Maximum **5 iterations** total (lasso + full regenerate combined) per project.
- Maximum **3 lasso regions** per single revision pass.
- Minimum **10 characters** for any text feedback (region instruction or full regenerate).
- These numbers are configurable post-launch but fixed for MVP.

### Image Generation Constraints
- All generated images must be **photorealistic**.
- No text may appear in generated images.
- Generated images must preserve the camera angle and room architecture from the original photo.
- Edits must be scoped: only change what the user asked to change.

### Error States
| Scenario | Behavior |
|----------|----------|
| Network lost during generation | Show: "No internet connection. Please check your connection and try again." Generation does not consume an iteration count. |
| Model returns an error | Show: "Something went wrong. Tap to retry." Retry does not consume an iteration count. |
| LiDAR scan fails mid-scan | Show: "Scan interrupted. Would you like to try again or skip?" No partial data saved. |
| App backgrounded during generation | Generation continues in background (if possible); on return, show result or loading state. |

### Accessibility
- Color contrast for region outlines and number chips must meet WCAG AA standards against varied image backgrounds (hence the high-contrast outline with shadow approach).

---

## 7. Glossary

| Term | Definition |
|------|-----------|
| **Design Project** | A single room redesign, from photo upload through approval |
| **Current Room Photo** | A photo of the room as it exists today |
| **Inspiration Photo** | A reference image showing styles/elements the user likes |
| **Room Scan** | A LiDAR-captured 3D model of the room's geometry and dimensions |
| **Design Brief** | Structured output of the intake chat: style profile, constraints, preferences |
| **Lasso Region** | A freehand-drawn closed area on the design image targeting a specific edit |
| **Region Editor** | The form for specifying what action and instruction to apply to a lasso region |
| **Edit List** | The ordered, numbered list of all lasso regions and their associated edits |
| **Revision** | One round of iteration (lasso annotation or full regenerate) producing a new image |
| **Shopping List** | The actionable output: real matched products with names, prices, retailer links, and (if LiDAR) fit verification |
| **Exa Search** | The search API used to find real purchasable products matching each design element in real time — no pre-curated catalog needed |
| **Fit Badge** | A LiDAR-derived indicator showing whether a recommended product physically fits the user's room |

---

*End of specification.*
