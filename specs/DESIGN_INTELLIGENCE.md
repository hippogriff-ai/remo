# Design Intelligence Reference — T3 Intake & Shopping Agents

> **Purpose**: This document encodes the design reasoning that Remo's intake agent and shopping pipeline must exhibit. It is the implementation reference for:
> 1. Engineering the intake agent's system prompt
> 2. Building the DesignBrief elevation logic (translating vague desires into prompt-ready parameters)
> 3. Constructing design-aware item extraction and product scoring
>
> **Audience**: The T3 coding agent building `run_intake_chat` and `generate_shopping_list` activities.
>
> **How to use**: Distill relevant sections into Claude system prompts. The intake system prompt should internalize Sections 1–5. The shopping extraction prompt should internalize Sections 6–7.

---

## 1. The Three-Layer Design Intelligence Stack

The intake agent reasons through three complementary layers, applied in order:

### Layer 1 — Spatial Foundation (Ching)

**What it answers**: Is the space organized correctly?

The agent should assess the room's spatial characteristics from the photos and user description:

- **Spatial relationships**: space-within-a-space, interlocking, adjacent, or linked-by-intermediary
- **Organizational patterns**: centralized, linear, radial, clustered, or grid. Residential homes most commonly use linear and clustered.
- **Circulation**: are traffic paths clear? (minimum 36" / 91cm for residential hallways)
- **Proportion**: the 2:3 rule — largest furniture piece ≈ 2/3 of seating area; coffee table ≈ 2/3 of sofa length
- **Ordering principles**: axis, symmetry, hierarchy, datum, rhythm, transformation, balance

**Ching's core maxim** (use as a validation check): "Order without diversity can result in monotony and diversity without order can produce chaos."

### Layer 2 — Human-Centered Refinement (de Wolfe)

**What it answers**: Is it right for these specific people?

Every recommendation passes through de Wolfe's triple filter: **suitability, simplicity, and proportion**.

- **Method acting approach**: "I study the people who are to live in this house, and their needs, as thoroughly as I studied my parts." → The agent must understand LIVES before prescribing SPACES.
- **Light as the first design decision**: "My first thought in laying out a room is the placing of the electric light openings."
- **Color gradient default**: floors darkest, walls lighter, ceilings lightest
- **The elimination principle**: every object must earn its place through functional meaning or aesthetic purpose. The agent should bias toward considered restraint over accumulation.

### Layer 3 — Emotional Impact (Draper + Neuroarchitecture)

**What it answers**: Does it elevate mood and create joy?

- **Color as a psychological tool**: "Lovely, clear colors have a vital effect on our mental happiness."
- **The boldness heuristic**: always include one element that makes people talk — prevents bland, forgettable designs
- **Curves reduce threat**: fMRI studies show curvilinear spaces activate reward centers; sharp angles activate the amygdala (threat). Default: ~70% curved/organic forms, 30% angular in relaxation spaces.
- **Biophilic design**: every primary room should have at least one nature element (plant, nature view, natural material, nature art)
- **Prospect-refuge**: primary seating needs back protection (wall, bookcase) with forward view toward entry and windows

**How to use in the system prompt**: The agent should process each user response through all three layers. When a user says "I want it to feel open," the agent should think: Layer 1 (spatial: circulation paths, sightlines), Layer 2 (is openness suitable for their lifestyle? — WFH needs privacy), Layer 3 (emotional: prospect-dominant with strategic refuge points).

---

## 2. Translation Engine: Client Language → Design Parameters

**This is the single most important section for prompt quality.** When the intake agent echoes "cozy" back into the DesignBrief, Gemini gets a vague prompt. When it translates "cozy" into specific parameters, Gemini generates the right image.

### Core Translation Table

Embed this in the intake system prompt. The agent should use it to ELEVATE user responses.

| Client says | Design parameters for DesignBrief |
|---|---|
| **"Cozy"** | Warm palette (amber, terracotta, warm wood tones), layered textiles (knit throws, plush rugs, velvet upholstery), low warm lighting (2200–2700K), intimate-scale furniture (deep-seated, enveloping forms), natural materials (wool, linen, wood), refuge-dominant layout |
| **"Modern"** | Neutral base (whites, grays, black) with strategic color accents, clean geometric lines, sleek furniture with exposed legs, glass/steel/concrete materials, architectural lighting (recessed, linear LED), open plan with negative space as design element |
| **"More space"** | Light palette (LRV 70+), mirrors opposite windows, transparent/glass furniture, consistent flooring throughout, multi-functional pieces, furniture with visible legs, improved circulation paths (≥36"), uplighting to raise perceived ceiling |
| **"Calm"** | Cool muted tones (soft blues, sage, lavender), minimal pattern, closed storage (no visual clutter), sound-absorbing materials (rugs, curtains, upholstered furniture), biophilic elements (plants, natural materials, water feature), diffused lighting, prospect-refuge balance |
| **"Luxurious"** | Rich fabrics (velvet, silk, leather), marble and brass accents, statement custom pieces, chandeliers and layered dramatic lighting, crown molding and decorative millwork, jewel tones (emerald, sapphire, ruby) or sophisticated deep neutrals |
| **"Bright & airy"** | White/off-white walls (LRV 80+), sheer curtains (maximize natural light), light-toned wood (ash, birch, bleached oak), cool-white lighting (3500–4000K), minimal furniture footprint, mirrors, glass, and transparent materials, high-contrast accent sparingly |
| **"Rustic"** | Reclaimed/distressed wood, wrought iron, natural stone, woven textiles (jute, burlap, linen), earth tones (brown, green, rust), warm filament-style lighting (2200K), handcrafted/artisanal objects, layered textures |
| **"Minimalist"** | Monochromatic or very limited palette, architectural furniture as sculpture, hidden storage (no visible clutter), negative space as intentional, high-quality materials over quantity, precise geometric forms, museum-like lighting |
| **"Bohemian"** | Saturated warm colors + rich jewel accents, mixed global patterns (kilim, ikat, suzani), layered textiles everywhere, low seating (floor cushions, poufs), natural + handmade materials, abundant plants, warm ambient string/lantern lighting, curated-eclectic clutter |
| **"Scandinavian"** | Pale neutral base (white, light gray, blonde wood), hygge-inspired textiles (sheepskin, knit, wool), simple functional furniture (clean lines, no ornament), plants as primary decor, natural light maximized, warm-white lighting (2700–3000K), minimal accessories |

### How the Agent Should Use Translations

1. When user says a vague preference, the agent should acknowledge it naturally AND internally map it
2. The `style_profile` fields should contain the TRANSLATED parameters, not the raw user words
3. The `pain_points` and `constraints` should preserve the user's original language (for user fidelity)
4. When generating the summary, show the TRANSLATED version so the user can correct it:
   - "You said 'cozy' — I'm interpreting that as warm earth tones, layered natural textiles, and soft ambient lighting. Does that capture what you mean?"

---

## 3. The DIAGNOSE Reasoning Pipeline

The intake agent should follow this 8-step reasoning process (encode in the system prompt as behavioral instructions):

**D — Detect**: Parse user input for explicit preferences AND implicit signals. Watch for contradictions (e.g., "I love minimalism" + "I can't throw anything away" → the real need is organized storage that LOOKS minimal).

**I — Interpret**: Translate vague desire terms into specific design parameter sets using the translation engine above.

**A — Analyze**: Identify root causes behind surface complaints using the "why" technique. When a user says "the room feels wrong," probe: Is it too dark? (lighting), too cramped? (spatial), too cold/disconnected? (comfort), too chaotic? (harmony), or not "them"? (identity).

**G — Generate**: Map diagnosed problems to applicable design principles from the three-layer stack.

**N — Narrate**: Construct a coherent design concept as a unified vision, not a checklist of disconnected fixes. The brief should tell a story: "warm minimalist retreat" not "warm + minimal + whatever."

**O — Optimize**: Check internal consistency. Does the color palette work with the lighting plan? Does the furniture scale work with the room dimensions? Resolve conflicts and surface trade-offs to the user.

**S — Specify**: Fill DesignBrief fields with specific, prompt-ready parameters (not vague adjectives).

**E — Evaluate**: Present the synthesized brief back to the user for confirmation. Highlight any inferred preferences ("I interpreted your love of natural light as wanting sheer curtains — correct?").

---

## 4. Enhanced Question Strategy

### The "Three-Why" Technique

When a user gives a surface-level answer, probe deeper to reach the root cause:

> User: "I want new furniture"
> → Why? → "The living room doesn't feel right"
> → Why? → "We never sit in there"
> → Why? → "It's not comfortable and there's nowhere to put drinks"
> **Real need**: comfortable seating with functional surfaces, not new furniture

The agent doesn't literally ask "why" three times. It uses intelligent follow-up questions that naturally reach the root cause.

### Diagnostic Questions by Domain

These replace or supplement the generic domain questions. The agent should select from these based on what it's learned:

| Domain | Surface question (current) | Diagnostic alternative |
|---|---|---|
| Pain points | "What bothers you about this room?" | **"What daily task feels harder than it should because of your space?"** — identifies functional failures, not just aesthetic complaints |
| Keep/replace | "Anything you want to keep?" | **"What would you grab first if you had to furnish this room from scratch?"** — reveals true attachment vs. inertia |
| Lighting | "Warm or cool lighting?" | **"What time of day does this room feel best? What time does it feel worst?"** — diagnoses natural light issues and desired ambiance |
| Color | "What colors do you like?" | **"Walk me through a space you've been in recently that made you feel great. What do you remember about it?"** — captures emotional color response, not just named colors |
| Mood | "How should it feel?" | **"If a friend walked into this room, what would you want them to think or feel?"** — reveals social and identity aspirations |
| Clutter | "Minimal or layered?" | **"Where does clutter tend to accumulate, and why?"** — reveals missing storage systems, not just preference |
| Practical | "Pets or kids?" | **"What room do you avoid, and why?"** — uncovers emotional associations with space |
| Space | (not currently in domains) | **"Where do family conflicts about space happen?"** — exposes zoning and privacy failures |

### When to Use Quick-Reply Chips vs. Open-Ended

- **Use chips** when the answer is classifiable (lighting feel, clutter level, style direction) — reduces friction
- **Use open-ended** when probing for pain points, lifestyle stories, or emotional responses — chips would constrain valuable information
- **Use chips AFTER an open-ended answer** to refine: "You mentioned it feels cramped — is that more about: 1. Too much furniture, 2. Poor layout, 3. Dark colors making it feel smaller, 4. Something else?"

---

## 5. Design-Aware Brief Elevation

### What "Elevation" Means

The intake agent's job is NOT to record what the user said. It's to translate user intent into professional design language that Gemini can execute.

**Bad brief** (echos user words):
```json
{
  "style_profile": {
    "lighting": "warm",
    "colors": ["blue", "white"],
    "textures": ["soft"],
    "mood": "cozy"
  }
}
```

**Good brief** (elevated to design parameters):
```json
{
  "style_profile": {
    "lighting": "warm ambient base (2700K), table lamps at reading positions, accent uplighting on textured wall",
    "colors": ["navy accent (10% — throw pillows, art)", "warm ivory walls (60%)", "natural oak and cream (30% — furniture, textiles)"],
    "textures": ["boucle upholstery on primary seating", "woven jute area rug", "linen curtains", "brushed brass hardware accents"],
    "clutter_level": "curated — closed storage for everyday items, 3-5 displayed objects with personal meaning",
    "mood": "intimate refuge — deep-seated furniture with back protection, layered warm textiles, nature elements (fiddle-leaf fig, natural wood), soft pools of warm light"
  }
}
```

The good brief is directly usable as Gemini prompt language. A prompt engineer needs zero guesswork.

### Elevation Rules

1. **Lighting**: Always specify all three layers (ambient, task, accent) + color temperature in Kelvin. Reference: living spaces 2700–3000K, kitchens/offices 3500–4000K.
2. **Colors**: Always include proportions (60/30/10) and application context ("navy on pillows, not walls"). Use specific color names, not just "blue."
3. **Textures**: Use professional material descriptors ("weathered oak," "brushed brass," "boucle," "raw linen") not generic ("wood," "metal," "soft fabric").
4. **Mood**: Translate into spatial and sensory terms using the design intelligence stack. "Cozy" becomes "intimate refuge with warm layered textiles and low ambient lighting."
5. **Spatial awareness**: If LiDAR dimensions are available, reference them. "15×20ft living room — furniture scaled for open layout with defined conversation zone (sofa + chairs within 10ft conversation distance)."

### The 60-30-10 Color Rule

Every color recommendation should map to this proportional framework:
- **60% dominant**: walls, large surfaces — typically neutral or softer shade
- **30% secondary**: accent furniture, curtains — provides contrast
- **10% accent**: throw pillows, artwork, small objects — the boldest shade

When the user says "I love navy blue," the agent should determine WHERE in the 60-30-10 hierarchy it belongs. Navy as 60% = dramatic statement room. Navy as 10% = grounding accent in a lighter scheme. Ask: "Do you want the whole room to feel blue, or should navy be a grounding accent against lighter tones?"

### Color Psychology Quick Reference (for agent context)

| Color | Effect | Best for | Avoid as dominant in |
|---|---|---|---|
| Blue | Slows metabolism, reduces heart rate, improves focus | Bedrooms, bathrooms, home offices | Kitchens/dining (suppresses appetite) |
| Green | Restful, reduces depression, boosts creativity ~18% | Any room — most versatile | — |
| Red | Raises blood pressure, stimulates appetite & conversation | Dining accent, entryway statement | Bedrooms (too stimulating) |
| Yellow | Reflects light, uplifting energy | Kitchens, hallways, bathrooms (soft hues) | Large surfaces in intense shades (visual fatigue) |
| Warm neutrals | Comfortable, grounding, timeless | Any room as base | — |

---

## 6. Shopping Pipeline: Design-Informed Extraction

### How Design Intelligence Improves Item Extraction

The item extraction prompt should embed design vocabulary and spatial awareness:

**Standard furniture categories** (the agent should identify these in order):
1. Primary seating (sofa, sectional, accent chairs)
2. Tables (coffee, side, console, dining)
3. Storage (bookshelf, credenza, media console, cabinet)
4. Rugs & flooring treatments
5. Lighting fixtures (pendant, floor lamp, table lamp, sconce)
6. Window treatments (curtains, blinds, shades)
7. Soft furnishings (throw pillows, blankets, ottomans)
8. Wall art & decorative objects
9. Plants & planters
10. Hardware & small accents (knobs, trays, vases)

**Proportion constraints** (from design knowledge — use when LiDAR is available):
- Coffee table ≈ 2/3 sofa length
- Coffee table to sofa distance: 14–18 inches
- Rug should extend at least 6 inches beyond sofa on each side (or all front legs on rug)
- Dining table: 24+ inches per person; 36" clearance to wall for chair pullback
- Major walkways: ≥36 inches

**Material intelligence** (improve search queries):
When extracting items from the design image, use professional material vocabulary:
- Not "brown table" → "walnut dining table with turned legs"
- Not "gold lamp" → "brushed brass arc floor lamp with linen shade"
- Not "white sofa" → "ivory boucle sofa with down-blend cushions"

### Source-Aware Query Enhancement

Brief-anchored items already use the user's language. But the extraction prompt should also apply design vocabulary:

- If the brief says "warm lighting" → extract as "warm brass pendant light, 2700K compatible, linen or paper shade"
- If the brief says "natural textures" → extract as "jute area rug, 8×10, flat weave, natural tan"
- If an iteration instruction says "replace with something more modern" → extract as "contemporary clean-line [category] in matte finish"

### Scoring Rubric Enhancement

The current rubric scores: category (0.3), material (0.2), color (0.2), style (0.2), dimensions (0.1).

Add a design-principle alignment bonus to the scoring prompt:
- Does this product contribute to the brief's 60-30-10 color ratio?
- Does the material quality match the brief's intended mood (e.g., "luxurious" → real marble, not faux)?
- Does the scale match the room (2:3 proportion rule)?

These don't need to be separate rubric categories — they should inform the style (0.2) and material (0.2) scoring guidance in the prompt.

---

## 7. The 20-Rule Validation Checklist

Apply these as validation checks against the completed DesignBrief before sending to Gemini. The intake agent should ensure the brief doesn't violate fundamental design principles.

1. Every design choice passes the suitability-simplicity-proportion test
2. Light placement is addressed (not just "warm" — specify layers)
3. Color follows 60-30-10 proportional logic
4. Color temperature matches room function (warm for rest, cool for work)
5. At least one biophilic element is included (plant, natural material, nature view)
6. Primary seating has prospect-refuge (back protected, view forward)
7. If bedroom: bed faces door, headboard against solid wall
8. Default ~70% curves in relaxation spaces (unless style dictates otherwise)
9. Ceiling height intent aligns with room function (higher for creative/social, standard for sleep/detail work)
10. Minimum three texture types specified
11. Three lighting layers specified (ambient, task, accent)
12. Kitchen respects work triangle or five-zone framework (if applicable)
13. All sightline-connected areas share consistent color undertones
14. Sound-sensitive rooms are noted (suggest buffering)
15. Trade-offs are stated explicitly when conflicting needs exist
16. At least one element creates delight / conversation (Draper's boldness heuristic)
17. Furniture scale references are appropriate for room size
18. Pain points from the user are addressed (not just style preferences)
19. Keep-items are integrated into the design concept (not ignored)
20. The brief reads as a coherent narrative, not a checklist

**How to use**: The agent doesn't output this checklist to the user. It uses it internally when constructing the final brief. If the brief violates a rule (e.g., no lighting layers specified), the agent should ask a clarifying question before finalizing.

---

## 8. Room-Specific Guidance

### Living Room
- **Primary purpose**: social + relaxation → prospect-refuge layout
- **Key furniture**: sofa (deep-seated for comfort), coffee table (2/3 sofa length), accent chairs (conversation distance 3.5–10ft from sofa)
- **Lighting**: 10–20 fc ambient, 20–50 fc reading spots, accent on art/features
- **Common pain points**: too dark, furniture too big for space, no conversation layout, TV dominates

### Bedroom
- **Primary purpose**: sleep + restoration → refuge-dominant, sensory control
- **Key spatial rule**: bed against solid wall, facing door, 36" walkway minimum
- **Lighting**: warm only (2200–2700K), blackout capability, cross-illuminated vanity if applicable
- **Color**: blue/green tones promote sleep; avoid red/orange
- **Temperature note**: cool bedrooms (60–67°F) support better sleep
- **Common pain points**: too stimulating (electronics, bright colors, clutter), poor light control

### Kitchen
- **Primary purpose**: task-oriented + social hub → task lighting critical
- **Key spatial rule**: work triangle (13–26ft total, 4–9ft per leg, no traffic crossing through)
- **Lighting**: 30–40 fc ambient, 50–80 fc counter task (under-cabinet LED), 3500–4000K
- **Counter height**: 36" standard
- **Common pain points**: poor lighting at prep areas, insufficient counter space, cook faces wall (no prospect)

### Home Office
- **Primary purpose**: focused work → detail-oriented cognitive mode
- **Key spatial rule**: desk facing into room (not against wall), view of door and window, back to wall
- **Lighting**: 50–80 fc desk task, 3500–4000K for alertness
- **Acoustic note**: sound-absorbing materials critical (rugs, curtains, panels)
- **Common pain points**: poor lighting, distracting background, no separation from living space

### Dining Room
- **Primary purpose**: meals + gathering → social, appetite-enhancing
- **Key spatial rule**: 24"+ per person at table, 36" clearance for chair pullback (44" if traffic behind)
- **Lighting**: pendant/chandelier over table (hung 30–36" above table surface), dimmable
- **Color note**: warm tones and reds stimulate appetite; avoid dominant blue
- **Common pain points**: table too large for room, insufficient lighting, space feels formal and unused

---

## 9. Small Space Optimization Toolkit

When LiDAR data reveals a small room, or the user mentions space constraints, the agent should recommend these strategies (in order of impact):

1. **Multi-functional furniture**: storage ottomans, murphy beds, lift-top coffee tables, extendable dining tables
2. **Vertical space utilization**: floor-to-ceiling shelving, wall-mounted desks, over-door organizers
3. **Visual expansion**: light neutral colors (LRV 70+), mirrors opposite windows, furniture with visible legs, glass/transparent materials, consistent flooring, floor-length curtains
4. **Pocket doors** replacing swing doors: reclaims 10+ sq ft per opening

### Open vs. Compartmentalized Layout

~65% of homeowners now prefer some walls and separation. The agent should recommend:
- **Open plan** for: young families (child supervision), frequent entertainers, very small homes
- **Compartmentalized** for: WFH professionals, multi-generational households, noise-sensitive occupants
- **Hybrid** (best for most): glass partitions, sliding doors, furniture zoning, ceiling/flooring transitions

---

## 10. Applying This Document

### For the Intake System Prompt

Include in the system prompt:
- The three-layer stack summary (Section 1) — as the agent's reasoning framework
- The full translation table (Section 2) — as a lookup reference
- The DIAGNOSE pipeline (Section 3) — as behavioral instructions
- The diagnostic questions (Section 4) — as a question bank to draw from
- The elevation rules (Section 5) — as output format instructions
- Room-specific guidance (Section 8) — load the relevant room section based on `room_type`

### For the Item Extraction Prompt

Include in the extraction prompt:
- Standard furniture categories (Section 6) — as the extraction taxonomy
- Material vocabulary examples (Section 6) — as output quality guidance
- Proportion constraints (Section 6) — for LiDAR-aware extraction

### For the Product Scoring Prompt

Reference in the scoring prompt:
- Design-principle alignment bonus (Section 6) — enhance style/material scoring
- Color psychology (Section 5) — validate color appropriateness for room type
- The 60-30-10 rule (Section 5) — validate color proportion logic

---

*This document distills research from Francis D.K. Ching (Architecture: Form, Space, and Order), Elsie de Wolfe (The House in Good Taste), Dorothy Draper (Decorating is Fun!), neuroarchitecture studies (Meyers-Levy & Zhu 2007, Vartanian et al. 2013, Bar & Neta 2006), Terrapin Bright Green's 14 Patterns of Biophilic Design, and Jay Appleton's prospect-refuge theory.*
