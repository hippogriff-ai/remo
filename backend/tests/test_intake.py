"""Tests for the intake chat activity.

Unit tests (no API key needed) cover:
- System prompt loading and mode customization
- Message building from conversation history
- Tool call extraction from Claude responses
- DesignBrief construction from tool data
- QuickReplyOption parsing
- Turn counting logic

Integration tests (marked @pytest.mark.integration) test real Claude calls.
"""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import httpx

from app.activities.intake import (
    INTAKE_TOOLS,
    MAX_TURNS,
    MODE_INSTRUCTIONS,
    SKILL_NAMES,
    _format_brief_context,
    _format_room_analysis_section,
    _get_inspiration_note,
    _run_intake_core,
    build_brief,
    build_messages,
    build_options,
    extract_skill_call,
    load_system_prompt,
)
from app.models.contracts import (
    ChatMessage,
    DesignBrief,
    IntakeChatInput,
    IntakeChatOutput,
    QuickReplyOption,
    StyleProfile,
)

# === System Prompt Tests ===


class TestLoadSystemPrompt:
    def test_loads_prompt_file(self):
        prompt = load_system_prompt("quick", 1)
        assert "design translator" in prompt.lower() or "design consultant" in prompt.lower()

    def test_quick_mode_injected(self):
        prompt = load_system_prompt("quick", 1)
        assert "Quick Mode" in prompt
        assert "~3 turns" in prompt

    def test_full_mode_injected(self):
        prompt = load_system_prompt("full", 1)
        assert "Full Mode" in prompt
        assert "~10 turns" in prompt

    def test_open_mode_injected(self):
        prompt = load_system_prompt("open", 1)
        assert "Open Conversation Mode" in prompt
        assert "~15 turns" in prompt

    def test_remaining_turns_calculated(self):
        # Quick mode, turn 1: should have ~3 remaining (MAX_TURNS["quick"]=4, 4-1=3)
        prompt = load_system_prompt("quick", 1)
        assert "3 turns remaining" in prompt

    def test_remaining_turns_decrements(self):
        prompt = load_system_prompt("quick", 3)
        assert "1 turns remaining" in prompt

    def test_remaining_turns_floors_at_zero(self):
        prompt = load_system_prompt("quick", 10)
        assert "0 turns remaining" in prompt

    def test_translation_table_present(self):
        """P1 #1 success metric: translation table encoded in prompt."""
        prompt = load_system_prompt("quick", 1)
        assert "Cozy" in prompt
        assert "amber, terracotta" in prompt
        assert "2200" in prompt  # Kelvin temperature

    def test_diagnose_pipeline_present(self):
        """P1 #1 success metric: DIAGNOSE pipeline encoded in prompt."""
        prompt = load_system_prompt("quick", 1)
        assert "DIAGNOSE" in prompt
        assert "D — DETECT" in prompt
        assert "I — INTERPRET" in prompt
        assert "E — EVALUATE" in prompt

    def test_room_specific_guidance_present(self):
        """P1 #1 success metric: room-specific guidance encoded in prompt."""
        prompt = load_system_prompt("quick", 1)
        assert "Living Room" in prompt
        assert "Bedroom" in prompt
        assert "prospect-refuge" in prompt

    def test_elevation_rules_present(self):
        """The prompt must instruct the model to elevate user language."""
        prompt = load_system_prompt("quick", 1)
        assert "60/30/10" in prompt
        assert "Kelvin" in prompt
        assert "three layers" in prompt.lower() or "three lighting layers" in prompt.lower()

    def test_skill_selection_instructions_present(self):
        """The prompt must instruct the model to choose one skill per turn."""
        prompt = load_system_prompt("quick", 1)
        assert "interview_client" in prompt
        assert "draft_design_brief" in prompt
        assert "EXACTLY ONE" in prompt

    def test_mode_placeholder_replaced(self):
        """The {mode_instructions} placeholder should not appear in output."""
        for mode in ("quick", "full", "open"):
            prompt = load_system_prompt(mode, 1)
            assert "{mode_instructions}" not in prompt


# === Brief Context Injection Tests ===


class TestBriefContextInjection:
    def test_no_previous_brief_no_injection(self):
        """Without previous brief, prompt should not contain 'GATHERED SO FAR'."""
        prompt = load_system_prompt("quick", 2, previous_brief=None)
        assert "GATHERED SO FAR" not in prompt

    def test_first_turn_no_injection(self):
        """Turn 1 should never inject brief context (nothing gathered yet)."""
        brief = {"room_type": "bedroom", "pain_points": ["too dark"]}
        prompt = load_system_prompt("quick", 1, previous_brief=brief)
        assert "GATHERED SO FAR" not in prompt

    def test_second_turn_with_brief_injects(self):
        """Turn 2+ with previous brief should inject 'GATHERED SO FAR' section."""
        brief = {
            "room_type": "living room",
            "occupants": "couple, 30s",
            "pain_points": ["too dark", "uncomfortable armchair"],
            "style_profile": {"mood": "warm and inviting", "colors": ["warm ivory (60%)"]},
        }
        prompt = load_system_prompt("full", 3, previous_brief=brief)
        assert "GATHERED SO FAR" in prompt
        assert "living room" in prompt
        assert "couple, 30s" in prompt
        assert "too dark" in prompt
        assert "warm and inviting" in prompt
        assert "Do NOT drop any fields" in prompt

    def test_format_brief_context_all_fields(self):
        """All brief fields should format correctly."""
        brief = {
            "room_type": "bedroom",
            "occupants": "single, 25",
            "pain_points": ["insomnia", "too bright"],
            "keep_items": ["vintage lamp"],
            "constraints": ["renting", "budget $5000"],
            "style_profile": {
                "mood": "calm retreat",
                "lighting": "warm 2200K ambient",
                "colors": ["soft blue (60%)", "cream (30%)"],
                "textures": ["linen", "cotton"],
                "clutter_level": "minimal",
            },
            "domains_covered": ["room purpose", "style", "lighting"],
        }
        ctx = _format_brief_context(brief)
        assert "bedroom" in ctx
        assert "insomnia" in ctx
        assert "vintage lamp" in ctx
        assert "renting" in ctx
        assert "calm retreat" in ctx
        assert "warm 2200K" in ctx
        assert "soft blue" in ctx
        assert "linen" in ctx
        assert "minimal" in ctx
        assert "room purpose" in ctx

    def test_format_brief_context_empty(self):
        """Empty brief should return fallback message."""
        ctx = _format_brief_context({})
        assert ctx == "No information gathered yet."


