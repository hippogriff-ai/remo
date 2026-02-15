# ruff: noqa: E501
"""A/B prompt eval — compare generation prompt versions via VLM judge.

Generates images with baseline and candidate prompt versions,
runs VLM eval (Claude Vision judge) on each, and bootstraps significance.

Usage:
    cd backend
    source ../.env
    export EVAL_MODE=on
    .venv/bin/python -m pytest tests/eval/test_prompt_ab.py -x -v -s -m integration

Each run produces one baseline + one candidate image. Run 5+ times for
statistical significance, then use the bootstrap test below.
"""

from __future__ import annotations

import asyncio
import json
import os
from datetime import UTC, datetime
from pathlib import Path

import pytest
from PIL import Image

from app.activities.generate import _format_color_palette
from app.models.contracts import DesignBrief, StyleProfile

EVAL_HISTORY = Path(__file__).parent / "prompt_ab_history.jsonl"

# Test brief: a realistic mid-century living room scenario
TEST_BRIEF = DesignBrief(
    room_type="living room",
    occupants="couple with one child",
    lifestyle="casual, active",
    style_profile=StyleProfile(
        mood="warm and inviting",
        colors=["warm ivory", "walnut brown", "olive green"],
        textures=["linen", "wood", "wool"],
        lighting="warm ambient with natural light",
        clutter_level="minimal",
    ),
    pain_points=["outdated furniture", "poor lighting in evening"],
    constraints=["budget-friendly"],
    emotional_drivers=["calm", "welcoming"],
)

# Test brief matching the real bathroom fixture photos
BATHROOM_BRIEF = DesignBrief(
    room_type="bathroom",
    occupants="couple",
    lifestyle="urban professional",
    style_profile=StyleProfile(
        mood="spa-like and serene",
        colors=["warm white", "walnut", "brass"],
        textures=["natural stone", "wood", "glass"],
        lighting="soft warm ambient with accent lighting",
        clutter_level="minimal",
    ),
    pain_points=["cluttered countertop", "dated hardware"],
    constraints=["keep existing tub and vanity layout"],
    emotional_drivers=["relaxation", "clean"],
)

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        not os.environ.get("GOOGLE_AI_API_KEY"),
        reason="GOOGLE_AI_API_KEY not set",
    ),
    pytest.mark.skipif(
        not os.environ.get("ANTHROPIC_API_KEY"),
        reason="ANTHROPIC_API_KEY not set (needed for VLM eval)",
    ),
]


FIXTURES_DIR = Path(__file__).parent.parent / "fixtures"
REAL_ROOM_PHOTO = FIXTURES_DIR / "room_photo.jpg"
REAL_ROOM_PHOTO_2 = FIXTURES_DIR / "room_photo_2.jpg"


def _make_room_image() -> Image.Image:
    """Load the real room test photo, or fall back to synthetic."""
    if REAL_ROOM_PHOTO.exists():
        return Image.open(REAL_ROOM_PHOTO).convert("RGB")

    import numpy as np

    # Fallback: synthetic room-like image
    img = np.zeros((768, 1024, 3), dtype=np.uint8)
    img[:256, :, :] = [220, 220, 225]
    img[256:512, :, :] = [210, 195, 175]
    img[512:, :, :] = [140, 110, 80]
    img[100:400, 400:624, :] = [180, 210, 240]
    return Image.fromarray(img)


def _make_room_images() -> list[Image.Image]:
    """Load all available real room photos for multi-image eval."""
    images = []
    for path in [REAL_ROOM_PHOTO, REAL_ROOM_PHOTO_2]:
        if path.exists():
            images.append(Image.open(path).convert("RGB"))
    if not images:
        images.append(_make_room_image())
    return images


def _build_prompt(gen_version: str, room_pres_version: str, room_context: str = "") -> str:
    """Build the generation prompt using specific versions."""
    from app.activities.generate import _OPTION_VARIANTS

    # Temporarily override the active version by loading specific version files
    gen_template = (Path("prompts") / f"generation_{gen_version}.txt").read_text()
    room_pres = (Path("prompts") / f"room_preservation_{room_pres_version}.txt").read_text()

    # Build brief text (same logic as _build_generation_prompt)
    parts = [f"Room type: {TEST_BRIEF.room_type}"]
    if TEST_BRIEF.occupants:
        parts.append(f"Occupants: {TEST_BRIEF.occupants}")
    if TEST_BRIEF.lifestyle:
        parts.append(f"Lifestyle: {TEST_BRIEF.lifestyle}")
    if TEST_BRIEF.style_profile:
        sp = TEST_BRIEF.style_profile
        if sp.mood:
            parts.append(f"Mood: {sp.mood}")
        if sp.colors:
            parts.append(_format_color_palette(sp.colors))
        if sp.textures:
            parts.append(f"Textures: {', '.join(sp.textures)}")
        if sp.lighting:
            parts.append(f"Lighting: {sp.lighting}")
        if sp.clutter_level:
            parts.append(f"Clutter level: {sp.clutter_level}")
    if TEST_BRIEF.pain_points:
        parts.append(f"Pain points to address: {', '.join(TEST_BRIEF.pain_points)}")
    if TEST_BRIEF.constraints:
        parts.append(f"Constraints: {', '.join(TEST_BRIEF.constraints)}")
    if TEST_BRIEF.emotional_drivers:
        parts.append(f"Emotional drivers: {', '.join(TEST_BRIEF.emotional_drivers)}")
    brief_text = "\n".join(parts)

    return gen_template.format(
        brief=brief_text.replace("{", "{{").replace("}", "}}"),
        keep_items="",
        room_context=room_context.replace("{", "{{").replace("}", "}}"),
        room_preservation=room_pres,
        option_variant=_OPTION_VARIANTS[0],
    )


