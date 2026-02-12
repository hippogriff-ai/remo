"""Tests for Gemini chat session manager (backend/app/utils/gemini_chat.py).

No API keys needed — uses mock data for serialization/deserialization tests.
"""

import base64
import json
from unittest.mock import MagicMock

import pytest
from google.genai import types
from PIL import Image

from app.utils.gemini_chat import (
    CHAT_HISTORY_KEY_TEMPLATE,
    GEMINI_MODEL,
    _contents_to_serializable,
    _dict_to_part,
    _part_to_dict,
    deserialize_to_contents,
    extract_image,
    extract_text,
    response_to_content,
)


def _make_text_part(text: str, signature: str | None = None) -> types.Part:
    part = types.Part(text=text)
    if signature:
        part.thought_signature = signature
    return part


def _make_image_part(width: int = 10, height: int = 10, signature: str | None = None) -> types.Part:
    import io

    img = Image.new("RGB", (width, height), "red")
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    part = types.Part.from_bytes(data=buf.getvalue(), mime_type="image/png")
    if signature:
        part.thought_signature = signature
    return part


def _make_content(role: str, parts: list[types.Part]) -> types.Content:
    return types.Content(role=role, parts=parts)


class TestPartSerialization:
    """Tests for individual Part serialization/deserialization."""

    def test_text_part_roundtrip(self):
        original = _make_text_part("hello world")
        serialized = _part_to_dict(original)
        assert serialized == {"text": "hello world"}
        restored = _dict_to_part(serialized)
        assert restored.text == "hello world"

    def test_image_part_roundtrip(self):
        original = _make_image_part(10, 10)
        serialized = _part_to_dict(original)
        assert "inline_data" in serialized
        assert serialized["inline_data"]["mime_type"] == "image/png"
        # Verify base64 data is valid
        decoded = base64.b64decode(serialized["inline_data"]["data"])
        assert decoded[:8] == b"\x89PNG\r\n\x1a\n"

        restored = _dict_to_part(serialized)
        assert restored.inline_data is not None

    def test_thought_signature_preserved(self):
        original = _make_text_part("response", signature="sig_abc123")
        serialized = _part_to_dict(original)
        assert serialized["thought_signature"] == "sig_abc123"
        restored = _dict_to_part(serialized)
        assert restored.thought_signature == "sig_abc123"

    def test_bytes_thought_signature_roundtrip(self):
        """Thought signatures that are bytes should be base64-encoded for JSON."""
        part = _make_text_part("response")
        part.thought_signature = b"\x89binary\x00sig"
        serialized = _part_to_dict(part)
        assert serialized["thought_signature_encoding"] == "base64"
        # Verify it's valid base64 string
        assert isinstance(serialized["thought_signature"], str)
        restored = _dict_to_part(serialized)
        assert restored.thought_signature == b"\x89binary\x00sig"

    def test_image_with_thought_signature(self):
        original = _make_image_part(10, 10, signature="img_sig_456")
        serialized = _part_to_dict(original)
        assert "inline_data" in serialized
        assert serialized["thought_signature"] == "img_sig_456"
        restored = _dict_to_part(serialized)
        assert restored.thought_signature == "img_sig_456"

    def test_empty_dict_produces_empty_text(self):
        """Edge case: dict with no text or inline_data."""
        restored = _dict_to_part({})
        assert restored.text == ""


class TestHistorySerialization:
    """Tests for full conversation history serialization."""

    def test_single_turn_roundtrip(self):
        contents = [
            _make_content("user", [_make_text_part("hello")]),
            _make_content("model", [_make_text_part("hi there")]),
        ]
        serialized = _contents_to_serializable(contents)
        assert len(serialized) == 2
        assert serialized[0]["role"] == "user"
        assert serialized[1]["role"] == "model"

        restored = deserialize_to_contents(serialized)
        assert len(restored) == 2
        assert restored[0].role == "user"
        assert restored[0].parts[0].text == "hello"
        assert restored[1].role == "model"
        assert restored[1].parts[0].text == "hi there"

    def test_multi_turn_with_images(self):
        contents = [
            _make_content("user", [_make_image_part(), _make_text_part("what is this?")]),
            _make_content(
                "model",
                [
                    _make_text_part("It's a red image", signature="sig1"),
                    _make_image_part(20, 20, signature="sig2"),
                ],
            ),
            _make_content("user", [_make_text_part("make it blue")]),
        ]
        serialized = _contents_to_serializable(contents)
        assert len(serialized) == 3

        # Verify thought signatures in model turn
        model_parts = serialized[1]["parts"]
        assert model_parts[0]["thought_signature"] == "sig1"
        assert model_parts[1]["thought_signature"] == "sig2"

        # Roundtrip
        restored = deserialize_to_contents(serialized)
        assert len(restored) == 3
        assert restored[1].parts[0].thought_signature == "sig1"
        assert restored[1].parts[1].thought_signature == "sig2"

    def test_json_serializable(self):
        """Serialized history must be JSON-serializable."""
        contents = [
            _make_content("user", [_make_image_part(), _make_text_part("hello")]),
            _make_content("model", [_make_text_part("world", signature="sig")]),
        ]
        serialized = _contents_to_serializable(contents)
        json_str = json.dumps(serialized)
        parsed = json.loads(json_str)
        assert len(parsed) == 2

    def test_empty_history(self):
        serialized = _contents_to_serializable([])
        assert serialized == []
        restored = deserialize_to_contents(serialized)
        assert restored == []