class TestRoomAnalysisInjection:
    """PR-6: Room analysis injection into the intake system prompt."""

    def test_no_analysis_fallback(self):
        """Without analysis, prompt contains fallback instruction."""
        section = _format_room_analysis_section(None)
        assert "no pre-analysis" in section.lower()

    def test_empty_analysis_fallback(self):
        """Empty dict analysis returns fallback."""
        section = _format_room_analysis_section({})
        assert "no pre-analysis" in section.lower()

    def test_analysis_with_hypothesis(self):
        """Hypothesis appears in the analysis section."""
        analysis = {
            "room_type": "living room",
            "room_type_confidence": 0.85,
            "hypothesis": "Well-lit family space with mixed warmth",
        }
        section = _format_room_analysis_section(analysis)
        assert "living room" in section
        assert "85%" in section
        assert "Well-lit family space" in section
        assert "HYPOTHESIS CORRECTIONS" in section

    def test_analysis_with_lighting(self):
        """Lighting details appear in the section."""
        analysis = {
            "room_type": "bedroom",
            "lighting": {
                "natural_light_intensity": "limited",
                "natural_light_direction": "north-facing",
                "existing_artificial": "single overhead",
                "lighting_gaps": ["dark reading corner"],
            },
        }
        section = _format_room_analysis_section(analysis)
        assert "limited" in section
        assert "north-facing" in section
        assert "dark reading corner" in section

    def test_analysis_with_furniture(self):
        """Furniture observations appear in the section."""
        analysis = {
            "room_type": "kitchen",
            "furniture": [
                {"item": "oak dining table", "condition": "good", "keep_candidate": True},
                {"item": "metal stools", "condition": "worn"},
            ],
        }
        section = _format_room_analysis_section(analysis)
        assert "oak dining table" in section
        assert "[keep]" in section
        assert "metal stools" in section
        assert "worn" in section

    def test_analysis_with_behavioral_signals(self):
        """Behavioral signals appear in the section."""
        analysis = {
            "room_type": "living room",
            "behavioral_signals": [
                {"observation": "toys on floor", "inference": "young children"},
            ],
        }
        section = _format_room_analysis_section(analysis)
        assert "toys on floor" in section
        assert "young children" in section

    def test_analysis_with_uncertain_aspects(self):
        """Uncertain aspects are flagged for probing."""
        analysis = {
            "room_type": "bedroom",
            "uncertain_aspects": ["ceiling height", "wall color accuracy"],
        }
        section = _format_room_analysis_section(analysis)
        assert "Uncertain" in section
        assert "ceiling height" in section

    def test_analysis_instructions_present(self):
        """Using This Analysis and HYPOTHESIS CORRECTIONS instructions present."""
        analysis = {"room_type": "living room"}
        section = _format_room_analysis_section(analysis)
        assert "Do NOT re-ask" in section
        assert "HYPOTHESIS CORRECTIONS" in section
        assert "photos can be misleading" in section

    def test_system_prompt_includes_analysis(self):
        """load_system_prompt injects analysis section into prompt."""
        analysis = {
            "room_type": "bedroom",
            "hypothesis": "Dark bedroom needing light layers",
        }
        prompt = load_system_prompt("quick", 1, room_analysis=analysis)
        assert "Dark bedroom needing light layers" in prompt
        assert "ROOM ANALYSIS" in prompt

    def test_system_prompt_without_analysis(self):
        """load_system_prompt works without analysis (backward compat)."""
        prompt = load_system_prompt("quick", 1)
        assert "ROOM ANALYSIS" in prompt
        assert "no pre-analysis" in prompt.lower()


# === Message Building Tests ===


class TestBuildMessages:
    def test_empty_history(self):
        messages = build_messages([], "Hello")
        assert len(messages) == 1
        assert messages[0] == {"role": "user", "content": "Hello"}

    def test_with_history(self):
        history = [
            ChatMessage(role="user", content="Hi"),
            ChatMessage(role="assistant", content="Hello! Tell me about your room."),
        ]
        messages = build_messages(history, "It's a living room")
        assert len(messages) == 3
        assert messages[0]["role"] == "user"
        assert messages[1]["role"] == "assistant"
        assert messages[2]["content"] == "It's a living room"

    def test_preserves_order(self):
        history = [
            ChatMessage(role="user", content="msg1"),
            ChatMessage(role="assistant", content="msg2"),
            ChatMessage(role="user", content="msg3"),
            ChatMessage(role="assistant", content="msg4"),
        ]
        messages = build_messages(history, "msg5")
        assert len(messages) == 5
        contents = [m["content"] for m in messages]
        assert contents == ["msg1", "msg2", "msg3", "msg4", "msg5"]

    def test_first_turn_with_room_photos(self):
        """First turn should include room photos as image blocks."""
        photos = ["https://r2.example.com/room1.jpg", "https://r2.example.com/room2.jpg"]
        messages = build_messages([], "My kitchen needs help", room_photo_urls=photos)
        assert len(messages) == 1
        content = messages[0]["content"]
        # Should be a list of content blocks (multimodal)
        assert isinstance(content, list)
        assert len(content) == 3  # 2 images + 1 text
        assert content[0]["type"] == "image"
        assert content[0]["source"]["url"] == photos[0]
        assert content[1]["type"] == "image"
        assert content[1]["source"]["url"] == photos[1]
        assert content[2]["type"] == "text"
        assert content[2]["text"] == "My kitchen needs help"

    def test_second_turn_no_photo_injection(self):
        """Photos should only be injected on first turn (empty history)."""
        history = [
            ChatMessage(role="user", content="Hi"),
            ChatMessage(role="assistant", content="Hello!"),
        ]
        photos = ["https://r2.example.com/room1.jpg"]
        messages = build_messages(history, "It's cozy", room_photo_urls=photos)
        # Second turn: user message should be plain text, not multimodal
        assert messages[-1]["content"] == "It's cozy"

    def test_first_turn_no_photos(self):
        """First turn without photos should work normally (text only)."""
        messages = build_messages([], "Hello", room_photo_urls=[])
        assert messages[0]["content"] == "Hello"

    def test_first_turn_none_photos(self):
        """First turn with None photos should work normally."""
        messages = build_messages([], "Hello", room_photo_urls=None)
        assert messages[0]["content"] == "Hello"

    def test_first_turn_with_inspiration_photos(self):
        """First turn should include inspiration photos after room photos."""
        room = ["https://r2.example.com/room1.jpg"]
        inspo = ["https://r2.example.com/inspo1.jpg", "https://r2.example.com/inspo2.jpg"]
        notes = [{"photo_index": 0, "note": "Love the warm lighting"}]
        messages = build_messages(
            [],
            "Help me redesign",
            room_photo_urls=room,
            inspiration_photo_urls=inspo,
            inspiration_notes=notes,
        )
        content = messages[0]["content"]
        assert isinstance(content, list)
        # room photo + inspo photo 1 + note text + inspo photo 2 + user text = 5
        assert len(content) == 5
        # Room photo first
        assert content[0]["type"] == "image"
        assert content[0]["source"]["url"] == room[0]
        # Inspo photo 1 + note
        assert content[1]["type"] == "image"
        assert content[1]["source"]["url"] == inspo[0]
        assert content[2]["type"] == "text"
        assert "Inspiration photo 1 note" in content[2]["text"]
        assert "Love the warm lighting" in content[2]["text"]
        # Inspo photo 2 (no note)
        assert content[3]["type"] == "image"
        assert content[3]["source"]["url"] == inspo[1]
        # User text last
        assert content[4]["type"] == "text"
        assert content[4]["text"] == "Help me redesign"

    def test_inspiration_only_no_room_photos(self):
        """Inspiration photos work without room photos."""
        inspo = ["https://r2.example.com/inspo1.jpg"]
        messages = build_messages(
            [],
            "I want this vibe",
            inspiration_photo_urls=inspo,
        )
        content = messages[0]["content"]
        assert isinstance(content, list)
        assert len(content) == 2  # inspo image + user text
        assert content[0]["type"] == "image"
        assert content[1]["type"] == "text"

    def test_inspiration_with_no_notes(self):
        """Inspiration photos without notes should not add text annotations."""
        inspo = ["https://r2.example.com/inspo1.jpg"]
        messages = build_messages(
            [],
            "Redesign please",
            inspiration_photo_urls=inspo,
            inspiration_notes=None,
        )
        content = messages[0]["content"]
        assert isinstance(content, list)
        # Just 1 image + 1 text (no note annotation)
        assert len(content) == 2
        text_blocks = [b for b in content if b["type"] == "text"]
        assert len(text_blocks) == 1  # only user message


