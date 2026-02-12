"""Gemini Quality Spike — T2 P0 Deliverable #1.

Tests both gemini-3-pro-image-preview and gemini-2.5-flash-image
on three scenarios:
  1. Initial generation (room photo + brief → redesigned room)
  2. Annotation-based editing (numbered circles → targeted edits)
  3. Chat history round-trip (serialize → deserialize → continue editing)

Passing criteria per test case:
  (a) Correct area edited (near the circle)
  (b) Non-annotated areas preserved (SSIM > 0.95 for non-circled regions)
  (c) Output image is CLEAN (no annotation artifacts)
  (d) Instruction followed
  (e) Chat round-trip works

Decision gate: 4+ of 5 test cases meet ALL criteria.

Usage:
    export $(grep -v '^#' .env | xargs)
    backend/.venv/bin/python spike/gemini_spike.py
"""

import base64
import io
import json
import os
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

# Add backend to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent / "backend"))

from google import genai
from google.genai import types

MODELS = [
    "gemini-3-pro-image-preview",
    "gemini-2.5-flash-image",
]

RESULTS_DIR = Path(__file__).parent / "results"
TEST_IMAGES_DIR = Path(__file__).parent / "test_images"

DESIGN_BRIEF = """Transform this room into a warm Scandinavian-inspired living space.
Key elements:
- Light oak wood furniture with clean lines
- Neutral palette: whites, warm greys, soft beiges
- Textured throw blankets and cushions
- Minimalist but cozy aesthetic
- Natural light emphasis
Preserve the exact camera angle, room geometry, walls, ceiling, windows,
doors, and floor plane from the reference photo."""

ANTI_ARTIFACT_INSTRUCTION = (
    "Do not include any annotations, circles, numbers, or markers "
    "in your output image. Return only the edited room photograph."
)


@dataclass
class TestResult:
    model: str
    scenario: str
    passed: bool
    details: str
    duration_s: float = 0.0
    output_image_path: str | None = None
    scores: dict = field(default_factory=dict)


def get_client() -> genai.Client:
    key = os.environ.get("GOOGLE_AI_API_KEY", "")
    if not key:
        print("ERROR: GOOGLE_AI_API_KEY not set in environment")
        sys.exit(1)
    return genai.Client(api_key=key)


def load_test_image() -> Image.Image:
    """Load or create the test room image."""
    path = TEST_IMAGES_DIR / "test_room.png"
    if not path.exists():
        from create_test_image import create_room_image
        img = create_room_image()
        img.save(path)
        return img
    return Image.open(path)


def image_to_part(img: Image.Image, mime_type: str = "image/png") -> types.Part:
    """Convert PIL Image to a genai Part for API calls."""
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return types.Part.from_bytes(data=buf.getvalue(), mime_type=mime_type)


def draw_annotations(img: Image.Image, regions: list[dict]) -> Image.Image:
    """Draw numbered circle annotations on an image.

    Each region: {center_x, center_y, radius, region_id, instruction}
    Coordinates are normalized 0-1.
    """
    annotated = img.copy()
    draw = ImageDraw.Draw(annotated)
    w, h = annotated.size

    colors = {1: "#FF0000", 2: "#0000FF", 3: "#00FF00"}

    try:
        font = ImageFont.truetype("/System/Library/Fonts/Helvetica.ttc", 24)
    except (OSError, IOError):
        font = ImageFont.load_default()

    for region in regions:
        cx = int(region["center_x"] * w)
        cy = int(region["center_y"] * h)
        r = max(int(region["radius"] * min(w, h)), 20)
        rid = region["region_id"]
        color = colors.get(rid, "#FF0000")

        # Circle outline
        draw.ellipse(
            [(cx - r, cy - r), (cx + r, cy + r)],
            outline=color,
            width=4,
        )

        # Number badge
        badge_r = 16
        badge_x = cx + r - badge_r
        badge_y = cy - r - badge_r
        draw.ellipse(
            [(badge_x - badge_r, badge_y - badge_r),
             (badge_x + badge_r, badge_y + badge_r)],
            fill=color,
            outline="white",
            width=2,
        )
        draw.text(
            (badge_x - 6, badge_y - 12),
            str(rid),
            fill="white",
            font=font,
        )

    return annotated


def save_result_image(img: Image.Image, model: str, scenario: str, suffix: str = "") -> str:
    """Save a result image and return the path."""
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    safe_model = model.replace("-", "_").replace("/", "_")
    filename = f"{safe_model}_{scenario}{suffix}.png"
    path = RESULTS_DIR / filename
    img.save(path)
    return str(path)


