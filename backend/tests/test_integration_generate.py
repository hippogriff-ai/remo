"""Integration tests for generate_designs — real Gemini API calls.

Run with: GOOGLE_AI_API_KEY=... .venv/bin/python -m pytest tests/test_integration_generate.py -x -q

These tests require a valid Google AI API key and make real API calls.
They are marked with @pytest.mark.integration so they can be skipped in CI.
"""

import io
import os

import pytest
from PIL import Image

pytestmark = pytest.mark.integration

GEMINI_KEY = os.environ.get("GOOGLE_AI_API_KEY", "")
skip_no_key = pytest.mark.skipif(not GEMINI_KEY, reason="GOOGLE_AI_API_KEY not set")


def _create_test_room(w: int = 1024, h: int = 1024) -> Image.Image:
    """Create a synthetic room image for testing."""
    from PIL import ImageDraw

    img = Image.new("RGB", (w, h), "#F5F0E8")
    draw = ImageDraw.Draw(img)
    draw.polygon([(0, 600), (w, 600), (w, h), (0, h)], fill="#C4A882")
    draw.rectangle([(0, 0), (w, 600)], fill="#E8E0D0")
    draw.rectangle([(400, 150), (624, 400)], fill="#87CEEB", outline="#8B7355", width=3)
    draw.rounded_rectangle([(250, 480), (600, 580)], radius=10, fill="#4A6741")
    draw.rectangle([(350, 600), (550, 660)], fill="#6B4226")
    return img


@skip_no_key
class TestInitialGeneration:
    """Test initial image generation with real Gemini API."""

    def test_generate_with_minimal_prompt(self):
        """Gemini should generate an image from a room photo + simple prompt."""
        from google import genai
        from google.genai import types

        client = genai.Client(api_key=GEMINI_KEY)
        room = _create_test_room()

        response = client.models.generate_content(
            model="gemini-3-pro-image-preview",
            contents=[
                room,
                "Redesign this room in a modern Scandinavian style. "
                "Preserve the room architecture.",
            ],
            config=types.GenerateContentConfig(response_modalities=["TEXT", "IMAGE"]),
        )

        # Should have at least one candidate
        assert response.candidates, "No candidates in response"
        # Should contain an image
        found_image = False
        for part in response.candidates[0].content.parts:
            img = part.as_image()
            if img is not None:
                found_image = True
                assert img.image_bytes, "Image bytes are empty"
                # Verify it's a valid image
                pil_img = Image.open(io.BytesIO(img.image_bytes))
                assert pil_img.size[0] > 0
                assert pil_img.size[1] > 0
        assert found_image, "No image found in response parts"

    def test_generate_with_detailed_brief(self):
        """Gemini should handle a detailed design brief."""
        from google import genai
        from google.genai import types

        client = genai.Client(api_key=GEMINI_KEY)
        room = _create_test_room()

        brief = """Redesign this living room:
        - Style: Mid-century modern
        - Colors: Warm wood tones, mustard yellow accents, olive green
        - Furniture: Low-profile sofa, walnut coffee table, Eames lounge chair
        - Lighting: Warm, layered — floor lamp + table lamp
        - Mood: Sophisticated but inviting
        Preserve the exact camera angle and room geometry."""

        response = client.models.generate_content(
            model="gemini-3-pro-image-preview",
            contents=[room, brief],
            config=types.GenerateContentConfig(response_modalities=["TEXT", "IMAGE"]),
        )

        found_image = False
        for part in response.candidates[0].content.parts:
            if part.as_image() is not None:
                found_image = True
        assert found_image, "No image generated from detailed brief"

    def test_output_is_photorealistic_quality(self):
        """Generated image should be at least 512x512 (quality baseline)."""
        from google import genai
        from google.genai import types

        client = genai.Client(api_key=GEMINI_KEY)
        room = _create_test_room()

        response = client.models.generate_content(
            model="gemini-3-pro-image-preview",
            contents=[room, "Redesign this room in a warm bohemian style."],
            config=types.GenerateContentConfig(response_modalities=["TEXT", "IMAGE"]),
        )

        for part in response.candidates[0].content.parts:
            img = part.as_image()
            if img is not None:
                pil_img = Image.open(io.BytesIO(img.image_bytes))
                assert pil_img.size[0] >= 512, f"Width too small: {pil_img.size[0]}"
                assert pil_img.size[1] >= 512, f"Height too small: {pil_img.size[1]}"
                return
        pytest.fail("No image in response")


