"""Silver dataset for intake eval calibration.

30 curated examples (15 GOOD, 15 BAD) of DesignBrief + conversation pairs.
Used to verify that the intake_eval judge can distinguish quality:
- Good briefs should score >= 70 (PASS or PASS:EXCELLENT)
- Bad briefs should score < 70 (FAIL:WEAK or FAIL:POOR)

Based on PRODUCT_SPEC.md Section 4.5 and DESIGN_INTELLIGENCE.md rubric criteria:
  Style Coherence (10), Color Strategy (15), Lighting Design (15),
  Material & Texture (15), Design Intelligence (10), Diagnostic Depth (5),
  Actionability (15), Completeness (10), User Fidelity (5).

Each example has:
  - brief: A DesignBrief dict (matches Pydantic model fields)
  - conversation: A list of {"role": ..., "content": ...} dicts
  - expected_tag: "PASS:EXCELLENT" | "PASS" | "FAIL:WEAK" | "FAIL:POOR"
  - reason: Why this is good or bad (for human reviewers)
"""

from __future__ import annotations

from typing import Any

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# GOOD EXAMPLES (15) — should score >= 70
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

GOOD_EXAMPLES: list[dict[str, Any]] = [
    # ── GOOD 1: Full living room — excellent elevation ──
    {
        "brief": {
            "room_type": "living room",
            "occupants": "Couple in their 30s, one golden retriever",
            "pain_points": [
                "Room feels cold and sterile despite warm intentions",
                "Furniture is oversized for the space, blocking circulation paths",
                "No defined conversation zone — seating scattered randomly",
            ],
            "keep_items": ["Antique walnut bookshelf (family heirloom)"],
            "style_profile": {
                "lighting": (
                    "Ambient: warm 2700K recessed cans on dimmer. "
                    "Task: brass swing-arm reading lamps flanking sofa. "
                    "Accent: LED strip behind bookshelf, picture lights on art wall"
                ),
                "colors": [
                    "Warm ivory walls (60%) — Benjamin Moore White Dove OC-17",
                    "Walnut and cognac leather tones (30%) — furniture and textiles",
                    "Burnt sienna and terracotta accents (10%) — throw pillows, pottery, art",
                ],
                "textures": [
                    "Boucle upholstery on primary sofa",
                    "Cognac full-grain leather on accent chair",
                    "Hand-knotted wool area rug with subtle geometric pattern",
                    "Raw linen curtains with thermal lining",
                    "Brushed brass hardware and lamp finishes",
                ],
                "clutter_level": (
                    "Curated — closed walnut credenza for daily items, "
                    "bookshelf displays limited to 3-5 meaningful objects per shelf, "
                    "woven baskets for dog toys"
                ),
                "mood": (
                    "Warm modern refuge — deep-seated furniture with back protection "
                    "against solid wall, layered natural textiles, soft pools of warm "
                    "light, biophilic elements (fiddle-leaf fig, natural wood grain), "
                    "conversation zone within 8ft diameter"
                ),
            },
            "constraints": [
                "Large dog — durable upholstery, no delicate surfaces at tail height",
                "Rental — no permanent wall modifications",
            ],
            "inspiration_notes": [
                {
                    "photo_index": 0,
                    "note": "Love the layered warmth",
                    "agent_clarification": (
                        "Client drawn to the layered textile approach: multiple throw "
                        "blankets, mixed cushion sizes, and natural fiber rug. Translates "
                        "to refuge-dominant layout with emphasis on tactile comfort."
                    ),
                }
            ],
        },
        "conversation": [
            {
                "role": "user",
                "content": "It's a living room for me and my partner. We have a golden retriever.",
            },
            {
                "role": "assistant",
                "content": (
                    "A living room for two plus a golden — I'll design for durability and warmth. "
                    "What daily activity feels harder than it should because of this space?"
                ),
            },
            {
                "role": "user",
                "content": (
                    "Honestly, we never sit in there. It feels cold even though we tried "
                    "to make it cozy. The furniture is too big."
                ),
            },
            {
                "role": "assistant",
                "content": (
                    "That's telling — a room you avoid despite wanting it to be cozy. Sounds like "
                    "the furniture scale is blocking circulation and the 'coldness' might be lighting "
                    "and material choices, not temperature. I'm interpreting 'cozy' as warm earth "
                    "tones, layered natural textiles (wool, linen, leather), and warm ambient "
                    "lighting around 2700K. Does that capture what you mean?"
                ),
            },
            {
                "role": "user",
                "content": "Yes exactly! Warm and inviting, layered blankets, that kind of thing.",
            },
            {
                "role": "assistant",
                "content": (
                    "Here's what I've gathered: a warm modern retreat with deep-seated "
                    "furniture, natural textiles, and warm brass lighting. Colors centered "
                    "on ivory walls with walnut and cognac leather, terracotta accents. "
                    "Your antique bookshelf anchors the design. All materials pet-friendly. "
                    "Does this capture your vision?"
                ),
            },
        ],
        "expected_tag": "PASS:EXCELLENT",
        "reason": (
            "All fields elevated: 3-layer lighting with Kelvin, 60/30/10 colors, "
            "5 specific textures, design intelligence (prospect-refuge, biophilic), "
            "diagnostic depth (probed why room feels cold), fully actionable."
        ),
    },
    # ── GOOD 2: Bedroom — sleep-optimized design ──
    {
        "brief": {
            "room_type": "bedroom",
            "occupants": "Single professional, works from home occasionally",
            "pain_points": [
                "Can't fall asleep — room is too stimulating (blue LED strip, bright walls)",
                "No separation between work and rest — desk faces the bed",
                "Clutter accumulates on nightstands with no storage system",
            ],
            "keep_items": ["Queen platform bed frame (Muji-style, solid oak)"],
            "style_profile": {
                "lighting": (
                    "Ambient: warm 2200K recessed on dimmer (sleep-optimized). "
                    "Task: adjustable wall-mounted reading sconce (2700K, directional). "
                    "Accent: none — bedroom should minimize stimulation after 9pm. "
                    "Blackout roller shades for complete darkness"
                ),
                "colors": [
                    "Soft sage green walls (60%) — calming, promotes sleep per color psychology",
                    "Natural blonde oak and warm cream linens (30%) — bed frame, shelving, bedding",
                    "Muted terracotta (10%) — single throw, one art piece",
                ],
                "textures": [
                    "Washed linen duvet and pillowcases",
                    "Merino wool throw blanket",
                    "Low-pile wool area rug (sound-absorbing)",
                    "Solid oak nightstands with enclosed drawers",
                ],
                "clutter_level": (
                    "Minimal — enclosed nightstand drawers for daily items, "
                    "under-bed storage boxes for seasonal, "
                    "desk relocated behind a linen room divider"
                ),
                "mood": (
                    "Restful sanctuary — refuge-dominant with headboard against solid wall, "
                    "bed facing door, cool color palette for circadian alignment, "
                    "sound-absorbing materials throughout, no electronics visible from bed"
                ),
            },
            "constraints": [
                "Work desk must stay in room — need visual separation from sleep zone",
                "Rental — no painting allowed, using peel-and-stick or textile wall treatment",
            ],
            "inspiration_notes": [],
        },
        "conversation": [
            {"role": "user", "content": "It's my bedroom, I live alone, work from home sometimes."},
            {
                "role": "assistant",
                "content": "A bedroom that also hosts work — that's a common challenge. What time of day does this room feel worst?",
            },
            {"role": "user", "content": "At night. I can't fall asleep. Too wired."},
            {
                "role": "assistant",
                "content": (
                    "That's a real issue — your bedroom might be fighting your circadian rhythm. "
                    "What's the lighting situation right now? Any blue-toned LEDs or bright fixtures?"
                ),
            },
            {
                "role": "user",
                "content": "Yeah I have an LED strip that's blue-white. And my desk faces my bed so I see work.",
            },
            {
                "role": "assistant",
                "content": (
                    "Two clear culprits: blue-spectrum light suppresses melatonin (you need warm "
                    "2200K max for bedrooms), and seeing your work desk from bed creates cognitive "
                    "activation. I'd recommend: warm-only lighting on a dimmer, relocating the desk "
                    "behind a divider, and a sage green palette that promotes sleep. Sound right?"
                ),
            },
        ],
        "expected_tag": "PASS:EXCELLENT",
        "reason": (
            "Room-specific sleep optimization applied: 2200K lighting, blackout shades, "
            "sage green (sleep-promoting), bed placement rules, work/rest separation diagnosed. "
            "Diagnostic depth excellent — probed timing, identified LED and desk as root causes."
        ),
    },
    # ── GOOD 3: Kitchen — task lighting focus ──
    {
        "brief": {
            "room_type": "kitchen",
            "occupants": "Family of 5 — couple with 3 kids (ages 4, 7, 11)",
            "pain_points": [
                "Prep areas are dark — cook faces wall with no overhead task lighting",
                "Kids do homework at kitchen island but light is insufficient",
                "Counter space overwhelmed with appliances, no dedicated storage",
            ],
            "keep_items": [
                "Granite countertops (recently installed)",
                "Stainless steel appliances",
            ],
            "style_profile": {
                "lighting": (
                    "Ambient: 3500K flush-mount ceiling fixtures for general brightness. "
                    "Task: under-cabinet LED strips (4000K, high CRI) for all prep surfaces. "
                    "Accent: pendant trio over island (3000K, dimmable) — doubles as homework task light"
                ),
                "colors": [
                    "Warm white walls and cabinets (60%) — SW Alabaster",
                    "Granite countertops + brushed nickel hardware (30%)",
                    "Ocean blue backsplash tile accents (10%)",
                ],
                "textures": [
                    "Brushed nickel cabinet pulls and faucet",
                    "Glazed ceramic subway tile backsplash",
                    "Washable flat-weave cotton runner",
                    "Sealed natural granite (existing)",
                ],
                "clutter_level": (
                    "Minimal surfaces — appliance garage for daily tools, "
                    "deep drawer organizers for utensils, "
                    "pull-out pantry shelving for dry goods"
                ),
                "mood": (
                    "Bright functional hub — well-lit work surfaces, kid-friendly "
                    "durability, social prospect from cooking position toward living area"
                ),
            },
            "constraints": [
                "3 young kids — all surfaces must be wipeable and durable",
                "Budget-conscious — keep existing counters and appliances",
                "Cook needs to see kids in adjacent room while preparing meals",
            ],
            "inspiration_notes": [],
        },
        "conversation": [
            {"role": "user", "content": "Kitchen, family of 5, 3 kids under 12."},
            {
                "role": "assistant",
                "content": "A busy family kitchen — that's a command center. What daily task feels harder than it should because of this space?",
            },
            {
                "role": "user",
                "content": "Cooking in the dark. The main prep area has no light, I face the wall.",
            },
            {
                "role": "assistant",
                "content": (
                    "Classic kitchen pain point — task lighting gap. Kitchens need 50-80 fc at "
                    "prep surfaces, and facing the wall means you're casting your own shadow. "
                    "Under-cabinet LEDs at 4000K solve both. Do the kids use the kitchen for "
                    "anything beyond meals?"
                ),
            },
            {"role": "user", "content": "They do homework at the island every afternoon."},
            {
                "role": "assistant",
                "content": (
                    "The island is doing double duty — meal prep and study hall. I'd recommend "
                    "a pendant trio over the island: 3000K dimmable, bright enough for homework "
                    "but warm enough for dinner. Let me put together a brief."
                ),
            },
        ],
        "expected_tag": "PASS:EXCELLENT",
        "reason": (
            "Kitchen-specific: work triangle awareness, task lighting with CRI and lux, "
            "prospect from cook position, kid-friendly materials. Diagnostic depth probed "
            "dual-use of island. All fields prompt-ready."
        ),
    },
    # ── GOOD 4: Home office — focus-optimized ──
    {
        "brief": {
            "room_type": "home office",
            "occupants": "Software engineer, 8-10 hours daily desk work",
            "pain_points": [
                "Eye strain from overhead fluorescent lighting — wrong color temperature",
                "Can hear everything from the living room — zero acoustic privacy",
                "Desk against wall feels claustrophobic — no prospect view",
            ],
            "keep_items": ["Standing desk (motorized, walnut veneer)"],
            "style_profile": {
                "lighting": (
                    "Ambient: indirect 4000K LED panel (reduces glare on screens). "
                    "Task: adjustable desk lamp (3500-4000K, high CRI 95+) for document work. "
                    "Accent: warm 2700K bias lighting behind monitor (reduces eye strain)"
                ),
                "colors": [
                    "Cool gray walls (60%) — promotes focus, minimizes distraction",
                    "Walnut desk + warm wood accents (30%) — grounds the space",
                    "Forest green accent (10%) — plants, desk accessories, chair upholstery",
                ],
                "textures": [
                    "Dense wool area rug (sound-absorbing, 8x10)",
                    "Acoustic felt wall panels in charcoal",
                    "Walnut veneer desk surface (existing)",
                    "Full-grain leather desk chair with mesh back",
                ],
                "clutter_level": (
                    "Minimal — cable management tray under desk, "
                    "closed credenza for papers and supplies, "
                    "single shelf for 3-4 reference books"
                ),
                "mood": (
                    "Focused studio — desk oriented to face room entry (prospect), "
                    "back to solid wall (refuge), natural light from side window "
                    "(reduces screen glare), acoustic isolation for deep work"
                ),
            },
            "constraints": [
                "8+ hours seated — ergonomic seating critical",
                "Multiple monitors — needs glare management",
                "Video calls — background needs to look professional",
            ],
            "inspiration_notes": [],
        },
        "conversation": [
            {
                "role": "user",
                "content": "Home office. I'm a software engineer, I spend all day here.",
            },
            {
                "role": "assistant",
                "content": "8+ hours daily — this room needs to support sustained focus. What's the worst thing about working here right now?",
            },
            {
                "role": "user",
                "content": "My eyes hurt by 3pm and I can hear my roommate's TV through the wall.",
            },
            {
                "role": "assistant",
                "content": (
                    "Eye strain at 3pm suggests your lighting is the wrong color temperature for "
                    "screens — you need 4000K ambient with bias lighting behind your monitors. "
                    "The noise issue needs acoustic treatment: dense rug, felt panels, possibly "
                    "a bookshelf against the shared wall. Where does your desk face?"
                ),
            },
            {"role": "user", "content": "Against the wall. I stare at the wall all day."},
            {
                "role": "assistant",
                "content": (
                    "That's a prospect-refuge issue — facing a wall all day is cognitively "
                    "draining. Rotating the desk to face the room entry with your back to the "
                    "wall will feel dramatically better. Natural light should come from the side "
                    "to reduce screen glare."
                ),
            },
        ],
        "expected_tag": "PASS:EXCELLENT",
        "reason": (
            "Home office-specific: correct color temps for focus, acoustic treatment, "
            "prospect-refuge desk orientation, bias lighting, CRI specified. "
            "Deep diagnostic: identified root cause of eye strain and claustrophobia."
        ),
    },
    # ── GOOD 5: Dining room — social and appetite-enhancing ──
    {
        "brief": {
            "room_type": "dining room",
            "occupants": "Couple who hosts dinner parties for 6-8 guests monthly",
            "pain_points": [
                "Room feels formal and unused between events",
                "Lighting is a single harsh overhead fixture — no ambiance",
                "Table seats 4 but they host 8 — constant squeeze",
            ],
            "keep_items": ["Built-in china cabinet (original to the 1920s house)"],
            "style_profile": {
                "lighting": (
                    "Ambient: warm 2700K wall sconces on dimmer for everyday. "
                    'Task: statement chandelier over table (hung 32" above surface, dimmable). '
                    "Accent: LED strip inside china cabinet to showcase glassware"
                ),
                "colors": [
                    "Deep charcoal accent wall behind china cabinet (60% — dining rooms handle dark colors well)",
                    "Warm cream remaining walls and ceiling (30%)",
                    "Antique gold and burgundy accents (10%) — chandelier, table runner, napkins",
                ],
                "textures": [
                    "Solid walnut extendable dining table (seats 4 daily, 8 extended)",
                    "Upholstered linen dining chairs with nailhead trim",
                    "Wool-silk blend area rug (9x12, dark pattern hides spills)",
                    "Velvet curtains for acoustic softening and drama",
                ],
                "clutter_level": (
                    "Curated — table clear when not in use, china cabinet as display focal point, "
                    "sideboard for serving pieces and table linens"
                ),
                "mood": (
                    "Intimate supper club — dramatic but warm, conversation-encouraging "
                    "layout with all seats within comfortable speaking distance, "
                    "dimmable lighting for transition from bright dinner prep to low ambient dessert"
                ),
            },
            "constraints": [
                'Must seat 8 comfortably when extended — 24"+ per person',
                "1920s home character — design must complement, not fight, the period",
                "Wine service — need proper storage and serving surface",
            ],
            "inspiration_notes": [
                {
                    "photo_index": 0,
                    "note": "Love the moody, intimate feeling",
                    "agent_clarification": (
                        "Client responds to dark-walled intimate dining with warm lighting. "
                        "Translates to charcoal accent wall, statement chandelier, "
                        "rich textile layers."
                    ),
                }
            ],
        },
        "conversation": [
            {
                "role": "user",
                "content": "Dining room. We host dinner parties monthly for 6-8 people.",
            },
            {
                "role": "assistant",
                "content": "Monthly hosting for 8 — the dining room needs to transform between intimate daily use and social events. What's your biggest frustration with it right now?",
            },
            {
                "role": "user",
                "content": "It feels too formal and we only use it for parties. The light is terrible.",
            },
            {
                "role": "assistant",
                "content": (
                    "A room that only comes alive for parties is a lost opportunity. The lighting "
                    "fix is straightforward — a dimmable chandelier at 2700K creates warmth, and "
                    "sconces let the room feel inviting even for a Tuesday dinner for two. What "
                    "about seating — does your current table handle 8?"
                ),
            },
            {
                "role": "user",
                "content": "No, it's a 4-seater. We squeeze extra chairs in. Also love moody, intimate vibes.",
            },
            {
                "role": "assistant",
                "content": (
                    "An extendable table solves the flex — seats 4 daily, 8 for events. Your "
                    "'moody, intimate' preference translates well to a charcoal accent wall, "
                    "velvet curtains, and layered warm lighting you can dim for dessert. The "
                    "1920s china cabinet becomes the focal point with interior cabinet lighting."
                ),
            },
        ],
        "expected_tag": "PASS:EXCELLENT",
        "reason": (
            'Dining-specific: chandelier placement (32" above table), seating calculation, '
            "appetite-enhancing warm colors, dimmable transitions. Design intelligence: "
            "dual-use everyday/entertaining, period-appropriate. Full elevation."
        ),
    },
    # ── GOOD 6: Small apartment living room — space optimization ──
    {
        "brief": {
            "room_type": "living room",
            "occupants": "Young professional in studio apartment — room serves as living room and sleeping area",
            "pain_points": [
                "12x14ft total — every piece of furniture competes for floor space",
                "No separation between sleep and social zones",
                "Room feels dark due to single north-facing window",
            ],
            "keep_items": ["Murphy bed (wall-mounted, recently installed)"],
            "style_profile": {
                "lighting": (
                    "Ambient: 3000K recessed slim-profile ceiling lights. "
                    "Task: adjustable floor lamp for reading zone. "
                    "Accent: warm LED strip above Murphy bed niche for evening warmth. "
                    "Mirror on wall opposite window to double natural light"
                ),
                "colors": [
                    "Bright warm white walls and ceiling (60%) — LRV 80+ to expand perceived space",
                    "Light ash wood tones throughout (30%) — furniture, flooring, shelving",
                    "Mustard yellow accents (10%) — throw pillows, single art piece",
                ],
                "textures": [
                    "Light ash plywood shelving (floor-to-ceiling, vertical storage)",
                    'Bouclé sleeper sofa — compact 72" loveseat size',
                    "Flat-weave cotton rug (low pile, easy to fold when bed is down)",
                    "Sheer linen curtains to maximize natural light",
                ],
                "clutter_level": (
                    "Minimal — vertical storage only, multi-functional furniture "
                    "(storage ottoman, lift-top coffee table), nothing on floor except "
                    "rug and essential furniture"
                ),
                "mood": (
                    "Airy urban nest — light palette to expand perceived space, "
                    "furniture with visible legs, transparent/glass where possible, "
                    "one biophilic element (trailing pothos on high shelf)"
                ),
            },
            "constraints": [
                "12x14ft studio — every piece must be multi-functional",
                "North-facing window — limited natural light, need artificial compensation",
                "Rental — no permanent modifications",
            ],
            "inspiration_notes": [],
        },
        "conversation": [
            {
                "role": "user",
                "content": "It's a tiny studio, 12 by 14, I use it as living room and bedroom.",
            },
            {
                "role": "assistant",
                "content": "168 sq ft total — every choice matters. What's the one thing that frustrates you most?",
            },
            {
                "role": "user",
                "content": "It's always dark and cramped. I feel like I'm living in a cave.",
            },
            {
                "role": "assistant",
                "content": (
                    "North-facing windows plus a small room is a tough combo. The fix is a "
                    "light palette (LRV 80+), a mirror opposite the window, and furniture "
                    "with visible legs to keep sightlines open. What's your Murphy bed situation?"
                ),
            },
            {"role": "user", "content": "Just installed one. It folds up during the day."},
            {
                "role": "assistant",
                "content": (
                    "Perfect — that reclaims 15+ sq ft. I'd pair it with a compact loveseat, "
                    "floor-to-ceiling shelving for vertical storage, and a lift-top coffee table. "
                    "Light ash tones throughout with mustard accents for energy."
                ),
            },
        ],
        "expected_tag": "PASS",
        "reason": (
            "Small space optimization applied: LRV specified, multi-functional furniture, "
            "vertical storage, visual expansion techniques. Good elevation though "
            "lighting layers could be more detailed."
        ),
    },
    # ── GOOD 7: Bohemian bedroom — layered and eclectic ──
    {
        "brief": {
            "room_type": "bedroom",
            "occupants": "Artist, late 20s, lives with two cats",
            "pain_points": [
                "Room feels generic — looks like a hotel, no personality",
                "Cats scratch everything — needs durable materials",
                "Piles of art supplies and books with no storage system",
            ],
            "keep_items": ["Vintage brass bed frame", "Collection of global textiles from travels"],
            "style_profile": {
                "lighting": (
                    "Ambient: warm string lights draped above headboard (2200K). "
                    "Task: Moroccan-style brass desk lamp for art journaling. "
                    "Accent: beeswax candle cluster on nightstand (firelight warmth)"
                ),
                "colors": [
                    "Deep ochre walls (60%) — warm, saturated, enveloping",
                    "Mixed jewel-tone textiles (30%) — kilim rug, suzani throw, ikat pillows",
                    "Brass and terracotta accents (10%) — bed frame, pottery, lamp",
                ],
                "textures": [
                    "Hand-woven kilim wool rug (flat-weave — cat-claw resistant)",
                    "Linen duvet with global textile layered on top",
                    "Macramé wall hanging above headboard",
                    "Terracotta pottery for plants",
                    "Woven seagrass baskets for art supply storage",
                ],
                "clutter_level": (
                    "Layered-eclectic — curated maximalism where every object tells a story. "
                    "Woven baskets for corralling supplies, wall-mounted shelves for books, "
                    "nothing on the floor except rug and baskets"
                ),
                "mood": (
                    "Global creative sanctuary — saturated warmth, handcrafted textures, "
                    "personal travel mementos as decor, low warm lighting for refuge feeling, "
                    "plants in every corner (biophilic)"
                ),
            },
            "constraints": [
                "Two cats — no delicate fabrics, flat-weave rugs only, no dangling cords",
                "Budget under $2000 total — vintage/thrift where possible",
                "Renter — no wall painting (using removable wallpaper or large textile hangings)",
            ],
            "inspiration_notes": [],
        },
        "conversation": [
            {
                "role": "user",
                "content": "Bedroom, I'm an artist. Two cats. The room has zero personality.",
            },
            {
                "role": "assistant",
                "content": "An artist's bedroom should reflect your creative eye. What style of art do you make, and have you traveled recently?",
            },
            {
                "role": "user",
                "content": "Mixed media, lots of textiles. I travel a lot, I've collected fabric from Morocco, Turkey, India.",
            },
            {
                "role": "assistant",
                "content": (
                    "Your textile collection IS the design palette — kilim, suzani, ikat patterns "
                    "layered together create a bohemian warmth that's authentically you. For the "
                    "cats, flat-weave rugs resist claws, and woven baskets corral your supplies. "
                    "Warm ochre walls would make those jewel tones sing."
                ),
            },
            {"role": "user", "content": "Yes! Warm and layered, like a cozy travel den."},
            {
                "role": "assistant",
                "content": (
                    "I'm translating 'cozy travel den' to: deep ochre walls, layered global "
                    "textiles, brass accents, warm string lights, and terracotta pottery with "
                    "trailing plants. All cat-safe, all with story."
                ),
            },
        ],
        "expected_tag": "PASS",
        "reason": (
            "Style-coherent bohemian: specific textiles named, cat-safe materials, "
            "personal narrative woven into design. Good elevation from 'cozy travel den' "
            "to specific parameters. Lighting could have more Kelvin detail."
        ),
    },
    # ── GOOD 8: Scandinavian living room — hygge focus ──
    {
        "brief": {
            "room_type": "living room",
            "occupants": "Couple, both Scandinavian heritage, one infant",
            "pain_points": [
                "Current furniture is dark and heavy — makes room feel small",
                "Baby toys everywhere with no containment system",
                "Cold tile floor — need warmth underfoot",
            ],
            "keep_items": ["Inherited Hans Wegner CH07 shell chair"],
            "style_profile": {
                "lighting": (
                    "Ambient: warm 2700K pendant in linen shade (hygge glow). "
                    "Task: slim floor lamp with adjustable arm for reading nook. "
                    "Accent: battery tealights in window niche (safe with infant)"
                ),
                "colors": [
                    "Warm white walls (60%) — Farrow & Ball All White",
                    "Blonde ash and pale birch wood tones (30%) — furniture, shelving",
                    "Soft dusty rose accents (10%) — throw pillow, one art print",
                ],
                "textures": [
                    "Sheepskin throw on shell chair",
                    "Chunky knit wool blanket",
                    "Dense wool area rug (off-white, 8x10)",
                    "Solid ash coffee table with rounded edges (baby-safe)",
                    "Natural linen sofa slipcover (washable)",
                ],
                "clutter_level": (
                    "Minimal with hidden baby storage — woven lidded baskets "
                    "on low shelves for toy containment, "
                    "Kallax-style cubby for diapers and supplies"
                ),
                "mood": (
                    "Hygge sanctuary — pale, warm, calm with natural materials and soft "
                    "textiles everywhere. Baby-safe rounded edges on all furniture. "
                    "Natural light maximized, plants as primary decor"
                ),
            },
            "constraints": [
                "Infant — rounded edges mandatory, no glass, no small objects at floor level",
                "Tile floor — needs large area rug for warmth and crawling surface",
                "Washable slipcovers for all upholstery",
            ],
            "inspiration_notes": [],
        },
        "conversation": [
            {
                "role": "user",
                "content": "Living room, couple with a baby. We're both Scandinavian.",
            },
            {
                "role": "assistant",
                "content": "Scandinavian heritage — shall I lean into hygge design principles? What's not working now?",
            },
            {
                "role": "user",
                "content": "Current stuff is too dark and heavy. Tiles are cold. Baby toys everywhere.",
            },
            {
                "role": "assistant",
                "content": (
                    "Three fixes: swap to pale wood and light textiles, add a large wool rug for warmth "
                    "and crawling, and lidded baskets for toy containment. The Wegner shell chair is a "
                    "perfect anchor piece. I'll design around it with complementary blonde ash."
                ),
            },
            {"role": "user", "content": "Love it. Keep it simple and warm."},
            {
                "role": "assistant",
                "content": (
                    "Simple and warm in Scandi means: pale palette, natural materials (wool, linen, ash), "
                    "plants as decor, rounded baby-safe edges. 2700K warm lighting, sheepskin on the "
                    "Wegner, chunky knit blankets."
                ),
            },
        ],
        "expected_tag": "PASS",
        "reason": (
            "Scandinavian design applied correctly: blonde wood, hygge textiles, minimal "
            "palette with warm glow. Baby safety integrated throughout. Keep-item (Wegner) "
            "properly anchored in the design."
        ),
    },
    # ── GOOD 9: Luxury master suite ──
    {
        "brief": {
            "room_type": "bedroom",
            "occupants": "Couple in their 50s, empty nesters",
            "pain_points": [
                "Room hasn't been updated in 15 years — beige on beige, no drama",
                "Lighting is a single ceiling fan/light combo — zero ambiance",
                "Walk-in closet overflows into bedroom — visual noise",
            ],
            "keep_items": [],
            "style_profile": {
                "lighting": (
                    "Ambient: cove lighting along ceiling perimeter (2700K, dimmable). "
                    'Task: matching crystal bedside pendants (hung at 20" above nightstand). '
                    "Accent: LED strip in closet (3000K, auto-on with door)"
                ),
                "colors": [
                    "Deep forest green feature wall behind bed (60% visual weight at focal point)",
                    "Warm cream and gold tones (30%) — remaining walls, bedding, curtains",
                    "Burnished brass and blush pink accents (10%) — hardware, throws, art",
                ],
                "textures": [
                    "Velvet upholstered headboard (forest green, channel-tufted)",
                    "Egyptian cotton sateen sheets (600TC)",
                    "Silk-wool blend area rug with subtle pattern",
                    "Marble-topped nightstands with brass legs",
                    "Brushed brass closet system and hardware",
                ],
                "clutter_level": (
                    "Minimal luxury — custom closet system to contain overflow, "
                    "nightstands with single drawer each, "
                    "no visible clutter — only curated objects"
                ),
                "mood": (
                    "Boutique hotel suite — indulgent materials, jewel-tone drama, "
                    "spa-like serenity. Statement headboard as focal point. "
                    "Prospect-refuge: bed centered on solid wall, facing room entry"
                ),
            },
            "constraints": [
                "No budget limit — quality over quantity",
                "Both hot sleepers — breathable fabrics, no heavy synthetic",
            ],
            "inspiration_notes": [
                {
                    "photo_index": 0,
                    "note": "Love the green velvet headboard vibe",
                    "agent_clarification": (
                        "Client drawn to jewel-tone luxury: velvet upholstery, brass accents, "
                        "rich layered bedding. Translates to boutique hotel aesthetic with "
                        "forest green as dominant accent."
                    ),
                }
            ],
        },
        "conversation": [
            {
                "role": "user",
                "content": "Master bedroom. Empty nesters, we want something special. No budget limit.",
            },
            {
                "role": "assistant",
                "content": "A bedroom rebirth — exciting. What feels dated or wrong about it now?",
            },
            {
                "role": "user",
                "content": "Everything is beige. It's boring. The closet is exploding into the bedroom.",
            },
            {
                "role": "assistant",
                "content": (
                    "Fifteen years of beige — time for drama. Your inspiration photo with the "
                    "green velvet headboard points to a jewel-tone palette. The closet overflow "
                    "needs a proper system to contain it. You mentioned you both like hotel suites — "
                    "that boutique-hotel feel with indulgent materials?"
                ),
            },
            {
                "role": "user",
                "content": "Yes, exactly like a high-end hotel. But we run hot, so nothing too heavy.",
            },
            {
                "role": "assistant",
                "content": (
                    "Luxury with breathability: sateen cotton (not flannel), silk-wool rugs, "
                    "velvet headboard (visually warm without heat retention). Forest green, "
                    "brass, and blush pink. Cove lighting for ambiance instead of that fan."
                ),
            },
        ],
        "expected_tag": "PASS:EXCELLENT",
        "reason": (
            "Luxury execution: specific material grades (600TC sateen, silk-wool), "
            "practical constraint (hot sleepers) integrated into material choices, "
            "closet overflow addressed. Full 3-layer lighting. Design narrative coherent."
        ),
    },
    # ── GOOD 10: Modern farmhouse — rustic-modern blend ──
    {
        "brief": {
            "room_type": "living room",
            "occupants": "Family of 4, two kids (6 and 9), one labrador",
            "pain_points": [
                "Previous design was too 'country' — want modern refinement",
                "Dog jumps on furniture — need performance fabrics",
                "Kids' art projects need display space",
            ],
            "keep_items": ["Reclaimed barn wood mantel (original to 1890 farmhouse)"],
            "style_profile": {
                "lighting": (
                    "Ambient: warm 2700K semi-flush fixtures (farmhouse-modern style). "
                    "Task: industrial-style floor lamp by reading chair. "
                    "Accent: battery-operated candles on mantel, warm LED in built-in shelves"
                ),
                "colors": [
                    "Warm white shiplap walls (60%) — SW Pure White, maintaining farmhouse character",
                    "Weathered wood and iron tones (30%) — mantel, coffee table, shelving brackets",
                    "Muted sage green and terracotta accents (10%) — pillows, kid art frames, pottery",
                ],
                "textures": [
                    "Performance fabric sofa (Crypton or Sunbrella — stain-proof, dog-proof)",
                    "Reclaimed wood coffee table with rounded edges",
                    "Jute area rug (natural, hides dirt, dog-friendly)",
                    "Wrought iron shelving brackets and curtain rods",
                    "Linen-cotton blend curtains",
                ],
                "clutter_level": (
                    "Curated family — gallery wall for rotating kid art, "
                    "storage ottoman for blankets and dog toys, "
                    "open shelving with intentional styling (books + 2-3 objects per shelf)"
                ),
                "mood": (
                    "Warm modern farmhouse — original character preserved (barn wood mantel, "
                    "shiplap) with contemporary clean lines. Family-proof durability. "
                    "Biophilic: potted herbs, natural materials throughout"
                ),
            },
            "constraints": [
                "Labrador + 2 kids — performance fabrics mandatory, nothing precious",
                "1890 farmhouse — design must honor original architecture",
                "Kids' art must be displayable, not hidden",
            ],
            "inspiration_notes": [],
        },
        "conversation": [
            {"role": "user", "content": "Living room in our farmhouse. Family of 4, big lab."},
            {
                "role": "assistant",
                "content": "A farmhouse with a lab — durability meets character. What do you want to change?",
            },
            {
                "role": "user",
                "content": "It's too 'country', we want something more modern but keeping the farmhouse feel.",
            },
            {
                "role": "assistant",
                "content": (
                    "Modern farmhouse — clean lines paired with original character. The barn wood "
                    "mantel is the hero piece. I'd pair it with performance fabric seating "
                    "(Crypton or Sunbrella for the lab), iron hardware, and a gallery wall for "
                    "the kids' art. Country charm, modern execution."
                ),
            },
            {
                "role": "user",
                "content": "Yes! And the kids make tons of art, I want to show it off not hide it.",
            },
            {
                "role": "assistant",
                "content": (
                    "A rotating gallery wall with matching frames turns kid art into a design "
                    "feature. Sage green and terracotta for accents — earthy but not 'country'. "
                    "Jute rug hides dirt. Everything wipes clean."
                ),
            },
        ],
        "expected_tag": "PASS",
        "reason": (
            "Modern farmhouse balance: preserved original elements, specific performance "
            "fabrics named, kid art as design feature. Good pain point resolution. "
            "Lighting could have more specificity."
        ),
    },
    # ── GOOD 11: Quick mode — concise but elevated ──
    {
        "brief": {
            "room_type": "bedroom",
            "occupants": "College student in dorm room",
            "pain_points": ["Room feels institutional — concrete walls, fluorescent overhead"],
            "keep_items": ["Standard twin XL bed frame (dorm-provided)"],
            "style_profile": {
                "lighting": (
                    "Ambient: warm 2700K LED strip behind headboard. "
                    "Task: clip-on reading light (2700K). "
                    "No overhead use — cover fluorescent with paper shade diffuser"
                ),
                "colors": [
                    "Warm neutral (60%) — peel-and-stick removable wallpaper in light linen texture",
                    "White bedding with warm gray accents (30%)",
                    "Dusty blue accent (10%) — throw blanket, desk organizer",
                ],
                "textures": [
                    "Jersey cotton fitted sheets",
                    "Chunky-knit throw blanket",
                    "Faux sheepskin rug beside bed",
                    "Canvas and wood desk organizers",
                ],
                "clutter_level": "Minimal — over-door hooks, under-bed bins, vertical shelving",
                "mood": "Personal retreat in a shared building — warm, soft, distinctly not institutional",
            },
            "constraints": [
                "Dorm rules — no painting, no nails, no candles",
                "Extreme budget constraint — under $200 total",
                "Twin XL only — can't change bed",
            ],
            "inspiration_notes": [],
        },
        "conversation": [
            {"role": "user", "content": "Dorm room. It's ugly. I want it to feel like home."},
            {
                "role": "assistant",
                "content": (
                    "Dorm-to-home transformation on a budget — the biggest impact comes from "
                    "lighting and textiles. Warm LED strips replace the fluorescents, removable "
                    "wallpaper covers concrete, and layered bedding makes the twin feel cozy. "
                    "Dusty blue accents for personality. All removable."
                ),
            },
        ],
        "expected_tag": "PASS",
        "reason": (
            "Quick mode but still elevated: specific Kelvin temps, removable solutions "
            "for dorm constraints, texture variety. Concise but actionable. "
            "Could have deeper diagnostic probing for full marks."
        ),
    },
    # ── GOOD 12: Open mode — rich multi-turn conversation ──
    {
        "brief": {
            "room_type": "living room",
            "occupants": "Retired couple, art collectors, entertain weekly",
            "pain_points": [
                "Art collection has outgrown available wall space",
                "Seating is arranged for TV, not conversation — guests talk to the back of heads",
                "Room lacks a focal point — everything competes for attention",
            ],
            "keep_items": [
                "Art collection (12 pieces, mixed sizes)",
                "Grand piano (baby grand, black lacquer)",
            ],
            "style_profile": {
                "lighting": (
                    "Ambient: recessed 2700K on separate zones (living vs piano area). "
                    "Task: piano lamp (brass, 3000K). "
                    "Accent: picture lights on each major artwork, gallery-style track lighting"
                ),
                "colors": [
                    "Gallery white walls (60%) — lets art provide the color",
                    "Rich wood tones and black (30%) — piano, walnut credenza, dark frames",
                    "Colors come from the art itself (10%) — accent textiles pull from key pieces",
                ],
                "textures": [
                    "Mohair upholstery on conversation chairs",
                    "Walnut credenza with brass inlay",
                    "Hand-knotted Persian rug (defines conversation zone)",
                    "Raw silk curtains (floor-length, pooling)",
                    "Black lacquer piano (existing)",
                ],
                "clutter_level": (
                    "Museum-curated — art salon hanging (floor to eye level), "
                    "negative space between groupings, "
                    "piano corner as secondary vignette"
                ),
                "mood": (
                    "Intimate gallery salon — art and piano as dual focal points, "
                    "conversation-first seating within 8ft circle, "
                    "dramatic lighting isolates each artwork"
                ),
            },
            "constraints": [
                "12 art pieces need proper display — salon-style hanging",
                "Baby grand piano cannot be moved — design around it",
                "Weekly entertaining — seating must accommodate 8-10 guests comfortably",
            ],
            "inspiration_notes": [],
        },
        "conversation": [
            {
                "role": "user",
                "content": "Tell us about this room. We're retired, we collect art, we have a piano.",
            },
            {
                "role": "assistant",
                "content": "An art collector's living room with a piano — two strong focal points. Tell me more about what brings you joy in this space and what frustrates you.",
            },
            {
                "role": "user",
                "content": "We love having friends over for music and wine. But the room is set up for TV. Art is crammed on walls.",
            },
            {
                "role": "assistant",
                "content": (
                    "The room is optimized for passive watching but your life is about active "
                    "hosting — music, conversation, art. That's a fundamental layout mismatch. "
                    "The TV can be demoted; the piano and art collection become the focal points. "
                    "How many pieces do you have and how big are they?"
                ),
            },
            {"role": "user", "content": "12 pieces, various sizes. Some are large — 4 feet wide."},
            {
                "role": "assistant",
                "content": (
                    "Twelve pieces need proper gallery treatment — salon-style hanging with "
                    "picture lights. White walls let the art do the talking. Conversation seating "
                    "in a circle, piano corner as a performance zone, art gallery on the long wall. "
                    "Persian rug to define the social zone."
                ),
            },
            {"role": "user", "content": "That sounds perfect. Like a private gallery."},
            {
                "role": "assistant",
                "content": (
                    "Exactly — intimate gallery salon. Gallery white walls, picture lighting on "
                    "each piece, mohair chairs in a conversation circle, Persian rug to ground it. "
                    "Piano corner with its own brass lamp. Art provides all the color you need."
                ),
            },
        ],
        "expected_tag": "PASS:EXCELLENT",
        "reason": (
            "Deep design intelligence: reframed TV-centric to conversation-centric, "
            "art gallery treatment, dual focal point management, specific seating diameter. "
            "Excellent user fidelity — traced every choice to their lifestyle."
        ),
    },
    # ── GOOD 13: Rustic cabin bedroom ──
    {
        "brief": {
            "room_type": "bedroom",
            "occupants": "Couple, mountain cabin weekend retreat",
            "pain_points": [
                "Too dark — log walls absorb all light",
                "Feels like a hunting lodge, not a retreat",
            ],
            "keep_items": ["Log walls (structural, can't change)", "Stone fireplace"],
            "style_profile": {
                "lighting": (
                    "Ambient: warm 2200K wall sconces (lodge-modern style). "
                    "Task: bedside table lamps with linen shades (2700K). "
                    "Accent: fire glow from stone fireplace, LED candles on mantel"
                ),
                "colors": [
                    "Existing honey-tone log walls (60%) — embrace, don't fight them",
                    "Creamy white linens and light accents (30%) — contrast to lighten",
                    "Muted forest green (10%) — throws, plant, single accent chair",
                ],
                "textures": [
                    "Linen duvet and euro shams (bright white — light contrast)",
                    "Faux fur throw at foot of bed",
                    "Woven wool area rug in cream",
                    "Stone fireplace surround (existing)",
                    "Wrought iron curtain rod with linen curtains",
                ],
                "clutter_level": "Minimal — cabin retreat simplicity, only essentials plus fireplace mantel display",
                "mood": (
                    "Modern mountain retreat — rustic bones (log, stone) softened with "
                    "cream linens and warm lighting. Not hunting lodge, not Airbnb-generic. "
                    "Biophilic: real fire, natural materials, forest view"
                ),
            },
            "constraints": [
                "Log walls are structural — can't paint or cover",
                "Weekend use only — low-maintenance materials",
            ],
            "inspiration_notes": [],
        },
        "conversation": [
            {
                "role": "user",
                "content": "Our cabin bedroom. Log walls, stone fireplace. It's too dark and hunting-lodge-y.",
            },
            {
                "role": "assistant",
                "content": (
                    "Log walls absorb light — the fix is contrast, not coverage. Bright white "
                    "linens against honey logs is striking. Warm sconces add ambient glow. "
                    "We soften the hunting lodge feel with cream textiles and remove anything "
                    "that reads 'rustic cliché' (antler decor, plaids). Sound right?"
                ),
            },
            {"role": "user", "content": "Yes! Sophisticated mountain, not kitschy cabin."},
            {
                "role": "assistant",
                "content": (
                    "Sophisticated mountain: white linen against warm logs, faux fur throw, "
                    "muted forest green accents, warm sconces. The stone fireplace stays as "
                    "the hero. Modern wrought iron hardware."
                ),
            },
        ],
        "expected_tag": "PASS",
        "reason": (
            "Strong constraint handling: log walls as feature not flaw. Good translation "
            "of 'not hunting lodge' to specific design parameters. Lighting could "
            "specify more detail on sconce placement."
        ),
    },
    # ── GOOD 14: Minimalist bathroom ──
    {
        "brief": {
            "room_type": "bathroom",
            "occupants": "Single professional, daily spa routine",
            "pain_points": [
                "Cluttered vanity — products everywhere with no storage",
                "Harsh overhead light makes morning routine unpleasant",
                "Tiles are dated beige — feels dingy",
            ],
            "keep_items": ["Freestanding soaking tub (recently installed)"],
            "style_profile": {
                "lighting": (
                    "Ambient: recessed 3000K waterproof LED (soft, not clinical). "
                    "Task: backlit vanity mirror (4000K, high CRI for accurate skin tone). "
                    "Accent: battery waterproof candles around tub for evening ritual"
                ),
                "colors": [
                    "Warm white large-format tile walls (60%) — minimal grout lines, clean",
                    "Pale gray concrete-look floor tile (30%) — spa aesthetic",
                    "Matte black hardware accents (10%) — faucets, shower fixtures, towel bars",
                ],
                "textures": [
                    "Large-format porcelain wall tile (minimal grout lines)",
                    "Honed concrete-look floor tile (non-slip)",
                    "Turkish cotton bath towels in white",
                    "Teak bath mat and shower bench",
                    "Matte black fixtures throughout",
                ],
                "clutter_level": (
                    "Minimal — recessed medicine cabinet, floating vanity with drawers, "
                    "shower niche for bottles, everything hidden"
                ),
                "mood": (
                    "Daily spa — clean surfaces, warm materials (teak, cotton), "
                    "soft lighting transitions from bright morning prep to dim evening soak"
                ),
            },
            "constraints": [
                "Waterproof everything — bathroom-grade materials only",
                "Non-slip flooring — safety requirement",
                "Products must be hidden — zero visual clutter",
            ],
            "inspiration_notes": [],
        },
        "conversation": [
            {"role": "user", "content": "My bathroom. I want a spa vibe. It's currently a mess."},
            {
                "role": "assistant",
                "content": "Spa vibe starts with reducing visual noise. What's your daily bathroom routine like?",
            },
            {
                "role": "user",
                "content": "Skincare morning and night, long soaks in the tub on weekends.",
            },
            {
                "role": "assistant",
                "content": (
                    "Two use modes: efficient morning prep (needs bright, accurate task light) "
                    "and relaxing evening soak (needs dim, warm ambiance). The soaking tub is "
                    "your hero piece. I'd add a teak bench, hide all products, and upgrade the "
                    "vanity mirror to backlit 4000K for your skincare."
                ),
            },
            {"role": "user", "content": "Exactly. Clean, minimal, spa."},
            {
                "role": "assistant",
                "content": (
                    "Minimal spa: large-format tile (fewer grout lines), floating vanity, "
                    "recessed storage, matte black hardware, teak accents. Lighting that "
                    "transitions from 4000K morning to 3000K evening."
                ),
            },
        ],
        "expected_tag": "PASS",
        "reason": (
            "Bathroom-specific: waterproof specs, non-slip noted, CRI for skincare, "
            "two-mode lighting. Good translation of 'spa vibe' to specific parameters. "
            "Could detail spatial layout more."
        ),
    },
    # ── GOOD 15: Eclectic family room — contradiction resolved ──
    {
        "brief": {
            "room_type": "living room",
            "occupants": "Blended family of 6 — couple with 4 kids ages 3 to 14",
            "pain_points": [
                "Design taste conflict — one partner wants minimalism, other wants warmth",
                "Teenager needs gaming zone, toddler needs safe play area",
                "Previous room was either sterile or chaotic — never balanced",
            ],
            "keep_items": ["L-shaped sectional (2 years old, gray fabric)"],
            "style_profile": {
                "lighting": (
                    "Ambient: warm 2700K overhead on dimmer (family movie mode). "
                    "Task: floor lamp by reading corner (3000K). "
                    "Accent: LED strip behind TV console (bias lighting for gaming), "
                    "warm plug-in sconce in play corner"
                ),
                "colors": [
                    "Warm gray sectional as base (60%) — existing, ties to soft gray walls",
                    "Natural wood and cream (30%) — storage, shelving, curtains",
                    "Terracotta and sage green accents (10%) — distributed across zones",
                ],
                "textures": [
                    "Existing upholstered sectional (gray, performance fabric)",
                    "Solid oak storage cubes (rounded edges, toddler-safe)",
                    "Washable cotton play mat in play zone",
                    "Woven jute rug for main seating area",
                    "Linen-cotton curtains",
                ],
                "clutter_level": (
                    "Organized zones — teen corner has closed console cabinet for gaming gear, "
                    "play corner has lidded baskets at toddler height, "
                    "adult zone (sectional area) stays clear with storage ottoman"
                ),
                "mood": (
                    "Warm minimalism — the compromise. Clean lines and storage systems "
                    "(minimalist partner satisfied) with warm textiles and layered accessories "
                    "(warmth partner satisfied). Three defined zones: adult social, teen gaming, "
                    "toddler play — each visible from sectional for supervision"
                ),
            },
            "constraints": [
                "Toddler — rounded edges, anchored furniture, no small objects at floor level",
                "Teenager — gaming setup needs power access and bias lighting",
                "Design taste conflict — must satisfy both minimalist and warmth preferences",
                "Budget moderate — phased approach OK, start with storage",
            ],
            "inspiration_notes": [],
        },
        "conversation": [
            {
                "role": "user",
                "content": "Family room. 6 of us. My partner wants minimalism, I want warmth. We can't agree.",
            },
            {
                "role": "assistant",
                "content": (
                    "That's a real design contradiction — but 'warm minimalism' resolves it. "
                    "Clean storage systems and clear surfaces for your partner, layered textiles "
                    "and warm tones for you. What's the age range of the kids?"
                ),
            },
            {
                "role": "user",
                "content": "3, 7, 10, and 14. The 14-year-old games, the 3-year-old gets into everything.",
            },
            {
                "role": "assistant",
                "content": (
                    "Four kids spanning toddler to teen — this room needs zones. I'll create "
                    "three: adult social around the sectional, teen gaming corner with closed "
                    "cabinet, and a toddler play area with soft mat and lidded baskets. All "
                    "visible from the sectional for supervision. Sound right?"
                ),
            },
            {
                "role": "user",
                "content": "That's exactly what we need. Nobody's suggested zones before.",
            },
            {
                "role": "assistant",
                "content": (
                    "Zoning solves both the age spread and the design taste conflict. "
                    "Each zone gets its own character within the warm minimalist framework. "
                    "Rounded edges on everything the toddler can reach. "
                    "Terracotta and sage accents throughout to unify."
                ),
            },
        ],
        "expected_tag": "PASS:EXCELLENT",
        "reason": (
            "Resolved design contradiction (minimalism vs warmth). Multi-zone layout "
            "for age spread. Deep diagnostic: probed ages, identified supervision needs. "
            "Excellent user fidelity: 'warm minimalism' coined as compromise. "
            "Safety constraints comprehensive."
        ),
    },
]


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# BAD EXAMPLES (15) — should score < 70
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