# === Inspiration Note Helper Tests ===


class TestGetInspirationNote:
    def test_returns_note_for_matching_index(self):
        notes = [{"photo_index": 0, "note": "Love this palette"}]
        assert _get_inspiration_note(0, notes) == "Love this palette"

    def test_returns_none_for_unmatched_index(self):
        notes = [{"photo_index": 0, "note": "Love this palette"}]
        assert _get_inspiration_note(1, notes) is None

    def test_returns_none_for_empty_notes(self):
        assert _get_inspiration_note(0, []) is None

    def test_returns_none_for_none_notes(self):
        assert _get_inspiration_note(0, None) is None

    def test_skips_empty_note_text(self):
        notes = [{"photo_index": 0, "note": ""}]
        assert _get_inspiration_note(0, notes) is None

    def test_multiple_notes_correct_match(self):
        notes = [
            {"photo_index": 0, "note": "Warm tones"},
            {"photo_index": 1, "note": "Love the rug"},
            {"photo_index": 2, "note": "Great layout"},
        ]
        assert _get_inspiration_note(1, notes) == "Love the rug"
        assert _get_inspiration_note(2, notes) == "Great layout"


# === Tool Call Extraction Tests ===


class TestExtractSkillCall:
    def _make_response(self, content_blocks):
        """Create a mock Anthropic response with given content blocks."""
        response = MagicMock()
        response.content = content_blocks
        return response

    def _make_tool_use(self, name, input_data):
        block = MagicMock()
        block.type = "tool_use"
        block.name = name
        block.input = input_data
        return block

    def _make_text(self, text):
        block = MagicMock()
        block.type = "text"
        block.text = text
        return block

    def test_extracts_interview_skill(self):
        data = {"message": "Tell me more", "is_open_ended": True}
        response = self._make_response([self._make_tool_use("interview_client", data)])
        name, skill_data = extract_skill_call(response)
        assert name == "interview_client"
        assert skill_data == data

    def test_extracts_draft_skill(self):
        data = {"message": "Here's your brief", "design_brief": {"room_type": "bedroom"}}
        response = self._make_response([self._make_tool_use("draft_design_brief", data)])
        name, skill_data = extract_skill_call(response)
        assert name == "draft_design_brief"
        assert skill_data == data

    def test_takes_first_skill_if_multiple(self):
        """If model calls both skills (shouldn't), take the first one."""
        response = self._make_response(
            [
                self._make_tool_use("interview_client", {"message": "Q1"}),
                self._make_tool_use("draft_design_brief", {"message": "Summary"}),
            ]
        )
        name, _ = extract_skill_call(response)
        assert name == "interview_client"

    def test_ignores_text_blocks(self):
        response = self._make_response(
            [
                self._make_text("thinking..."),
                self._make_tool_use("interview_client", {"message": "Hi"}),
            ]
        )
        name, skill_data = extract_skill_call(response)
        assert name == "interview_client"
        assert skill_data["message"] == "Hi"

    def test_ignores_unknown_tool_names(self):
        response = self._make_response([self._make_tool_use("unknown_tool", {"message": "?"})])
        name, skill_data = extract_skill_call(response)
        assert name is None
        assert skill_data == {}

    def test_handles_empty_response(self):
        response = self._make_response([])
        name, skill_data = extract_skill_call(response)
        assert name is None
        assert skill_data == {}


# === Brief Building Tests ===