@skip_no_key
class TestAnnotationEditing:
    """Test annotation-based editing with real Gemini API."""

    def test_annotation_edit_targets_marked_area(self):
        """Editing with annotations should produce an image (basic check)."""
        from google import genai
        from google.genai import types

        from app.models.contracts import AnnotationRegion
        from app.utils.image import draw_annotations

        client = genai.Client(api_key=GEMINI_KEY)
        room = _create_test_room()

        # First generate a design
        gen_response = client.models.generate_content(
            model="gemini-3-pro-image-preview",
            contents=[room, "Redesign this room in a Scandinavian style."],
            config=types.GenerateContentConfig(response_modalities=["TEXT", "IMAGE"]),
        )
        base_img = None
        for part in gen_response.candidates[0].content.parts:
            genai_img = part.as_image()
            if genai_img is not None:
                base_img = Image.open(io.BytesIO(genai_img.image_bytes))
                break
        assert base_img is not None, "Failed to generate base image"

        # Draw annotations
        region = AnnotationRegion(
            region_id=1,
            center_x=0.4,
            center_y=0.5,
            radius=0.15,
            instruction="Replace the sofa with a mid-century modern leather sofa",
        )
        annotated = draw_annotations(base_img, [region])

        # Send annotated image with edit prompt
        chat = client.chats.create(
            model="gemini-3-pro-image-preview",
            config=types.GenerateContentConfig(response_modalities=["TEXT", "IMAGE"]),
        )
        edit_response = chat.send_message(
            [
                annotated,
                "This image has a red circle (1) marking the sofa area. "
                "Replace the sofa with a mid-century modern leather sofa. "
                "Keep everything else the same. "
                "CRITICAL: Return a clean image WITHOUT any circles or annotations.",
            ]
        )

        found_image = False
        for part in edit_response.candidates[0].content.parts:
            if part.as_image() is not None:
                found_image = True
        assert found_image, "No image returned from annotation edit"

    def test_output_is_clean_no_artifacts(self):
        """Edited image should not contain annotation artifacts (best effort check)."""
        from google import genai
        from google.genai import types

        from app.models.contracts import AnnotationRegion
        from app.utils.image import draw_annotations

        client = genai.Client(api_key=GEMINI_KEY)
        room = _create_test_room()

        # Generate base
        gen_resp = client.models.generate_content(
            model="gemini-3-pro-image-preview",
            contents=[room, "Redesign this room minimally."],
            config=types.GenerateContentConfig(response_modalities=["TEXT", "IMAGE"]),
        )
        base_img = None
        for part in gen_resp.candidates[0].content.parts:
            genai_img = part.as_image()
            if genai_img is not None:
                base_img = Image.open(io.BytesIO(genai_img.image_bytes))
                break
        assert base_img is not None

        region = AnnotationRegion(
            region_id=1,
            center_x=0.5,
            center_y=0.5,
            radius=0.1,
            instruction="Change this area to a warmer color palette",
        )
        annotated = draw_annotations(base_img, [region])

        chat = client.chats.create(
            model="gemini-3-pro-image-preview",
            config=types.GenerateContentConfig(response_modalities=["TEXT", "IMAGE"]),
        )
        edit_resp = chat.send_message(
            [
                annotated,
                "Edit the area marked with the red circle (1) to use warmer colors. "
                "CRITICAL: Return ONLY a clean photograph. Do NOT include any circles, "
                "numbers, badges, or annotation markers in the output image.",
            ]
        )

        for part in edit_resp.candidates[0].content.parts:
            genai_img = part.as_image()
            if genai_img is not None:
                result = Image.open(io.BytesIO(genai_img.image_bytes))
                # Basic size check — image should be reasonable
                assert result.size[0] >= 256
                assert result.size[1] >= 256
                return
        pytest.fail("No image returned")


@skip_no_key
class TestChatRoundTrip:
    """Test chat history serialization and continuation."""

    def test_serialize_deserialize_continue(self):
        """Full round-trip: generate → serialize → deserialize → edit."""
        from google import genai
        from google.genai import types

        from app.utils.gemini_chat import (
            _contents_to_serializable,
            deserialize_to_contents,
        )

        client = genai.Client(api_key=GEMINI_KEY)
        room = _create_test_room()

        # Turn 1: Generate
        chat = client.chats.create(
            model="gemini-3-pro-image-preview",
            config=types.GenerateContentConfig(response_modalities=["TEXT", "IMAGE"]),
        )
        resp1 = chat.send_message(
            [
                room,
                "Redesign this room in a Scandinavian style. Generate an image.",
            ]
        )

        found_image = False
        for part in resp1.candidates[0].content.parts:
            if part.as_image() is not None:
                found_image = True
        assert found_image, "No image in turn 1"

        # Serialize
        history = chat.get_history()
        serialized = _contents_to_serializable(history)
        assert len(serialized) >= 2  # at least user + model

        # Check thought signatures are captured
        model_turn = serialized[-1]
        assert model_turn["role"] == "model"

        # Deserialize
        restored = deserialize_to_contents(serialized)
        assert len(restored) == len(serialized)

        # Continue with restored history
        restored.append(
            types.Content(
                role="user",
                parts=[types.Part(text="Make the walls a light sage green. Generate an image.")],
            )
        )

        resp2 = client.models.generate_content(
            model="gemini-3-pro-image-preview",
            contents=restored,
            config=types.GenerateContentConfig(response_modalities=["TEXT", "IMAGE"]),
        )

        # Should not get a 400 error, and should get an image
        assert resp2.candidates, "No candidates after round-trip"
        found_image = False
        for part in resp2.candidates[0].content.parts:
            if part.as_image() is not None:
                found_image = True
        assert found_image, "No image in continuation after round-trip"
