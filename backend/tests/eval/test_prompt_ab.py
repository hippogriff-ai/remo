# ruff: noqa: E501
"""A/B prompt eval — compare generation prompt versions via direct Gemini calls.

Generates images with baseline and candidate prompt versions,
runs fast eval (CLIP/SSIM), and optionally deep eval (Claude judge).

Usage:
    cd backend
    source ../.env
    export EVAL_MODE=full  # or "fast" for $0 eval only
    .venv/bin/python -m pytest tests/eval/test_prompt_ab.py -x -v -s -m integration

Each run produces one baseline + one candidate image. Run 5+ times for
statistical significance, then use the bootstrap test from PROMPT_TUNING.md §6a.
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


def _run_fast_eval(
    result_image: Image.Image,
    original_image: Image.Image,
    brief: DesignBrief | None = None,
) -> dict:
    """Run fast eval and return scores as dict."""
    try:
        from app.utils.image_eval import run_fast_eval

        fast = run_fast_eval(result_image, original_image, brief=brief, is_edit=False)
        return {
            "clip_text_score": fast.clip_text_score,
            "clip_image_score": fast.clip_image_score,
            "edge_ssim_score": fast.edge_ssim_score,
            "composite_score": fast.composite_score,
            "has_artifacts": fast.has_artifacts,
        }
    except ImportError:
        return {"error": "eval deps not installed"}


async def _run_deep_eval(
    result_image: Image.Image,
    original_image: Image.Image,
    brief: DesignBrief | None = None,
) -> dict:
    """Run deep eval (Claude Vision judge) on PIL Images directly.

    Returns a dict with per-criterion scores (100-point rubric).
    """
    eval_mode = os.environ.get("EVAL_MODE", "off").lower()
    if eval_mode != "full":
        return {"skipped": True}

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
            # Try JPEG first (much smaller than PNG)
            buf = io.BytesIO()
            img_rgb = img.convert("RGB")
            img_rgb.save(buf, format="JPEG", quality=85)
            if buf.tell() <= max_bytes:
                return base64.standard_b64encode(buf.getvalue()).decode("ascii"), "image/jpeg"
            # Resize if still too large
            scale = (max_bytes / buf.tell()) ** 0.5
            new_size = (int(img.width * scale), int(img.height * scale))
            img_resized = img_rgb.resize(new_size, Image.LANCZOS)
            buf = io.BytesIO()
            img_resized.save(buf, format="JPEG", quality=80)
            return base64.standard_b64encode(buf.getvalue()).decode("ascii"), "image/jpeg"

        orig_b64, orig_mime = _pil_to_b64(original_image)
        gen_b64, gen_mime = _pil_to_b64(result_image)

        brief_json = brief.model_dump_json(indent=2) if brief else "{}"
        prompt_text = (
            f"{_GENERATION_RUBRIC}\n\n"
            f"## DesignBrief:\n```json\n{brief_json}\n```\n\n"
            f"{_GENERATION_RESPONSE_FORMAT}"
        )

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

        return {
            "total": total,
            "tag": tag,
            **{c.name: c.score for c in criteria},
            "notes": raw.get("notes", ""),
        }
    except Exception as e:
        return {"error": str(e)}


def _append_result(version: str, scores: dict) -> None:
    """Append eval result to history file."""
    entry = {
        "timestamp": datetime.now(UTC).isoformat(),
        "prompt_version": version,
        "fast_eval": scores,
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
    num_runs: int = NUM_RUNS,
) -> tuple[list[dict], list[dict]]:
    """Run A/B comparison synchronously via asyncio, return (a_scores, b_scores)."""

    async def _run():
        a_all, b_all = [], []
        print(f"\n{'=' * 60}")
        print(f"  {label_a} vs {label_b} ({num_runs} runs)")
        print("=" * 60)

        for i in range(num_runs):
            print(f"\n--- Run {i + 1}/{num_runs} ---")

            print(f"  {label_a}...", end=" ", flush=True)
            img_a = await _generate_image(prompt_a, room_image)
            s_a = _run_fast_eval(img_a, room_image, brief=TEST_BRIEF)
            a_all.append(s_a)
            print(
                f"comp={s_a.get('composite_score', 0):.4f} clip_img={s_a.get('clip_image_score', 0):.4f} clip_txt={s_a.get('clip_text_score', 0):.4f} ssim={s_a.get('edge_ssim_score', 0):.4f}"
            )
            _append_result(label_a, s_a)

            print(f"  {label_b}...", end=" ", flush=True)
            img_b = await _generate_image(prompt_b, room_image)
            s_b = _run_fast_eval(img_b, room_image, brief=TEST_BRIEF)
            b_all.append(s_b)
            print(
                f"comp={s_b.get('composite_score', 0):.4f} clip_img={s_b.get('clip_image_score', 0):.4f} clip_txt={s_b.get('clip_text_score', 0):.4f} ssim={s_b.get('edge_ssim_score', 0):.4f}"
            )
            _append_result(label_b, s_b)

        return a_all, b_all

    return asyncio.get_event_loop().run_until_complete(_run())


def _print_bootstrap_summary(
    label_a: str, label_b: str, a_scores: list[dict], b_scores: list[dict]
) -> None:
    """Print bootstrap significance test for all metrics."""
    import numpy as np

    for metric in ["composite_score", "clip_image_score", "clip_text_score", "edge_ssim_score"]:
        a_vals = np.array([s[metric] for s in a_scores])
        b_vals = np.array([s[metric] for s in b_scores])
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

        min_eff = 0.03 if metric == "edge_ssim_score" else 0.02
        if ci_low > min_eff:
            verdict = "SHIP"
        elif ci_low > 0:
            verdict = "LIKELY_BETTER"
        elif ci_high < 0:
            verdict = "ROLLBACK"
        else:
            verdict = "INCONCLUSIVE"

        print(
            f"  {metric}: {label_a}={a_vals.mean():.4f} {label_b}={b_vals.mean():.4f} delta={delta:+.4f} CI=[{ci_low:+.4f},{ci_high:+.4f}] P={p_better:.3f} → {verdict}"
        )


class TestPromptAB:
    """A/B test generation prompt versions."""

    @pytest.mark.asyncio
    async def test_baseline_vs_candidate(self):
        """Generate with baseline (v2+v2) and candidate (v5+v4), compare fast eval.

        Runs NUM_RUNS times per version for statistical significance.
        """
        room_image = _make_room_image()

        # Build prompts
        baseline_prompt = _build_prompt("v2", "v2")
        candidate_prompt = _build_prompt("v5", "v4")

        print("\n" + "=" * 60)
        print(f"  PROMPT A/B EVAL ({NUM_RUNS} runs per version)")
        print("=" * 60)

        baseline_composites = []
        candidate_composites = []

        for i in range(NUM_RUNS):
            print(f"\n--- Run {i + 1}/{NUM_RUNS} ---")

            # Generate baseline
            print("  Baseline (gen=v2, room_pres=v2)...", end=" ", flush=True)
            baseline_image = await _generate_image(baseline_prompt, room_image)
            baseline_scores = _run_fast_eval(baseline_image, room_image, brief=TEST_BRIEF)
            b_comp = baseline_scores.get("composite_score", 0)
            baseline_composites.append(b_comp)
            print(
                f"composite={b_comp:.4f} clip_img={baseline_scores.get('clip_image_score', 0):.4f} edge_ssim={baseline_scores.get('edge_ssim_score', 0):.4f}"
            )
            _append_result("baseline_v2+v2", baseline_scores)

            # Generate candidate
            print("  Candidate (gen=v5, room_pres=v4)...", end=" ", flush=True)
            candidate_image = await _generate_image(candidate_prompt, room_image)
            candidate_scores = _run_fast_eval(candidate_image, room_image, brief=TEST_BRIEF)
            c_comp = candidate_scores.get("composite_score", 0)
            candidate_composites.append(c_comp)
            print(
                f"composite={c_comp:.4f} clip_img={candidate_scores.get('clip_image_score', 0):.4f} edge_ssim={candidate_scores.get('edge_ssim_score', 0):.4f}"
            )
            _append_result("candidate_v5+v4", candidate_scores)

        # Summary
        import numpy as np

        b_arr = np.array(baseline_composites)
        c_arr = np.array(candidate_composites)

        print("\n" + "=" * 60)
        print("  SUMMARY")
        print("=" * 60)
        print(
            f"  Baseline  — mean={b_arr.mean():.4f}  std={b_arr.std():.4f}  scores={[f'{x:.4f}' for x in baseline_composites]}"
        )
        print(
            f"  Candidate — mean={c_arr.mean():.4f}  std={c_arr.std():.4f}  scores={[f'{x:.4f}' for x in candidate_composites]}"
        )
        print(f"  Delta (candidate - baseline): {c_arr.mean() - b_arr.mean():+.4f}")

        # Bootstrap significance test
        n_bootstrap = 10000
        boot_diffs = []
        for _ in range(n_bootstrap):
            b_sample = np.random.choice(b_arr, size=len(b_arr), replace=True)
            c_sample = np.random.choice(c_arr, size=len(c_arr), replace=True)
            boot_diffs.append(c_sample.mean() - b_sample.mean())
        boot_diffs = np.array(boot_diffs)
        ci_low = np.percentile(boot_diffs, 5)
        ci_high = np.percentile(boot_diffs, 95)
        p_better = np.mean(boot_diffs > 0)

        if ci_low > 0.02:
            verdict = "SHIP"
        elif ci_low > 0:
            verdict = "LIKELY_BETTER"
        elif ci_high < 0:
            verdict = "ROLLBACK"
        else:
            verdict = "INCONCLUSIVE"

        print(f"\n  Bootstrap test (90% CI): [{ci_low:+.4f}, {ci_high:+.4f}]")
        print(f"  P(candidate better): {p_better:.3f}")
        print(f"  Verdict: {verdict}")
        print("\n  Results in: tests/eval/prompt_ab_history.jsonl")
        print("=" * 60)

    @pytest.mark.asyncio
    async def test_bisect_room_preservation_only(self):
        """Bisect: test gen_v2 + room_pres_v4 (room preservation only, no gen changes)."""
        room_image = _make_room_image()

        baseline_prompt = _build_prompt("v2", "v2")
        bisect_prompt = _build_prompt("v2", "v4")

        print("\n" + "=" * 60)
        print(f"  BISECT: room_preservation_v4 only ({NUM_RUNS} runs)")
        print("=" * 60)

        baseline_all, bisect_all = [], []
        for i in range(NUM_RUNS):
            print(f"\n--- Run {i + 1}/{NUM_RUNS} ---")

            print("  Baseline (v2+v2)...", end=" ", flush=True)
            b_img = await _generate_image(baseline_prompt, room_image)
            b_scores = _run_fast_eval(b_img, room_image, brief=TEST_BRIEF)
            baseline_all.append(b_scores)
            print(
                f"comp={b_scores['composite_score']:.4f} clip_img={b_scores['clip_image_score']:.4f} clip_txt={b_scores['clip_text_score']:.4f} ssim={b_scores['edge_ssim_score']:.4f}"
            )
            _append_result("bisect_baseline_v2+v2", b_scores)

            print("  Bisect (v2+v4)...", end=" ", flush=True)
            c_img = await _generate_image(bisect_prompt, room_image)
            c_scores = _run_fast_eval(c_img, room_image, brief=TEST_BRIEF)
            bisect_all.append(c_scores)
            print(
                f"comp={c_scores['composite_score']:.4f} clip_img={c_scores['clip_image_score']:.4f} clip_txt={c_scores['clip_text_score']:.4f} ssim={c_scores['edge_ssim_score']:.4f}"
            )
            _append_result("bisect_v2+v4", c_scores)

        print("\n" + "=" * 60)
        print("  BISECT SUMMARY")
        print("=" * 60)
        _print_bootstrap_summary("v2+v2", "v2+v4", baseline_all, bisect_all)

    @pytest.mark.asyncio
    async def test_brief_emphasis(self):
        """Test gen_v6 (brief emphasis) vs baseline gen_v2, both with room_pres_v2.

        gen_v6 adds only a brief emphasis parenthetical to improve CLIP text
        without the distracting camera/detail instructions from v3-v5.
        """
        room_image = _make_room_image()

        baseline_prompt = _build_prompt("v2", "v2")
        v6_prompt = _build_prompt("v6", "v2")

        print("\n" + "=" * 60)
        print(f"  BRIEF EMPHASIS: gen_v6 + room_v2 ({NUM_RUNS} runs)")
        print("=" * 60)

        baseline_all, v6_all = [], []
        for i in range(NUM_RUNS):
            print(f"\n--- Run {i + 1}/{NUM_RUNS} ---")

            print("  Baseline (v2+v2)...", end=" ", flush=True)
            b_img = await _generate_image(baseline_prompt, room_image)
            b_scores = _run_fast_eval(b_img, room_image, brief=TEST_BRIEF)
            baseline_all.append(b_scores)
            print(
                f"comp={b_scores['composite_score']:.4f} clip_img={b_scores['clip_image_score']:.4f} clip_txt={b_scores['clip_text_score']:.4f} ssim={b_scores['edge_ssim_score']:.4f}"
            )
            _append_result("v6_baseline_v2+v2", b_scores)

            print("  gen_v6 (v6+v2)...", end=" ", flush=True)
            c_img = await _generate_image(v6_prompt, room_image)
            c_scores = _run_fast_eval(c_img, room_image, brief=TEST_BRIEF)
            v6_all.append(c_scores)
            print(
                f"comp={c_scores['composite_score']:.4f} clip_img={c_scores['clip_image_score']:.4f} clip_txt={c_scores['clip_text_score']:.4f} ssim={c_scores['edge_ssim_score']:.4f}"
            )
            _append_result("v6_candidate_v6+v2", c_scores)

        print("\n" + "=" * 60)
        print("  BRIEF EMPHASIS SUMMARY")
        print("=" * 60)
        _print_bootstrap_summary("v2+v2", "v6+v2", baseline_all, v6_all)

    @pytest.mark.asyncio
    @pytest.mark.skipif(
        not os.environ.get("ANTHROPIC_API_KEY"),
        reason="ANTHROPIC_API_KEY not set (needed for deep eval)",
    )
    async def test_deep_eval_baseline_vs_candidate(self):
        """Deep eval (Claude Vision judge) for v5+v4 vs baseline v2+v2.

        Uses the 100-point, 9-criterion rubric from design_eval.py.
        This captures qualitative differences (photorealism, style adherence,
        design coherence) that CLIP/SSIM cannot measure.
        """
        import numpy as np

        room_image = _make_room_image()
        baseline_prompt = _build_prompt("v2", "v2")
        candidate_prompt = _build_prompt("v5", "v4")

        print("\n" + "=" * 60)
        print(f"  DEEP EVAL: v2+v2 vs v5+v4 ({NUM_RUNS} runs)")
        print("=" * 60)

        baseline_deep, candidate_deep = [], []

        for i in range(NUM_RUNS):
            print(f"\n--- Run {i + 1}/{NUM_RUNS} ---")

            # Generate + deep eval baseline
            print("  Baseline (v2+v2)...", end=" ", flush=True)
            b_img = await _generate_image(baseline_prompt, room_image)
            print("generated...", end=" ", flush=True)
            b_deep = await _run_deep_eval(b_img, room_image, brief=TEST_BRIEF)
            baseline_deep.append(b_deep)
            if "total" in b_deep:
                print(f"total={b_deep['total']} tag={b_deep['tag']}")
                _append_result("deep_baseline_v2+v2", b_deep)
            else:
                print(f"SKIP: {b_deep}")

            # Generate + deep eval candidate
            print("  Candidate (v5+v4)...", end=" ", flush=True)
            c_img = await _generate_image(candidate_prompt, room_image)
            print("generated...", end=" ", flush=True)
            c_deep = await _run_deep_eval(c_img, room_image, brief=TEST_BRIEF)
            candidate_deep.append(c_deep)
            if "total" in c_deep:
                print(f"total={c_deep['total']} tag={c_deep['tag']}")
                _append_result("deep_candidate_v5+v4", c_deep)
            else:
                print(f"SKIP: {c_deep}")

        # Summary
        b_totals = [d["total"] for d in baseline_deep if "total" in d]
        c_totals = [d["total"] for d in candidate_deep if "total" in d]

        print("\n" + "=" * 60)
        print("  DEEP EVAL SUMMARY")
        print("=" * 60)

        if not b_totals or not c_totals:
            print("  Insufficient data for comparison")
            return

        b_arr = np.array(b_totals, dtype=float)
        c_arr = np.array(c_totals, dtype=float)
        delta = c_arr.mean() - b_arr.mean()

        print(f"  Baseline:  mean={b_arr.mean():.1f} std={b_arr.std():.1f} scores={b_totals}")
        print(f"  Candidate: mean={c_arr.mean():.1f} std={c_arr.std():.1f} scores={c_totals}")
        print(f"  Delta: {delta:+.1f}")

        # Per-criterion comparison
        criteria = [
            "photorealism",
            "style_adherence",
            "color_palette",
            "room_preservation",
            "furniture_scale",
            "lighting",
            "design_coherence",
            "brief_compliance",
            "keep_items",
        ]
        print("\n  Per-criterion breakdown:")
        for crit in criteria:
            b_vals = [d.get(crit, 0) for d in baseline_deep if "total" in d]
            c_vals = [d.get(crit, 0) for d in candidate_deep if "total" in d]
            if b_vals and c_vals:
                b_mean = np.mean(b_vals)
                c_mean = np.mean(c_vals)
                print(
                    f"    {crit:25s}: baseline={b_mean:.1f}  candidate={c_mean:.1f}  delta={c_mean - b_mean:+.1f}"
                )

        # Bootstrap on totals
        boot_diffs = []
        for _ in range(10000):
            b_sample = np.random.choice(b_arr, size=len(b_arr), replace=True)
            c_sample = np.random.choice(c_arr, size=len(c_arr), replace=True)
            boot_diffs.append(c_sample.mean() - b_sample.mean())
        boot_diffs = np.array(boot_diffs)
        ci_low = np.percentile(boot_diffs, 5)
        ci_high = np.percentile(boot_diffs, 95)
        p_better = np.mean(boot_diffs > 0)

        if ci_low > 3:
            verdict = "SHIP"
        elif ci_low > 0:
            verdict = "LIKELY_BETTER"
        elif ci_high < 0:
            verdict = "ROLLBACK"
        else:
            verdict = "INCONCLUSIVE"

        print(f"\n  Bootstrap (90% CI): [{ci_low:+.1f}, {ci_high:+.1f}]")
        print(f"  P(candidate better): {p_better:.3f}")
        print(f"  Verdict: {verdict}")
        print("=" * 60)

    @pytest.mark.asyncio
    async def test_ics_restructure(self):
        """Test gen_v7 (ICS framework restructure) vs baseline gen_v5, both with room_pres_v4.

        v7 restructures v5 into labeled sections (IMAGE TYPE, ROOM IDENTITY, DESIGN BRIEF,
        MATERIALS, LIVED-IN DETAILS, SPATIAL CONTEXT, ROOM PRESERVATION, OUTPUT RULES) with
        each section under 25 words for better Gemini parsing.

        Target metric: photorealism (13/15 → 15/15 deep eval).
        """
        room_image = _make_room_image()

        baseline_prompt = _build_prompt("v5", "v4")
        candidate_prompt = _build_prompt("v7", "v4")

        baseline_all, candidate_all = [], []

        print("\n" + "=" * 60)
        print(f"  ICS RESTRUCTURE: gen_v7+room_v4 vs gen_v5+room_v4 ({NUM_RUNS} runs)")
        print("=" * 60)

        for i in range(NUM_RUNS):
            print(f"\n--- Run {i + 1}/{NUM_RUNS} ---")

            print("  Baseline (v5+v4)...", end=" ", flush=True)
            b_img = await _generate_image(baseline_prompt, room_image)
            b_scores = _run_fast_eval(b_img, room_image, brief=TEST_BRIEF)
            baseline_all.append(b_scores)
            print(
                f"comp={b_scores['composite_score']:.4f} clip_img={b_scores['clip_image_score']:.4f} clip_txt={b_scores['clip_text_score']:.4f} ssim={b_scores['edge_ssim_score']:.4f}"
            )
            _append_result("ics_baseline_v5+v4", b_scores)

            print("  Candidate (v7+v4)...", end=" ", flush=True)
            c_img = await _generate_image(candidate_prompt, room_image)
            c_scores = _run_fast_eval(c_img, room_image, brief=TEST_BRIEF)
            candidate_all.append(c_scores)
            print(
                f"comp={c_scores['composite_score']:.4f} clip_img={c_scores['clip_image_score']:.4f} clip_txt={c_scores['clip_text_score']:.4f} ssim={c_scores['edge_ssim_score']:.4f}"
            )
            _append_result("ics_candidate_v7+v4", c_scores)

        print("\n" + "=" * 60)
        print("  ICS RESTRUCTURE SUMMARY")
        print("=" * 60)
        _print_bootstrap_summary("v5+v4", "v7+v4", baseline_all, candidate_all)

    @pytest.mark.asyncio
    async def test_practical_lighting(self):
        """Test gen_v8 (practical lighting directives) vs baseline gen_v5, both with room_pres_v4.

        v8 adds negative lighting exclusions ("No flat overhead recessed lighting",
        "No flash photography appearance") plus positive practical source guidance
        (visible lamps, pendant fixtures, directional shadows, 2700-3000K).

        Target metric: photorealism (13/15 → 15/15 deep eval), lighting (9/10 → 10/10).
        """
        room_image = _make_room_image()

        baseline_prompt = _build_prompt("v5", "v4")
        candidate_prompt = _build_prompt("v8", "v4")

        baseline_all, candidate_all = [], []

        print("\n" + "=" * 60)
        print(f"  PRACTICAL LIGHTING: gen_v8+room_v4 vs gen_v5+room_v4 ({NUM_RUNS} runs)")
        print("=" * 60)

        for i in range(NUM_RUNS):
            print(f"\n--- Run {i + 1}/{NUM_RUNS} ---")

            print("  Baseline (v5+v4)...", end=" ", flush=True)
            b_img = await _generate_image(baseline_prompt, room_image)
            b_scores = _run_fast_eval(b_img, room_image, brief=TEST_BRIEF)
            baseline_all.append(b_scores)
            print(
                f"comp={b_scores['composite_score']:.4f} clip_img={b_scores['clip_image_score']:.4f} clip_txt={b_scores['clip_text_score']:.4f} ssim={b_scores['edge_ssim_score']:.4f}"
            )
            _append_result("light_baseline_v5+v4", b_scores)

            print("  Candidate (v8+v4)...", end=" ", flush=True)
            c_img = await _generate_image(candidate_prompt, room_image)
            c_scores = _run_fast_eval(c_img, room_image, brief=TEST_BRIEF)
            candidate_all.append(c_scores)
            print(
                f"comp={c_scores['composite_score']:.4f} clip_img={c_scores['clip_image_score']:.4f} clip_txt={c_scores['clip_text_score']:.4f} ssim={c_scores['edge_ssim_score']:.4f}"
            )
            _append_result("light_candidate_v8+v4", c_scores)

        print("\n" + "=" * 60)
        print("  PRACTICAL LIGHTING SUMMARY")
        print("=" * 60)
        _print_bootstrap_summary("v5+v4", "v8+v4", baseline_all, candidate_all)

    @pytest.mark.asyncio
    @pytest.mark.skipif(
        not os.environ.get("ANTHROPIC_API_KEY"),
        reason="ANTHROPIC_API_KEY not set (needed for deep eval)",
    )
    async def test_deep_eval_practical_lighting(self):
        """Deep eval for gen_v8+v4 (practical lighting) vs gen_v5+v4 (current baseline).

        Uses the 100-point, 9-criterion rubric. Specifically targets photorealism
        (13/15, 2pt headroom) and lighting (9/10, 1pt headroom).
        """
        import numpy as np

        room_image = _make_room_image()
        baseline_prompt = _build_prompt("v5", "v4")
        candidate_prompt = _build_prompt("v8", "v4")

        print("\n" + "=" * 60)
        print(f"  DEEP EVAL LIGHTING: v5+v4 vs v8+v4 ({NUM_RUNS} runs)")
        print("=" * 60)

        baseline_deep, candidate_deep = [], []

        for i in range(NUM_RUNS):
            print(f"\n--- Run {i + 1}/{NUM_RUNS} ---")

            print("  Baseline (v5+v4)...", end=" ", flush=True)
            b_img = await _generate_image(baseline_prompt, room_image)
            print("generated...", end=" ", flush=True)
            b_deep = await _run_deep_eval(b_img, room_image, brief=TEST_BRIEF)
            baseline_deep.append(b_deep)
            if "total" in b_deep:
                print(f"total={b_deep['total']} tag={b_deep['tag']}")
                _append_result("deep_light_baseline_v5+v4", b_deep)
            else:
                print(f"SKIP: {b_deep}")

            print("  Candidate (v8+v4)...", end=" ", flush=True)
            c_img = await _generate_image(candidate_prompt, room_image)
            print("generated...", end=" ", flush=True)
            c_deep = await _run_deep_eval(c_img, room_image, brief=TEST_BRIEF)
            candidate_deep.append(c_deep)
            if "total" in c_deep:
                print(f"total={c_deep['total']} tag={c_deep['tag']}")
                _append_result("deep_light_candidate_v8+v4", c_deep)
            else:
                print(f"SKIP: {c_deep}")

        # Summary
        b_totals = [d["total"] for d in baseline_deep if "total" in d]
        c_totals = [d["total"] for d in candidate_deep if "total" in d]

        print("\n" + "=" * 60)
        print("  DEEP EVAL LIGHTING SUMMARY")
        print("=" * 60)

        if not b_totals or not c_totals:
            print("  Insufficient data for comparison")
            return

        b_arr = np.array(b_totals, dtype=float)
        c_arr = np.array(c_totals, dtype=float)
        delta = c_arr.mean() - b_arr.mean()

        print(f"  Baseline:  mean={b_arr.mean():.1f} std={b_arr.std():.1f} scores={b_totals}")
        print(f"  Candidate: mean={c_arr.mean():.1f} std={c_arr.std():.1f} scores={c_totals}")
        print(f"  Delta: {delta:+.1f}")

        # Per-criterion comparison
        criteria = [
            "photorealism",
            "style_adherence",
            "color_palette",
            "room_preservation",
            "furniture_scale",
            "lighting",
            "design_coherence",
            "brief_compliance",
            "keep_items",
        ]
        print("\n  Per-criterion breakdown:")
        for crit in criteria:
            b_vals = [d.get(crit, 0) for d in baseline_deep if "total" in d]
            c_vals = [d.get(crit, 0) for d in candidate_deep if "total" in d]
            if b_vals and c_vals:
                b_mean = np.mean(b_vals)
                c_mean = np.mean(c_vals)
                print(
                    f"    {crit:25s}: baseline={b_mean:.1f}  candidate={c_mean:.1f}  delta={c_mean - b_mean:+.1f}"
                )

        # Bootstrap on totals
        boot_diffs = []
        for _ in range(10000):
            b_sample = np.random.choice(b_arr, size=len(b_arr), replace=True)
            c_sample = np.random.choice(c_arr, size=len(c_arr), replace=True)
            boot_diffs.append(c_sample.mean() - b_sample.mean())
        boot_diffs = np.array(boot_diffs)
        ci_low = np.percentile(boot_diffs, 5)
        ci_high = np.percentile(boot_diffs, 95)
        p_better = np.mean(boot_diffs > 0)

        if ci_low > 3:
            verdict = "SHIP"
        elif ci_low > 0:
            verdict = "LIKELY_BETTER"
        elif ci_high < 0:
            verdict = "ROLLBACK"
        else:
            verdict = "INCONCLUSIVE"

        print(f"\n  Bootstrap (90% CI): [{ci_low:+.1f}, {ci_high:+.1f}]")
        print(f"  P(candidate better): {p_better:.3f}")
        print(f"  Verdict: {verdict}")
        print("=" * 60)

    @pytest.mark.asyncio
    @pytest.mark.skipif(
        not os.environ.get("ANTHROPIC_API_KEY"),
        reason="ANTHROPIC_API_KEY not set (needed for deep eval)",
    )
    async def test_deep_eval_ics_restructure(self):
        """Deep eval for gen_v7+v4 (ICS restructure) vs gen_v5+v4 (current baseline).

        Uses the 100-point, 9-criterion rubric. Specifically targets photorealism
        criterion (13/15 in baseline, 2pt headroom).
        """
        import numpy as np

        room_image = _make_room_image()
        baseline_prompt = _build_prompt("v5", "v4")
        candidate_prompt = _build_prompt("v7", "v4")

        print("\n" + "=" * 60)
        print(f"  DEEP EVAL ICS: v5+v4 vs v7+v4 ({NUM_RUNS} runs)")
        print("=" * 60)

        baseline_deep, candidate_deep = [], []

        for i in range(NUM_RUNS):
            print(f"\n--- Run {i + 1}/{NUM_RUNS} ---")

            print("  Baseline (v5+v4)...", end=" ", flush=True)
            b_img = await _generate_image(baseline_prompt, room_image)
            print("generated...", end=" ", flush=True)
            b_deep = await _run_deep_eval(b_img, room_image, brief=TEST_BRIEF)
            baseline_deep.append(b_deep)
            if "total" in b_deep:
                print(f"total={b_deep['total']} tag={b_deep['tag']}")
                _append_result("deep_ics_baseline_v5+v4", b_deep)
            else:
                print(f"SKIP: {b_deep}")

            print("  Candidate (v7+v4)...", end=" ", flush=True)
            c_img = await _generate_image(candidate_prompt, room_image)
            print("generated...", end=" ", flush=True)
            c_deep = await _run_deep_eval(c_img, room_image, brief=TEST_BRIEF)
            candidate_deep.append(c_deep)
            if "total" in c_deep:
                print(f"total={c_deep['total']} tag={c_deep['tag']}")
                _append_result("deep_ics_candidate_v7+v4", c_deep)
            else:
                print(f"SKIP: {c_deep}")

        # Summary
        b_totals = [d["total"] for d in baseline_deep if "total" in d]
        c_totals = [d["total"] for d in candidate_deep if "total" in d]

        print("\n" + "=" * 60)
        print("  DEEP EVAL ICS SUMMARY")
        print("=" * 60)

        if not b_totals or not c_totals:
            print("  Insufficient data for comparison")
            return

        b_arr = np.array(b_totals, dtype=float)
        c_arr = np.array(c_totals, dtype=float)
        delta = c_arr.mean() - b_arr.mean()

        print(f"  Baseline:  mean={b_arr.mean():.1f} std={b_arr.std():.1f} scores={b_totals}")
        print(f"  Candidate: mean={c_arr.mean():.1f} std={c_arr.std():.1f} scores={c_totals}")
        print(f"  Delta: {delta:+.1f}")

        # Per-criterion comparison
        criteria = [
            "photorealism",
            "style_adherence",
            "color_palette",
            "room_preservation",
            "furniture_scale",
            "lighting",
            "design_coherence",
            "brief_compliance",
            "keep_items",
        ]
        print("\n  Per-criterion breakdown:")
        for crit in criteria:
            b_vals = [d.get(crit, 0) for d in baseline_deep if "total" in d]
            c_vals = [d.get(crit, 0) for d in candidate_deep if "total" in d]
            if b_vals and c_vals:
                b_mean = np.mean(b_vals)
                c_mean = np.mean(c_vals)
                print(
                    f"    {crit:25s}: baseline={b_mean:.1f}  candidate={c_mean:.1f}  delta={c_mean - b_mean:+.1f}"
                )

        # Bootstrap on totals
        boot_diffs = []
        for _ in range(10000):
            b_sample = np.random.choice(b_arr, size=len(b_arr), replace=True)
            c_sample = np.random.choice(c_arr, size=len(c_arr), replace=True)
            boot_diffs.append(c_sample.mean() - b_sample.mean())
        boot_diffs = np.array(boot_diffs)
        ci_low = np.percentile(boot_diffs, 5)
        ci_high = np.percentile(boot_diffs, 95)
        p_better = np.mean(boot_diffs > 0)

        if ci_low > 3:
            verdict = "SHIP"
        elif ci_low > 0:
            verdict = "LIKELY_BETTER"
        elif ci_high < 0:
            verdict = "ROLLBACK"
        else:
            verdict = "INCONCLUSIVE"

        print(f"\n  Bootstrap (90% CI): [{ci_low:+.1f}, {ci_high:+.1f}]")
        print(f"  P(candidate better): {p_better:.3f}")
        print(f"  Verdict: {verdict}")
        print("=" * 60)

    def test_scene_data_fast(self):
        """Fast eval: gen_v5+room_v5 (structured scene data) vs gen_v5+room_v4 (baseline).

        room_preservation_v5 adds DIMENSIONAL CONSTRAINTS section.
        _format_room_context() now outputs structured sections (ROOM GEOMETRY,
        WALLS with compass, FIXED OPENINGS, EXISTING FURNITURE with proportions).

        Uses real bathroom photos. Runs each photo through the A/B comparison
        for stronger signal across different angles/compositions.

        Target: Room Preservation +0.5pt, Furniture Scale +0.5-1.0pt (deep eval).
        """
        room_images = _make_room_images()

        # Structured room context matching the real bathroom fixture photos
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
            print(f"\n{'='*60}")
            print(f"  PHOTO {idx + 1}/{len(room_images)}")
            print(f"{'='*60}")

            baseline_all, candidate_all = _run_ab_comparison(
                "gen_v5+room_v4 (baseline)",
                "gen_v5+room_v5 (scene data)",
                baseline_prompt,
                candidate_prompt,
                room_image,
            )
            all_baseline.extend(baseline_all)
            all_candidate.extend(candidate_all)

        print("\n" + "=" * 60)
        print(f"  SCENE DATA FAST EVAL SUMMARY ({len(room_images)} photos × {NUM_RUNS} runs)")
        print("=" * 60)
        _print_bootstrap_summary("v5+room_v4", "v5+room_v5", all_baseline, all_candidate)

    @pytest.mark.asyncio
    @pytest.mark.skipif(
        not os.environ.get("ANTHROPIC_API_KEY"),
        reason="ANTHROPIC_API_KEY not set (needed for deep eval)",
    )
    async def test_scene_data_deep(self):
        """Deep eval: gen_v5+room_v5 (structured scene data) vs gen_v5+room_v4 (baseline).

        Uses 100-point rubric with real bathroom photos. Specifically targets
        room_preservation and furniture_scale in a tight bathroom where spatial
        accuracy matters most.
        """
        import numpy as np

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

        baseline_deep, candidate_deep = [], []

        for photo_idx, room_image in enumerate(room_images):
            print(f"\n{'='*60}")
            print(f"  DEEP EVAL SCENE DATA: photo {photo_idx + 1}/{len(room_images)}, "
                  f"v5+room_v4 vs v5+room_v5 ({NUM_RUNS} runs)")
            print("=" * 60)

            for i in range(NUM_RUNS):
                print(f"\n--- Photo {photo_idx + 1}, Run {i + 1}/{NUM_RUNS} ---")

                print("  Baseline (v5+room_v4)...", end=" ", flush=True)
                b_img = await _generate_image(baseline_prompt, room_image)
                print("generated...", end=" ", flush=True)
                b_deep = await _run_deep_eval(b_img, room_image, brief=BATHROOM_BRIEF)
                baseline_deep.append(b_deep)
                if "total" in b_deep:
                    print(f"total={b_deep['total']} tag={b_deep['tag']}")
                    _append_result("deep_scene_baseline_v5+room_v4", b_deep)
                else:
                    print(f"SKIP: {b_deep}")

                print("  Candidate (v5+room_v5)...", end=" ", flush=True)
                c_img = await _generate_image(candidate_prompt, room_image)
                print("generated...", end=" ", flush=True)
                c_deep = await _run_deep_eval(c_img, room_image, brief=BATHROOM_BRIEF)
                candidate_deep.append(c_deep)
                if "total" in c_deep:
                    print(f"total={c_deep['total']} tag={c_deep['tag']}")
                    _append_result("deep_scene_candidate_v5+room_v5", c_deep)
                else:
                    print(f"SKIP: {c_deep}")

        # Summary
        b_totals = [d["total"] for d in baseline_deep if "total" in d]
        c_totals = [d["total"] for d in candidate_deep if "total" in d]

        print("\n" + "=" * 60)
        print("  DEEP EVAL SCENE DATA SUMMARY")
        print("=" * 60)

        if not b_totals or not c_totals:
            print("  Insufficient data for comparison")
            return

        b_arr = np.array(b_totals, dtype=float)
        c_arr = np.array(c_totals, dtype=float)
        delta = c_arr.mean() - b_arr.mean()

        print(f"  Baseline:  mean={b_arr.mean():.1f} std={b_arr.std():.1f} scores={b_totals}")
        print(f"  Candidate: mean={c_arr.mean():.1f} std={c_arr.std():.1f} scores={c_totals}")
        print(f"  Delta: {delta:+.1f}")

        # Per-criterion comparison
        criteria = [
            "photorealism",
            "style_adherence",
            "color_palette",
            "room_preservation",
            "furniture_scale",
            "lighting",
            "design_coherence",
            "brief_compliance",
            "keep_items",
        ]
        print("\n  Per-criterion breakdown:")
        for crit in criteria:
            b_vals = [d.get(crit, 0) for d in baseline_deep if "total" in d]
            c_vals = [d.get(crit, 0) for d in candidate_deep if "total" in d]
            if b_vals and c_vals:
                b_mean = np.mean(b_vals)
                c_mean = np.mean(c_vals)
                print(
                    f"    {crit:25s}: baseline={b_mean:.1f}  candidate={c_mean:.1f}  delta={c_mean - b_mean:+.1f}"
                )

        # Bootstrap on totals
        boot_diffs = []
        for _ in range(10000):
            b_sample = np.random.choice(b_arr, size=len(b_arr), replace=True)
            c_sample = np.random.choice(c_arr, size=len(c_arr), replace=True)
            boot_diffs.append(c_sample.mean() - b_sample.mean())
        boot_diffs = np.array(boot_diffs)
        ci_low = np.percentile(boot_diffs, 5)
        ci_high = np.percentile(boot_diffs, 95)
        p_better = np.mean(boot_diffs > 0)

        if ci_low > 3:
            verdict = "SHIP"
        elif ci_low > 0:
            verdict = "LIKELY_BETTER"
        elif ci_high < 0:
            verdict = "ROLLBACK"
        else:
            verdict = "INCONCLUSIVE"

        print(f"\n  Bootstrap (90% CI): [{ci_low:+.1f}, {ci_high:+.1f}]")
        print(f"  P(candidate better): {p_better:.3f}")
        print(f"  Verdict: {verdict}")
        print("=" * 60)