class TestBuildBrief:
    def test_minimal_brief(self):
        brief = build_brief({"room_type": "living room"})
        assert brief.room_type == "living room"
        assert brief.occupants is None
        assert brief.pain_points == []
        assert brief.style_profile is None

    def test_full_brief(self):
        data = {
            "room_type": "bedroom",
            "occupants": "couple, 30s",
            "pain_points": ["too dark", "cluttered"],
            "keep_items": ["grandmother's dresser"],
            "style_profile": {
                "lighting": "warm ambient 2700K, bedside task, accent on headboard wall",
                "colors": ["soft sage walls (60%)", "cream textiles (30%)", "brass accents (10%)"],
                "textures": ["linen bedding", "sheepskin rug", "walnut nightstands"],
                "clutter_level": "minimal — closed storage for daily items",
                "mood": "serene retreat — refuge-dominant, cool tones, blackout capability",
            },
            "constraints": ["budget under $5000", "keep existing floor"],
            "inspiration_notes": [
                {
                    "photo_index": 0,
                    "note": "Love the headboard style",
                    "agent_clarification": "Upholstered channel-tufted headboard in sage velvet",
                }
            ],
        }
        brief = build_brief(data)
        assert brief.room_type == "bedroom"
        assert brief.occupants == "couple, 30s"
        assert len(brief.pain_points) == 2
        assert "grandmother's dresser" in brief.keep_items
        assert brief.style_profile is not None
        assert "2700K" in brief.style_profile.lighting
        assert len(brief.style_profile.colors) == 3
        assert len(brief.style_profile.textures) == 3
        assert brief.style_profile.clutter_level == "minimal — closed storage for daily items"
        assert len(brief.constraints) == 2
        assert len(brief.inspiration_notes) == 1
        assert brief.inspiration_notes[0].photo_index == 0

    def test_brief_without_style_profile(self):
        brief = build_brief({"room_type": "kitchen", "pain_points": ["poor lighting"]})
        assert brief.room_type == "kitchen"
        assert brief.style_profile is None
        assert brief.pain_points == ["poor lighting"]

    def test_brief_with_empty_style_profile(self):
        brief = build_brief({"room_type": "office", "style_profile": {}})
        assert brief.style_profile is not None
        assert brief.style_profile.lighting is None
        assert brief.style_profile.colors == []

    def test_brief_validates_as_contract(self):
        """Brief must validate as the exact contract model."""
        brief = build_brief(
            {
                "room_type": "living room",
                "style_profile": {"lighting": "warm", "colors": ["blue"]},
            }
        )
        assert isinstance(brief, DesignBrief)
        assert isinstance(brief.style_profile, StyleProfile)

    def test_brief_with_new_designer_brain_fields(self):
        """New Designer Brain fields are populated from tool data."""
        data = {
            "room_type": "living room",
            "emotional_drivers": ["started WFH", "room feels oppressive"],
            "usage_patterns": "couple WFH Mon-Fri, host dinners monthly",
            "renovation_willingness": "repaint yes, fixtures maybe, tile no",
            "room_analysis_hypothesis": "Bright room needing warmth and storage",
        }
        brief = build_brief(data)
        assert brief.emotional_drivers == ["started WFH", "room feels oppressive"]
        assert brief.usage_patterns == "couple WFH Mon-Fri, host dinners monthly"
        assert brief.renovation_willingness == "repaint yes, fixtures maybe, tile no"
        assert brief.room_analysis_hypothesis == "Bright room needing warmth and storage"

    def test_brief_new_fields_default_when_missing(self):
        """New Designer Brain fields have safe defaults when omitted."""
        brief = build_brief({"room_type": "kitchen"})
        assert brief.emotional_drivers == []
        assert brief.usage_patterns is None
        assert brief.renovation_willingness is None
        assert brief.room_analysis_hypothesis is None

    def test_brief_lifestyle_separate_from_occupants(self):
        """Lifestyle is stored as its own field, not merged into occupants."""
        data = {
            "room_type": "living room",
            "occupants": "couple, 30s",
            "lifestyle": "Morning yoga, weekend hosting",
        }
        brief = build_brief(data)
        assert brief.occupants == "couple, 30s"
        assert brief.lifestyle == "Morning yoga, weekend hosting"


# === Options Building Tests ===


class TestBuildOptions:
    def test_none_input(self):
        assert build_options(None) is None

    def test_empty_list(self):
        assert build_options([]) is None

    def test_basic_options(self):
        raw = [
            {"number": 1, "label": "Cozy & warm", "value": "warm palette, layered textiles"},
            {"number": 2, "label": "Modern & sleek", "value": "neutral base, geometric lines"},
        ]
        options = build_options(raw)
        assert options is not None
        assert len(options) == 2
        assert isinstance(options[0], QuickReplyOption)
        assert options[0].number == 1
        assert options[0].label == "Cozy & warm"
        assert options[1].value == "neutral base, geometric lines"

    def test_auto_numbers_if_missing(self):
        raw = [
            {"label": "Option A", "value": "val_a"},
            {"label": "Option B", "value": "val_b"},
        ]
        options = build_options(raw)
        assert options is not None
        assert options[0].number == 1
        assert options[1].number == 2


# === Tool Definition Tests ===


class TestToolDefinitions:
    def test_two_skill_tools_defined(self):
        assert len(INTAKE_TOOLS) == 2

    def test_skill_names(self):
        names = {t["name"] for t in INTAKE_TOOLS}
        assert names == {"interview_client", "draft_design_brief"}

    def test_skill_names_constant_matches(self):
        """SKILL_NAMES frozenset matches actual tool names."""
        names = {t["name"] for t in INTAKE_TOOLS}
        assert names == SKILL_NAMES

    def test_interview_tool_requires_message(self):
        tool = next(t for t in INTAKE_TOOLS if t["name"] == "interview_client")
        assert "message" in tool["input_schema"]["required"]

    def test_interview_tool_has_partial_brief(self):
        tool = next(t for t in INTAKE_TOOLS if t["name"] == "interview_client")
        props = tool["input_schema"]["properties"]
        assert "partial_brief_update" in props
        # partial_brief_update should have brief sub-properties
        pb_props = props["partial_brief_update"]["properties"]
        assert "room_type" in pb_props
        assert "style_profile" in pb_props

    def test_interview_tool_has_options_and_open_ended(self):
        tool = next(t for t in INTAKE_TOOLS if t["name"] == "interview_client")
        props = tool["input_schema"]["properties"]
        assert "options" in props
        assert "is_open_ended" in props

    def test_draft_tool_requires_message_and_brief(self):
        tool = next(t for t in INTAKE_TOOLS if t["name"] == "draft_design_brief")
        assert "message" in tool["input_schema"]["required"]
        assert "design_brief" in tool["input_schema"]["required"]

    def test_draft_tool_brief_requires_room_type(self):
        tool = next(t for t in INTAKE_TOOLS if t["name"] == "draft_design_brief")
        brief_schema = tool["input_schema"]["properties"]["design_brief"]
        assert "room_type" in brief_schema["required"]

    def test_draft_tool_has_options(self):
        tool = next(t for t in INTAKE_TOOLS if t["name"] == "draft_design_brief")
        props = tool["input_schema"]["properties"]
        assert "options" in props

    def test_brief_properties_have_descriptions(self):
        """Brief property schemas should have descriptions for model guidance."""
        tool = next(t for t in INTAKE_TOOLS if t["name"] == "draft_design_brief")
        props = tool["input_schema"]["properties"]["design_brief"]["properties"]
        for key in ("room_type", "pain_points", "constraints", "domains_covered"):
            assert "description" in props[key], f"{key} missing description"

    def test_style_profile_properties_have_descriptions(self):
        """Style profile sub-properties should have elevation guidance."""
        tool = next(t for t in INTAKE_TOOLS if t["name"] == "draft_design_brief")
        sp_props = tool["input_schema"]["properties"]["design_brief"]["properties"][
            "style_profile"
        ]["properties"]
        for key in ("lighting", "colors", "textures", "mood", "clutter_level"):
            assert "description" in sp_props[key], f"style_profile.{key} missing description"

    def test_designer_brain_fields_in_brief_schema(self):
        """PR-6: New Designer Brain fields in brief properties."""
        tool = next(t for t in INTAKE_TOOLS if t["name"] == "draft_design_brief")
        props = tool["input_schema"]["properties"]["design_brief"]["properties"]
        assert "emotional_drivers" in props
        assert "usage_patterns" in props
        assert "renovation_willingness" in props
        assert "room_analysis_hypothesis" in props

    def test_designer_brain_fields_in_interview_partial_brief(self):
        """PR-6: New fields also in interview_client partial_brief_update."""
        tool = next(t for t in INTAKE_TOOLS if t["name"] == "interview_client")
        props = tool["input_schema"]["properties"]["partial_brief_update"]["properties"]
        assert "emotional_drivers" in props
        assert "usage_patterns" in props
        assert "renovation_willingness" in props
        assert "room_analysis_hypothesis" in props