async def _generate_image(
    prompt: str,
    room_image: Image.Image,
    max_retries: int = 5,
) -> Image.Image:
    """Call Gemini with prompt + room image, retrying on transient errors."""
    from app.utils.gemini_chat import (
        GEMINI_MODEL,
        IMAGE_CONFIG,
        extract_image,
        get_client,
    )

    client = get_client()
    contents = [room_image, prompt]

    for attempt in range(max_retries):
        try:
            response = await asyncio.to_thread(
                client.models.generate_content,
                model=GEMINI_MODEL,
                contents=contents,
                config=IMAGE_CONFIG,
            )

            result = extract_image(response)
            if result is None:
                # Retry with nudge
                response = await asyncio.to_thread(
                    client.models.generate_content,
                    model=GEMINI_MODEL,
                    contents=contents + ["Please generate the room image now."],
                    config=IMAGE_CONFIG,
                )
                result = extract_image(response)

            if result is not None:
                return result
            # No image returned — retry
            print(f"(no image, retry {attempt + 1})...", end=" ", flush=True)
        except Exception as e:
            err = str(e)
            if "503" in err or "UNAVAILABLE" in err or "429" in err or "RESOURCE_EXHAUSTED" in err:
                wait = 2**attempt * 5  # 5, 10, 20, 40, 80 seconds
                print(f"(rate limited, waiting {wait}s)...", end=" ", flush=True)
                await asyncio.sleep(wait)
            else:
                raise

    raise AssertionError(f"Gemini failed to generate an image after {max_retries} attempts")


async def _run_vlm_eval(
    result_image: Image.Image,
    original_image: Image.Image,
    brief: DesignBrief | None = None,
    generation_prompt: str = "",
    room_context: str = "",
) -> dict:
    """Run VLM eval (Claude Vision judge) on PIL Images directly.

    Returns a dict with per-criterion scores (100-point rubric) + diagnostics.
    """
    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        return {"skipped": True, "reason": "ANTHROPIC_API_KEY not set"}

    try:
        import base64
        import io

        import anthropic

        from app.activities.design_eval import (
            _GENERATION_CRITERIA_MAX,
            _GENERATION_RESPONSE_FORMAT,
            _GENERATION_RUBRIC,
            _generation_tag,
            _parse_criteria,
        )
        from app.activities.shopping import _extract_json

        # Convert PIL Images to base64, resizing if needed to stay under 5MB
        def _pil_to_b64(img: Image.Image, max_bytes: int = 4_500_000) -> tuple[str, str]:
            buf = io.BytesIO()
            img_rgb = img.convert("RGB")
            img_rgb.save(buf, format="JPEG", quality=85)
            if buf.tell() <= max_bytes:
                return base64.standard_b64encode(buf.getvalue()).decode("ascii"), "image/jpeg"
            scale = (max_bytes / buf.tell()) ** 0.5
            new_size = (int(img.width * scale), int(img.height * scale))
            img_resized = img_rgb.resize(new_size, Image.LANCZOS)
            buf = io.BytesIO()
            img_resized.save(buf, format="JPEG", quality=80)
            return base64.standard_b64encode(buf.getvalue()).decode("ascii"), "image/jpeg"

        orig_b64, orig_mime = _pil_to_b64(original_image)
        gen_b64, gen_mime = _pil_to_b64(result_image)

        brief_json = brief.model_dump_json(indent=2) if brief else "{}"

        # Build prompt with optional generation prompt and room context
        sections = [
            f"{_GENERATION_RUBRIC}\n\n",
            f"## DesignBrief:\n```json\n{brief_json}\n```\n\n",
        ]
        if generation_prompt:
            sections.append(f"## Generation Prompt:\n```\n{generation_prompt}\n```\n\n")
        if room_context:
            sections.append(f"## Room Context (LiDAR):\n{room_context}\n\n")
        sections.append(_GENERATION_RESPONSE_FORMAT)
        prompt_text = "".join(sections)

        content = [
            {"type": "text", "text": "Original room photo:"},
            {
                "type": "image",
                "source": {"type": "base64", "media_type": orig_mime, "data": orig_b64},
            },
            {"type": "text", "text": "AI-generated redesign:"},
            {
                "type": "image",
                "source": {"type": "base64", "media_type": gen_mime, "data": gen_b64},
            },
            {"type": "text", "text": prompt_text},
        ]

        async with anthropic.AsyncAnthropic(api_key=api_key) as client:
            response = await client.messages.create(
                model="claude-sonnet-4-5-20250929",
                max_tokens=2048,
                messages=[{"role": "user", "content": content}],
            )

        text = "".join(b.text for b in response.content if hasattr(b, "text"))
        raw = _extract_json(text)
        if not raw:
            return {"error": f"Could not parse JSON: {text[:200]}"}

        criteria = _parse_criteria(raw, _GENERATION_CRITERIA_MAX)
        total = sum(c.score for c in criteria)
        tag = _generation_tag(total)

        result = {
            "total": total,
            "tag": tag,
            **{c.name: c.score for c in criteria},
            "notes": raw.get("notes", ""),
        }

        # Extract diagnostic scores
        for diag_key, diag_max in [("instruction_adherence", 10), ("spatial_accuracy", 5)]:
            val = raw.get(diag_key, 0)
            if not isinstance(val, int):
                try:
                    val = int(val) if val else 0
                except (ValueError, TypeError):
                    val = 0
            result[diag_key] = max(0, min(val, diag_max))

        return result
    except Exception as e:
        return {"error": str(e)}