BAD_EXAMPLES: list[dict[str, Any]] = [
    # ── BAD 1: Echo chamber — no elevation at all ──
    {
        "brief": {
            "room_type": "living room",
            "occupants": "Family",
            "pain_points": [],
            "keep_items": [],
            "style_profile": {
                "lighting": "warm",
                "colors": ["blue", "white"],
                "textures": ["soft"],
                "clutter_level": "minimal",
                "mood": "cozy",
            },
            "constraints": [],
            "inspiration_notes": [],
        },
        "conversation": [
            {"role": "user", "content": "Living room for our family."},
            {"role": "assistant", "content": "What style do you like?"},
            {"role": "user", "content": "Cozy and warm."},
            {
                "role": "assistant",
                "content": "Great! I'll make it cozy and warm with blue and white colors.",
            },
        ],
        "expected_tag": "FAIL:POOR",
        "reason": (
            "Zero elevation: 'warm' lighting (no Kelvin, no layers), 'blue' colors "
            "(no proportions), 'soft' texture (generic), 'cozy' mood (user's own word). "
            "No pain points probed. No diagnostic depth. Not actionable."
        ),
    },
    # ── BAD 2: Lighting desert — no layers ──
    {
        "brief": {
            "room_type": "bedroom",
            "occupants": "Couple in their 40s",
            "pain_points": ["Room is too dark"],
            "keep_items": ["Bed frame"],
            "style_profile": {
                "lighting": "Bright overhead light",
                "colors": [
                    "Beige walls",
                    "Brown furniture",
                    "White bedding",
                ],
                "textures": ["Wood", "Fabric", "Cotton"],
                "clutter_level": "moderate",
                "mood": "relaxing",
            },
            "constraints": ["Budget limited"],
            "inspiration_notes": [],
        },
        "conversation": [
            {"role": "user", "content": "Bedroom, couple, it's too dark."},
            {
                "role": "assistant",
                "content": "We'll add brighter lighting. What colors do you like?",
            },
            {"role": "user", "content": "Beige and brown, keep it neutral."},
            {
                "role": "assistant",
                "content": "Got it. Beige walls, brown furniture, white bedding. Done!",
            },
        ],
        "expected_tag": "FAIL:POOR",
        "reason": (
            "Single lighting layer ('bright overhead'), no Kelvin, no task/accent. "
            "Generic textures ('wood', 'fabric'). No 60/30/10. "
            "Pain point 'too dark' addressed with 'brighter' — not a design solution. "
            "Bedroom getting 'bright overhead' violates sleep guidance."
        ),
    },
    # ── BAD 3: Contradictory brief — no conflict resolution ──
    {
        "brief": {
            "room_type": "living room",
            "occupants": "Single person",
            "pain_points": ["Too cluttered"],
            "keep_items": ["All existing furniture", "All books", "All decorations"],
            "style_profile": {
                "lighting": "Natural light",
                "colors": ["White", "Black", "Red", "Gold", "Pink", "Navy"],
                "textures": ["Velvet", "Chrome", "Rustic wood", "Industrial metal"],
                "clutter_level": "minimal",
                "mood": "minimalist zen",
            },
            "constraints": [],
            "inspiration_notes": [],
        },
        "conversation": [
            {"role": "user", "content": "I want minimalist zen but I can't get rid of anything."},
            {
                "role": "assistant",
                "content": "Sure! We'll make it minimalist while keeping all your things. What colors do you want?",
            },
            {"role": "user", "content": "White, black, red, gold, pink, and navy."},
            {
                "role": "assistant",
                "content": "Beautiful palette! Minimalist zen with all those colors. I love it.",
            },
        ],
        "expected_tag": "FAIL:POOR",
        "reason": (
            "Unresolved contradiction: 'keep everything' + 'minimalist'. "
            "6 colors with no proportional logic. Style conflict: velvet + chrome + "
            "rustic + industrial = incoherent. Agent accepted surface answers without "
            "any diagnostic probing."
        ),
    },
    # ── BAD 4: Missing critical fields ──
    {
        "brief": {
            "room_type": "kitchen",
            "occupants": None,
            "pain_points": [],
            "keep_items": [],
            "style_profile": {
                "lighting": None,
                "colors": [],
                "textures": [],
                "clutter_level": None,
                "mood": "modern",
            },
            "constraints": [],
            "inspiration_notes": [],
        },
        "conversation": [
            {"role": "user", "content": "Kitchen."},
            {"role": "assistant", "content": "What style?"},
            {"role": "user", "content": "Modern."},
            {"role": "assistant", "content": "Great, modern kitchen. Here's your brief."},
        ],
        "expected_tag": "FAIL:POOR",
        "reason": (
            "Almost completely empty: no lighting, no colors, no textures, no "
            "occupants, no constraints, no pain points. Only has room_type and 'modern'. "
            "Agent asked one question and quit. Zero diagnostic effort."
        ),
    },
    # ── BAD 5: Generic textures — no professional vocabulary ──
    {
        "brief": {
            "room_type": "living room",
            "occupants": "Young couple",
            "pain_points": ["Furniture is old"],
            "keep_items": [],
            "style_profile": {
                "lighting": "Floor lamp and ceiling light",
                "colors": [
                    "Gray walls",
                    "White ceiling",
                    "Brown floor",
                ],
                "textures": ["Soft fabric", "Hard wood", "Smooth metal", "Nice leather"],
                "clutter_level": "organized",
                "mood": "stylish and comfortable",
            },
            "constraints": ["Budget"],
            "inspiration_notes": [],
        },
        "conversation": [
            {"role": "user", "content": "Living room for me and my girlfriend. Old furniture."},
            {"role": "assistant", "content": "What materials do you like?"},
            {"role": "user", "content": "Soft stuff, wood, some metal."},
            {
                "role": "assistant",
                "content": "Great! Soft fabric, hard wood, smooth metal, and nice leather. Very stylish.",
            },
        ],
        "expected_tag": "FAIL:WEAK",
        "reason": (
            "Generic textures: 'soft fabric', 'hard wood', 'smooth metal', 'nice leather' — "
            "zero professional vocabulary. No specific materials (boucle, walnut, brushed brass). "
            "Lighting has no Kelvin or layers. Colors just describe surfaces, no 60/30/10."
        ),
    },
    # ── BAD 6: Color chaos — no proportional logic ──
    {
        "brief": {
            "room_type": "bedroom",
            "occupants": "Teenager",
            "pain_points": ["Boring room"],
            "keep_items": [],
            "style_profile": {
                "lighting": "RGB LED strips everywhere",
                "colors": [
                    "Neon purple",
                    "Electric blue",
                    "Hot pink",
                    "Lime green",
                    "Bright orange",
                ],
                "textures": ["Fluffy rug", "Bean bag"],
                "clutter_level": "fun chaos",
                "mood": "party vibes",
            },
            "constraints": [],
            "inspiration_notes": [],
        },
        "conversation": [
            {"role": "user", "content": "I'm 16 and I want my room to be COLORFUL."},
            {"role": "assistant", "content": "Let's make it super colorful! What colors?"},
            {"role": "user", "content": "Purple, blue, pink, green, orange — ALL of them!"},
            {
                "role": "assistant",
                "content": "Amazing! All the colors! RGB LEDs everywhere for party vibes!",
            },
        ],
        "expected_tag": "FAIL:POOR",
        "reason": (
            "5 neon colors with zero proportional logic. RGB LEDs have no color temperature "
            "control (wrong for a bedroom). Only 2 textures, both generic. No sleep guidance "
            "applied to a teenager's bedroom. Agent accepted without any design intelligence."
        ),
    },
    # ── BAD 7: User fidelity failure — hallucinated preferences ──
    {
        "brief": {
            "room_type": "living room",
            "occupants": "Retired woman, lives alone, has a cat",
            "pain_points": ["Room feels lonely"],
            "keep_items": ["Grandmother's rocking chair"],
            "style_profile": {
                "lighting": "Warm ambient with candles and fairy lights",
                "colors": [
                    "Dusty rose walls (60%)",
                    "Cream and gold (30%)",
                    "Deep burgundy accents (10%)",
                ],
                "textures": [
                    "Velvet sofa",
                    "Silk curtains",
                    "Persian rug",
                    "Crystal chandelier",
                ],
                "clutter_level": "curated maximalism",
                "mood": "Grand Victorian parlor — ornate and dramatic",
            },
            "constraints": ["Cat"],
            "inspiration_notes": [],
        },
        "conversation": [
            {
                "role": "user",
                "content": "Living room, just me and my cat. It feels lonely since my husband passed.",
            },
            {
                "role": "assistant",
                "content": "I'm sorry for your loss. Let's create a warm, comforting space. Do you like Victorian style?",
            },
            {"role": "user", "content": "Not really, I prefer simple things. Nothing fancy."},
            {
                "role": "assistant",
                "content": (
                    "I think a Victorian parlor would really suit you — dusty rose walls, "
                    "crystal chandelier, silk curtains. Very elegant!"
                ),
            },
        ],
        "expected_tag": "FAIL:POOR",
        "reason": (
            "User explicitly said 'simple, nothing fancy' but agent hallucinated Victorian "
            "parlor with crystal chandelier and silk curtains. Total user fidelity failure. "
            "Cat constraint ignored (silk curtains + velvet with a cat). "
            "Pain point 'lonely' not addressed at all."
        ),
    },
    # ── BAD 8: Wrong room guidance — bedroom gets office treatment ──
    {
        "brief": {
            "room_type": "bedroom",
            "occupants": "Couple, both light sleepers",
            "pain_points": ["Can't sleep well"],
            "keep_items": [],
            "style_profile": {
                "lighting": "Bright 5000K overhead for productivity",
                "colors": [
                    "Bright white walls",
                    "Chrome and glass furniture",
                    "Bright red accent wall",
                ],
                "textures": ["Glass desk surface", "Chrome legs", "Polyester bedding"],
                "clutter_level": "open office style",
                "mood": "energizing and motivating",
            },
            "constraints": ["Light sleepers"],
            "inspiration_notes": [],
        },
        "conversation": [
            {
                "role": "user",
                "content": "Bedroom for me and my wife. We both have trouble sleeping.",
            },
            {
                "role": "assistant",
                "content": "Let's make it bright and energizing so you feel great when you wake up!",
            },
            {"role": "user", "content": "We just want to sleep better."},
            {
                "role": "assistant",
                "content": "5000K overhead lighting will keep things bright! Red accent wall for energy!",
            },
        ],
        "expected_tag": "FAIL:POOR",
        "reason": (
            "Catastrophic room-type violation: 5000K in a bedroom suppresses melatonin, "
            "red accent wall is stimulating, 'energizing' mood is the opposite of sleep. "
            "Users said 'trouble sleeping' and got an office design. Glass/chrome textures "
            "are cold and clinical for a bedroom."
        ),
    },
    # ── BAD 9: Half-baked brief — started but didn't finish ──
    {
        "brief": {
            "room_type": "home office",
            "occupants": "Freelance writer",
            "pain_points": ["Desk is cramped"],
            "keep_items": ["Bookshelf"],
            "style_profile": {
                "lighting": "Desk lamp",
                "colors": ["Green"],
                "textures": [],
                "clutter_level": None,
                "mood": None,
            },
            "constraints": [],
            "inspiration_notes": [],
        },
        "conversation": [
            {"role": "user", "content": "Home office, I'm a writer. My desk is too small."},
            {"role": "assistant", "content": "Bigger desk! What color?"},
            {"role": "user", "content": "Green maybe."},
            {"role": "assistant", "content": "Green it is. Here's your brief."},
        ],
        "expected_tag": "FAIL:POOR",
        "reason": (
            "Drastically incomplete: no textures, no clutter strategy, no mood, "
            "lighting is just 'desk lamp', single color 'green' (what shade? where?). "
            "Writer's needs not explored (focus, quiet, ergonomics). Pain point 'cramped' "
            "addressed with 'bigger desk' — no design thinking."
        ),
    },
    # ── BAD 10: Style soup — every style mixed together ──
    {
        "brief": {
            "room_type": "living room",
            "occupants": "Couple",
            "pain_points": ["Room looks dated"],
            "keep_items": [],
            "style_profile": {
                "lighting": "Mix of industrial pendant, crystal chandelier, and paper lantern",
                "colors": [
                    "Mid-century orange",
                    "Scandinavian white",
                    "Industrial gray",
                    "Bohemian jewel tones",
                ],
                "textures": [
                    "Concrete floors (industrial)",
                    "Sheepskin throws (scandi)",
                    "Macramé wall hangings (boho)",
                    "Chrome and glass (modern)",
                    "Distressed wood (farmhouse)",
                ],
                "clutter_level": "eclectic mix",
                "mood": "industrial-scandinavian-bohemian-modern farmhouse fusion",
            },
            "constraints": [],
            "inspiration_notes": [],
        },
        "conversation": [
            {
                "role": "user",
                "content": "We like a lot of styles. Industrial, Scandi, boho, modern.",
            },
            {"role": "assistant", "content": "Let's mix them all together! What else?"},
            {"role": "user", "content": "Farmhouse too. And mid-century."},
            {
                "role": "assistant",
                "content": "Amazing fusion! Industrial-Scandi-boho-modern-farmhouse-mid-century. So unique!",
            },
        ],
        "expected_tag": "FAIL:WEAK",
        "reason": (
            "5+ styles mashed together with no coherence. Crystal chandelier + paper "
            "lantern + industrial pendant in one room. Concrete + sheepskin + macramé + chrome. "
            "Agent should have helped user identify a PRIMARY style and use others as accents. "
            "No diagnostic probing to find the unifying thread."
        ),
    },
    # ── BAD 11: Budget ignored, constraints invisible ──
    {
        "brief": {
            "room_type": "bedroom",
            "occupants": "College student in shared apartment",
            "pain_points": ["Can't personalize rental"],
            "keep_items": ["IKEA bed and desk"],
            "style_profile": {
                "lighting": "Custom built-in LED cove lighting throughout ceiling perimeter",
                "colors": [
                    "Custom hand-mixed Farrow & Ball paint in Heritage Blue",
                    "Italian marble nightstand tops",
                    "Hand-dyed silk accent wall",
                ],
                "textures": [
                    "Italian marble nightstands",
                    "Hand-loomed silk wallcovering",
                    "Custom millwork headboard",
                    "Cashmere throw blanket",
                ],
                "clutter_level": "minimal — custom built-in closet system",
                "mood": "bespoke luxury suite",
            },
            "constraints": ["Rental — no modifications", "Student budget"],
            "inspiration_notes": [],
        },
        "conversation": [
            {
                "role": "user",
                "content": "I'm a student, shared rental. I can't change anything permanent.",
            },
            {
                "role": "assistant",
                "content": "Let's design a luxury bedroom! Custom cove lighting would be stunning.",
            },
            {"role": "user", "content": "I have like no money though."},
            {
                "role": "assistant",
                "content": (
                    "Italian marble nightstands and hand-dyed silk would elevate the space. "
                    "Custom millwork headboard for a bespoke feel!"
                ),
            },
        ],
        "expected_tag": "FAIL:POOR",
        "reason": (
            "Both constraints completely ignored: rental (suggests built-in cove lighting, "
            "custom millwork, paint) and student budget (Italian marble, hand-dyed silk, "
            "cashmere). Agent designed a luxury suite for someone who explicitly said "
            "'no money' and 'can't change anything'."
        ),
    },
    # ── BAD 12: Pain points ignored ──
    {
        "brief": {
            "room_type": "kitchen",
            "occupants": "Family with 3 kids",
            "pain_points": [
                "No counter space — can't prep meals",
                "Kids can't do homework because dining table is always covered",
            ],
            "keep_items": [],
            "style_profile": {
                "lighting": "Pendant light over island",
                "colors": ["White", "Gray"],
                "textures": ["Quartz counters", "Tile backsplash"],
                "clutter_level": "clean",
                "mood": "magazine-worthy showroom kitchen",
            },
            "constraints": ["3 young kids"],
            "inspiration_notes": [],
        },
        "conversation": [
            {
                "role": "user",
                "content": "Kitchen, 3 kids. There's no counter space and kids can't do homework.",
            },
            {
                "role": "assistant",
                "content": "Let's create a gorgeous showroom kitchen! White and gray.",
            },
            {"role": "user", "content": "But I need actual counter space to cook..."},
            {
                "role": "assistant",
                "content": "A beautiful pendant light over a quartz island would look amazing in photos!",
            },
        ],
        "expected_tag": "FAIL:WEAK",
        "reason": (
            "Pain points explicitly stated but completely ignored. User needs functional "
            "counter space and homework zone; agent designed for aesthetics ('magazine-worthy'). "
            "'Clean' clutter level with 3 kids is unrealistic. Lighting only mentions one "
            "fixture. No task lighting for prep or homework."
        ),
    },
    # ── BAD 13: Vague everything — slightly better than empty ──
    {
        "brief": {
            "room_type": "living room",
            "occupants": "Me and my roommates",
            "pain_points": ["It's ugly"],
            "keep_items": ["The TV"],
            "style_profile": {
                "lighting": "Something warm",
                "colors": ["Neutral tones", "Maybe a pop of color"],
                "textures": ["Comfortable furniture", "A nice rug"],
                "clutter_level": "not too much not too little",
                "mood": "chill vibes",
            },
            "constraints": ["Roommates"],
            "inspiration_notes": [],
        },
        "conversation": [
            {"role": "user", "content": "Living room with roommates. It's ugly."},
            {"role": "assistant", "content": "What do you want it to look like?"},
            {"role": "user", "content": "Just nicer. Chill vibes."},
            {
                "role": "assistant",
                "content": "Neutral tones with a pop of color. Comfortable furniture. Warm lighting. Chill vibes!",
            },
        ],
        "expected_tag": "FAIL:WEAK",
        "reason": (
            "Everything vague: 'something warm' lighting, 'neutral tones', "
            "'comfortable furniture', 'a nice rug', 'chill vibes'. Agent echoed user's "
            "vagueness instead of probing. 'Pop of color' is the quintessential "
            "non-specific design phrase. Not actionable for generation."
        ),
    },
    # ── BAD 14: Keep items ignored ──
    {
        "brief": {
            "room_type": "living room",
            "occupants": "Family with heirloom furniture collection",
            "pain_points": ["Room needs updating but heirlooms are precious"],
            "keep_items": [
                "Grandmother's Chippendale secretary desk",
                "Hand-carved Indonesian coffee table",
                "Persian Kashan rug (hand-knotted, 50 years old)",
            ],
            "style_profile": {
                "lighting": "Recessed LED throughout (4000K)",
                "colors": [
                    "All white everything — walls, furniture, accessories",
                ],
                "textures": [
                    "White lacquer furniture",
                    "Acrylic/lucite side tables",
                    "Solid white area rug",
                ],
                "clutter_level": "minimal — stark and clean",
                "mood": "ultra-modern gallery space — stark white cube",
            },
            "constraints": ["Heirlooms must stay"],
            "inspiration_notes": [],
        },
        "conversation": [
            {
                "role": "user",
                "content": "Our living room has my grandmother's desk, a carved coffee table, and a 50-year-old Persian rug. They're precious.",
            },
            {"role": "assistant", "content": "Let's go ultra-modern! All white everything."},
            {"role": "user", "content": "But the heirlooms..."},
            {
                "role": "assistant",
                "content": (
                    "White lacquer furniture and acrylic tables would look so fresh! "
                    "A solid white rug ties it together."
                ),
            },
        ],
        "expected_tag": "FAIL:POOR",
        "reason": (
            "3 significant heirlooms (Chippendale, Indonesian carved, Persian rug) completely "
            "erased by all-white-everything scheme. White lacquer + acrylic would clash "
            "catastrophically with traditional pieces. User's emotional attachment dismissed. "
            "The design should integrate and celebrate heirlooms, not erase them."
        ),
    },
    # ── BAD 15: Good structure but shallow content ──
    {
        "brief": {
            "room_type": "dining room",
            "occupants": "Couple, occasional dinners for 4",
            "pain_points": ["Table is too big for the space"],
            "keep_items": [],
            "style_profile": {
                "lighting": (
                    "Ambient: overhead fixture. Task: candles on table. Accent: none needed"
                ),
                "colors": [
                    "White walls (60%)",
                    "Wood table (30%)",
                    "Napkins for color (10%)",
                ],
                "textures": ["Wood table", "Fabric chairs", "Cotton curtains"],
                "clutter_level": "minimal",
                "mood": "clean and simple dining space",
            },
            "constraints": [],
            "inspiration_notes": [],
        },
        "conversation": [
            {"role": "user", "content": "Dining room for 2, sometimes 4. Table is too big."},
            {"role": "assistant", "content": "Smaller table! What style?"},
            {"role": "user", "content": "Clean and simple."},
            {
                "role": "assistant",
                "content": (
                    "White walls, wood table, fabric chairs. Overhead light and candles. "
                    "Clean and simple dining."
                ),
            },
        ],
        "expected_tag": "FAIL:WEAK",
        "reason": (
            "Has 60/30/10 structure but content is vapid: 'overhead fixture' (no type, "
            "no Kelvin, no chandelier height), 'wood table' (what wood?), 'fabric chairs' "
            "(what fabric?). Looks like a design brief template that wasn't filled in. "
            "'Accent: none needed' shows zero design ambition. Dining room has no "
            "atmosphere — candles as task lighting is inadequate."
        ),
    },
]


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# Combined dataset for easy iteration
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

ALL_EXAMPLES = GOOD_EXAMPLES + BAD_EXAMPLES

# Sanity checks
assert len(GOOD_EXAMPLES) == 15, f"Expected 15 good, got {len(GOOD_EXAMPLES)}"
assert len(BAD_EXAMPLES) == 15, f"Expected 15 bad, got {len(BAD_EXAMPLES)}"
assert len(ALL_EXAMPLES) == 30