# === Turn Counting Tests ===


class TestTurnCounting:
    def test_max_turns_defined(self):
        assert MAX_TURNS["quick"] == 4
        assert MAX_TURNS["full"] == 11
        assert MAX_TURNS["open"] == 16

    def test_turn_counter_from_history(self):
        """Turn number should be user message count + 1."""
        history = [
            ChatMessage(role="user", content="msg1"),
            ChatMessage(role="assistant", content="reply1"),
            ChatMessage(role="user", content="msg2"),
            ChatMessage(role="assistant", content="reply2"),
        ]
        # 2 user messages in history + 1 new = turn 3
        turn = len([m for m in history if m.role == "user"]) + 1
        assert turn == 3


# === Input Validation Tests ===


class TestInputValidation:
    def test_empty_message_raises(self):
        """Empty user message should raise ApplicationError."""
        import asyncio

        import pytest
        from temporalio.exceptions import ApplicationError

        from app.activities.intake import _run_intake_core

        input_data = IntakeChatInput(
            mode="quick",
            project_context={},
            conversation_history=[],
            user_message="   ",
        )
        with pytest.raises(ApplicationError, match="empty"):
            asyncio.run(_run_intake_core(input_data))

    def test_nonempty_message_passes_validation(self):
        """Non-empty message should pass validation (may fail later on API key)."""
        import asyncio

        from temporalio.exceptions import ApplicationError

        from app.activities.intake import _run_intake_core

        input_data = IntakeChatInput(
            mode="quick",
            project_context={},
            conversation_history=[],
            user_message="Hello",
        )
        try:
            asyncio.run(_run_intake_core(input_data))
        except ApplicationError as e:
            # Should fail on API key, not on validation
            assert "API_KEY" in str(e)


# === Mode Instructions Tests ===


class TestModeInstructions:
    def test_all_modes_have_instructions(self):
        for mode in ("quick", "full", "open"):
            assert mode in MODE_INSTRUCTIONS

    def test_quick_mode_mentions_3_turns(self):
        assert "~3 turns" in MODE_INSTRUCTIONS["quick"]

    def test_full_mode_mentions_diagnose(self):
        assert "DIAGNOSE" in MODE_INSTRUCTIONS["full"]

    def test_open_mode_mentions_open_prompt(self):
        assert "Tell us about this room" in MODE_INSTRUCTIONS["open"]

    def test_all_modes_have_turn_budget_placeholder(self):
        for mode in ("quick", "full", "open"):
            assert "{remaining_turns}" in MODE_INSTRUCTIONS[mode]

    def test_all_modes_have_skill_selection(self):
        for mode in ("quick", "full", "open"):
            assert "interview_client" in MODE_INSTRUCTIONS[mode]
            assert "draft_design_brief" in MODE_INSTRUCTIONS[mode]


# === Eval Harness Unit Tests ===


class TestEvalHarness:
    """Unit tests for the eval harness (no API calls)."""

    def test_score_tag_excellent(self):
        from app.activities.intake_eval import score_tag

        assert score_tag(85) == "PASS:EXCELLENT"
        assert score_tag(100) == "PASS:EXCELLENT"

    def test_score_tag_pass(self):
        from app.activities.intake_eval import score_tag

        assert score_tag(70) == "PASS"
        assert score_tag(84) == "PASS"

    def test_score_tag_fail_weak(self):
        from app.activities.intake_eval import score_tag

        assert score_tag(50) == "FAIL:WEAK"
        assert score_tag(69) == "FAIL:WEAK"

    def test_score_tag_fail_poor(self):
        from app.activities.intake_eval import score_tag

        assert score_tag(0) == "FAIL:POOR"
        assert score_tag(49) == "FAIL:POOR"

    def test_format_transcript(self):
        from app.activities.intake_eval import format_transcript

        convo = [
            {"role": "user", "content": "It's a living room"},
            {"role": "assistant", "content": "Great! Tell me more."},
        ]
        result = format_transcript(convo)
        assert "USER: It's a living room" in result
        assert "ASSISTANT: Great! Tell me more." in result

    def test_format_transcript_empty(self):
        from app.activities.intake_eval import format_transcript

        assert format_transcript([]) == ""

    def test_rubric_prompt_has_all_criteria(self):
        from app.activities.intake_eval import RUBRIC_PROMPT

        assert "Style Coherence" in RUBRIC_PROMPT
        assert "Color Strategy" in RUBRIC_PROMPT
        assert "Lighting Design" in RUBRIC_PROMPT
        assert "Material" in RUBRIC_PROMPT
        assert "Design Intelligence" in RUBRIC_PROMPT
        assert "Diagnostic Depth" in RUBRIC_PROMPT
        assert "Actionability" in RUBRIC_PROMPT
        assert "Completeness" in RUBRIC_PROMPT
        assert "User Fidelity" in RUBRIC_PROMPT

    def test_rubric_prompt_has_format_placeholders(self):
        from app.activities.intake_eval import RUBRIC_PROMPT

        assert "{brief_json}" in RUBRIC_PROMPT
        assert "{transcript}" in RUBRIC_PROMPT