class TestChatHistoryKeyTemplate:
    """Tests for the R2 key template."""

    def test_key_format(self):
        key = CHAT_HISTORY_KEY_TEMPLATE.format(project_id="abc-123")
        assert key == "projects/abc-123/gemini_chat_history.json"


class TestExtractImage:
    """Tests for extracting images from responses."""

    def test_extract_from_response_with_image(self):
        # Create a mock response with an image part
        import io

        img = Image.new("RGB", (10, 10), "blue")
        buf = io.BytesIO()
        img.save(buf, format="PNG")
        image_bytes = buf.getvalue()

        mock_genai_image = MagicMock()
        mock_genai_image.image_bytes = image_bytes

        mock_part = MagicMock()
        mock_part.as_image.return_value = mock_genai_image
        mock_part.text = None

        mock_candidate = MagicMock()
        mock_candidate.content.parts = [mock_part]

        mock_response = MagicMock()
        mock_response.candidates = [mock_candidate]

        result = extract_image(mock_response)
        assert result is not None
        assert result.size == (10, 10)

    def test_extract_from_empty_response(self):
        mock_response = MagicMock()
        mock_response.candidates = []
        assert extract_image(mock_response) is None

    def test_extract_from_text_only_response(self):
        mock_part = MagicMock()
        mock_part.as_image.return_value = None
        mock_part.text = "no image"

        mock_candidate = MagicMock()
        mock_candidate.content.parts = [mock_part]

        mock_response = MagicMock()
        mock_response.candidates = [mock_candidate]

        assert extract_image(mock_response) is None


class TestExtractText:
    """Tests for extracting text from responses."""

    def test_extract_text(self):
        mock_part = MagicMock()
        mock_part.text = "hello"
        mock_part.as_image.return_value = None

        mock_candidate = MagicMock()
        mock_candidate.content.parts = [mock_part]

        mock_response = MagicMock()
        mock_response.candidates = [mock_candidate]

        assert extract_text(mock_response) == "hello"

    def test_extract_multi_text(self):
        parts = []
        for text in ["hello", "world"]:
            p = MagicMock()
            p.text = text
            parts.append(p)

        mock_candidate = MagicMock()
        mock_candidate.content.parts = parts

        mock_response = MagicMock()
        mock_response.candidates = [mock_candidate]

        assert extract_text(mock_response) == "hello\nworld"

    def test_extract_from_empty(self):
        mock_response = MagicMock()
        mock_response.candidates = []
        assert extract_text(mock_response) == ""


class TestResponseToContent:
    """Tests for converting responses to Content objects."""

    def test_converts_response(self):
        mock_content = _make_content("model", [_make_text_part("hello")])
        mock_candidate = MagicMock()
        mock_candidate.content = mock_content

        mock_response = MagicMock()
        mock_response.candidates = [mock_candidate]

        result = response_to_content(mock_response)
        assert result is not None
        assert result.role == "model"

    def test_empty_response_returns_none(self):
        mock_response = MagicMock()
        mock_response.candidates = []
        assert response_to_content(mock_response) is None