def _append_result(version: str, scores: dict) -> None:
    """Append eval result to history file."""
    entry = {
        "timestamp": datetime.now(UTC).isoformat(),
        "prompt_version": version,
        "vlm_eval": scores,
    }
    with open(EVAL_HISTORY, "a") as f:
        f.write(json.dumps(entry) + "\n")


NUM_RUNS = 5


def _run_ab_comparison(
    label_a: str,
    label_b: str,
    prompt_a: str,
    prompt_b: str,
    room_image: Image.Image,
    brief: DesignBrief = TEST_BRIEF,
    generation_prompt_a: str = "",
    generation_prompt_b: str = "",
    room_context: str = "",
    num_runs: int = NUM_RUNS,
) -> tuple[list[dict], list[dict]]:
    """Run A/B comparison via VLM eval, return (a_scores, b_scores)."""

    async def _run():
        a_all, b_all = [], []
        print(f"\n{'=' * 60}")
        print(f"  {label_a} vs {label_b} ({num_runs} runs)")
        print("=" * 60)

        for i in range(num_runs):
            print(f"\n--- Run {i + 1}/{num_runs} ---")

            print(f"  {label_a}...", end=" ", flush=True)
            img_a = await _generate_image(prompt_a, room_image)
            print("generated...", end=" ", flush=True)
            s_a = await _run_vlm_eval(
                img_a,
                room_image,
                brief=brief,
                generation_prompt=generation_prompt_a,
                room_context=room_context,
            )
            a_all.append(s_a)
            if "total" in s_a:
                print(f"total={s_a['total']} tag={s_a['tag']}")
            else:
                print(f"SKIP: {s_a}")
            _append_result(label_a, s_a)

            print(f"  {label_b}...", end=" ", flush=True)
            img_b = await _generate_image(prompt_b, room_image)
            print("generated...", end=" ", flush=True)
            s_b = await _run_vlm_eval(
                img_b,
                room_image,
                brief=brief,
                generation_prompt=generation_prompt_b,
                room_context=room_context,
            )
            b_all.append(s_b)
            if "total" in s_b:
                print(f"total={s_b['total']} tag={s_b['tag']}")
            else:
                print(f"SKIP: {s_b}")
            _append_result(label_b, s_b)

        return a_all, b_all

    return asyncio.get_event_loop().run_until_complete(_run())