class TestEvaluateBrief:
    """Tests for evaluate_brief() with mocked Anthropic client."""

    _VALID_SCORES = (
        '{"style_coherence": 8, "color_strategy": 12, "lighting_design": 10,'
        ' "material_texture": 11, "design_intelligence": 7, "diagnostic_depth": 3,'
        ' "actionability": 12, "completeness": 8, "user_fidelity": 4,'
        ' "total": 75, "tag": "PASS", "notes": "Good brief."}'
    )

    def _brief(self):
        from app.models.contracts import DesignBrief

        return DesignBrief(
            room_type="living_room",
            occupants="2 adults, one cat",
        )

    def _conversation(self):
        return [
            {"role": "assistant", "content": "What type of room?"},
            {"role": "user", "content": "Living room"},
        ]

    def _mock_response(self, text: str) -> MagicMock:
        block = MagicMock()
        block.text = text
        resp = MagicMock()
        resp.content = [block]
        return resp

    def test_returns_parsed_scores(self, monkeypatch):
        """evaluate_brief calls Claude and returns parsed JSON scores."""
        from app.activities.intake_eval import evaluate_brief

        monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
        mock_client = MagicMock()
        mock_client.messages.create.return_value = self._mock_response(self._VALID_SCORES)

        with patch("app.activities.intake_eval.anthropic.Anthropic", return_value=mock_client):
            result = evaluate_brief(self._brief(), self._conversation())

        assert result["total"] == 75
        assert result["tag"] == "PASS"
        assert result["style_coherence"] == 8
        mock_client.messages.create.assert_called_once()

    def test_missing_api_key_raises(self, monkeypatch):
        """evaluate_brief raises RuntimeError when ANTHROPIC_API_KEY not set."""
        import pytest

        from app.activities.intake_eval import evaluate_brief

        monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)

        with pytest.raises(RuntimeError, match="ANTHROPIC_API_KEY"):
            evaluate_brief(self._brief(), self._conversation())

    def test_invalid_json_response_raises(self, monkeypatch):
        """evaluate_brief raises ValueError when Claude returns non-JSON."""
        import pytest

        from app.activities.intake_eval import evaluate_brief

        monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
        mock_client = MagicMock()
        mock_client.messages.create.return_value = self._mock_response(
            "I can't evaluate this brief."
        )

        with (
            patch("app.activities.intake_eval.anthropic.Anthropic", return_value=mock_client),
            pytest.raises(ValueError, match="Could not extract JSON"),
        ):
            evaluate_brief(self._brief(), self._conversation())

    def test_code_fenced_json_parsed(self, monkeypatch):
        """evaluate_brief handles code-fenced JSON response."""
        from app.activities.intake_eval import evaluate_brief

        monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
        fenced = f"```json\n{self._VALID_SCORES}\n```"
        mock_client = MagicMock()
        mock_client.messages.create.return_value = self._mock_response(fenced)

        with patch("app.activities.intake_eval.anthropic.Anthropic", return_value=mock_client):
            result = evaluate_brief(self._brief(), self._conversation())

        assert result["total"] == 75

    def test_prompt_includes_brief_and_transcript(self, monkeypatch):
        """evaluate_brief passes brief JSON and transcript to Claude."""
        from app.activities.intake_eval import evaluate_brief

        monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
        mock_client = MagicMock()
        mock_client.messages.create.return_value = self._mock_response(self._VALID_SCORES)

        with patch("app.activities.intake_eval.anthropic.Anthropic", return_value=mock_client):
            evaluate_brief(self._brief(), self._conversation())

        call_args = mock_client.messages.create.call_args
        prompt_text = call_args.kwargs["messages"][0]["content"]
        assert "2 adults, one cat" in prompt_text  # brief content
        assert "Living room" in prompt_text  # transcript content


class TestEvaluateConversationQuality:
    """Tests for evaluate_conversation_quality() with mocked Anthropic client."""

    _VALID_SCORES = (
        '{"probing_quality": 4, "acknowledgment": 3, "adaptation": 4,'
        ' "translation_visibility": 2, "conversational_flow": 4,'
        ' "total": 17, "notes": "Good conversation flow."}'
    )

    def _conversation(self):
        return [
            {"role": "assistant", "content": "Tell me about the room."},
            {"role": "user", "content": "A cozy living room."},
            {"role": "assistant", "content": "Sounds warm!"},
        ]

    def _mock_response(self, text: str) -> MagicMock:
        block = MagicMock()
        block.text = text
        resp = MagicMock()
        resp.content = [block]
        return resp

    def test_returns_parsed_scores(self, monkeypatch):
        """evaluate_conversation_quality calls Claude and returns parsed scores."""
        from app.activities.intake_eval import evaluate_conversation_quality

        monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
        mock_client = MagicMock()
        mock_client.messages.create.return_value = self._mock_response(self._VALID_SCORES)

        with patch("app.activities.intake_eval.anthropic.Anthropic", return_value=mock_client):
            result = evaluate_conversation_quality(self._conversation())

        assert result["total"] == 17
        assert result["probing_quality"] == 4
        mock_client.messages.create.assert_called_once()

    def test_missing_api_key_raises(self, monkeypatch):
        """evaluate_conversation_quality raises when no API key set."""
        import pytest

        from app.activities.intake_eval import evaluate_conversation_quality

        monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)

        with pytest.raises(RuntimeError, match="ANTHROPIC_API_KEY"):
            evaluate_conversation_quality(self._conversation())

    def test_invalid_json_response_raises(self, monkeypatch):
        """evaluate_conversation_quality raises on non-JSON response."""
        import pytest

        from app.activities.intake_eval import evaluate_conversation_quality

        monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
        mock_client = MagicMock()
        mock_client.messages.create.return_value = self._mock_response("Not JSON")

        with (
            patch("app.activities.intake_eval.anthropic.Anthropic", return_value=mock_client),
            pytest.raises(ValueError, match="Could not extract JSON"),
        ):
            evaluate_conversation_quality(self._conversation())

    def test_prompt_includes_transcript(self, monkeypatch):
        """evaluate_conversation_quality passes transcript to Claude."""
        from app.activities.intake_eval import evaluate_conversation_quality

        monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
        mock_client = MagicMock()
        mock_client.messages.create.return_value = self._mock_response(self._VALID_SCORES)

        with patch("app.activities.intake_eval.anthropic.Anthropic", return_value=mock_client):
            evaluate_conversation_quality(self._conversation())

        call_args = mock_client.messages.create.call_args
        prompt_text = call_args.kwargs["messages"][0]["content"]
        assert "cozy living room" in prompt_text


# === Mocked _run_intake_core Tests ===


def _mock_tool_block(name: str, input_data: dict) -> MagicMock:
    """Create a mock content block that looks like a tool_use block.

    Uses spec to prevent auto-creation of .text attribute,
    which would confuse hasattr() checks in the fallback path.
    """
    block = MagicMock(spec=["type", "name", "input"])
    block.type = "tool_use"
    block.name = name
    block.input = input_data
    return block