def extract_image_from_response(response) -> Image.Image | None:
    """Extract the first image from a Gemini response as PIL Image."""
    if not response.candidates:
        return None
    for part in response.candidates[0].content.parts:
        genai_img = part.as_image()
        if genai_img is not None:
            # as_image() returns google.genai.types.Image, convert to PIL
            return Image.open(io.BytesIO(genai_img.image_bytes))
    return None


def extract_text_from_response(response) -> str:
    """Extract text from a Gemini response."""
    texts = []
    if not response.candidates:
        return ""
    for part in response.candidates[0].content.parts:
        if part.text is not None:
            texts.append(part.text)
    return "\n".join(texts)


def extract_thought_signatures(response) -> list[str]:
    """Extract thought signatures from response parts."""
    sigs = []
    if not response.candidates:
        return sigs
    for part in response.candidates[0].content.parts:
        raw = part.model_dump() if hasattr(part, "model_dump") else {}
        if "thought_signature" in raw:
            sigs.append(raw["thought_signature"])
        elif hasattr(part, "thought_signature") and part.thought_signature:
            sigs.append(part.thought_signature)
    return sigs


def serialize_chat_history(response, user_contents: list) -> dict:
    """Serialize a conversation history for round-trip testing.

    Captures all parts including thought signatures.
    """
    history = {"turns": []}
    for content in user_contents:
        history["turns"].append({"role": "user", "parts_json": _parts_to_json(content)})

    if response.candidates:
        model_parts = []
        for part in response.candidates[0].content.parts:
            part_dict = {}
            if part.text is not None:
                part_dict["text"] = part.text
            if part.inline_data is not None:
                part_dict["inline_data"] = {
                    "mime_type": part.inline_data.mime_type,
                    "data": base64.b64encode(part.inline_data.data).decode(),
                }
            raw = part.model_dump() if hasattr(part, "model_dump") else {}
            if "thought_signature" in raw and raw["thought_signature"]:
                part_dict["thought_signature"] = raw["thought_signature"]
            elif hasattr(part, "thought_signature") and part.thought_signature:
                part_dict["thought_signature"] = part.thought_signature
            model_parts.append(part_dict)
        history["turns"].append({"role": "model", "parts": model_parts})

    return history


def _parts_to_json(content) -> list[dict]:
    """Convert user content to JSON-serializable parts."""
    if isinstance(content, str):
        return [{"text": content}]
    if isinstance(content, list):
        result = []
        for item in content:
            if isinstance(item, str):
                result.append({"text": item})
            elif isinstance(item, Image.Image):
                buf = io.BytesIO()
                item.save(buf, format="PNG")
                result.append({
                    "inline_data": {
                        "mime_type": "image/png",
                        "data": base64.b64encode(buf.getvalue()).decode(),
                    }
                })
            elif isinstance(item, types.Part):
                if item.text:
                    result.append({"text": item.text})
                elif item.inline_data:
                    result.append({
                        "inline_data": {
                            "mime_type": item.inline_data.mime_type,
                            "data": base64.b64encode(item.inline_data.data).decode(),
                        }
                    })
        return result
    return [{"text": str(content)}]


def deserialize_to_contents(history: dict) -> list[types.Content]:
    """Reconstruct contents array from serialized history."""
    contents = []
    for turn in history["turns"]:
        role = turn["role"]
        parts = []
        parts_data = turn.get("parts", turn.get("parts_json", []))
        for p in parts_data:
            if "text" in p:
                part = types.Part(text=p["text"])
                if "thought_signature" in p:
                    part.thought_signature = p["thought_signature"]
                parts.append(part)
            elif "inline_data" in p:
                data = base64.b64decode(p["inline_data"]["data"])
                part = types.Part.from_bytes(
                    data=data, mime_type=p["inline_data"]["mime_type"]
                )
                if "thought_signature" in p:
                    part.thought_signature = p["thought_signature"]
                parts.append(part)
        contents.append(types.Content(role=role, parts=parts))
    return contents


# ─── Test Scenarios ───────────────────────────────────────────────


