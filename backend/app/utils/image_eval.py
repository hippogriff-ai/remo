"""Fast eval layer — local metrics that run in <100ms, no API calls.

Four metrics:
1. CLIP Text-Image Alignment (does the image match the brief?)
2. CLIP Image-Image Similarity (is it the same room?)
3. Edge-SSIM (are walls/windows/doors preserved?)
4. Annotation Artifact Detection (did annotation markers leak into the output?)

All metrics degrade gracefully when dependencies are missing — they return
neutral scores instead of crashing, so the main pipeline never blocks.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    import numpy as np
    from PIL import Image

    from app.models.contracts import DesignBrief

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Lazy imports — heavy deps gated behind try/except
# ---------------------------------------------------------------------------

_np_available = False
try:
    import numpy as np

    _np_available = True
except ImportError:
    np = None  # type: ignore[assignment]

_clip_model = None
_clip_preprocess = None
_clip_tokenize = None
_clip_available = False

try:
    import open_clip
    import torch

    _clip_available = True
except ImportError:
    open_clip = None  # type: ignore[assignment]
    torch = None  # type: ignore[assignment]

_cv2_available = False
try:
    import cv2

    _cv2_available = True
except ImportError:
    cv2 = None  # type: ignore[assignment]

_ssim_available = False
try:
    from skimage.metrics import structural_similarity as _ssim_fn

    _ssim_available = True
except ImportError:
    _ssim_fn = None  # type: ignore[assignment]


# ---------------------------------------------------------------------------
# Thresholds
# ---------------------------------------------------------------------------

CLIP_TEXT_THRESHOLD = 0.20
CLIP_IMAGE_THRESHOLD = 0.70
EDGE_SSIM_THRESHOLD = 0.30

COMPOSITE_WEIGHTS = {"clip_text": 0.35, "clip_image": 0.35, "edge_ssim": 0.30}
COMPOSITE_THRESHOLD = 0.40

# Canny edge detection params
CANNY_LOW = 50
CANNY_HIGH = 150

# Annotation artifact detection (HSV ranges for red/blue/green markers)
_ANNOTATION_HSV_RANGES = [
    # Red (wraps around hue=0)
    ((0, 120, 100), (10, 255, 255)),
    ((170, 120, 100), (180, 255, 255)),
    # Blue
    ((100, 120, 100), (130, 255, 255)),
    # Green
    ((35, 120, 100), (85, 255, 255)),
]
ARTIFACT_AREA_THRESHOLD = 0.001  # 0.1% of image area = suspicious
ARTIFACT_CIRCLE_MIN_RADIUS = 5
ARTIFACT_CIRCLE_MAX_RADIUS = 40


# ---------------------------------------------------------------------------
# Result dataclass
# ---------------------------------------------------------------------------


@dataclass
class FastEvalResult:
    """Result of the fast (local, $0) eval layer."""

    clip_text_score: float  # 0-1, similarity between image and brief text
    clip_image_score: float  # 0-1, similarity between original and generated
    edge_ssim_score: float  # 0-1, structural similarity on edge maps
    has_artifacts: bool  # True if annotation markers detected
    composite_score: float  # weighted average of the three scores
    needs_deep_eval: bool  # True if any metric below threshold
    metrics: dict[str, Any] = field(default_factory=dict)


# ---------------------------------------------------------------------------
# CLIP model loading (singleton)
# ---------------------------------------------------------------------------


def _load_clip():
    """Load CLIP ViT-B/32 model (lazy singleton, CPU only)."""
    global _clip_model, _clip_preprocess, _clip_tokenize
    if _clip_model is not None:
        return
    if not _clip_available:
        return

    model, _, preprocess = open_clip.create_model_and_transforms("ViT-B-32", pretrained="openai")
    model.eval()
    _clip_model = model
    _clip_preprocess = preprocess
    _clip_tokenize = open_clip.get_tokenizer("ViT-B-32")


# ---------------------------------------------------------------------------
# C0a: CLIP Text-Image Alignment
# ---------------------------------------------------------------------------


def _brief_to_text(brief: DesignBrief) -> str:
    """Build a natural sentence from the DesignBrief for CLIP comparison."""
    parts = []
    parts.append(brief.room_type)
    if brief.style_profile:
        if brief.style_profile.mood:
            parts.append(brief.style_profile.mood)
        if brief.style_profile.colors:
            parts.append(", ".join(brief.style_profile.colors[:4]))
        if brief.style_profile.textures:
            parts.append(", ".join(brief.style_profile.textures[:3]))
    return " ".join(parts)


def clip_text_image_score(image: Image.Image, brief: DesignBrief | None) -> float:
    """CLIP cosine similarity between the image and a text description of the brief.

    Returns 0.5 (neutral) if CLIP is unavailable or brief is None.
    """
    if brief is None:
        return 0.5
    if not _clip_available:
        logger.debug("clip_unavailable, returning neutral score")
        return 0.5
    _load_clip()
    if _clip_model is None:
        return 0.5

    text = _brief_to_text(brief)
    if not text.strip():
        return 0.5

    assert _clip_preprocess is not None  # ensured by _load_clip
    assert _clip_tokenize is not None

    img_tensor = _clip_preprocess(image).unsqueeze(0)
    text_tokens = _clip_tokenize([text])

    with torch.no_grad():  # type: ignore[union-attr]
        img_features = _clip_model.encode_image(img_tensor)
        text_features = _clip_model.encode_text(text_tokens)
        img_features /= img_features.norm(dim=-1, keepdim=True)
        text_features /= text_features.norm(dim=-1, keepdim=True)
        similarity = (img_features @ text_features.T).item()

    return float(max(0.0, min(1.0, similarity)))


# ---------------------------------------------------------------------------
# C0b: CLIP Image-Image Similarity
# ---------------------------------------------------------------------------


def clip_image_image_score(original: Image.Image, generated: Image.Image) -> float:
    """CLIP cosine similarity between two images (room preservation check).

    Returns 0.5 (neutral) if CLIP is unavailable.
    """
    if not _clip_available:
        logger.debug("clip_unavailable, returning neutral score")
        return 0.5
    _load_clip()
    if _clip_model is None:
        return 0.5

    assert _clip_preprocess is not None  # ensured by _load_clip

    img1 = _clip_preprocess(original).unsqueeze(0)
    img2 = _clip_preprocess(generated).unsqueeze(0)

    with torch.no_grad():  # type: ignore[union-attr]
        feat1 = _clip_model.encode_image(img1)
        feat2 = _clip_model.encode_image(img2)
        feat1 /= feat1.norm(dim=-1, keepdim=True)
        feat2 /= feat2.norm(dim=-1, keepdim=True)
        similarity = (feat1 @ feat2.T).item()

    return float(max(0.0, min(1.0, similarity)))


# ---------------------------------------------------------------------------
# C0c: Edge-SSIM
# ---------------------------------------------------------------------------

_EDGE_SSIM_SIZE = (512, 512)


def _to_edge_map(image: Image.Image) -> np.ndarray:
    """Convert a PIL image to a Canny edge map (grayscale uint8)."""
    gray = np.array(image.convert("L").resize(_EDGE_SSIM_SIZE))
    return cv2.Canny(gray, CANNY_LOW, CANNY_HIGH)


def edge_ssim_score(original: Image.Image, generated: Image.Image) -> float:
    """SSIM on Canny edge maps — measures structural (geometry) preservation.

    Returns 0.5 (neutral) if cv2 or scikit-image is unavailable.
    """
    if not _cv2_available or not _ssim_available or not _np_available:
        logger.debug("cv2/skimage/numpy unavailable, returning neutral edge_ssim")
        return 0.5

    edges_orig = _to_edge_map(original)
    edges_gen = _to_edge_map(generated)

    score = _ssim_fn(edges_orig, edges_gen, data_range=255)
    return float(max(0.0, min(1.0, score)))


# ---------------------------------------------------------------------------
# C0d: Annotation Artifact Detection
# ---------------------------------------------------------------------------


@dataclass
class ArtifactRegion:
    """A detected annotation artifact region."""

    x: int
    y: int
    radius: int
    color_range: str  # "red", "blue", or "green"


def detect_annotation_artifacts(
    image: Image.Image,
) -> tuple[bool, list[ArtifactRegion]]:
    """Detect residual annotation markers (colored circles) in an image.

    Looks for the same red/blue/green circle markers used by the annotation
    drawing utility (see image.py). Uses HSV color masking + HoughCircles.

    Returns (has_artifacts, regions). Returns (False, []) if cv2 is unavailable.
    """
    if not _cv2_available or not _np_available:
        logger.debug("cv2/numpy unavailable, skipping artifact detection")
        return False, []

    img_array = np.array(image.convert("RGB"))
    hsv = cv2.cvtColor(img_array, cv2.COLOR_RGB2HSV)
    h, w = img_array.shape[:2]
    total_pixels = h * w
    color_names = ["red", "red", "blue", "green"]

    regions: list[ArtifactRegion] = []

    for idx, (lower, upper) in enumerate(_ANNOTATION_HSV_RANGES):
        mask = cv2.inRange(hsv, np.array(lower), np.array(upper))
        colored_area = np.count_nonzero(mask)

        if colored_area / total_pixels < ARTIFACT_AREA_THRESHOLD:
            continue

        # Look for circular shapes in the mask
        circles = cv2.HoughCircles(
            mask,
            cv2.HOUGH_GRADIENT,
            dp=1.5,
            minDist=20,
            param1=50,
            param2=15,
            minRadius=ARTIFACT_CIRCLE_MIN_RADIUS,
            maxRadius=ARTIFACT_CIRCLE_MAX_RADIUS,
        )
        if circles is not None:
            for circle in circles[0]:
                cx, cy, cr = int(circle[0]), int(circle[1]), int(circle[2])
                regions.append(ArtifactRegion(x=cx, y=cy, radius=cr, color_range=color_names[idx]))

    return len(regions) > 0, regions


# ---------------------------------------------------------------------------
# Composite evaluator
# ---------------------------------------------------------------------------


def run_fast_eval(
    generated: Image.Image,
    original: Image.Image,
    brief: DesignBrief | None = None,
    *,
    is_edit: bool = False,
) -> FastEvalResult:
    """Run all fast eval metrics and produce a composite result.

    Args:
        generated: The AI-generated/edited image.
        original: The original room photo.
        brief: The DesignBrief describing desired style.
        is_edit: If True, also runs annotation artifact detection.

    Returns:
        FastEvalResult with individual scores and composite.
    """
    ct = clip_text_image_score(generated, brief)
    ci = clip_image_image_score(original, generated)
    es = edge_ssim_score(original, generated)

    has_artifacts = False
    artifact_regions: list[ArtifactRegion] = []
    if is_edit:
        has_artifacts, artifact_regions = detect_annotation_artifacts(generated)

    composite = (
        COMPOSITE_WEIGHTS["clip_text"] * ct
        + COMPOSITE_WEIGHTS["clip_image"] * ci
        + COMPOSITE_WEIGHTS["edge_ssim"] * es
    )

    needs_deep = (
        ct < CLIP_TEXT_THRESHOLD
        or ci < CLIP_IMAGE_THRESHOLD
        or es < EDGE_SSIM_THRESHOLD
        or composite < COMPOSITE_THRESHOLD
        or has_artifacts
    )

    return FastEvalResult(
        clip_text_score=ct,
        clip_image_score=ci,
        edge_ssim_score=es,
        has_artifacts=has_artifacts,
        composite_score=round(composite, 4),
        needs_deep_eval=needs_deep,
        metrics={
            "clip_text_raw": ct,
            "clip_image_raw": ci,
            "edge_ssim_raw": es,
            "artifact_count": len(artifact_regions),
            "artifact_regions": [
                {"x": r.x, "y": r.y, "radius": r.radius, "color": r.color_range}
                for r in artifact_regions
            ],
            "is_edit": is_edit,
        },
    )
