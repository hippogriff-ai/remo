"""Gemini chat session manager for multi-turn image editing.

Handles creation, serialization to R2, and restoration of Gemini chat
sessions. Chat history (including thought signatures and inline images)
is persisted to R2 between Temporal activity calls so that each activity
invocation is stateless and independently restartable.
"""

from __future__ import annotations

import base64
import io
import json
from typing import Any, cast

import structlog
from google import genai
from google.genai import types
from PIL import Image

from app.config import settings

logger = structlog.get_logger()

GEMINI_MODEL = "gemini-3-pro-image-preview"

IMAGE_CONFIG = types.GenerateContentConfig(
    response_modalities=["TEXT", "IMAGE"],
)

CHAT_HISTORY_KEY_TEMPLATE = "projects/{project_id}/gemini_chat_history.json"


def get_client() -> genai.Client:
    """Create a Gemini client using the configured API key."""
    return genai.Client(api_key=settings.google_ai_api_key)


def create_chat(client: genai.Client | None = None) -> genai.chats.Chat:
    """Create a new Gemini chat session configured for image generation."""
    if client is None:
        client = get_client()
    return client.chats.create(model=GEMINI_MODEL, config=IMAGE_CONFIG)


def serialize_history(chat: genai.chats.Chat) -> list[dict[str, Any]]:
    """Serialize a chat's history to a JSON-compatible structure.

    Preserves all parts including text, inline images (base64-encoded),
    and thought signatures for Gemini 3 Pro.
    """
    history = chat.get_history()
    return _contents_to_serializable(history)


def _contents_to_serializable(contents: list[types.Content]) -> list[dict[str, Any]]:
    """Convert a list of Content objects to JSON-serializable dicts."""
    serialized = []
    for content in contents:
        turn: dict[str, Any] = {"role": content.role, "parts": []}
        if content.parts:
            for part in content.parts:
                part_dict = _part_to_dict(part)
                if part_dict:
                    turn["parts"].append(part_dict)
        serialized.append(turn)
    return serialized


def _part_to_dict(part: types.Part) -> dict[str, Any]:
    """Convert a single Part to a JSON-serializable dict."""
    result: dict[str, Any] = {}

    if part.text is not None:
        result["text"] = part.text

    if part.inline_data is not None and part.inline_data.data is not None:
        result["inline_data"] = {
            "mime_type": part.inline_data.mime_type,
            "data": base64.b64encode(part.inline_data.data).decode("ascii"),
        }

    # Preserve thought signatures (critical for Gemini 3 Pro multi-turn)
    if hasattr(part, "thought_signature") and part.thought_signature:
        result["thought_signature"] = part.thought_signature

    return result


def deserialize_to_contents(serialized: list[dict[str, Any]]) -> list[types.Content]:
    """Reconstruct Content objects from serialized history."""
    contents = []
    for turn in serialized:
        if "role" not in turn or "parts" not in turn:
            msg = f"Invalid turn: requires 'role' and 'parts', got {list(turn.keys())}"
            raise ValueError(msg)
        parts = []
        for p in turn["parts"]:
            part = _dict_to_part(p)
            parts.append(part)
        contents.append(types.Content(role=turn["role"], parts=parts))
    return contents


def _dict_to_part(data: dict[str, Any]) -> types.Part:
    """Reconstruct a Part from a serialized dict."""
    if "inline_data" in data:
        inline = data["inline_data"]
        if not isinstance(inline, dict) or "data" not in inline or "mime_type" not in inline:
            raise ValueError("Invalid inline_data structure: requires 'data' and 'mime_type'")
        image_bytes = base64.b64decode(inline["data"])
        part = types.Part.from_bytes(
            data=image_bytes,
            mime_type=inline["mime_type"],
        )
    elif "text" in data:
        part = types.Part(text=data["text"])
    else:
        part = types.Part(text="")

    # Restore thought signature if present
    if "thought_signature" in data:
        part.thought_signature = data["thought_signature"]

    return part


def serialize_to_r2(chat: genai.chats.Chat, project_id: str) -> str:
    """Serialize chat history and upload to R2.

    Returns the R2 storage key for the history file.
    """
    from app.utils.r2 import upload_object

    serialized = serialize_history(chat)
    json_bytes = json.dumps(serialized).encode("utf-8")
    key = CHAT_HISTORY_KEY_TEMPLATE.format(project_id=project_id)
    upload_object(key, json_bytes, content_type="application/json")

    logger.info(
        "gemini_chat_serialized",
        project_id=project_id,
        key=key,
        turns=len(serialized),
        size_bytes=len(json_bytes),
    )
    return key


