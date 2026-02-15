"""edit_design activity — multi-turn image editing via text coordinates.

Handles region-based edits (described by text position coordinates),
text-only feedback, or both combined in a single call. Uses a persistent
Gemini chat session serialized to R2 between Temporal activity calls.

First call: bootstraps a new chat with reference images + selected design.
Subsequent calls: restores chat history from R2, continues the conversation.
"""

from __future__ import annotations

import asyncio
import io
import os
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from google import genai

import structlog
from PIL import Image
from temporalio import activity
from temporalio.exceptions import ApplicationError

from app.models.contracts import (
    AnnotationRegion,
    EditDesignInput,
    EditDesignOutput,
)
from app.utils.gemini_chat import (
    MAX_INPUT_IMAGES,
    continue_chat,
    create_chat,
    extract_image,
    get_client,
    response_to_content,
    restore_from_r2,
    serialize_contents_to_r2,
    serialize_to_r2,
)
from app.utils.http import download_image, download_images
from app.utils.prompt_versioning import load_versioned_prompt

logger = structlog.get_logger()

# Strong references to background eval tasks to prevent GC before completion
_background_tasks: set[asyncio.Task] = set()  # type: ignore[type-arg]

CONTEXT_PROMPT = (
    "Here is a room redesign. I will send you edits describing specific areas "
    "to change by their position in the image, or as text instructions. "
    "Preserve the exact camera angle, perspective, and all architectural features "
    "(walls, windows, doors, ceiling, floor). Only modify the specific elements "
    "requested in each edit instruction. Your output must always be a clean "
    "photorealistic photograph with no overlays, markers, or annotation-like shapes."
)

TEXT_FEEDBACK_TEMPLATE = (
    "Please modify this room design based on the following feedback:\n"
    "{feedback}\n\n"
    "Keep all architectural features (walls, windows, doors, ceiling, floor), "
    "camera angle, perspective, and overall composition unchanged. "
    "Return a clean photorealistic photograph with zero annotations or markers."
)


def _position_description(center_x: float, center_y: float, radius: float) -> str:
    """Convert normalized coordinates to a human-readable position description."""
    # Horizontal position
    if center_x < 0.33:
        h_pos = "left"
    elif center_x < 0.66:
        h_pos = "center"
    else:
        h_pos = "right"

    # Vertical position
    if center_y < 0.33:
        v_pos = "upper"
    elif center_y < 0.66:
        v_pos = "middle"
    else:
        v_pos = "lower"

    # Combine
    if v_pos == "middle" and h_pos == "center":
        location = "center of the image"
    elif v_pos == "middle":
        location = f"{h_pos} side of the image"
    elif h_pos == "center":
        location = f"{v_pos} area of the image"
    else:
        location = f"{v_pos}-{h_pos} area of the image"

    # Size hint
    if radius > 0.25:
        size = "large area"
    elif radius > 0.12:
        size = "medium area"
    else:
        size = "small area"

    pct_x = int(center_x * 100)
    pct_y = int(center_y * 100)
    return f"{location} ({pct_x}% from left, {pct_y}% from top, {size})"


def _build_edit_instructions(annotations: list[AnnotationRegion]) -> str:
    """Build structured edit instruction text from annotation regions.

    Uses text-only coordinate descriptions instead of visual circle references.
    This prevents Gemini from reproducing annotation circles in its output.
    """
    blocks = []
    for ann in annotations:
        position = _position_description(ann.center_x, ann.center_y, ann.radius)
        lines = [f"Region {ann.region_id}: {position}"]
        if ann.action:
            lines.append(f"  ACTION: {ann.action}")
        lines.append(f"  INSTRUCTION: {ann.instruction}")
        if ann.avoid:
            lines.append(f"  AVOID: {', '.join(ann.avoid)}")
        if ann.constraints:
            lines.append(f"  CONSTRAINTS: {', '.join(ann.constraints)}")
        blocks.append("\n".join(lines))
    return "\n\n".join(blocks)