class TestDeserializationValidation:
    """Tests for deserialization input validation."""

    def test_invalid_turn_missing_role(self):
        with pytest.raises(ValueError, match="requires 'role' and 'parts'"):
            deserialize_to_contents([{"parts": [{"text": "hello"}]}])

    def test_invalid_turn_missing_parts(self):
        with pytest.raises(ValueError, match="requires 'role' and 'parts'"):
            deserialize_to_contents([{"role": "user"}])

    def test_invalid_turn_parts_not_list(self):
        with pytest.raises(ValueError, match="Expected 'parts' to be a list"):
            deserialize_to_contents([{"role": "user", "parts": "not a list"}])

    def test_invalid_turn_not_dict(self):
        with pytest.raises(ValueError, match="Expected turn to be a dict"):
            deserialize_to_contents(["not a dict"])

    def test_invalid_inline_data_missing_data_field(self):
        with pytest.raises(ValueError, match="Invalid inline_data"):
            _dict_to_part({"inline_data": {"mime_type": "image/png"}})

    def test_invalid_inline_data_missing_mime_type(self):
        with pytest.raises(ValueError, match="Invalid inline_data"):
            _dict_to_part({"inline_data": {"data": "abc"}})

    def test_invalid_inline_data_not_dict(self):
        with pytest.raises(ValueError, match="Invalid inline_data"):
            _dict_to_part({"inline_data": "not a dict"})


class TestSerializeToR2:
    """Tests for R2 serialization with mocked R2 client."""

    def test_serialize_to_r2_uploads_json(self):
        from unittest.mock import patch

        from app.utils.gemini_chat import serialize_to_r2

        mock_chat = MagicMock()
        mock_chat.get_history.return_value = [
            _make_content("user", [_make_text_part("hello")]),
            _make_content("model", [_make_text_part("hi")]),
        ]

        with patch("app.utils.r2.upload_object") as mock_upload:
            key = serialize_to_r2(mock_chat, "proj-123")

        assert key == "projects/proj-123/gemini_chat_history.json"
        mock_upload.assert_called_once()
        call_args = mock_upload.call_args
        assert call_args[0][0] == key
        assert call_args[1]["content_type"] == "application/json"

    def test_serialize_contents_to_r2_uploads_json(self):
        from unittest.mock import patch

        from app.utils.gemini_chat import serialize_contents_to_r2

        contents = [
            _make_content("user", [_make_text_part("hello")]),
            _make_content("model", [_make_text_part("response")]),
        ]

        with patch("app.utils.r2.upload_object") as mock_upload:
            key = serialize_contents_to_r2(contents, "proj-456")

        assert key == "projects/proj-456/gemini_chat_history.json"
        mock_upload.assert_called_once()


class TestRestoreFromR2:
    """Tests for R2 deserialization with mocked R2 client."""

    def test_restore_success(self):
        import json
        from unittest.mock import patch

        from app.utils.gemini_chat import restore_from_r2

        serialized = [
            {"role": "user", "parts": [{"text": "hello"}]},
            {"role": "model", "parts": [{"text": "hi"}]},
        ]
        mock_body = MagicMock()
        mock_body.read.return_value = json.dumps(serialized).encode("utf-8")
        mock_r2 = MagicMock()
        mock_r2.get_object.return_value = {"Body": mock_body}

        with patch("app.utils.r2._get_client", return_value=mock_r2):
            contents = restore_from_r2("proj-123")

        assert len(contents) == 2
        assert contents[0].role == "user"
        assert contents[0].parts[0].text == "hello"

    def test_restore_not_found_raises_application_error(self):
        from unittest.mock import patch

        from botocore.exceptions import ClientError
        from temporalio.exceptions import ApplicationError

        from app.utils.gemini_chat import restore_from_r2

        mock_r2 = MagicMock()
        mock_r2.get_object.side_effect = ClientError(
            {"Error": {"Code": "NoSuchKey", "Message": "not found"}},
            "GetObject",
        )

        with (
            patch("app.utils.r2._get_client", return_value=mock_r2),
            pytest.raises(ApplicationError, match="not found"),
        ):
            restore_from_r2("proj-missing")

    def test_restore_corrupt_json_raises_application_error(self):
        from unittest.mock import patch

        from temporalio.exceptions import ApplicationError

        from app.utils.gemini_chat import restore_from_r2

        mock_body = MagicMock()
        mock_body.read.return_value = b"not valid json{{"
        mock_r2 = MagicMock()
        mock_r2.get_object.return_value = {"Body": mock_body}

        with (
            patch("app.utils.r2._get_client", return_value=mock_r2),
            pytest.raises(ApplicationError, match="corrupted"),
        ):
            restore_from_r2("proj-corrupt")


