# T2 Model Selection Decision — P0 Spike Results

> Date: 2026-02-11
> Author: T2 (Image Gen Pipeline)
> Status: **DECISION MADE**

## Executive Summary

**Winner: `gemini-3-pro-image-preview`** for all image generation and editing.

Both models passed the decision gate (4/4 test cases), but Pro is required due to our multi-image input requirements (5-6 images per call). Flash is limited to 3 recommended input images.

## Test Configuration

- **Test image**: Synthetic room scene (1024x1024, Pillow-generated geometry)
- **Models tested**: `gemini-3-pro-image-preview`, `gemini-2.5-flash-image`
- **SDK**: `google-genai` v1.62.0
- **Scenarios**: Initial generation, annotation editing, chat round-trip, text-only editing

## Side-by-Side Results

### Scenario 1: Initial Generation (room photo + brief → Scandinavian redesign)

| Criterion | Pro | Flash |
|---|---|---|
| Image generated | Yes (1024x1024) | Yes (1024x1024) |
| Photorealism | High — warm lighting, textured fabrics, natural materials | Good — cleaner/flatter style, still convincing |
| Room architecture preserved | Yes — window, walls, floor plane match | Yes — similar layout maintained |
| Style adherence | Excellent — nailed Scandinavian warmth | Good — minimalist but less cozy |
| Duration | 17.5s | 9.0s |

**Winner**: Pro (higher fidelity, warmer photorealism)

### Scenario 2: Annotation-Based Editing (numbered circles → targeted edits)

| Criterion | Pro | Flash |
|---|---|---|
| Correct area edited | Yes — sofa replaced, lamp replaced | Yes — sofa replaced, lamp replaced |
| Non-annotated areas preserved | Mostly — some style drift in surrounding area | Mostly — similar drift |
| Output image clean | **No** — red circle outline visible | **No** — numbered badges visible |
| Instruction followed | Yes | Yes |
| Duration | 21.2s | 6.8s |

**Both leave annotation artifacts** despite explicit anti-artifact prompt. This is a known Gemini behavior that needs stronger prompt engineering (see Findings below).

**Winner**: Tie (both edit correctly, both leave artifacts)

### Scenario 3: Chat History Round-Trip (serialize → deserialize → continue)

| Criterion | Pro | Flash |
|---|---|---|
| Turn 1 image generated | Yes | Yes |
| Thought signatures captured | 1 per response | 1 per response |
| Serialized history size | 6.02 MB | 1.62 MB |
| Turn 2 after deserialization | Success — curtain edit applied | Success — curtain edit applied |
| 400 error on follow-up | No | No |
| Duration (both turns) | 30.8s | 15.6s |

**Winner**: Flash (smaller history, faster), but Pro also passes completely.

### Scenario 4: Text-Only Editing (no annotations, text feedback only)

| Criterion | Pro | Flash |
|---|---|---|
| Edit applied | Yes — earth tones visible | Yes — earth tones visible |
| Furniture layout preserved | Yes | Yes |
| Clean output | Yes | Yes |
| Duration | 28.6s | 17.4s |

**Winner**: Tie (both handle text-only edits well)

## Decision Rationale

### Why Pro wins despite Flash being faster/cheaper

1. **Input image limit is the blocker.** Our workflow sends 2 room photos + up to 3 inspiration photos + the generated/annotated image = up to 6 images per call. Flash supports max 3 recommended input images. Pro supports up to 14.

2. **Higher photorealism for initial generation.** The first impression matters — Pro produces warmer, more convincing room redesigns that better match real interior design quality.

3. **Thought signature handling.** Pro's thought signatures are required and well-supported. This gives us more reliable multi-turn editing.

4. **Cost is acceptable.** At ~$0.134/image vs ~$0.039/image, the 3.4x premium is worth the quality and flexibility. A typical session costs ~$0.70 (initial + 3 edits with caching).

### Flash as fallback

Flash remains viable as a fallback for:
- Rate limit overflow (if Pro hits limits, fall back to Flash for edits with fewer ref images)
- Cost optimization in high-volume scenarios
- Simpler edits where fewer reference images suffice

## Critical Findings

### Finding 1: Annotation Artifacts in Output (MUST FIX)

Both models leave circle/badge artifacts in edited images despite explicit anti-artifact instructions. The current prompt includes:

> "Do not include any annotations, circles, numbers, or markers in your output image. Return only the edited room photograph."

**Action required**: The `edit.txt` prompt template must use stronger language and possibly a two-step approach:
1. Send annotated image with edit instructions
2. Follow up with: "Now generate the final clean image with all edits applied. Remove ALL annotation circles, numbers, and markers. Output only a clean photorealistic photograph."

This will be addressed in P1 deliverable #5 (prompt templates) and validated in P2 quality tests.

### Finding 2: History Size Growth

Pro model histories are ~6MB per turn (mostly base64 images). After 5 edits with 5 reference images, history could reach 30-50MB. R2 handles this fine, but deserialization adds latency.

**Recommendation**: Consider starting a fresh session every 3 edits, carrying only the latest image + refs. Evaluate quality impact during P1.

### Finding 3: `as_image()` Returns Google Image Type

The `google.genai.types.Image` returned by `part.as_image()` is NOT a PIL Image. It has `.image_bytes` and `.save()` but no `.size`/`.width`/`.height`. Activity code must convert via `Image.open(io.BytesIO(img.image_bytes))`.

### Finding 4: Both Models Return Thought Signatures

Even Flash returns thought signatures (1 per response), not just Pro. This simplifies the serialization logic — same code path works for both models.

## Escalation Plan

If Pro becomes unavailable or pricing changes significantly:
1. **Flash + reduced refs**: Send only 1 room photo + 1 inspiration + generated image (3 total)
2. **Imagen 4.0**: Available in the API (`imagen-4.0-generate-001`), but doesn't support multi-turn editing
3. **Two-model strategy**: Pro for initial generation (needs many refs), Flash for edits (needs fewer)

## Model Configuration for Activities

```python
# Selected model for all T2 activities
GEMINI_MODEL = "gemini-3-pro-image-preview"

# Config for image generation
IMAGE_GEN_CONFIG = types.GenerateContentConfig(
    response_modalities=["TEXT", "IMAGE"],
)
```