def _build_eval_instruction(input: EditDesignInput) -> str:
    """Build a combined instruction string for eval from annotations and/or feedback."""
    parts = []
    if input.annotations:
        parts.append(_build_edit_instructions(input.annotations))
    if input.feedback:
        parts.append(input.feedback)
    return "\n".join(parts) if parts else "edit"


def _build_changelog(history: list) -> str:
    """Extract previous edit instructions from chat history into a changelog.

    Scans user turns for text parts containing edit instructions (Region/ACTION
    patterns) or feedback (TEXT_FEEDBACK_TEMPLATE markers). Returns a formatted
    changelog string, or empty string if this is the first edit (bootstrap).
    """
    edits: list[str] = []
    for content in history:
        if content.role != "user":
            continue
        for part in content.parts:
            if not hasattr(part, "text") or not part.text:
                continue
            text = part.text
            # Skip context prompt (Turn 1 bootstrap)
            if text == CONTEXT_PROMPT:
                continue
            # Detect region-based edits
            if "Region " in text and "ACTION:" in text:
                # Extract a compact summary per region
                for line in text.split("\n"):
                    line = line.strip()
                    if line.startswith("Region "):
                        edits.append(f"- {line}")
                    elif line.startswith("INSTRUCTION:"):
                        edits.append(f"  {line}")
            # Detect text feedback
            elif "modify this room design" in text.lower():
                # Extract the user's feedback from TEXT_FEEDBACK_TEMPLATE
                for line in text.split("\n"):
                    line = line.strip()
                    if (
                        line
                        and not line.startswith("Keep all")
                        and not line.startswith("Return")
                        and "modify this room design" not in line.lower()
                    ):
                        edits.append(f"- Feedback: {line}")

    if not edits:
        return ""

    header = "PREVIOUS EDITS (preserve all these changes):"
    return f"{header}\n" + "\n".join(edits)


def _upload_image(image: Image.Image, project_id: str) -> str:
    """Upload revised image to R2 and return the storage key.

    Returns the R2 key (not a presigned URL) so the workflow stores a stable
    reference.  The API layer presigns on every state query, giving iOS
    always-fresh URLs.
    """
    import uuid

    from app.utils.r2 import upload_object

    revision_id = uuid.uuid4().hex[:8]
    key = f"projects/{project_id}/revisions/{revision_id}.png"
    buf = io.BytesIO()
    image.save(buf, format="PNG")
    logger.info("r2_upload_start", key=key, size_bytes=buf.tell())
    upload_object(key, buf.getvalue(), content_type="image/png")
    return key