class TestCleanup:
    """Tests for cleanup with mocked R2."""

    def test_cleanup_deletes_key(self):
        from unittest.mock import patch

        from app.utils.gemini_chat import cleanup

        with patch("app.utils.r2.delete_object") as mock_delete:
            cleanup("proj-123")

        mock_delete.assert_called_once_with("projects/proj-123/gemini_chat_history.json")


class TestContinueChat:
    """Tests for continue_chat with mocked Gemini client."""

    def test_builds_contents_and_calls_generate(self):
        from app.utils.gemini_chat import continue_chat

        history = [
            _make_content("user", [_make_text_part("hello")]),
            _make_content("model", [_make_text_part("hi")]),
        ]

        mock_response = MagicMock()
        mock_client = MagicMock()
        mock_client.models.generate_content.return_value = mock_response

        result = continue_chat(history, ["make it blue"], mock_client)

        assert result == mock_response
        mock_client.models.generate_content.assert_called_once()
        call_args = mock_client.models.generate_content.call_args
        contents = call_args[1]["contents"]
        # Should have history (2 turns) + new user turn (1)
        assert len(contents) == 3
        assert contents[2].role == "user"

    def test_handles_image_in_message(self):
        from app.utils.gemini_chat import continue_chat

        history = [_make_content("user", [_make_text_part("hello")])]
        img = Image.new("RGB", (10, 10), "red")

        mock_client = MagicMock()
        mock_client.models.generate_content.return_value = MagicMock()

        continue_chat(history, [img, "edit this"], mock_client)

        call_args = mock_client.models.generate_content.call_args
        contents = call_args[1]["contents"]
        user_turn = contents[-1]
        assert len(user_turn.parts) == 2  # image + text


class TestContinueChatWithParts:
    """Test continue_chat with types.Part items."""

    def test_handles_types_part_directly(self):
        from app.utils.gemini_chat import continue_chat

        history = [_make_content("user", [_make_text_part("hello")])]
        raw_part = types.Part(text="already a part")

        mock_client = MagicMock()
        mock_client.models.generate_content.return_value = MagicMock()

        continue_chat(history, [raw_part], mock_client)

        call_args = mock_client.models.generate_content.call_args
        contents = call_args[1]["contents"]
        user_turn = contents[-1]
        assert len(user_turn.parts) == 1
        assert user_turn.parts[0].text == "already a part"


class TestImageCountAndPruning:
    """Tests for _count_image_parts and _prune_history_images."""

    def test_count_image_parts(self):
        from app.utils.gemini_chat import _count_image_parts

        history = [
            _make_content("user", [_make_image_part(), _make_text_part("hello")]),
            _make_content("model", [_make_image_part(), _make_text_part("response")]),
            _make_content("user", [_make_image_part()]),
        ]
        assert _count_image_parts(history) == 3

    def test_count_image_parts_empty(self):
        from app.utils.gemini_chat import _count_image_parts

        assert _count_image_parts([]) == 0

    def test_prune_no_op_when_under_limit(self):
        from app.utils.gemini_chat import _prune_history_images

        history = [
            _make_content("user", [_make_image_part(), _make_text_part("ctx")]),
            _make_content("model", [_make_text_part("ok")]),
        ]
        result = _prune_history_images(history, max_images=5)
        assert len(result) == len(history)

    def test_prune_strips_middle_turn_images(self):
        from app.utils.gemini_chat import _count_image_parts, _prune_history_images

        # 6 turns: 2 context + 2 middle + 2 recent, each with an image
        history = [
            _make_content("user", [_make_image_part(), _make_text_part("ctx1")]),
            _make_content("model", [_make_image_part(), _make_text_part("ctx2")]),
            _make_content("user", [_make_image_part(), _make_text_part("edit1")]),
            _make_content("model", [_make_image_part(), _make_text_part("result1")]),
            _make_content("user", [_make_image_part(), _make_text_part("edit2")]),
            _make_content("model", [_make_image_part(), _make_text_part("result2")]),
        ]
        assert _count_image_parts(history) == 6

        result = _prune_history_images(history, max_images=4)
        # Middle turns (indices 2,3) should have images stripped
        assert _count_image_parts(result) == 4
        # All 6 turns preserved
        assert len(result) == 6

    def test_prune_adds_placeholder_text(self):
        from app.utils.gemini_chat import _prune_history_images

        history = [
            _make_content("user", [_make_image_part()]),
            _make_content("model", [_make_text_part("ok")]),
            _make_content("user", [_make_image_part()]),  # middle: will be pruned
            _make_content("model", [_make_image_part()]),  # middle: will be pruned
            _make_content("user", [_make_text_part("latest")]),
            _make_content("model", [_make_text_part("done")]),
        ]
        result = _prune_history_images(history, max_images=1)
        # Check that pruned middle turns have placeholder
        middle_turn = result[2]
        texts = [p.text for p in middle_turn.parts if p.text]
        assert "[image removed for context limit]" in texts

    def test_continue_chat_prunes_when_over_limit(self):
        from app.utils.gemini_chat import MAX_INPUT_IMAGES, continue_chat

        # Build history with MAX_INPUT_IMAGES images
        history = []
        for i in range(MAX_INPUT_IMAGES):
            history.append(_make_content("user" if i % 2 == 0 else "model", [_make_image_part()]))

        mock_client = MagicMock()
        mock_client.models.generate_content.return_value = MagicMock()

        # Send a new message with 1 image — should trigger pruning
        img = Image.new("RGB", (10, 10), "blue")
        continue_chat(history, [img, "edit this"], mock_client)

        # Should have been called (pruning doesn't prevent the call)
        mock_client.models.generate_content.assert_called_once()