def serialize_contents_to_r2(contents: list[types.Content], project_id: str) -> str:
    """Serialize a contents list and upload to R2.

    Unlike serialize_to_r2 (which takes a Chat), this takes raw Content objects.
    Useful when updating history from restored contents + new turns.

    Returns the R2 storage key.
    """
    from app.utils.r2 import upload_object

    serialized = _contents_to_serializable(contents)
    json_bytes = json.dumps(serialized).encode("utf-8")
    key = CHAT_HISTORY_KEY_TEMPLATE.format(project_id=project_id)
    upload_object(key, json_bytes, content_type="application/json")

    logger.info(
        "gemini_chat_serialized",
        project_id=project_id,
        key=key,
        turns=len(serialized),
        size_bytes=len(json_bytes),
    )
    return key


def restore_from_r2(project_id: str) -> list[types.Content]:
    """Download and deserialize chat history from R2.

    Returns the contents array ready for generate_content.
    Raises ValueError if history is missing or corrupted.
    """
    from botocore.exceptions import ClientError

    from app.utils.r2 import _get_client as get_r2_client

    key = CHAT_HISTORY_KEY_TEMPLATE.format(project_id=project_id)
    r2 = get_r2_client()

    try:
        response = r2.get_object(Bucket=settings.r2_bucket_name, Key=key)
        json_bytes = response["Body"].read()
    except ClientError as e:
        error_code = e.response.get("Error", {}).get("Code", "Unknown")
        if error_code in ("NoSuchKey", "404"):
            raise ValueError(f"Chat history not found for project {project_id}") from e
        raise

    try:
        serialized = json.loads(json_bytes)
    except json.JSONDecodeError as e:
        logger.error("gemini_chat_json_corrupt", project_id=project_id, error=str(e))
        raise ValueError(f"Chat history JSON corrupted for {project_id}") from e

    contents = deserialize_to_contents(serialized)
    logger.info(
        "gemini_chat_restored",
        project_id=project_id,
        key=key,
        turns=len(contents),
    )
    return contents


def cleanup(project_id: str) -> None:
    """Delete chat history from R2."""
    from app.utils.r2 import delete_object

    key = CHAT_HISTORY_KEY_TEMPLATE.format(project_id=project_id)
    delete_object(key)
    logger.info("gemini_chat_cleaned", project_id=project_id)


def continue_chat(
    history: list[types.Content],
    new_message: list[types.Part | str | Image.Image],
    client: genai.Client | None = None,
) -> types.GenerateContentResponse:
    """Continue a chat by sending history + new message via generate_content.

    Since we can't inject history into a new Chat object, we reconstruct
    the full contents array and call generate_content directly.
    """
    if client is None:
        client = get_client()

    # Build new user turn from the message parts
    user_parts = []
    for item in new_message:
        if isinstance(item, str):
            user_parts.append(types.Part(text=item))
        elif isinstance(item, Image.Image):
            buf = io.BytesIO()
            item.save(buf, format="PNG")
            user_parts.append(types.Part.from_bytes(data=buf.getvalue(), mime_type="image/png"))
        elif isinstance(item, types.Part):
            user_parts.append(item)

    contents = list(history) + [types.Content(role="user", parts=user_parts)]

    return client.models.generate_content(
        model=GEMINI_MODEL,
        contents=cast("list", contents),
        config=IMAGE_CONFIG,
    )


def extract_image(response: types.GenerateContentResponse) -> Image.Image | None:
    """Extract the first image from a Gemini response as PIL Image."""
    if not response.candidates:
        return None
    content = response.candidates[0].content
    if content is None or content.parts is None:
        return None
    for part in content.parts:
        genai_img = part.as_image()
        if genai_img is not None and genai_img.image_bytes is not None:
            return Image.open(io.BytesIO(genai_img.image_bytes))
    return None


def extract_text(response: types.GenerateContentResponse) -> str:
    """Extract all text parts from a Gemini response."""
    if not response.candidates:
        return ""
    content = response.candidates[0].content
    if content is None or content.parts is None:
        return ""
    texts = []
    for part in content.parts:
        if part.text is not None:
            texts.append(part.text)
    return "\n".join(texts)


def response_to_content(response: types.GenerateContentResponse) -> types.Content | None:
    """Convert a response to a Content object for history tracking."""
    if not response.candidates:
        return None
    return response.candidates[0].content