async def _bootstrap_chat(
    input: EditDesignInput,
    base_image: Image.Image,
) -> tuple[genai.chats.Chat, Image.Image | None]:
    """Bootstrap a new chat session with context images.

    Returns (chat object, generated image or None if Gemini returned text-only).
    """
    client = get_client()

    # Download reference images for context
    room_images, inspiration_images = await asyncio.gather(
        download_images(input.room_photo_urls),
        download_images(input.inspiration_photo_urls),
    )

    # Safety cap: product allows 2 room + 3 inspiration = 5 refs, plus
    # base_image (always, sent twice when annotations present: context + edit) =
    # 7 images max in bootstrap. Well under the model's 14-image ceiling.
    # This guard only fires if upstream validation is bypassed.
    reserved = 2 if input.annotations else 1  # base_image in context + edit turns
    max_ref_images = MAX_INPUT_IMAGES - reserved
    total_ref = len(room_images) + len(inspiration_images)
    if total_ref > max_ref_images:
        max_inspiration = max_ref_images - len(room_images)
        if max_inspiration <= 0:
            room_images = room_images[:max_ref_images]
            inspiration_images = []
        else:
            inspiration_images = inspiration_images[:max_inspiration]
        logger.warning(
            "bootstrap_images_truncated",
            original_count=total_ref + reserved,
            room_kept=len(room_images),
            inspiration_kept=len(inspiration_images),
        )

    # Create chat (sync SDK call in thread pool)
    chat = await asyncio.to_thread(create_chat, client)

    # Turn 1: Reference images + selected design + context
    context_parts: list = []
    for img in room_images:
        context_parts.append(img)
    for img in inspiration_images:
        context_parts.append(img)
    context_parts.append(base_image)
    context_parts.append(CONTEXT_PROMPT)

    await asyncio.to_thread(chat.send_message, context_parts)
    logger.info("edit_bootstrap_context_sent", project_id=input.project_id)

    # Turn 2: Send the actual edit (supports annotations, feedback, or both)
    result_image = None
    edit_parts: list = []

    if input.annotations:
        # Send the CLEAN base image with text-only coordinate descriptions.
        # Previously we drew visual circles on the image, but Gemini sometimes
        # reproduced those circles in its output. Text coordinates eliminate this.
        edit_template = load_versioned_prompt("edit")
        instructions = _build_edit_instructions(input.annotations)
        edit_prompt = edit_template.format(
            edit_instructions=instructions.replace("{", "{{").replace("}", "}}"),
            changelog="",  # No previous edits on bootstrap
        )
        edit_parts.extend([base_image, edit_prompt])

    if input.feedback:
        feedback_prompt = TEXT_FEEDBACK_TEMPLATE.format(
            feedback=input.feedback.replace("{", "{{").replace("}", "}}")
        )
        if edit_parts:
            # Both annotations and feedback — append feedback as additional context
            edit_parts.append(f"\nAdditional feedback: {input.feedback}")
        else:
            edit_parts.append(feedback_prompt)

    if not edit_parts:
        raise ApplicationError(
            "No annotations or feedback provided for bootstrap",
            non_retryable=True,
        )

    response2 = await asyncio.to_thread(chat.send_message, edit_parts)
    result_image = extract_image(response2)

    if result_image is None:
        retry_msg = (
            "Please generate the edited room image now. "
            "Output only a clean photorealistic photograph with no overlays or markers."
        )
        response2 = await asyncio.to_thread(chat.send_message, retry_msg)
        result_image = extract_image(response2)

    return chat, result_image