class TestExtractImageEdgeCases:
    """Edge case tests for extract_image."""

    def test_returns_none_when_content_is_none(self):
        mock_candidate = MagicMock()
        mock_candidate.content = None

        mock_response = MagicMock()
        mock_response.candidates = [mock_candidate]

        assert extract_image(mock_response) is None


class TestExtractTextEdgeCases:
    """Edge case tests for extract_text."""

    def test_returns_empty_when_content_is_none(self):
        mock_candidate = MagicMock()
        mock_candidate.content = None

        mock_response = MagicMock()
        mock_response.candidates = [mock_candidate]

        assert extract_text(mock_response) == ""


class TestRestoreFromR2EdgeCases:
    """Edge cases for restore_from_r2."""

    def test_non_nosuchkey_error_raises_application_error(self):
        from unittest.mock import patch

        from temporalio.exceptions import ApplicationError

        from app.utils.gemini_chat import restore_from_r2

        mock_r2 = MagicMock()
        from botocore.exceptions import ClientError

        mock_r2.get_object.side_effect = ClientError(
            {"Error": {"Code": "AccessDenied", "Message": "forbidden"}},
            "GetObject",
        )

        with (
            patch("app.utils.r2._get_client", return_value=mock_r2),
            pytest.raises(ApplicationError, match="AccessDenied"),
        ):
            restore_from_r2("proj-denied")


class TestGetClient:
    """Tests for get_client() factory function."""

    def test_creates_client_with_api_key(self):
        from unittest.mock import patch

        from app.utils.gemini_chat import get_client

        with patch("app.utils.gemini_chat.genai.Client") as mock_client_cls:
            mock_client_cls.return_value = MagicMock()
            client = get_client()
            mock_client_cls.assert_called_once()
            assert client is not None


class TestCreateChatDefaultClient:
    """Tests for create_chat() with default client path."""

    def test_creates_chat_with_default_client(self):
        from unittest.mock import patch

        from app.utils.gemini_chat import create_chat

        mock_client = MagicMock()
        mock_chat = MagicMock()
        mock_client.chats.create.return_value = mock_chat

        with patch("app.utils.gemini_chat.get_client", return_value=mock_client):
            chat = create_chat()  # No client arg -> uses get_client()
            assert chat is mock_chat
            mock_client.chats.create.assert_called_once()

    def test_creates_chat_with_provided_client(self):
        from app.utils.gemini_chat import create_chat

        mock_client = MagicMock()
        mock_chat = MagicMock()
        mock_client.chats.create.return_value = mock_chat

        chat = create_chat(client=mock_client)
        assert chat is mock_chat


class TestContinueChatDefaultClient:
    """Tests for continue_chat() with default client path."""

    def test_uses_default_client_when_none(self):
        from unittest.mock import patch

        from app.utils.gemini_chat import continue_chat

        history = [_make_content("user", [_make_text_part("hello")])]

        mock_client = MagicMock()
        mock_client.models.generate_content.return_value = MagicMock()

        with patch("app.utils.gemini_chat.get_client", return_value=mock_client):
            continue_chat(history, ["test message"])  # No client -> default
            mock_client.models.generate_content.assert_called_once()


class TestModelConfig:
    """Tests for model configuration constants."""

    def test_model_name(self):
        assert GEMINI_MODEL == "gemini-3-pro-image-preview"