def _print_bootstrap_summary(
    label_a: str, label_b: str, a_scores: list[dict], b_scores: list[dict]
) -> None:
    """Print bootstrap significance test for VLM criteria."""
    import numpy as np

    # Filter to successful runs
    a_valid = [s for s in a_scores if "total" in s]
    b_valid = [s for s in b_scores if "total" in s]

    if not a_valid or not b_valid:
        print("  Insufficient valid data for bootstrap")
        return

    criteria = [
        ("total", 3),
        ("photorealism", 1),
        ("style_adherence", 1),
        ("room_preservation", 1),
        ("furniture_scale", 1),
        ("instruction_adherence", 1),
        ("spatial_accuracy", 1),
    ]

    for metric, min_eff in criteria:
        a_vals = np.array([s.get(metric, 0) for s in a_valid], dtype=float)
        b_vals = np.array([s.get(metric, 0) for s in b_valid], dtype=float)
        delta = b_vals.mean() - a_vals.mean()

        boot_diffs = []
        for _ in range(10000):
            a_sample = np.random.choice(a_vals, size=len(a_vals), replace=True)
            b_sample = np.random.choice(b_vals, size=len(b_vals), replace=True)
            boot_diffs.append(b_sample.mean() - a_sample.mean())
        boot_diffs = np.array(boot_diffs)
        ci_low = np.percentile(boot_diffs, 5)
        ci_high = np.percentile(boot_diffs, 95)
        p_better = np.mean(boot_diffs > 0)

        if ci_low > min_eff:
            verdict = "SHIP"
        elif ci_low > 0:
            verdict = "LIKELY_BETTER"
        elif ci_high < 0:
            verdict = "ROLLBACK"
        else:
            verdict = "INCONCLUSIVE"

        print(
            f"  {metric:25s}: {label_a}={a_vals.mean():.1f} {label_b}={b_vals.mean():.1f} "
            f"delta={delta:+.1f} CI=[{ci_low:+.1f},{ci_high:+.1f}] P={p_better:.3f} → {verdict}"
        )


class TestPromptAB:
    """A/B test generation prompt versions using VLM eval."""

    @pytest.mark.asyncio
    async def test_baseline_vs_candidate(self):
        """VLM eval: v5+v4 vs v2+v2 baseline."""
        room_image = _make_room_image()
        baseline_prompt = _build_prompt("v2", "v2")
        candidate_prompt = _build_prompt("v5", "v4")

        baseline_all, candidate_all = _run_ab_comparison(
            "baseline_v2+v2",
            "candidate_v5+v4",
            baseline_prompt,
            candidate_prompt,
            room_image,
        )

        print("\n" + "=" * 60)
        print("  VLM EVAL SUMMARY")
        print("=" * 60)
        _print_bootstrap_summary("v2+v2", "v5+v4", baseline_all, candidate_all)

    def test_scene_data(self):
        """VLM eval: gen_v5+room_v5 (scene data) vs gen_v5+room_v4 (baseline).

        Uses real bathroom photos. Passes room_context to VLM so
        spatial_accuracy diagnostic is meaningful.
        """
        room_images = _make_room_images()

        room_context = (
            "ROOM GEOMETRY (LiDAR-measured, precise):\n"
            "- Dimensions: 1.8m wide × 2.5m long, ceiling height 2.4m\n"
            "- Floor area: 4.5 m²\n\n"
            "WALLS (4 detected):\n"
            "- wall_0: 1.8m wide, 2.4m tall, faces south (0°)\n"
            "- wall_1: 2.5m wide, 2.4m tall, faces west (90°)\n"
            "- wall_2: 1.8m wide, 2.4m tall, faces north (180°)\n"
            "- wall_3: 2.5m wide, 2.4m tall, faces east (270°)\n\n"
            "FIXED OPENINGS (do not relocate):\n"
            "- door (0.7m × 2.1m)\n\n"
            "EXISTING FURNITURE (scale reference — respect these proportions):\n"
            "- bathtub: 1.5m × 0.7m footprint, 0.6m tall — spans ~83% of shorter wall\n"
            "- vanity: 0.6m × 0.5m footprint, 0.9m tall\n"
            "- toilet: 0.4m × 0.7m footprint, 0.4m tall\n"
        )

        baseline_prompt = _build_prompt("v5", "v4")
        candidate_prompt = _build_prompt("v5", "v5", room_context=room_context)

        all_baseline, all_candidate = [], []
        for idx, room_image in enumerate(room_images):
            print(f"\n{'=' * 60}")
            print(f"  PHOTO {idx + 1}/{len(room_images)}")
            print(f"{'=' * 60}")

            baseline_all, candidate_all = _run_ab_comparison(
                "gen_v5+room_v4 (baseline)",
                "gen_v5+room_v5 (scene data)",
                baseline_prompt,
                candidate_prompt,
                room_image,
                brief=BATHROOM_BRIEF,
                generation_prompt_b=candidate_prompt,
                room_context=room_context,
            )
            all_baseline.extend(baseline_all)
            all_candidate.extend(candidate_all)

        print("\n" + "=" * 60)
        print(f"  SCENE DATA VLM EVAL SUMMARY ({len(room_images)} photos × {NUM_RUNS} runs)")
        print("=" * 60)
        _print_bootstrap_summary("v5+room_v4", "v5+room_v5", all_baseline, all_candidate)