async def _continue_chat(
    input: EditDesignInput,
    base_image: Image.Image | None,
) -> tuple[Image.Image | None, list]:
    """Continue an existing chat from R2 history.

    Returns (result image, updated history contents).
    """
    from google.genai import types as gtypes

    if not input.annotations and not input.feedback:
        raise ApplicationError(
            "No annotations or feedback provided",
            non_retryable=True,
        )

    client = get_client()
    # Sync R2 download in thread pool
    history = await asyncio.to_thread(restore_from_r2, input.project_id)

    # Re-anchor room photos so Gemini retains architectural context across edits.
    # Without these, spatial drift accumulates after several edit turns.
    room_images: list[Image.Image] = []
    if input.room_photo_urls:
        room_images = await download_images(input.room_photo_urls)

    # Build message parts (supports annotations, feedback, or both)
    message_parts: list = []

    # Prepend room photos as spatial anchors (capped to leave room for edit images)
    if room_images:
        # Reserve slots: base_image (1 if annotations) + safety margin
        reserved = 2 if input.annotations else 1
        max_room = MAX_INPUT_IMAGES - reserved
        if len(room_images) > max_room:
            room_images = room_images[:max_room]
        message_parts.append("Reference room photos (preserve this architecture):")
        message_parts.extend(room_images)

    # Build cumulative changelog from previous edit turns
    changelog = _build_changelog(history)

    if input.annotations:
        assert base_image is not None  # guaranteed: caller downloads when annotations present
        # Send clean base image with text coordinates — no visual annotations
        edit_template = load_versioned_prompt("edit")
        instructions = _build_edit_instructions(input.annotations)
        edit_prompt = edit_template.format(
            edit_instructions=instructions.replace("{", "{{").replace("}", "}}"),
            changelog=changelog.replace("{", "{{").replace("}", "}}"),
        )
        message_parts.extend([base_image, edit_prompt])

    if input.feedback:
        feedback_prompt = TEXT_FEEDBACK_TEMPLATE.format(
            feedback=input.feedback.replace("{", "{{").replace("}", "}}")
        )
        if input.annotations:
            # Both annotations and feedback — append feedback as additional context
            message_parts.append(f"\nAdditional feedback: {input.feedback}")
        else:
            message_parts.append(feedback_prompt)

    # Sync Gemini call in thread pool
    response = await asyncio.to_thread(continue_chat, history, message_parts, client)
    result_image = extract_image(response)

    # Build updated history: history + user turn + model response
    # Reconstruct the user Content that continue_chat() built internally
    user_parts = []
    for item in message_parts:
        if isinstance(item, str):
            user_parts.append(gtypes.Part(text=item))
        elif isinstance(item, Image.Image):
            buf = io.BytesIO()
            item.save(buf, format="PNG")
            user_parts.append(gtypes.Part.from_bytes(data=buf.getvalue(), mime_type="image/png"))
        else:
            logger.warning(
                "edit_chat_unexpected_part_type",
                item_type=type(item).__name__,
            )

    updated_history = list(history)
    updated_history.append(gtypes.Content(role="user", parts=user_parts))

    model_content = response_to_content(response)
    if model_content:
        updated_history.append(model_content)
    else:
        logger.warning(
            "gemini_model_response_empty",
            project_id=input.project_id,
            history_len=len(updated_history),
        )

    if result_image is None:
        # Retry with explicit request
        retry_text = (
            "Please generate the edited room image now. "
            "Output only a clean photorealistic photograph with no overlays or markers."
        )
        response = await asyncio.to_thread(
            continue_chat,
            updated_history,
            [retry_text],
            client,
        )
        result_image = extract_image(response)

        # Add retry turns to history
        updated_history.append(gtypes.Content(role="user", parts=[gtypes.Part(text=retry_text)]))
        retry_content = response_to_content(response)
        if retry_content:
            updated_history.append(retry_content)

    return result_image, updated_history


async def _maybe_run_edit_eval(
    result_image: Image.Image,
    original_image: Image.Image,
    original_url: str,
    revised_url: str,
    instruction: str,
) -> None:
    """Run VLM eval pipeline on edit result if EVAL_MODE is set. Never raises."""
    eval_mode = os.environ.get("EVAL_MODE", "off").lower()
    if eval_mode == "off":
        return

    try:
        from app.utils.image_eval import run_artifact_check

        artifact = run_artifact_check(result_image)
        if artifact.has_artifacts:
            logger.warning(
                "eval_edit_artifacts_detected",
                count=artifact.artifact_count,
            )

        artifact_dict = {
            "has_artifacts": artifact.has_artifacts,
            "artifact_count": artifact.artifact_count,
        }

        from app.activities.design_eval import evaluate_edit

        vlm_result = await evaluate_edit(
            original_image_url=original_url,
            edited_image_url=revised_url,
            edit_instruction=instruction,
            artifact_check=artifact_dict,
        )
        logger.info(
            "eval_edit_vlm_result",
            total=vlm_result.total,
            tag=vlm_result.tag,
        )

        from app.utils.score_tracking import append_score

        append_score(
            history_path=Path("eval_history.jsonl"),
            scenario="edit",
            prompt_version="v1",
            vlm_eval={"total": vlm_result.total, "tag": vlm_result.tag},
            artifact_check=artifact_dict,
        )
    except Exception:
        logger.warning("eval_edit_failed", exc_info=True)