class TestRunIntakeCoreMocked:
    """Test _run_intake_core with mocked AsyncAnthropic client."""

    def _make_input(self, message: str = "It's a living room") -> IntakeChatInput:
        return IntakeChatInput(
            mode="quick",
            project_context={"room_photos": []},
            conversation_history=[],
            user_message=message,
        )

    def _mock_skill_response(
        self,
        skill_name: str,
        skill_data: dict,
    ) -> MagicMock:
        """Build a mock Claude response with a single skill tool_use block."""
        blocks = [_mock_tool_block(skill_name, skill_data)]
        resp = MagicMock()
        resp.content = blocks
        resp.usage = MagicMock(input_tokens=500, output_tokens=200)
        return resp

    @patch.dict("os.environ", {"ANTHROPIC_API_KEY": "test-key"})
    @patch("app.activities.intake.anthropic.AsyncAnthropic")
    def test_interview_skill_returns_output(self, mock_client_cls):
        """Interview skill produces IntakeChatOutput with partial brief."""
        mock_resp = self._mock_skill_response(
            "interview_client",
            {
                "message": "I can see this is a living room!",
                "options": [
                    {"number": 1, "label": "Warm & cozy", "value": "warm palette"},
                    {"number": 2, "label": "Cool & modern", "value": "cool palette"},
                ],
                "is_open_ended": False,
                "partial_brief_update": {
                    "room_type": "living room",
                    "style_profile": {"mood": "cozy retreat", "colors": ["warm ivory"]},
                    "domains_covered": ["room_purpose", "style"],
                },
                "domains_covered": ["room_purpose", "style"],
            },
        )
        mock_instance = MagicMock()
        mock_instance.messages = MagicMock()
        mock_instance.messages.create = AsyncMock(return_value=mock_resp)
        mock_client_cls.return_value = mock_instance

        result = asyncio.run(_run_intake_core(self._make_input()))

        assert isinstance(result, IntakeChatOutput)
        assert "living room" in result.agent_message
        assert result.options is not None
        assert len(result.options) == 2
        assert result.partial_brief is not None
        assert result.partial_brief.room_type == "living room"
        assert result.is_summary is False
        assert "Turn 1" in result.progress

    @patch.dict("os.environ", {"ANTHROPIC_API_KEY": "test-key"})
    @patch("app.activities.intake.anthropic.AsyncAnthropic")
    def test_draft_skill_returns_summary(self, mock_client_cls):
        """Draft skill produces IntakeChatOutput with is_summary=True and complete brief."""
        mock_resp = self._mock_skill_response(
            "draft_design_brief",
            {
                "message": "Here's your design summary!",
                "design_brief": {
                    "room_type": "kitchen",
                    "style_profile": {"mood": "bright and functional"},
                    "domains_covered": list(range(10)),
                },
            },
        )
        mock_instance = MagicMock()
        mock_instance.messages = MagicMock()
        mock_instance.messages.create = AsyncMock(return_value=mock_resp)
        mock_client_cls.return_value = mock_instance

        result = asyncio.run(_run_intake_core(self._make_input()))

        assert result.is_summary is True
        assert result.partial_brief is not None
        assert result.partial_brief.room_type == "kitchen"

    @patch.dict("os.environ", {"ANTHROPIC_API_KEY": "test-key"})
    @patch("app.activities.intake.anthropic.AsyncAnthropic")
    def test_interview_without_partial_brief(self, mock_client_cls):
        """Interview skill without partial_brief_update still works."""
        mock_resp = self._mock_skill_response(
            "interview_client",
            {"message": "Tell me about your room."},
        )
        mock_instance = MagicMock()
        mock_instance.messages = MagicMock()
        mock_instance.messages.create = AsyncMock(return_value=mock_resp)
        mock_client_cls.return_value = mock_instance

        result = asyncio.run(_run_intake_core(self._make_input()))

        assert result.agent_message == "Tell me about your room."
        assert result.partial_brief is None
        assert result.is_summary is False

    @patch.dict("os.environ", {"ANTHROPIC_API_KEY": "test-key"})
    @patch("app.activities.intake.anthropic.AsyncAnthropic")
    def test_no_skill_called_uses_fallback(self, mock_client_cls):
        """If model calls no skill tool, fallback message is used."""
        text_block = MagicMock()
        text_block.type = "text"
        text_block.text = "I need to understand your space."

        mock_resp = MagicMock()
        mock_resp.content = [text_block]
        mock_resp.usage = MagicMock(input_tokens=200, output_tokens=50)

        mock_instance = MagicMock()
        mock_instance.messages = MagicMock()
        mock_instance.messages.create = AsyncMock(return_value=mock_resp)
        mock_client_cls.return_value = mock_instance

        result = asyncio.run(_run_intake_core(self._make_input()))

        assert result.agent_message  # Should have some message
        assert result.partial_brief is None
        assert result.is_summary is False

    @patch.dict("os.environ", {"ANTHROPIC_API_KEY": "test-key"})
    @patch("app.activities.intake.anthropic.AsyncAnthropic")
    def test_turn_counter_from_history(self, mock_client_cls):
        """Turn number should be based on user messages in history + 1."""
        mock_resp = self._mock_skill_response(
            "interview_client",
            {"message": "Tell me more."},
        )
        mock_instance = MagicMock()
        mock_instance.messages = MagicMock()
        mock_instance.messages.create = AsyncMock(return_value=mock_resp)
        mock_client_cls.return_value = mock_instance

        input_data = IntakeChatInput(
            mode="quick",
            project_context={"room_photos": []},
            conversation_history=[
                ChatMessage(role="user", content="It's a living room"),
                ChatMessage(role="assistant", content="Great!"),
                ChatMessage(role="user", content="I like warm colors"),
                ChatMessage(role="assistant", content="Nice!"),
            ],
            user_message="Also I have a cat",
        )
        result = asyncio.run(_run_intake_core(input_data))

        # Turn 3 (2 user messages in history + this one)
        assert "Turn 3" in result.progress

    @patch.dict("os.environ", {"ANTHROPIC_API_KEY": "test-key"})
    @patch("app.activities.intake.anthropic.AsyncAnthropic")
    def test_forces_summary_on_final_turn(self, mock_client_cls):
        """Server-side enforcement: is_summary forced True when turn >= max_turns."""
        # Model picks interview on the final turn (shouldn't, but safety net catches it)
        mock_resp = self._mock_skill_response(
            "interview_client",
            {
                "message": "What about lighting?",
                "domains_covered": ["style"],
            },
        )
        mock_instance = MagicMock()
        mock_instance.messages = MagicMock()
        mock_instance.messages.create = AsyncMock(return_value=mock_resp)
        mock_client_cls.return_value = mock_instance

        # Quick mode: MAX_TURNS=4. With 3 user messages in history + 1 new = turn 4
        input_data = IntakeChatInput(
            mode="quick",
            project_context={"room_photos": []},
            conversation_history=[
                ChatMessage(role="user", content="Living room"),
                ChatMessage(role="assistant", content="Great!"),
                ChatMessage(role="user", content="Cozy"),
                ChatMessage(role="assistant", content="Warm tones!"),
                ChatMessage(role="user", content="Yes warm"),
                ChatMessage(role="assistant", content="Textures?"),
            ],
            user_message="Boucle and velvet",
        )
        result = asyncio.run(_run_intake_core(input_data))

        # Model used interview, but server forces is_summary=True on turn 4
        assert result.is_summary is True

    @patch.dict("os.environ", {"ANTHROPIC_API_KEY": "test-key"})
    @patch("app.activities.intake.anthropic.AsyncAnthropic")
    def test_no_forced_summary_before_max_turns(self, mock_client_cls):
        """Summary should NOT be forced when turns remain."""
        mock_resp = self._mock_skill_response(
            "interview_client",
            {"message": "Tell me about colors."},
        )
        mock_instance = MagicMock()
        mock_instance.messages = MagicMock()
        mock_instance.messages.create = AsyncMock(return_value=mock_resp)
        mock_client_cls.return_value = mock_instance

        # Turn 1 of quick mode (MAX_TURNS=4) — should NOT force summary
        result = asyncio.run(_run_intake_core(self._make_input()))

        assert result.is_summary is False

    @patch.dict("os.environ", {"ANTHROPIC_API_KEY": "test-key"})
    @patch("app.activities.intake.anthropic.AsyncAnthropic")
    def test_forced_summary_uses_previous_brief_fallback(self, mock_client_cls):
        """When forced summary but no brief from this turn, use previous_brief."""
        # Model calls interview (no partial_brief_update) on the final turn
        mock_resp = self._mock_skill_response(
            "interview_client",
            {"message": "What about lighting?"},
        )
        mock_instance = MagicMock()
        mock_instance.messages = MagicMock()
        mock_instance.messages.create = AsyncMock(return_value=mock_resp)
        mock_client_cls.return_value = mock_instance

        # Turn 4 of quick mode, with previous_brief accumulated from earlier turns
        input_data = IntakeChatInput(
            mode="quick",
            project_context={
                "room_photos": [],
                "previous_brief": {
                    "room_type": "bedroom",
                    "style_profile": {"mood": "calm retreat"},
                    "pain_points": ["too dark"],
                },
            },
            conversation_history=[
                ChatMessage(role="user", content="Bedroom"),
                ChatMessage(role="assistant", content="Nice!"),
                ChatMessage(role="user", content="Calm"),
                ChatMessage(role="assistant", content="Great!"),
                ChatMessage(role="user", content="Very dark"),
                ChatMessage(role="assistant", content="I see!"),
            ],
            user_message="Yes warm lighting",
        )
        result = asyncio.run(_run_intake_core(input_data))

        # Forced summary AND previous_brief used as fallback
        assert result.is_summary is True
        assert result.partial_brief is not None
        assert result.partial_brief.room_type == "bedroom"