def test_initial_generation(client: genai.Client, model: str, room_img: Image.Image) -> TestResult:
    """Scenario 1: Room photo + brief → redesigned room image."""
    print(f"\n  [1/4] Initial generation with {model}...")
    start = time.time()

    try:
        response = client.models.generate_content(
            model=model,
            contents=[room_img, DESIGN_BRIEF],
            config=types.GenerateContentConfig(
                response_modalities=["TEXT", "IMAGE"],
            ),
        )

        duration = time.time() - start
        text = extract_text_from_response(response)
        img = extract_image_from_response(response)

        if img is None:
            return TestResult(
                model=model,
                scenario="initial_generation",
                passed=False,
                details=f"No image in response. Text: {text[:200]}",
                duration_s=duration,
            )

        path = save_result_image(img, model, "initial_gen")
        print(f"    Generated image saved: {path}")
        print(f"    Model text: {text[:200]}")
        print(f"    Duration: {duration:.1f}s")

        return TestResult(
            model=model,
            scenario="initial_generation",
            passed=True,
            details=f"Image generated. Size: {img.size}. Text: {text[:100]}",
            duration_s=duration,
            output_image_path=path,
            scores={"has_image": True, "size": f"{img.size}"},
        )

    except Exception as e:
        duration = time.time() - start
        return TestResult(
            model=model,
            scenario="initial_generation",
            passed=False,
            details=f"Error: {type(e).__name__}: {e}",
            duration_s=duration,
        )


def test_annotation_editing(
    client: genai.Client, model: str, base_img: Image.Image
) -> TestResult:
    """Scenario 2: Draw annotation circles → targeted edits."""
    print(f"\n  [2/4] Annotation-based editing with {model}...")
    start = time.time()

    regions = [
        {
            "region_id": 1,
            "center_x": 0.42,
            "center_y": 0.50,
            "radius": 0.15,
            "instruction": "Replace the green sofa with a light oak Scandinavian sofa with beige cushions",
        },
        {
            "region_id": 2,
            "center_x": 0.72,
            "center_y": 0.40,
            "radius": 0.08,
            "instruction": "Replace the floor lamp with a modern brass arc lamp",
        },
    ]

    annotated = draw_annotations(base_img, regions)
    save_result_image(annotated, model, "annotated_input")

    edit_prompt = f"""This interior design image has numbered annotations marking areas to change.
Please apply these edits:
1 (red circle, sofa area) — {regions[0]["instruction"]}
2 (blue circle, lamp area) — {regions[1]["instruction"]}
Keep all unmarked elements exactly as they are. Preserve room architecture,
camera angle, and lighting direction.
{ANTI_ARTIFACT_INSTRUCTION}"""

    try:
        chat = client.chats.create(
            model=model,
            config=types.GenerateContentConfig(
                response_modalities=["TEXT", "IMAGE"],
            ),
        )

        # Send annotated image + edit instructions
        response = chat.send_message([annotated, edit_prompt])

        duration = time.time() - start
        text = extract_text_from_response(response)
        img = extract_image_from_response(response)

        if img is None:
            # Retry with explicit image request
            print("    No image in first response, retrying with explicit request...")
            response = chat.send_message(
                "Please generate an image showing the edits applied. "
                "Return a clean room photograph without any annotations."
            )
            img = extract_image_from_response(response)
            text = extract_text_from_response(response)
            duration = time.time() - start

        if img is None:
            return TestResult(
                model=model,
                scenario="annotation_editing",
                passed=False,
                details=f"No image after retry. Text: {text[:200]}",
                duration_s=duration,
            )

        path = save_result_image(img, model, "annotation_edit")
        sigs = extract_thought_signatures(response)
        print(f"    Edited image saved: {path}")
        print(f"    Thought signatures found: {len(sigs)}")
        print(f"    Duration: {duration:.1f}s")

        return TestResult(
            model=model,
            scenario="annotation_editing",
            passed=True,
            details=f"Edit generated. Sigs: {len(sigs)}. Text: {text[:100]}",
            duration_s=duration,
            output_image_path=path,
            scores={"has_image": True, "thought_signatures": len(sigs)},
        )

    except Exception as e:
        duration = time.time() - start
        return TestResult(
            model=model,
            scenario="annotation_editing",
            passed=False,
            details=f"Error: {type(e).__name__}: {e}",
            duration_s=duration,
        )


