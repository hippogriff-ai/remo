"""Gemini chat session manager for multi-turn image editing.

Handles creation, serialization to R2, and restoration of Gemini chat
sessions. Chat history (including thought signatures and inline images)
is persisted to R2 between Temporal activity calls so that each activity
invocation is stateless and can reconstruct full chat context from
persisted R2 state.
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
from temporalio.exceptions import ApplicationError

from app.config import settings

logger = structlog.get_logger()

GEMINI_MODEL = "gemini-3-pro-image-preview"
MAX_INPUT_IMAGES = 14  # Gemini Pro image model limit

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
        sig = part.thought_signature
        # Base64-encode bytes signatures for JSON serialization
        if isinstance(sig, bytes):
            result["thought_signature"] = base64.b64encode(sig).decode("ascii")
            result["thought_signature_encoding"] = "base64"
        else:
            result["thought_signature"] = sig

    if not result:
        logger.error(
            "gemini_part_dropped_during_serialization",
            part_type=type(part).__name__,
        )

    return result


def deserialize_to_contents(serialized: list[dict[str, Any]]) -> list[types.Content]:
    """Reconstruct Content objects from serialized history."""
    contents = []
    for turn in serialized:
        if not isinstance(turn, dict):
            raise ValueError(f"Expected turn to be a dict, got {type(turn).__name__}")
        if "role" not in turn or "parts" not in turn:
            msg = f"Invalid turn: requires 'role' and 'parts', got {list(turn.keys())}"
            raise ValueError(msg)
        if not isinstance(turn["parts"], list):
            raise ValueError(f"Expected 'parts' to be a list, got {type(turn['parts']).__name__}")
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
        sig = data["thought_signature"]
        if data.get("thought_signature_encoding") == "base64":
            part.thought_signature = base64.b64decode(sig)
        else:
            part.thought_signature = sig

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
    Raises ApplicationError (non-retryable) if history is missing or corrupted.
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
            raise ApplicationError(
                f"Chat history not found for project {project_id}",
                non_retryable=True,
            ) from e
        logger.error("r2_chat_history_fetch_failed", project_id=project_id, error_code=error_code)
        is_client_error = error_code in ("AccessDenied", "InvalidBucketName", "403")
        raise ApplicationError(
            f"R2 error fetching chat history: {error_code}",
            non_retryable=is_client_error,
        ) from e

    try:
        serialized = json.loads(json_bytes)
    except json.JSONDecodeError as e:
        logger.error("gemini_chat_json_corrupt", project_id=project_id, error=str(e))
        raise ApplicationError(
            f"Chat history JSON corrupted for {project_id}",
            non_retryable=True,
        ) from e

    try:
        contents = deserialize_to_contents(serialized)
    except (ValueError, TypeError, KeyError) as e:
        logger.error("gemini_chat_deserialization_failed", project_id=project_id, error=str(e))
        raise ApplicationError(
            f"Chat history data invalid for {project_id}: {e}",
            non_retryable=True,
        ) from e

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


def _count_image_parts(contents: list[types.Content]) -> int:
    """Count inline image parts across all content turns."""
    count = 0
    for content in contents:
        if content.parts:
            for part in content.parts:
                if part.inline_data is not None:
                    count += 1
    return count


def _prune_history_images(
    history: list[types.Content],
    max_images: int,
) -> list[types.Content]:
    """Strip image parts from intermediate history turns to stay under the model limit.

    Keeps the first 2 turns (context setup) and the most recent 2 turns intact.
    Strips inline_data from middle turns, replacing with a text placeholder.
    """
    if _count_image_parts(history) <= max_images:
        return history

    # Protected: first 2 turns (context) + last 2 turns (most recent exchange)
    protected_start = min(2, len(history))
    protected_end = min(2, len(history) - protected_start)
    prunable_start = protected_start
    prunable_end = len(history) - protected_end

    pruned = list(history[:protected_start])
    for turn in history[prunable_start:prunable_end]:
        new_parts = []
        had_images = False
        for part in turn.parts or []:
            if part.inline_data is not None:
                had_images = True
            else:
                new_parts.append(part)
        if had_images and not any(p.text == "[image removed for context limit]" for p in new_parts):
            new_parts.append(types.Part(text="[image removed for context limit]"))
        pruned.append(types.Content(role=turn.role, parts=new_parts))
    pruned.extend(history[prunable_end:])

    remaining = _count_image_parts(pruned)
    logger.info(
        "history_images_pruned",
        original_images=_count_image_parts(history),
        remaining_images=remaining,
        history_turns=len(history),
    )
    return pruned


def continue_chat(
    history: list[types.Content],
    new_message: list[types.Part | str | Image.Image],
    client: genai.Client | None = None,
) -> types.GenerateContentResponse:
    """Continue a chat by sending history + new message via generate_content.

    Since we can't inject history into a new Chat object, we reconstruct
    the full contents array and call generate_content directly.

    new_message items can be strings, types.Part objects, or PIL Images
    (auto-converted to inline PNG parts).
    """
    if client is None:
        client = get_client()

    # Build new user turn from the message parts
    user_parts = []
    new_image_count = 0
    for item in new_message:
        if isinstance(item, str):
            user_parts.append(types.Part(text=item))
        elif isinstance(item, Image.Image):
            buf = io.BytesIO()
            item.save(buf, format="PNG")
            user_parts.append(types.Part.from_bytes(data=buf.getvalue(), mime_type="image/png"))
            new_image_count += 1
        elif isinstance(item, types.Part):
            user_parts.append(item)
            if item.inline_data is not None:
                new_image_count += 1
        else:
            raise ValueError(f"Unexpected message part type: {type(item).__name__}")

    # Prune history images if total would exceed model limit
    max_history_images = MAX_INPUT_IMAGES - new_image_count
    pruned_history = _prune_history_images(history, max_history_images)

    contents = pruned_history + [types.Content(role="user", parts=user_parts)]

    return client.models.generate_content(
        model=GEMINI_MODEL,
        contents=cast("list", contents),
        config=IMAGE_CONFIG,
    )


def extract_image(response: types.GenerateContentResponse) -> Image.Image | None:
    """Extract the first image from a Gemini response as PIL Image.

    Returns None if no image parts found. May raise if image data is corrupt.
    """
    if not response.candidates:
        return None
    content = response.candidates[0].content
    if content is None or content.parts is None:
        return None
    for part in content.parts:
        try:
            genai_img = part.as_image()
        except (AttributeError, ValueError):
            # Expected: part is not an image type
            continue
        if genai_img is not None and genai_img.image_bytes is not None:
            try:
                return Image.open(io.BytesIO(genai_img.image_bytes))
            except Exception:
                logger.error(
                    "gemini_image_decode_failed",
                    image_bytes_len=len(genai_img.image_bytes),
                )
                raise
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