# === Error Handler Tests ===


def _make_httpx_response(status_code: int, body: str = "error") -> httpx.Response:
    """Build a minimal httpx.Response for constructing anthropic errors."""
    return httpx.Response(
        status_code=status_code,
        text=body,
        request=httpx.Request("POST", "https://api.anthropic.com/v1/messages"),
    )


class TestIntakeErrorHandling:
    """Test that API errors are correctly classified as retryable/non-retryable."""

    def _make_input(self) -> IntakeChatInput:
        return IntakeChatInput(
            mode="quick",
            project_context={"room_photos": []},
            conversation_history=[],
            user_message="Hello",
        )

    @patch.dict("os.environ", {"ANTHROPIC_API_KEY": "test-key"})
    @patch("app.activities.intake.anthropic.AsyncAnthropic")
    def test_rate_limit_is_retryable(self, mock_client_cls):
        """RateLimitError should raise retryable ApplicationError."""
        import anthropic
        import pytest
        from temporalio.exceptions import ApplicationError

        error = anthropic.RateLimitError(
            message="Rate limited",
            response=_make_httpx_response(429),
            body=None,
        )
        mock_instance = MagicMock()
        mock_instance.messages = MagicMock()
        mock_instance.messages.create = AsyncMock(side_effect=error)
        mock_client_cls.return_value = mock_instance

        with pytest.raises(ApplicationError) as exc_info:
            asyncio.run(_run_intake_core(self._make_input()))
        assert exc_info.value.non_retryable is False

    @patch.dict("os.environ", {"ANTHROPIC_API_KEY": "test-key"})
    @patch("app.activities.intake.anthropic.AsyncAnthropic")
    def test_content_policy_is_non_retryable(self, mock_client_cls):
        """Content policy violation (400) should raise non-retryable ApplicationError."""
        import anthropic
        import pytest
        from temporalio.exceptions import ApplicationError

        error = anthropic.BadRequestError(
            message="Content policy violation",
            response=_make_httpx_response(400, "content policy"),
            body=None,
        )
        mock_instance = MagicMock()
        mock_instance.messages = MagicMock()
        mock_instance.messages.create = AsyncMock(side_effect=error)
        mock_client_cls.return_value = mock_instance

        with pytest.raises(ApplicationError) as exc_info:
            asyncio.run(_run_intake_core(self._make_input()))
        assert exc_info.value.non_retryable is True

    @patch.dict("os.environ", {"ANTHROPIC_API_KEY": "test-key"})
    @patch("app.activities.intake.anthropic.AsyncAnthropic")
    def test_server_error_is_retryable(self, mock_client_cls):
        """Server errors (500) should raise retryable ApplicationError."""
        import anthropic
        import pytest
        from temporalio.exceptions import ApplicationError

        error = anthropic.InternalServerError(
            message="Server error",
            response=_make_httpx_response(500),
            body=None,
        )
        mock_instance = MagicMock()
        mock_instance.messages = MagicMock()
        mock_instance.messages.create = AsyncMock(side_effect=error)
        mock_client_cls.return_value = mock_instance

        with pytest.raises(ApplicationError) as exc_info:
            asyncio.run(_run_intake_core(self._make_input()))
        assert exc_info.value.non_retryable is False

    @patch.dict("os.environ", {"ANTHROPIC_API_KEY": "test-key"})
    @patch("app.activities.intake.anthropic.AsyncAnthropic")
    def test_auth_error_is_non_retryable(self, mock_client_cls):
        """401 Unauthorized should raise non-retryable ApplicationError."""
        import anthropic
        import pytest
        from temporalio.exceptions import ApplicationError

        error = anthropic.AuthenticationError(
            message="Invalid API key",
            response=_make_httpx_response(401),
            body=None,
        )
        mock_instance = MagicMock()
        mock_instance.messages = MagicMock()
        mock_instance.messages.create = AsyncMock(side_effect=error)
        mock_client_cls.return_value = mock_instance

        with pytest.raises(ApplicationError) as exc_info:
            asyncio.run(_run_intake_core(self._make_input()))
        assert exc_info.value.non_retryable is True


class TestRunIntakeChatWrapper:
    """Test the Temporal activity wrapper delegates to _run_intake_core."""

    @patch("app.activities.intake._run_intake_core")
    def test_delegates_to_core(self, mock_core):
        """run_intake_chat should directly call _run_intake_core with the same input."""
        from app.activities.intake import run_intake_chat

        mock_output = MagicMock()
        mock_core.return_value = mock_output

        input_data = IntakeChatInput(
            mode="full",
            project_context={"project_id": "test-proj"},
            conversation_history=[],
            user_message="hello",
        )

        result = asyncio.run(run_intake_chat(input_data))

        mock_core.assert_called_once_with(input_data)
        assert result is mock_output