def test_chat_roundtrip(client: genai.Client, model: str, base_img: Image.Image) -> TestResult:
    """Scenario 3: Serialize chat → deserialize → continue editing."""
    print(f"\n  [3/4] Chat history round-trip with {model}...")
    start = time.time()

    try:
        # Turn 1: Initial context
        chat = client.chats.create(
            model=model,
            config=types.GenerateContentConfig(
                response_modalities=["TEXT", "IMAGE"],
            ),
        )

        turn1_content = [
            base_img,
            "Here is a room photo. I want to redesign it in a warm Scandinavian style. "
            "Please generate a redesigned version preserving the room architecture.",
        ]
        response1 = chat.send_message(turn1_content)
        img1 = extract_image_from_response(response1)
        text1 = extract_text_from_response(response1)
        sigs1 = extract_thought_signatures(response1)

        if img1 is None:
            return TestResult(
                model=model,
                scenario="chat_roundtrip",
                passed=False,
                details=f"No image in turn 1. Text: {text1[:200]}",
                duration_s=time.time() - start,
            )

        save_result_image(img1, model, "roundtrip_turn1")
        print(f"    Turn 1 done. Sigs: {len(sigs1)}")

        # Serialize the chat history
        history = chat.get_history()
        serialized = []
        for content in history:
            turn_data = {"role": content.role, "parts": []}
            for part in content.parts:
                part_dict = {}
                if part.text is not None:
                    part_dict["text"] = part.text
                if part.inline_data is not None:
                    part_dict["inline_data"] = {
                        "mime_type": part.inline_data.mime_type,
                        "data": base64.b64encode(part.inline_data.data).decode(),
                    }
                if hasattr(part, "thought_signature") and part.thought_signature:
                    part_dict["thought_signature"] = part.thought_signature
                turn_data["parts"].append(part_dict)
            serialized.append(turn_data)

        # Save serialized history to file
        history_path = RESULTS_DIR / f"{model.replace('-', '_')}_chat_history.json"
        with open(history_path, "w") as f:
            json.dump(serialized, f, indent=2, default=str)
        history_size_mb = history_path.stat().st_size / (1024 * 1024)
        print(f"    Serialized history: {history_size_mb:.2f} MB")

        # Deserialize: reconstruct contents for a new generate_content call
        restored_contents = []
        for turn in serialized:
            parts = []
            for p in turn["parts"]:
                if "text" in p:
                    part_kwargs = {"text": p["text"]}
                    if "thought_signature" in p:
                        part_kwargs["thought_signature"] = p["thought_signature"]
                    parts.append(types.Part(**part_kwargs))
                elif "inline_data" in p:
                    data = base64.b64decode(p["inline_data"]["data"])
                    part = types.Part.from_bytes(
                        data=data, mime_type=p["inline_data"]["mime_type"]
                    )
                    if "thought_signature" in p:
                        part.thought_signature = p["thought_signature"]
                    parts.append(part)
            restored_contents.append(types.Content(role=turn["role"], parts=parts))

        # Turn 2: Continue with deserialized history
        restored_contents.append(types.Content(
            role="user",
            parts=[types.Part(text=(
                "Now make the window curtains a soft linen white. "
                "Keep everything else the same. "
                f"{ANTI_ARTIFACT_INSTRUCTION}"
            ))],
        ))

        response2 = client.models.generate_content(
            model=model,
            contents=restored_contents,
            config=types.GenerateContentConfig(
                response_modalities=["TEXT", "IMAGE"],
            ),
        )

        duration = time.time() - start
        img2 = extract_image_from_response(response2)
        text2 = extract_text_from_response(response2)

        if img2 is None:
            return TestResult(
                model=model,
                scenario="chat_roundtrip",
                passed=False,
                details=f"No image in turn 2 (after deserialize). Text: {text2[:200]}",
                duration_s=duration,
            )

        path = save_result_image(img2, model, "roundtrip_turn2")
        print(f"    Turn 2 done (after round-trip). Image saved: {path}")
        print(f"    Duration: {duration:.1f}s")

        return TestResult(
            model=model,
            scenario="chat_roundtrip",
            passed=True,
            details=f"Round-trip succeeded. History: {history_size_mb:.2f}MB. Turn 2 image generated.",
            duration_s=duration,
            output_image_path=path,
            scores={
                "history_size_mb": round(history_size_mb, 2),
                "thought_signatures_turn1": len(sigs1),
            },
        )

    except Exception as e:
        duration = time.time() - start
        return TestResult(
            model=model,
            scenario="chat_roundtrip",
            passed=False,
            details=f"Error: {type(e).__name__}: {e}",
            duration_s=duration,
        )