@activity.defn
async def edit_design(input: EditDesignInput) -> EditDesignOutput:
    """Edit a design image using annotations or text feedback."""
    activity.logger.info(
        "edit_design_start",
        project_id=input.project_id,
        has_annotations=bool(input.annotations),
        has_feedback=bool(input.feedback),
        has_history=bool(input.chat_history_key),
    )

    if not input.annotations and not input.feedback:
        raise ApplicationError(
            "Either annotations or feedback must be provided",
            non_retryable=True,
        )

    # Resolve R2 storage keys to presigned URLs (pass through existing URLs)
    from app.utils.r2 import resolve_url, resolve_urls
    from app.utils.tracing import trace_thread

    resolved_input = input.model_copy(
        update={
            "base_image_url": await asyncio.to_thread(resolve_url, input.base_image_url),
            "room_photo_urls": await asyncio.to_thread(resolve_urls, input.room_photo_urls),
            "inspiration_photo_urls": await asyncio.to_thread(
                resolve_urls, input.inspiration_photo_urls
            ),
        }
    )

    try:
        original_image: Image.Image | None = None
        if resolved_input.chat_history_key is None:
            # First call: bootstrap — always needs the base image
            base_image = await download_image(resolved_input.base_image_url)
            original_image = base_image
            with trace_thread(input.project_id, "edit"):
                chat, result_image = await _bootstrap_chat(resolved_input, base_image)

            if result_image is None:
                raise ApplicationError(
                    "Gemini failed to generate edited image",
                    non_retryable=False,
                )

            # Upload result and serialize history (sync calls in thread pool)
            revised_url = await asyncio.to_thread(
                _upload_image, result_image, resolved_input.project_id
            )
            history_key = await asyncio.to_thread(serialize_to_r2, chat, resolved_input.project_id)

        else:
            # Subsequent call: continue from R2 history
            # Only download base image if annotations require it
            cont_base: Image.Image | None = (
                await download_image(resolved_input.base_image_url)
                if resolved_input.annotations
                else None
            )
            original_image = cont_base
            with trace_thread(input.project_id, "edit"):
                result_image, updated_history = await _continue_chat(resolved_input, cont_base)

            if result_image is None:
                raise ApplicationError(
                    "Gemini failed to generate edited image on continuation",
                    non_retryable=False,
                )

            # Upload result (sync call in thread pool)
            revised_url = await asyncio.to_thread(
                _upload_image, result_image, resolved_input.project_id
            )

            # Serialize updated history back to R2 using shared helper
            history_key = await asyncio.to_thread(
                serialize_contents_to_r2, updated_history, resolved_input.project_id
            )

        # Download original for eval if not already available (text-only continue path)
        if original_image is None:
            original_image = await download_image(resolved_input.base_image_url)

        # Run eval if enabled — fire-and-forget, never blocks the activity
        task = asyncio.create_task(
            _maybe_run_edit_eval(
                result_image=result_image,
                original_image=original_image,
                original_url=resolved_input.base_image_url,
                revised_url=revised_url,
                instruction=_build_eval_instruction(input),
            )
        )
        _background_tasks.add(task)
        task.add_done_callback(_background_tasks.discard)

        return EditDesignOutput(
            revised_image_url=revised_url,
            chat_history_key=history_key,
        )

    except ApplicationError:
        raise
    except Exception as e:
        error_type = type(e).__name__
        error_msg = str(e)

        # TODO: Catch typed google.genai exceptions when SDK stabilizes
        is_rate_limit = (
            "429" in error_msg
            or "RESOURCE_EXHAUSTED" in error_msg
            or "ResourceExhausted" in error_type
        )
        if is_rate_limit:
            raise ApplicationError(
                "Gemini rate limited",
                non_retryable=False,
            ) from e

        if "SAFETY" in error_msg or "blocked" in error_msg.lower():
            raise ApplicationError(
                f"Content policy violation: {error_msg[:200]}",
                non_retryable=True,
            ) from e

        if "400" in error_msg and "thought" in error_msg.lower():
            raise ApplicationError(
                "Chat history corrupted (thought signature validation failed)",
                non_retryable=True,
            ) from e

        raise ApplicationError(
            f"Edit failed: {error_type}: {error_msg[:200]}",
            non_retryable=False,
        ) from e