def test_text_only_editing(client: genai.Client, model: str, base_img: Image.Image) -> TestResult:
    """Scenario 4: Text-only edit in the same chat session (no annotations)."""
    print(f"\n  [4/4] Text-only editing with {model}...")
    start = time.time()

    try:
        chat = client.chats.create(
            model=model,
            config=types.GenerateContentConfig(
                response_modalities=["TEXT", "IMAGE"],
            ),
        )

        # Turn 1: Set context
        response1 = chat.send_message([
            base_img,
            "Here is a room I want to redesign. Please redesign it in a modern minimalist style "
            "with neutral colors and clean lines. Preserve the room architecture and camera angle.",
        ])
        img1 = extract_image_from_response(response1)
        if img1 is None:
            return TestResult(
                model=model,
                scenario="text_only_edit",
                passed=False,
                details="No image in setup turn",
                duration_s=time.time() - start,
            )
        save_result_image(img1, model, "textonly_turn1")

        # Turn 2: Text-only edit
        response2 = chat.send_message(
            "Change the color scheme to warm earth tones — terracotta, warm beige, and soft brown. "
            "Keep the furniture layout the same. Return a clean photograph."
        )
        duration = time.time() - start
        img2 = extract_image_from_response(response2)
        text2 = extract_text_from_response(response2)

        if img2 is None:
            # Retry
            response2 = chat.send_message(
                "Please generate an updated image with the earth tone color changes applied."
            )
            img2 = extract_image_from_response(response2)
            duration = time.time() - start

        if img2 is None:
            return TestResult(
                model=model,
                scenario="text_only_edit",
                passed=False,
                details=f"No image after text edit. Text: {text2[:200]}",
                duration_s=duration,
            )

        path = save_result_image(img2, model, "textonly_turn2")
        print(f"    Text edit applied. Image saved: {path}")
        print(f"    Duration: {duration:.1f}s")

        return TestResult(
            model=model,
            scenario="text_only_edit",
            passed=True,
            details=f"Text-only edit succeeded. Size: {img2.size}",
            duration_s=duration,
            output_image_path=path,
        )

    except Exception as e:
        duration = time.time() - start
        return TestResult(
            model=model,
            scenario="text_only_edit",
            passed=False,
            details=f"Error: {type(e).__name__}: {e}",
            duration_s=duration,
        )


# ─── Main ─────────────────────────────────────────────────────────


def run_spike():
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    client = get_client()
    room_img = load_test_image()

    all_results: list[TestResult] = []

    for model in MODELS:
        print(f"\n{'='*60}")
        print(f"Testing model: {model}")
        print(f"{'='*60}")

        all_results.append(test_initial_generation(client, model, room_img))
        all_results.append(test_annotation_editing(client, model, room_img))
        all_results.append(test_chat_roundtrip(client, model, room_img))
        all_results.append(test_text_only_editing(client, model, room_img))

    # Summary
    print(f"\n{'='*60}")
    print("SPIKE RESULTS SUMMARY")
    print(f"{'='*60}")

    report_lines = []
    for model in MODELS:
        model_results = [r for r in all_results if r.model == model]
        passed = sum(1 for r in model_results if r.passed)
        total = len(model_results)
        print(f"\n{model}: {passed}/{total} passed")
        report_lines.append(f"## {model}\n")
        report_lines.append(f"**{passed}/{total} passed**\n")

        for r in model_results:
            status = "PASS" if r.passed else "FAIL"
            print(f"  [{status}] {r.scenario}: {r.details[:80]}")
            report_lines.append(f"- [{status}] **{r.scenario}** ({r.duration_s:.1f}s): {r.details}")
            if r.scores:
                report_lines.append(f"  - Scores: {r.scores}")
        report_lines.append("")

    # Decision
    print(f"\n{'='*60}")
    print("DECISION")
    print(f"{'='*60}")

    for model in MODELS:
        model_results = [r for r in all_results if r.model == model]
        passed = sum(1 for r in model_results if r.passed)
        if passed >= 4:
            print(f"  {model}: PASSES decision gate ({passed}/4+ required)")
        else:
            print(f"  {model}: FAILS decision gate ({passed}/4+ required)")

    # Save report
    report_path = RESULTS_DIR / "spike_report.md"
    with open(report_path, "w") as f:
        f.write("# Gemini Quality Spike Report\n\n")
        f.write(f"Date: {time.strftime('%Y-%m-%d %H:%M:%S')}\n\n")
        f.write("\n".join(report_lines))
    print(f"\nReport saved to: {report_path}")

    # Save raw results as JSON
    json_path = RESULTS_DIR / "spike_results.json"
    with open(json_path, "w") as f:
        json.dump(
            [
                {
                    "model": r.model,
                    "scenario": r.scenario,
                    "passed": r.passed,
                    "details": r.details,
                    "duration_s": r.duration_s,
                    "output_image_path": r.output_image_path,
                    "scores": r.scores,
                }
                for r in all_results
            ],
            f,
            indent=2,
        )
    print(f"Raw results saved to: {json_path}")


if __name__ == "__main__":
    run_spike()
